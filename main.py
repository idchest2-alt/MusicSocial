from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Header, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from sqlalchemy import create_engine, Column, Integer, String, DateTime, ForeignKey, UniqueConstraint, Float, Text, inspect, text
from sqlalchemy.orm import declarative_base, sessionmaker, Session
from datetime import datetime
from pathlib import Path
import hashlib
import secrets
import shutil
import uuid
import re
import os
import json
import hmac

import httpx
from dotenv import load_dotenv

load_dotenv()

app = FastAPI(title="MusicSocial API V4 - Paystack Live Credits")

# =========================
# PAYSTACK LIVE CONFIGURATION
# =========================
# Keep PAYSTACK_SECRET_KEY ONLY on the backend. Never put it in Android code.
PAYSTACK_SECRET_KEY = os.getenv("PAYSTACK_SECRET_KEY", "")
PAYSTACK_PUBLIC_KEY = os.getenv(
    "PAYSTACK_PUBLIC_KEY",
    "pk_live_27da0261b7770f325f3b3acd6c0699bbcf44c9be",
)
PAYSTACK_BASE_URL = "https://api.paystack.co"
OWNER_EMAIL = os.getenv("MUSICSOCIAL_OWNER_EMAIL", "").strip().lower()

# Price is in naira; Paystack receives amount in kobo.
CREATOR_COINS_PER_5000_NAIRA = 800
CREATOR_COIN_NAIRA = 5000 / CREATOR_COINS_PER_5000_NAIRA  # ₦6.25 per withdrawable coin
MIN_WITHDRAWAL_NAIRA = 5000
WITHDRAWAL_FEE_PERCENT = 15
VIDEO_BOOST_COINS = 20

CREDIT_PACKAGES = {
    "starter": {"name": "Starter", "naira": 500, "credits": 50},
    "creator": {"name": "Creator", "naira": 1000, "credits": 120},
    "pro": {"name": "Pro", "naira": 2500, "credits": 350},
    "growth": {"name": "Growth", "naira": 5000, "credits": 800},
    "mega": {"name": "Mega", "naira": 10000, "credits": 1800},
}

def is_owner(user) -> bool:
    return bool(OWNER_EMAIL and user.email.lower() == OWNER_EMAIL)

ALLOWED_ORIGINS = [x.strip() for x in os.getenv("MUSICSOCIAL_ALLOWED_ORIGINS", "*").split(",") if x.strip()]

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

BASE_DIR = Path(__file__).resolve().parent
DATABASE_URL = os.getenv("DATABASE_URL", f"sqlite:///{BASE_DIR / 'musicsocial.db'}")
engine_kwargs = {"pool_pre_ping": True}
if DATABASE_URL.startswith("sqlite"):
    engine_kwargs["connect_args"] = {"check_same_thread": False}
engine = create_engine(DATABASE_URL, **engine_kwargs)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

UPLOAD_DIR = Path(os.getenv("MUSICSOCIAL_UPLOAD_DIR", str(BASE_DIR / "uploads")))
UPLOAD_DIR.mkdir(exist_ok=True)
app.mount("/uploads", StaticFiles(directory=str(UPLOAD_DIR)), name="uploads")


class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, unique=True, nullable=False, index=True)
    email = Column(String, unique=True, nullable=False, index=True)
    password_hash = Column(String, nullable=False)
    bio = Column(String, default="")
    avatar_url = Column(String, default="")
    credits = Column(Integer, default=100)
    referral_code = Column(String, unique=True, nullable=True, index=True)
    referred_by_user_id = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    referral_coins = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.utcnow)


class SessionToken(Base):
    __tablename__ = "sessions"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    token = Column(String, unique=True, nullable=False, index=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class Post(Base):
    __tablename__ = "posts"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    username = Column(String, default="@creator")
    caption = Column(String, default="")
    video_url = Column(String, nullable=False)
    music_name = Column(String, default="Original Sound")
    likes = Column(Integer, default=0)
    comments = Column(Integer, default=0)
    shares = Column(Integer, default=0)
    views = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.utcnow)


class Comment(Base):
    __tablename__ = "comments"
    id = Column(Integer, primary_key=True, index=True)
    post_id = Column(Integer, nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    username = Column(String, default="@creator")
    text = Column(String, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)


class Like(Base):
    __tablename__ = "likes"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    post_id = Column(Integer, ForeignKey("posts.id"), nullable=False)
    __table_args__ = (UniqueConstraint("user_id", "post_id", name="uq_user_post_like"),)


class Follow(Base):
    __tablename__ = "follows"
    id = Column(Integer, primary_key=True)
    follower_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    following_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    __table_args__ = (UniqueConstraint("follower_id", "following_id", name="uq_follow"),)


class Notification(Base):
    __tablename__ = "notifications"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    text = Column(String, nullable=False)
    is_read = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.utcnow)


class Promotion(Base):
    __tablename__ = "promotions"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    post_id = Column(Integer, ForeignKey("posts.id"), nullable=False)
    credits = Column(Integer, nullable=False)
    status = Column(String, default="active")
    created_at = Column(DateTime, default=datetime.utcnow)


class PaymentTransaction(Base):
    __tablename__ = "payment_transactions"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    reference = Column(String, unique=True, nullable=False, index=True)
    package_id = Column(String, nullable=False)
    amount_naira = Column(Integer, nullable=False)
    credits = Column(Integer, nullable=False)
    status = Column(String, default="initialized", index=True)
    paystack_transaction_id = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    paid_at = Column(DateTime, nullable=True)



def _ensure_db_column(table: str, column: str, definition: str):
    """Add a missing column on both SQLite and PostgreSQL without requiring Alembic."""
    try:
        inspector = inspect(engine)
        if not inspector.has_table(table):
            return
        existing = {col["name"] for col in inspector.get_columns(table)}
        if column in existing:
            return
        with engine.begin() as conn:
            conn.execute(text(f'ALTER TABLE "{table}" ADD COLUMN "{column}" {definition}'))
    except Exception:
        # Do not prevent the API from starting because an optional migration was already applied
        # concurrently by another worker. Re-checking is safer than crashing the service.
        try:
            inspector = inspect(engine)
            if column in {col["name"] for col in inspector.get_columns(table)}:
                return
        except Exception:
            pass
        raise




class ReferralReward(Base):
    __tablename__ = "referral_rewards"
    id = Column(Integer, primary_key=True, index=True)
    referrer_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    referred_user_id = Column(Integer, ForeignKey("users.id"), nullable=False, unique=True, index=True)
    coins = Column(Integer, default=2)
    status = Column(String, default="eligible")
    created_at = Column(DateTime, default=datetime.utcnow)

class ViewEvent(Base):
    __tablename__ = "view_events"
    id = Column(Integer, primary_key=True, index=True)
    post_id = Column(Integer, ForeignKey("posts.id"), nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    __table_args__ = (UniqueConstraint("post_id", "user_id", name="uq_post_view_user"),)

class CoinTransfer(Base):
    __tablename__ = "coin_transfers"
    id = Column(Integer, primary_key=True, index=True)
    sender_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    recipient_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    coins = Column(Integer, nullable=False)
    note = Column(String, default="")
    created_at = Column(DateTime, default=datetime.utcnow)

class WithdrawalRequest(Base):
    __tablename__ = "withdrawal_requests"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    amount_naira = Column(Integer, nullable=False)
    fee_naira = Column(Integer, nullable=False)
    net_naira = Column(Integer, nullable=False)
    # Coins reserved by this request. Pending/paid requests reserve coins; rejected requests release them.
    reserved_coins = Column(Integer, default=0, nullable=False)
    status = Column(String, default="pending", index=True)
    bank_name = Column(String, default="")
    account_name = Column(String, default="")
    account_number = Column(String, default="")
    rejection_reason = Column(String, default="")
    processed_at = Column(DateTime, nullable=True)
    processed_by_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

Base.metadata.create_all(bind=engine)
_ensure_db_column("posts", "views", "INTEGER DEFAULT 0")
_ensure_db_column("users", "referral_code", "VARCHAR")
_ensure_db_column("users", "referred_by_user_id", "INTEGER")
_ensure_db_column("users", "referral_coins", "INTEGER DEFAULT 0")
_ensure_db_column("withdrawal_requests", "reserved_coins", "INTEGER DEFAULT 0")
_ensure_db_column("withdrawal_requests", "rejection_reason", "VARCHAR DEFAULT ''")
_ensure_db_column("withdrawal_requests", "processed_at", "TIMESTAMP NULL")
_ensure_db_column("withdrawal_requests", "processed_by_user_id", "INTEGER NULL")

# Backfill coin reservations for withdrawal rows created by older versions.
try:
    _migration_db = SessionLocal()
    _old_withdrawals = _migration_db.query(WithdrawalRequest).filter(
        WithdrawalRequest.amount_naira > 0,
        WithdrawalRequest.reserved_coins == 0,
        WithdrawalRequest.status.in_(["pending", "approved", "paid"])
    ).all()
    for _w in _old_withdrawals:
        _w.reserved_coins = (_w.amount_naira * CREATOR_COINS_PER_5000_NAIRA + 4999) // 5000
    if _old_withdrawals:
        _migration_db.commit()
    _migration_db.close()
except Exception:
    try:
        _migration_db.rollback()
        _migration_db.close()
    except Exception:
        pass

def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 120000)
    return salt.hex() + ":" + digest.hex()


def verify_password(password: str, stored: str) -> bool:
    try:
        salt_hex, digest_hex = stored.split(":")
        digest = hashlib.pbkdf2_hmac(
            "sha256", password.encode(), bytes.fromhex(salt_hex), 120000
        )
        return secrets.compare_digest(digest.hex(), digest_hex)
    except Exception:
        return False


def current_user(authorization: str | None) -> User:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "Login required")
    token = authorization[7:].strip()
    db = SessionLocal()
    try:
        session = db.query(SessionToken).filter(SessionToken.token == token).first()
        if not session:
            raise HTTPException(401, "Invalid or expired session")
        user = db.query(User).filter(User.id == session.user_id).first()
        if not user:
            raise HTTPException(401, "User not found")
        return user
    finally:
        db.close()


def user_json(user: User):
    return {
        "id": user.id,
        "username": user.username,
        "email": user.email,
        "bio": user.bio or "",
        "avatar_url": user.avatar_url or "",
        "credits": user.credits or 0,
    }


def post_json(post: Post, liked=False, following=False, hashtags=None):
    return {
        "id": post.id, "user_id": post.user_id, "username": post.username,
        "caption": post.caption, "video_url": post.video_url,
        "music_name": post.music_name, "likes": post.likes or 0,
        "comments": post.comments or 0, "shares": post.shares or 0, "views": post.views or 0,
        "liked": liked, "following": following, "hashtags": hashtags or [],
        "created_at": post.created_at.isoformat() if post.created_at else None,
    }



@app.get("/")
def root():
    return {"message": "MusicSocial API V2 is running", "status": "ok"}


@app.get("/health")
def health():
    return {"status": "healthy"}


@app.post("/auth/register")
def register(username: str = Form(...), email: str = Form(...), password: str = Form(...), referral_code: str = Form("")):
    username = username.strip()
    email = email.strip().lower()
    if not username.startswith("@"):
        username = "@" + username
    if len(username) < 3 or len(username) > 30:
        raise HTTPException(400, "Username must be 3-30 characters")
    if len(password) < 6:
        raise HTTPException(400, "Password must be at least 6 characters")
    db = SessionLocal()
    try:
        if db.query(User).filter((User.username == username) | (User.email == email)).first():
            raise HTTPException(400, "Username or email already exists")
        referrer = None
        clean_referral = referral_code.strip().upper()
        if clean_referral:
            referrer = db.query(User).filter(User.referral_code == clean_referral).first()
            if not referrer:
                raise HTTPException(400, "Invalid referral code")
        user = User(
            username=username,
            email=email,
            password_hash=hash_password(password),
            credits=100,
            referral_code=("MSC" + secrets.token_hex(5)).upper(),
            referred_by_user_id=referrer.id if referrer else None,
        )
        db.add(user)
        db.commit()
        db.refresh(user)
        token = secrets.token_urlsafe(48)
        db.add(SessionToken(user_id=user.id, token=token))
        if referrer and referrer.id != user.id:
            # Exactly one reward for this newly-created, unique account.
            referrer.referral_coins = (referrer.referral_coins or 0) + 2
            db.add(Notification(user_id=referrer.id, text=f"Referral reward: +2 coins for {user.username}"))
            db.add(ReferralReward(referrer_id=referrer.id, referred_user_id=user.id, coins=2, status="eligible"))
        db.commit()
        return {"success": True, "token": token, "user": user_json(user), "referral_code": user.referral_code}
    finally:
        db.close()


@app.post("/auth/login")
def login(email: str = Form(...), password: str = Form(...)):
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.email == email.strip().lower()).first()
        if not user or not verify_password(password, user.password_hash):
            raise HTTPException(401, "Invalid email or password")
        token = secrets.token_urlsafe(48)
        db.add(SessionToken(user_id=user.id, token=token))
        db.commit()
        return {"success": True, "token": token, "user": user_json(user)}
    finally:
        db.close()


@app.post("/auth/logout")
def logout(authorization: str | None = Header(default=None)):
    if authorization and authorization.startswith("Bearer "):
        token = authorization[7:].strip()
        db = SessionLocal()
        try:
            db.query(SessionToken).filter(SessionToken.token == token).delete()
            db.commit()
        finally:
            db.close()
    return {"success": True}


@app.get("/me")
def me(authorization: str | None = Header(default=None)):
    return {"success": True, "user": user_json(current_user(authorization))}


@app.put("/me")
def update_me(
    bio: str = Form(""),
    authorization: str | None = Header(default=None),
):
    user = current_user(authorization)
    db = SessionLocal()
    try:
        row = db.query(User).filter(User.id == user.id).first()
        row.bio = bio[:160]
        db.commit()
        db.refresh(row)
        return {"success": True, "user": user_json(row)}
    finally:
        db.close()


@app.get("/users/{username}")
def profile(username: str):
    username = username if username.startswith("@") else "@" + username
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.username == username).first()
        if not user:
            raise HTTPException(404, "User not found")
        followers = db.query(Follow).filter(Follow.following_id == user.id).count()
        following = db.query(Follow).filter(Follow.follower_id == user.id).count()
        posts = db.query(Post).filter(Post.user_id == user.id).order_by(Post.id.desc()).all()
        return {
            "success": True,
            "user": user_json(user),
            "followers": followers,
            "following": following,
            "posts": [post_json(p) for p in posts],
        }
    finally:
        db.close()


@app.post("/users/{user_id}/follow")
def follow_user(user_id: int, authorization: str | None = Header(default=None)):
    me_user = current_user(authorization)
    if me_user.id == user_id:
        raise HTTPException(400, "You cannot follow yourself")
    db = SessionLocal()
    try:
        target = db.query(User).filter(User.id == user_id).first()
        if not target:
            raise HTTPException(404, "User not found")
        existing = db.query(Follow).filter(
            Follow.follower_id == me_user.id, Follow.following_id == user_id
        ).first()
        if existing:
            db.delete(existing)
            following = False
        else:
            db.add(Follow(follower_id=me_user.id, following_id=user_id))
            db.add(Notification(
                user_id=user_id,
                text=f"{me_user.username} started following you"
            ))
            following = True
        db.commit()
        count = db.query(Follow).filter(Follow.following_id == user_id).count()
        return {"success": True, "following": following, "followers": count}
    finally:
        db.close()


@app.get("/posts")
def get_posts(authorization: str | None = Header(default=None)):
    db = SessionLocal()
    try:
        me_id = None
        if authorization and authorization.startswith("Bearer "):
            session = db.query(SessionToken).filter(
                SessionToken.token == authorization[7:].strip()
            ).first()
            if session:
                me_id = session.user_id
        posts = db.query(Post).order_by(Post.id.desc()).all()
        liked_ids = set()
        if me_id:
            liked_ids = {
                x.post_id for x in db.query(Like).filter(Like.user_id == me_id).all()
            }
        return {"success": True, "posts": [post_json(p, p.id in liked_ids) for p in posts]}
    finally:
        db.close()


@app.post("/posts")
async def create_post(
    username: str = Form("@creator"),
    caption: str = Form(""),
    music_name: str = Form("Original Sound"),
    video: UploadFile = File(...),
    authorization: str | None = Header(default=None),
):
    user = current_user(authorization)
    db = SessionLocal()
    file_path = None
    try:
        if not video.content_type or not video.content_type.startswith("video/"):
            raise HTTPException(400, f"Only video files are allowed. Received: {video.content_type}")
        extension = Path(video.filename or "video.mp4").suffix.lower() or ".mp4"
        filename = f"{uuid.uuid4().hex}{extension}"
        file_path = UPLOAD_DIR / filename
        max_bytes = 150 * 1024 * 1024
        total = 0
        with open(file_path, "wb") as buffer:
            while True:
                chunk = await video.read(1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > max_bytes:
                    raise HTTPException(413, "Video is too large (max 150 MB)")
                buffer.write(chunk)
        post = Post(
            user_id=user.id,
            username=user.username,
            caption=caption,
            video_url=f"/uploads/{filename}",
            music_name=music_name,
            likes=0,
            comments=0,
            shares=0,
        )
        db.add(post)
        db.commit()
        db.refresh(post)
        tags = sorted({t.lower() for t in re.findall(r"(?<!\w)#([A-Za-z0-9_]{1,40})", caption)})
        for tag in tags:
            db.add(PostHashtag(post_id=post.id, hashtag=tag))
        db.commit()
        return {"success": True, "post": post_json(post, hashtags=tags)}
    except HTTPException:
        if file_path and file_path.exists():
            file_path.unlink()
        raise
    except Exception as e:
        if file_path and file_path.exists():
            file_path.unlink()
        db.rollback()
        raise HTTPException(500, f"Upload failed: {e}")
    finally:
        await video.close()
        db.close()



@app.post("/posts/{post_id}/view")
def record_view(post_id: int, authorization: str | None = Header(default=None)):
    user = current_user(authorization)
    db = SessionLocal()
    try:
        post = db.query(Post).filter(Post.id == post_id).first()
        if not post:
            raise HTTPException(404, "Post not found")
        existing = db.query(ViewEvent).filter(ViewEvent.post_id == post_id, ViewEvent.user_id == user.id).first()
        if existing:
            return {"success": True, "counted": False, "views": post.views or 0}
        db.add(ViewEvent(post_id=post_id, user_id=user.id))
        post.views = (post.views or 0) + 1
        db.commit()
        return {"success": True, "counted": True, "views": post.views}
    finally:
        db.close()

@app.post("/posts/{post_id}/like")
def like_post(post_id: int, authorization: str | None = Header(default=None)):
    user = current_user(authorization)
    db = SessionLocal()
    try:
        post = db.query(Post).filter(Post.id == post_id).first()
        if not post:
            raise HTTPException(404, "Post not found")
        existing = db.query(Like).filter(Like.user_id == user.id, Like.post_id == post_id).first()
        if existing:
            db.delete(existing)
            post.likes = max(0, (post.likes or 0) - 1)
            liked = False
        else:
            db.add(Like(user_id=user.id, post_id=post_id))
            post.likes = (post.likes or 0) + 1
            liked = True
            if post.user_id and post.user_id != user.id:
                db.add(Notification(user_id=post.user_id, text=f"{user.username} liked your video"))
        db.commit()
        return {"success": True, "likes": post.likes, "liked": liked}
    finally:
        db.close()


@app.get("/posts/{post_id}/comments")
def get_comments(post_id: int):
    db = SessionLocal()
    try:
        if not db.query(Post).filter(Post.id == post_id).first():
            raise HTTPException(404, "Post not found")
        comments = db.query(Comment).filter(Comment.post_id == post_id).order_by(Comment.id.asc()).all()
        return {"success": True, "comments": [{
            "id": c.id, "post_id": c.post_id, "username": c.username,
            "text": c.text, "created_at": c.created_at.isoformat() if c.created_at else None
        } for c in comments]}
    finally:
        db.close()


@app.post("/posts/{post_id}/comments")
def add_comment(
    post_id: int,
    text: str = Form(...),
    authorization: str | None = Header(default=None),
):
    user = current_user(authorization)
    clean = text.strip()
    if not clean:
        raise HTTPException(400, "Comment cannot be empty")
    if len(clean) > 1000:
        raise HTTPException(400, "Comment is too long")
    db = SessionLocal()
    try:
        post = db.query(Post).filter(Post.id == post_id).first()
        if not post:
            raise HTTPException(404, "Post not found")
        c = Comment(post_id=post_id, user_id=user.id, username=user.username, text=clean)
        db.add(c)
        post.comments = (post.comments or 0) + 1
        if post.user_id and post.user_id != user.id:
            db.add(Notification(user_id=post.user_id, text=f"{user.username} commented on your video"))
        db.commit()
        db.refresh(c)
        return {"success": True, "comment": {
            "id": c.id, "post_id": c.post_id, "username": c.username,
            "text": c.text, "created_at": c.created_at.isoformat() if c.created_at else None
        }, "comments": post.comments}
    finally:
        db.close()


@app.post("/posts/{post_id}/share")
def share_post(post_id: int, authorization: str | None = Header(default=None)):
    current_user(authorization)
    db = SessionLocal()
    try:
        post = db.query(Post).filter(Post.id == post_id).first()
        if not post:
            raise HTTPException(404, "Post not found")
        post.shares = (post.shares or 0) + 1
        db.commit()
        return {"success": True, "shares": post.shares}
    finally:
        db.close()


@app.get("/notifications")
def notifications(authorization: str | None = Header(default=None)):
    user = current_user(authorization)
    db = SessionLocal()
    try:
        rows = db.query(Notification).filter(Notification.user_id == user.id).order_by(Notification.id.desc()).limit(50).all()
        return {"success": True, "notifications": [
            {"id": n.id, "text": n.text, "read": bool(n.is_read),
             "created_at": n.created_at.isoformat() if n.created_at else None}
            for n in rows
        ]}
    finally:
        db.close()


@app.post("/notifications/read")
def notifications_read(authorization: str | None = Header(default=None)):
    user = current_user(authorization)
    db = SessionLocal()
    try:
        db.query(Notification).filter(Notification.user_id == user.id).update({"is_read": 1})
        db.commit()
        return {"success": True}
    finally:
        db.close()


@app.get("/wallet")
def wallet(authorization: str | None = Header(default=None)):
    user = current_user(authorization)
    return {
        "success": True,
        "credits": (user.credits or 0) + (user.referral_coins or 0),
        "referral_coins": user.referral_coins or 0,
        "owner": is_owner(user),
        "currency": "NGN",
    }


@app.get("/credits/packages")
def credit_packages():
    return {
        "success": True,
        "currency": "NGN",
        "packages": [
            {"id": key, **value} for key, value in CREDIT_PACKAGES.items()
        ],
    }


def _paystack_headers():
    if not PAYSTACK_SECRET_KEY:
        raise HTTPException(503, "Paystack secret key is not configured on the server")
    return {
        "Authorization": f"Bearer {PAYSTACK_SECRET_KEY}",
        "Content-Type": "application/json",
    }


@app.post("/payments/paystack/initialize")
def initialize_paystack(
    package_id: str = Form(...),
    authorization: str | None = Header(default=None),
):
    user = current_user(authorization)
    package = CREDIT_PACKAGES.get(package_id)
    if not package:
        raise HTTPException(400, "Invalid credit package")

    reference = f"MSC-{uuid.uuid4().hex}"
    payload = {
        "email": user.email,
        "amount": str(package["naira"] * 100),
        "currency": "NGN",
        "reference": reference,
        "metadata": json.dumps({
            "user_id": user.id,
            "package_id": package_id,
            "credits": package["credits"],
        }),
    }

    try:
        response = httpx.post(
            f"{PAYSTACK_BASE_URL}/transaction/initialize",
            headers=_paystack_headers(),
            json=payload,
            timeout=30.0,
        )
        data = response.json()
    except Exception as exc:
        raise HTTPException(502, f"Could not connect to Paystack: {exc}")

    if response.status_code >= 400 or not data.get("status"):
        raise HTTPException(502, data.get("message", "Paystack initialization failed"))

    pay_data = data.get("data") or {}
    db = SessionLocal()
    try:
        db.add(PaymentTransaction(
            user_id=user.id,
            reference=reference,
            package_id=package_id,
            amount_naira=package["naira"],
            credits=package["credits"],
            status="initialized",
        ))
        db.commit()
    finally:
        db.close()

    return {
        "success": True,
        "public_key": PAYSTACK_PUBLIC_KEY,
        "reference": reference,
        "access_code": pay_data.get("access_code"),
        "authorization_url": pay_data.get("authorization_url"),
        "package": {"id": package_id, **package},
    }


def _fulfill_payment(db, transaction: PaymentTransaction, paystack_data: dict):
    if transaction.status == "success":
        return False

    transaction.status = "success"
    transaction.paystack_transaction_id = str(paystack_data.get("id", ""))
    transaction.paid_at = datetime.utcnow()
    user = db.query(User).filter(User.id == transaction.user_id).first()
    if not user:
        raise HTTPException(404, "User not found")
    user.credits = (user.credits or 0) + transaction.credits
    db.add(Notification(
        user_id=user.id,
        text=f"Payment successful. {transaction.credits} boost credits were added to your wallet."
    ))
    return True


@app.get("/payments/paystack/verify/{reference}")
def verify_paystack(reference: str, authorization: str | None = Header(default=None)):
    user = current_user(authorization)
    db = SessionLocal()
    try:
        transaction = db.query(PaymentTransaction).filter(
            PaymentTransaction.reference == reference,
            PaymentTransaction.user_id == user.id,
        ).first()
        if not transaction:
            raise HTTPException(404, "Payment transaction not found")

        try:
            response = httpx.get(
                f"{PAYSTACK_BASE_URL}/transaction/verify/{reference}",
                headers=_paystack_headers(),
                timeout=30.0,
            )
            data = response.json()
        except Exception as exc:
            raise HTTPException(502, f"Could not verify payment with Paystack: {exc}")

        pay_data = data.get("data") or {}
        if response.status_code >= 400 or not data.get("status"):
            raise HTTPException(400, data.get("message", "Payment verification failed"))

        if pay_data.get("status") != "success":
            transaction.status = pay_data.get("status", "pending")
            db.commit()
            return {"success": True, "paid": False, "status": transaction.status}

        # Verify the amount/currency before delivering credits.
        expected_kobo = transaction.amount_naira * 100
        if int(pay_data.get("amount") or 0) != expected_kobo or pay_data.get("currency") != "NGN":
            raise HTTPException(400, "Payment amount or currency does not match the credit package")

        added = _fulfill_payment(db, transaction, pay_data)
        db.commit()
        db.refresh(user)
        return {
            "success": True,
            "paid": True,
            "already_fulfilled": not added,
            "credits_added": transaction.credits if added else 0,
            "credits": user.credits or 0,
            "reference": reference,
        }
    finally:
        db.close()


@app.post("/payments/paystack/webhook")
async def paystack_webhook(
    request: Request,
    x_paystack_signature: str | None = Header(default=None),
):
    if not PAYSTACK_SECRET_KEY:
        raise HTTPException(503, "Paystack secret key is not configured")

    raw_body = await request.body()
    expected = hmac.new(
        PAYSTACK_SECRET_KEY.encode(),
        raw_body,
        hashlib.sha512,
    ).hexdigest()
    if not x_paystack_signature or not hmac.compare_digest(expected, x_paystack_signature):
        raise HTTPException(401, "Invalid Paystack signature")

    try:
        event = json.loads(raw_body.decode("utf-8"))
    except Exception:
        raise HTTPException(400, "Invalid JSON payload")

    if event.get("event") != "charge.success":
        return {"received": True}

    pay_data = event.get("data") or {}
    reference = pay_data.get("reference")
    if not reference:
        return {"received": True}

    db = SessionLocal()
    try:
        transaction = db.query(PaymentTransaction).filter(
            PaymentTransaction.reference == reference
        ).first()
        if not transaction:
            return {"received": True}

        if int(pay_data.get("amount") or 0) != transaction.amount_naira * 100:
            return {"received": True}
        if pay_data.get("currency") != "NGN":
            return {"received": True}

        _fulfill_payment(db, transaction, pay_data)
        db.commit()
        return {"received": True}
    finally:
        db.close()


@app.get("/admin/dashboard")
def admin_dashboard(authorization: str | None = Header(default=None)):
    user = current_user(authorization)
    if not is_owner(user):
        raise HTTPException(403, "Owner access required")
    db = SessionLocal()
    try:
        paid = db.query(PaymentTransaction).filter(PaymentTransaction.status == "success").all()
        promotions = db.query(Promotion).all()
        users = db.query(User).count()
        posts = db.query(Post).count()
        withdrawal_rows = db.query(WithdrawalRequest).order_by(WithdrawalRequest.id.desc()).limit(100).all()
        withdrawal_items = []
        for row in withdrawal_rows:
            creator = db.query(User).filter(User.id == row.user_id).first()
            withdrawal_items.append(_withdrawal_json(row, creator.username if creator else ""))
        pending = sum(1 for r in withdrawal_rows if r.status == "pending")
        return {
            "success": True,
            "owner": user_json(user),
            "owner_promotional_credits": "UNLIMITED",
            "revenue_naira": sum(t.amount_naira for t in paid),
            "credits_sold": sum(t.credits for t in paid),
            "credits_spent_on_boosts": sum(p.credits for p in promotions),
            "successful_payments": len(paid),
            "users": users,
            "posts": posts,
            "referral_rewards": db.query(ReferralReward).count(),
            "gift_coins_sent": sum(g.coins for g in db.query(CreatorGift).all()),
            "withdrawal_requests": db.query(WithdrawalRequest).count(),
            "pending_withdrawals": pending,
            "withdrawals": withdrawal_items,
        }
    finally:
        db.close()


@app.post("/promotions")
def promote_post(
    post_id: int = Form(...),
    credits: int = Form(...),
    authorization: str | None = Header(default=None),
):
    user = current_user(authorization)
    if credits != VIDEO_BOOST_COINS:
        raise HTTPException(400, "A video boost costs exactly 20 coins.")
    db = SessionLocal()
    try:
        post = db.query(Post).filter(Post.id == post_id).first()
        if not post:
            raise HTTPException(404, "Post not found")
        if post.user_id != user.id:
            raise HTTPException(403, "You can only boost your own videos")
        row = db.query(User).filter(User.id == user.id).first()
        owner = is_owner(user)
        if not owner:
            _spend_coins(row, credits)
        promotion = Promotion(user_id=user.id, post_id=post_id, credits=credits)
        db.add(promotion)
        if post.user_id and post.user_id != user.id:
            db.add(Notification(user_id=post.user_id, text=f"{user.username} boosted your video with {credits} coins"))
        db.commit()
        return {"success": True, "message": "Video boost activated", "credits": row.credits}
    finally:
        db.close()




@app.get("/discover")
def discover(category: str = "for_you", authorization: str | None = Header(default=None)):
    db = SessionLocal()
    try:
        rows = db.query(Post).order_by(Post.id.desc()).limit(300).all()
        cat = category.strip().lower()
        def text(p): return f"{p.caption or ''} {p.music_name or ''}".lower()
        if cat == "new_music":
            rows = sorted(rows, key=lambda p: p.id, reverse=True)[:100]
        elif cat == "trending":
            rows = sorted(rows, key=lambda p: ((p.likes or 0)*4 + (p.comments or 0)*3 + (p.shares or 0)*2 + (p.views or 0)), reverse=True)[:100]
        elif cat in {"afrobeats","street","emotional"}:
            keys = {"afrobeats":["afrobeats","afrobeat","afro","naija","yoruba"], "street":["street","hustle","igboro","streetpop","alhaji"], "emotional":["emotional","love","heartbreak","sad","feelings"]}[cat]
            rows = [p for p in rows if any(k in text(p) for k in keys) or any(k in (text(p)) for k in keys)]
        return {"success": True, "category": cat, "posts": [post_json(p) for p in rows]}
    finally:
        db.close()

@app.get("/music/{title}")
def music_feed(title: str):
    db = SessionLocal()
    try:
        rows = db.query(Post).filter(Post.music_name.ilike(f"%{title.strip()}%")).order_by(Post.id.desc()).limit(100).all()
        return {"success": True, "music": title, "posts": [post_json(p) for p in rows]}
    finally:
        db.close()

def _withdrawal_coins_for_amount(amount_naira: int) -> int:
    # 800 coins = ₦5,000, so every 4 coins = ₦25. Require exact ₦25 increments.
    if amount_naira % 25 != 0:
        raise HTTPException(400, "Withdrawal amount must be in ₦25 increments.")
    return (amount_naira * CREATOR_COINS_PER_5000_NAIRA) // 5000


def _available_withdrawable_coins(db, user_id: int) -> int:
    total = sum(
        (g.withdrawable_coins or 0)
        for g in db.query(CreatorGift).filter(CreatorGift.recipient_id == user_id).all()
    )
    reserved = sum(
        (w.reserved_coins or 0)
        for w in db.query(WithdrawalRequest).filter(
            WithdrawalRequest.user_id == user_id,
            WithdrawalRequest.status.in_(["pending", "approved", "paid"])
        ).all()
    )
    return max(0, total - reserved)


def _withdrawal_json(row: WithdrawalRequest, username: str | None = None):
    return {
        "id": row.id,
        "username": username or "",
        "amount_naira": row.amount_naira,
        "fee_naira": row.fee_naira,
        "net_naira": row.net_naira,
        "reserved_coins": row.reserved_coins or 0,
        "status": row.status,
        "bank_name": row.bank_name or "",
        "account_name": row.account_name or "",
        "account_number": row.account_number or "",
        "rejection_reason": row.rejection_reason or "",
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "processed_at": row.processed_at.isoformat() if row.processed_at else None,
    }


@app.get("/creator/earnings")
def creator_earnings(authorization: str | None = Header(default=None)):
    user = current_user(authorization)
    db = SessionLocal()
    try:
        promos = db.query(Promotion).filter(Promotion.user_id == user.id).all()
        videos = db.query(Post).filter(Post.user_id == user.id).all()
        gifts = db.query(CreatorGift).filter(CreatorGift.recipient_id == user.id).all()
        return {
            "success": True, "credits": user.credits or 0,
            "videos": len(videos),
            "likes": sum(p.likes or 0 for p in videos),
            "comments": sum(p.comments or 0 for p in videos),
            "shares": sum(p.shares or 0 for p in videos),
            "views": sum(p.views or 0 for p in videos),
            "credits_spent_on_boosts": sum(p.credits or 0 for p in promos),
            "gift_coins_received": sum(g.coins for g in gifts),
            "withdrawable_coins": sum((g.withdrawable_coins or 0) for g in gifts),
            "available_withdrawable_coins": _available_withdrawable_coins(db, user.id),
            "creator_earnings": sum((g.withdrawable_coins or 0) for g in gifts),
            "creator_earnings_naira": int(sum((g.withdrawable_coins or 0) for g in gifts) * CREATOR_COIN_NAIRA),
            "available_earnings_naira": int(_available_withdrawable_coins(db, user.id) * CREATOR_COIN_NAIRA)
        }
    finally:
        db.close()


@app.get("/rewards")
def rewards(authorization: str | None = Header(default=None)):
    user = current_user(authorization); db = SessionLocal()
    try:
        row = db.query(User).filter(User.id == user.id).first()
        if not row.referral_code:
            row.referral_code = ("MSC" + secrets.token_hex(5)).upper()
            db.commit()
        earned = db.query(ReferralReward).filter(ReferralReward.referrer_id == user.id).all()
        sent = sum(x.coins for x in db.query(CoinTransfer).filter(CoinTransfer.sender_id == user.id).all())
        gifts = sum(x.coins for x in db.query(CreatorGift).filter(CreatorGift.sender_id == user.id).all())
        received = sum(x.coins for x in db.query(CreatorGift).filter(CreatorGift.recipient_id == user.id).all())
        return {"success": True, "referral_code": user.referral_code, "referral_coins": sum(x.coins for x in earned), "transfer_sent": sent, "gifts_sent": gifts, "gifts_received": received, "credits": (user.credits or 0) + (user.referral_coins or 0)}
    finally: db.close()

def _spendable_balances(user):
    referral = user.referral_coins or 0
    paid = user.credits or 0
    return referral, paid

def _spend_coins(user, amount:int):
    referral = min(user.referral_coins or 0, amount)
    paid = amount - referral
    if paid > (user.credits or 0):
        raise HTTPException(400, "Not enough coins")
    user.referral_coins = (user.referral_coins or 0) - referral
    user.credits = (user.credits or 0) - paid
    return referral, paid

@app.post("/coins/transfer")
def transfer_coins(recipient_username: str = Form(...), coins: int = Form(...), note: str = Form(""), authorization: str | None = Header(default=None)):
    user = current_user(authorization); coins = int(coins)
    if coins <= 0: raise HTTPException(400, "Coins must be greater than zero")
    db=SessionLocal()
    try:
        recipient = db.query(User).filter(User.username == (recipient_username.strip() if recipient_username.strip().startswith("@") else "@"+recipient_username.strip())).first()
        if not recipient: raise HTTPException(404, "Recipient not found")
        if recipient.id == user.id: raise HTTPException(400, "You cannot transfer coins to yourself")
        sender=db.query(User).filter(User.id==user.id).first()
        referral_used = 0
        if not is_owner(sender):
            referral_used, paid_used = _spend_coins(sender, coins)
        else:
            paid_used = coins
        # Keep the existing coin-transfer wallet credit, and also record the transfer as creator
        # earnings. This follows the requested behavior that transferred coins can be withdrawn.
        recipient.referral_coins = (recipient.referral_coins or 0) + referral_used
        recipient.credits = (recipient.credits or 0) + paid_used
        db.add(CoinTransfer(sender_id=sender.id, recipient_id=recipient.id, coins=coins, note=note[:160]))
        # Record the received amount in the same earnings ledger used by gifts.
        db.add(CreatorGift(
            sender_id=sender.id,
            recipient_id=recipient.id,
            room_id=None,
            coins=coins,
            withdrawable_coins=coins,
            gift_name="Coin Transfer"
        ))
        db.add(Notification(user_id=recipient.id, text=f"{sender.username} sent you {coins} coins"))
        db.commit(); return {"success":True,"credits":(sender.credits or 0)+(sender.referral_coins or 0) if not is_owner(sender) else 999999999999,"recipient":recipient.username,"referral_coins_used":referral_used}
    finally: db.close()

@app.post("/gifts")
def send_gift(recipient_username: str = Form(...), coins: int = Form(...), gift_name: str = Form("Gift"), room_id: int | None = Form(None), authorization: str | None = Header(default=None)):
    user=current_user(authorization); coins=int(coins)
    if coins<=0: raise HTTPException(400,"Gift amount must be greater than zero")
    db=SessionLocal()
    try:
        normalized=recipient_username.strip(); normalized=normalized if normalized.startswith("@") else "@"+normalized
        recipient=db.query(User).filter(User.username==normalized).first()
        if not recipient: raise HTTPException(404,"Creator not found")
        if recipient.id==user.id: raise HTTPException(400,"You cannot gift yourself")
        sender=db.query(User).filter(User.id==user.id).first()
        referral_used = 0
        if not is_owner(sender):
            referral_used, paid_used = _spend_coins(sender, coins)
        else:
            paid_used = coins
        db.add(CreatorGift(sender_id=sender.id,recipient_id=recipient.id,room_id=room_id,coins=coins,withdrawable_coins=paid_used,gift_name=gift_name[:80]))
        db.add(Notification(user_id=recipient.id,text=f"{sender.username} sent you a {gift_name} gift worth {coins} coins"))
        db.commit(); return {"success":True,"credits":(sender.credits or 0)+(sender.referral_coins or 0) if not is_owner(sender) else 999999999999,"referral_coins_used":referral_used}
    finally: db.close()

@app.get("/withdrawals")
def withdrawals(authorization: str | None = Header(default=None)):
    user = current_user(authorization)
    db = SessionLocal()
    try:
        rows = db.query(WithdrawalRequest).filter(
            WithdrawalRequest.user_id == user.id
        ).order_by(WithdrawalRequest.id.desc()).limit(50).all()
        return {
            "success": True,
            "available_withdrawable_coins": _available_withdrawable_coins(db, user.id),
            "available_earnings_naira": int(_available_withdrawable_coins(db, user.id) * CREATOR_COIN_NAIRA),
            "withdrawals": [_withdrawal_json(r) for r in rows]
        }
    finally:
        db.close()


@app.post("/withdrawals")
def request_withdrawal(
    amount_naira: int = Form(...),
    bank_name: str = Form(...),
    account_name: str = Form(...),
    account_number: str = Form(...),
    authorization: str | None = Header(default=None)
):
    user = current_user(authorization)
    amount = int(amount_naira)
    bank_name = bank_name.strip()
    account_name = account_name.strip()
    account_number = account_number.strip()

    if amount < MIN_WITHDRAWAL_NAIRA:
        raise HTTPException(400, "Minimum withdrawal is ₦5,000.")
    if not bank_name or not account_name or not account_number:
        raise HTTPException(400, "Bank, account name and account number are required.")
    if not account_number.isdigit() or len(account_number) < 10:
        raise HTTPException(400, "Enter a valid bank account number.")

    reserved_coins = _withdrawal_coins_for_amount(amount)
    fee = (amount * WITHDRAWAL_FEE_PERCENT + 50) // 100
    net = amount - fee

    db = SessionLocal()
    try:
        available_coins = _available_withdrawable_coins(db, user.id)
        if reserved_coins > available_coins:
            available_naira = int(available_coins * CREATOR_COIN_NAIRA)
            raise HTTPException(
                400,
                f"Not enough withdrawable creator earnings. Available: ₦{available_naira:,} ({available_coins:,} coins)."
            )

        row = WithdrawalRequest(
            user_id=user.id,
            amount_naira=amount,
            fee_naira=fee,
            net_naira=net,
            reserved_coins=reserved_coins,
            status="pending",
            bank_name=bank_name[:120],
            account_name=account_name[:120],
            account_number=account_number[:40],
        )
        db.add(row)
        db.add(Notification(
            user_id=user.id,
            text=f"Withdrawal request received: ₦{amount:,} gross, ₦{fee:,} fee, ₦{net:,} net. Status: PENDING."
        ))
        db.commit()
        db.refresh(row)
        return {
            "success": True,
            **_withdrawal_json(row),
            "message": "Withdrawal request submitted. The owner will manually send the net amount and mark it paid."
        }
    finally:
        db.close()


@app.post("/admin/withdrawals/{withdrawal_id}/mark-paid")
def mark_withdrawal_paid(withdrawal_id: int, authorization: str | None = Header(default=None)):
    owner = current_user(authorization)
    if not is_owner(owner):
        raise HTTPException(403, "Owner access required")
    db = SessionLocal()
    try:
        row = db.query(WithdrawalRequest).filter(WithdrawalRequest.id == withdrawal_id).first()
        if not row:
            raise HTTPException(404, "Withdrawal request not found")
        if row.status != "pending":
            raise HTTPException(400, f"This withdrawal is already {row.status}.")
        row.status = "paid"
        row.processed_at = datetime.utcnow()
        row.processed_by_user_id = owner.id
        recipient = db.query(User).filter(User.id == row.user_id).first()
        if recipient:
            db.add(Notification(
                user_id=recipient.id,
                text=f"Your withdrawal of ₦{row.amount_naira:,} was marked PAID. ₦{row.net_naira:,} was the net payout after the 15% fee."
            ))
        db.commit()
        db.refresh(row)
        return {"success": True, "withdrawal": _withdrawal_json(row, recipient.username if recipient else "")}
    finally:
        db.close()


@app.post("/admin/withdrawals/{withdrawal_id}/reject")
def reject_withdrawal(
    withdrawal_id: int,
    reason: str = Form("Withdrawal rejected by owner."),
    authorization: str | None = Header(default=None)
):
    owner = current_user(authorization)
    if not is_owner(owner):
        raise HTTPException(403, "Owner access required")
    db = SessionLocal()
    try:
        row = db.query(WithdrawalRequest).filter(WithdrawalRequest.id == withdrawal_id).first()
        if not row:
            raise HTTPException(404, "Withdrawal request not found")
        if row.status != "pending":
            raise HTTPException(400, f"This withdrawal is already {row.status}.")
        row.status = "rejected"
        row.rejection_reason = (reason or "Withdrawal rejected by owner.").strip()[:300]
        row.processed_at = datetime.utcnow()
        row.processed_by_user_id = owner.id
        recipient = db.query(User).filter(User.id == row.user_id).first()
        if recipient:
            db.add(Notification(
                user_id=recipient.id,
                text=f"Your withdrawal request for ₦{row.amount_naira:,} was rejected. Your reserved earnings are available again. Reason: {row.rejection_reason}"
            ))
        db.commit()
        db.refresh(row)
        return {"success": True, "withdrawal": _withdrawal_json(row, recipient.username if recipient else "")}
    finally:
        db.close()


@app.get("/live")
def live_status():
    return {"success": True, "live": False, "message": "LIVE infrastructure ready for streaming provider integration"}



# =========================
# MUSIC / HASHTAG / CHAT / LIVE V3
# =========================

class MusicTrack(Base):
    __tablename__ = "music_tracks"
    id = Column(Integer, primary_key=True, index=True)
    owner_id = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    title = Column(String, nullable=False)
    artist = Column(String, default="")
    audio_url = Column(String, nullable=False)
    cover_url = Column(String, default="")
    uses = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.utcnow)

class PostHashtag(Base):
    __tablename__ = "post_hashtags"
    id = Column(Integer, primary_key=True, index=True)
    post_id = Column(Integer, ForeignKey("posts.id"), nullable=False, index=True)
    hashtag = Column(String, nullable=False, index=True)

class Conversation(Base):
    __tablename__ = "conversations"
    id = Column(Integer, primary_key=True, index=True)
    user_one_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    user_two_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    created_at = Column(DateTime, default=datetime.utcnow)

class Message(Base):
    __tablename__ = "messages"
    id = Column(Integer, primary_key=True, index=True)
    conversation_id = Column(Integer, ForeignKey("conversations.id"), nullable=False, index=True)
    sender_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    text = Column(String, nullable=False)
    is_read = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.utcnow)

class LiveRoom(Base):
    __tablename__ = "live_rooms"
    id = Column(Integer, primary_key=True, index=True)
    host_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    title = Column(String, default="Live on MusicSocial")
    status = Column(String, default="live")
    viewer_count = Column(Integer, default=0)
    room_key = Column(String, unique=True, nullable=False, index=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    ended_at = Column(DateTime, nullable=True)

class CreatorGift(Base):
    __tablename__ = "creator_gifts"
    id = Column(Integer, primary_key=True, index=True)
    sender_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    recipient_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    room_id = Column(Integer, ForeignKey("live_rooms.id"), nullable=True)
    coins = Column(Integer, nullable=False)
    withdrawable_coins = Column(Integer, default=0)
    gift_name = Column(String, default="Gift")
    created_at = Column(DateTime, default=datetime.utcnow)

Base.metadata.create_all(bind=engine)
@app.get("/music-hub")
def music_hub():
    db=SessionLocal()
    try:
        rows=db.query(MusicTrack).order_by(MusicTrack.uses.desc(),MusicTrack.id.desc()).limit(100).all()
        return {"success":True,"tracks":[{"id":r.id,"title":r.title,"artist":r.artist,"audio_url":r.audio_url,"cover_url":r.cover_url,"uses":r.uses or 0,"created_at":r.created_at.isoformat() if r.created_at else None} for r in rows]}
    finally: db.close()

@app.post("/music-hub/upload")
async def upload_music_hub(title:str=Form(...), audio:UploadFile=File(...), authorization:str|None=Header(default=None)):
    user=current_user(authorization)
    if not audio.content_type or not audio.content_type.startswith("audio/"): raise HTTPException(400,"Only audio files are allowed")
    data=await audio.read(25*1024*1024+1)
    if len(data)>25*1024*1024: raise HTTPException(413,"Audio file is too large (max 25 MB)")
    ext=Path(audio.filename or "song.mp3").suffix.lower() or ".mp3"
    name=f"song_{uuid.uuid4().hex}{ext}"; path=UPLOAD_DIR/name; path.write_bytes(data)
    db=SessionLocal()
    try:
        row=db.query(User).filter(User.id==user.id).first()
        if not is_owner(row):
            _spend_coins(row, 200)
        track=MusicTrack(owner_id=user.id,title=title.strip()[:120],artist=user.username,audio_url=f"/uploads/{name}",uses=0)
        db.add(track); db.commit(); db.refresh(track)
        return {"success":True,"credits":999999999999 if is_owner(row) else (row.credits or 0) + (row.referral_coins or 0),"upload_cost":200,"track":{"id":track.id,"title":track.title,"artist":track.artist,"audio_url":track.audio_url,"uses":0}}
    except Exception:
        db.rollback(); path.unlink(missing_ok=True); raise
    finally: db.close()

@app.post("/music-hub/{track_id}/use")
def use_music_track(track_id:int,authorization:str|None=Header(default=None)):
    current_user(authorization); db=SessionLocal()
    try:
        track=db.query(MusicTrack).filter(MusicTrack.id==track_id).first()
        if not track: raise HTTPException(404,"Song not found")
        track.uses=(track.uses or 0)+1; db.commit()
        return {"success":True,"uses":track.uses,"track":track.title}
    finally: db.close()



@app.get("/search")
def search_all(q: str = "", authorization: str | None = Header(default=None)):
    q = q.strip()
    db = SessionLocal()
    try:
        users = db.query(User).filter(User.username.ilike(f"%{q}%")).limit(20).all() if q else []
        posts = db.query(Post).filter(
            (Post.caption.ilike(f"%{q}%")) |
            (Post.username.ilike(f"%{q}%")) |
            (Post.music_name.ilike(f"%{q}%"))
        ).order_by(Post.id.desc()).limit(30).all() if q else []
        return {
            "success": True,
            "users": [user_json(u) for u in users],
            "posts": [post_json(p) for p in posts]
        }
    finally:
        db.close()

@app.get("/hashtags/{hashtag}")
def hashtag_feed(hashtag: str):
    tag = hashtag.strip().lstrip("#").lower()
    db = SessionLocal()
    try:
        rows = db.query(PostHashtag).filter(PostHashtag.hashtag == tag).order_by(PostHashtag.id.desc()).all()
        posts = []
        for row in rows:
            post = db.query(Post).filter(Post.id == row.post_id).first()
            if post:
                posts.append(post_json(post))
        return {"success": True, "hashtag": f"#{tag}", "posts": posts}
    finally:
        db.close()

@app.get("/trends")
def trends():
    db = SessionLocal()
    try:
        rows = db.query(PostHashtag).all()
        counts = {}
        for row in rows:
            counts[row.hashtag] = counts.get(row.hashtag, 0) + 1
        result = sorted(
            [{"hashtag": f"#{k}", "posts": v} for k, v in counts.items()],
            key=lambda x: x["posts"], reverse=True
        )[:20]
        return {"success": True, "trends": result}
    finally:
        db.close()

def _conversation(db, a, b):
    one, two = sorted([a, b])
    row = db.query(Conversation).filter(
        Conversation.user_one_id == one,
        Conversation.user_two_id == two
    ).first()
    if not row:
        row = Conversation(user_one_id=one, user_two_id=two)
        db.add(row)
        db.commit()
        db.refresh(row)
    return row

@app.get("/conversations")
def list_conversations(authorization: str | None = Header(default=None)):
    me = current_user(authorization)
    db = SessionLocal()
    try:
        rows = db.query(Conversation).filter(
            (Conversation.user_one_id == me.id) |
            (Conversation.user_two_id == me.id)
        ).order_by(Conversation.id.desc()).all()
        result = []
        for c in rows:
            other_id = c.user_two_id if c.user_one_id == me.id else c.user_one_id
            other = db.query(User).filter(User.id == other_id).first()
            last = db.query(Message).filter(
                Message.conversation_id == c.id
            ).order_by(Message.id.desc()).first()
            result.append({
                "id": c.id,
                "user": user_json(other) if other else None,
                "last_message": last.text if last else ""
            })
        return {"success": True, "conversations": result}
    finally:
        db.close()

@app.post("/conversations/{user_id}/messages")
def send_message(user_id: int, text: str = Form(...), authorization: str | None = Header(default=None)):
    me = current_user(authorization)
    clean = text.strip()
    if not clean:
        raise HTTPException(400, "Message cannot be empty")
    if me.id == user_id:
        raise HTTPException(400, "You cannot message yourself")
    db = SessionLocal()
    try:
        target = db.query(User).filter(User.id == user_id).first()
        if not target:
            raise HTTPException(404, "User not found")
        c = _conversation(db, me.id, user_id)
        msg = Message(conversation_id=c.id, sender_id=me.id, text=clean[:2000])
        db.add(msg)
        db.add(Notification(user_id=user_id, text=f"{me.username} sent you a message"))
        db.commit()
        db.refresh(msg)
        return {"success": True, "message": {
            "id": msg.id, "conversation_id": c.id,
            "sender_id": msg.sender_id, "text": msg.text,
            "created_at": msg.created_at.isoformat() if msg.created_at else None
        }}
    finally:
        db.close()

@app.get("/conversations/{conversation_id}/messages")
def get_messages(conversation_id: int, authorization: str | None = Header(default=None)):
    me = current_user(authorization)
    db = SessionLocal()
    try:
        c = db.query(Conversation).filter(Conversation.id == conversation_id).first()
        if not c or me.id not in (c.user_one_id, c.user_two_id):
            raise HTTPException(403, "Conversation not found")
        rows = db.query(Message).filter(
            Message.conversation_id == conversation_id
        ).order_by(Message.id.asc()).limit(200).all()
        db.query(Message).filter(
            Message.conversation_id == conversation_id,
            Message.sender_id != me.id
        ).update({"is_read": 1})
        db.commit()
        return {"success": True, "messages": [{
            "id": m.id, "conversation_id": m.conversation_id,
            "sender_id": m.sender_id, "text": m.text,
            "read": bool(m.is_read),
            "created_at": m.created_at.isoformat() if m.created_at else None
        } for m in rows]}
    finally:
        db.close()

@app.post("/live/start")
def start_live(title: str = Form("Live on MusicSocial"), authorization: str | None = Header(default=None)):
    me = current_user(authorization)
    db = SessionLocal()
    try:
        room = db.query(LiveRoom).filter(
            LiveRoom.host_id == me.id,
            LiveRoom.status == "live"
        ).first()
        if not room:
            room = LiveRoom(
                host_id=me.id,
                title=title.strip()[:120] or "Live on MusicSocial",
                room_key=secrets.token_urlsafe(24)
            )
            db.add(room)
            db.commit()
            db.refresh(room)
        return {"success": True, "room": {
            "id": room.id, "title": room.title,
            "room_key": room.room_key,
            "viewer_count": room.viewer_count or 0,
            "status": room.status
        }}
    finally:
        db.close()

@app.post("/live/{room_id}/end")
def end_live(room_id: int, authorization: str | None = Header(default=None)):
    me = current_user(authorization)
    db = SessionLocal()
    try:
        room = db.query(LiveRoom).filter(LiveRoom.id == room_id).first()
        if not room:
            raise HTTPException(404, "Live room not found")
        if room.host_id != me.id:
            raise HTTPException(403, "Only the host can end this live")
        room.status = "ended"
        room.ended_at = datetime.utcnow()
        db.commit()
        return {"success": True}
    finally:
        db.close()

@app.get("/live/rooms")
def live_rooms():
    db = SessionLocal()
    try:
        rows = db.query(LiveRoom).filter(
            LiveRoom.status == "live"
        ).order_by(LiveRoom.id.desc()).limit(50).all()
        return {"success": True, "rooms": [{
            "id": r.id, "host_id": r.host_id, "title": r.title,
            "room_key": r.room_key, "viewer_count": r.viewer_count or 0,
            "status": r.status
        } for r in rows]}
    finally:
        db.close()
