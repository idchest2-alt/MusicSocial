from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Header, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from sqlalchemy import create_engine, Column, Integer, String, DateTime, ForeignKey, UniqueConstraint, inspect
from sqlalchemy.orm import declarative_base, sessionmaker
from datetime import datetime, timedelta
from pathlib import Path
import hashlib, secrets, uuid, re, os, json, hmac, math
from typing import Optional
import httpx
from dotenv import load_dotenv

load_dotenv()

app = FastAPI(title="MusicSocial API")

PAYSTACK_SECRET_KEY = os.getenv("PAYSTACK_SECRET_KEY", "")
PAYSTACK_PUBLIC_KEY = os.getenv("PAYSTACK_PUBLIC_KEY", "pk_live_27da0261b7770f325f3b3acd6c0699bbcf44c9be")
PAYSTACK_BASE_URL = "https://api.paystack.co"
OWNER_EMAIL = os.getenv("MUSICSOCIAL_OWNER_EMAIL", "").strip().lower()
OWNER_USERNAME = os.getenv("MUSICSOCIAL_OWNER_USERNAME", "").strip()
if OWNER_USERNAME and not OWNER_USERNAME.startswith("@"):
    OWNER_USERNAME = "@" + OWNER_USERNAME

CREATOR_COIN_NAIRA = 6.25
WITHDRAWAL_COINS = 800
WITHDRAWAL_NAIRA = 5000
BOOST_COST = 20
MUSIC_UPLOAD_COST = 200
OWNER_PROMO = 999_999_999_999

CREDIT_PACKAGES = {
    "starter": {"name": "Starter", "naira": 500, "credits": 50},
    "creator": {"name": "Creator", "naira": 1000, "credits": 120},
    "pro": {"name": "Pro", "naira": 2500, "credits": 350},
    "growth": {"name": "Growth", "naira": 5000, "credits": 800},
    "mega": {"name": "Mega", "naira": 10000, "credits": 1800},
}

app.add_middleware(
    CORSMiddleware,
    allow_origins=[x.strip() for x in os.getenv("MUSICSOCIAL_ALLOWED_ORIGINS", "*").split(",") if x.strip()],
    allow_credentials=True, allow_methods=["*"], allow_headers=["*"],
)

BASE_DIR = Path(__file__).resolve().parent
DATABASE_URL = os.getenv("DATABASE_URL", f"sqlite:///{BASE_DIR / 'musicsocial.db'}")
kw = {"pool_pre_ping": True}
if DATABASE_URL.startswith("sqlite"):
    kw["connect_args"] = {"check_same_thread": False}
engine = create_engine(DATABASE_URL, **kw)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

UPLOAD_DIR = Path(os.getenv("MUSICSOCIAL_UPLOAD_DIR", str(BASE_DIR / "uploads")))
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
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
    referred_by_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    referral_coins = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.utcnow)

class SessionToken(Base):
    __tablename__ = "sessions"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    token = Column(String, unique=True, nullable=False, index=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    expires_at = Column(DateTime, nullable=True, index=True)

class Story(Base):
    __tablename__ = "stories"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    media_url = Column(String, nullable=False)
    media_type = Column(String, default="image")
    caption = Column(String, default="")
    created_at = Column(DateTime, default=datetime.utcnow)
    expires_at = Column(DateTime, nullable=False, index=True)

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
    boost_score = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.utcnow)

class Comment(Base):
    __tablename__ = "comments"
    id = Column(Integer, primary_key=True)
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
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    reference = Column(String, unique=True, nullable=False, index=True)
    package_id = Column(String, nullable=False)
    amount_naira = Column(Integer, nullable=False)
    credits = Column(Integer, nullable=False)
    status = Column(String, default="initialized", index=True)
    paystack_transaction_id = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    paid_at = Column(DateTime, nullable=True)

class ReferralReward(Base):
    __tablename__ = "referral_rewards"
    id = Column(Integer, primary_key=True)
    referrer_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    referred_user_id = Column(Integer, ForeignKey("users.id"), nullable=False, unique=True)
    coins = Column(Integer, default=2)
    status = Column(String, default="eligible")
    created_at = Column(DateTime, default=datetime.utcnow)

class ViewEvent(Base):
    __tablename__ = "view_events"
    id = Column(Integer, primary_key=True)
    post_id = Column(Integer, ForeignKey("posts.id"), nullable=False)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    __table_args__ = (UniqueConstraint("post_id", "user_id", name="uq_post_view_user"),)

class CoinTransfer(Base):
    __tablename__ = "coin_transfers"
    id = Column(Integer, primary_key=True)
    sender_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    recipient_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    coins = Column(Integer, nullable=False)
    note = Column(String, default="")
    created_at = Column(DateTime, default=datetime.utcnow)

class WithdrawalRequest(Base):
    __tablename__ = "withdrawal_requests"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    amount_naira = Column(Integer, nullable=False)
    fee_naira = Column(Integer, nullable=False)
    net_naira = Column(Integer, nullable=False)
    status = Column(String, default="pending")
    bank_name = Column(String, default="")
    account_name = Column(String, default="")
    account_number = Column(String, default="")
    withdrawable_coins = Column(Integer, default=0)
    paid_at = Column(DateTime, nullable=True)
    owner_note = Column(String, default="")
    created_at = Column(DateTime, default=datetime.utcnow)

class MusicTrack(Base):
    __tablename__ = "music_tracks"
    id = Column(Integer, primary_key=True)
    owner_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    title = Column(String, nullable=False)
    artist = Column(String, default="")
    audio_url = Column(String, nullable=False)
    cover_url = Column(String, default="")
    uses = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.utcnow)

class PostHashtag(Base):
    __tablename__ = "post_hashtags"
    id = Column(Integer, primary_key=True)
    post_id = Column(Integer, ForeignKey("posts.id"), nullable=False, index=True)
    hashtag = Column(String, nullable=False, index=True)

class Conversation(Base):
    __tablename__ = "conversations"
    id = Column(Integer, primary_key=True)
    user_one_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    user_two_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

class Message(Base):
    __tablename__ = "messages"
    id = Column(Integer, primary_key=True)
    conversation_id = Column(Integer, ForeignKey("conversations.id"), nullable=False, index=True)
    sender_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    text = Column(String, nullable=False)
    is_read = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.utcnow)

class LiveRoom(Base):
    __tablename__ = "live_rooms"
    id = Column(Integer, primary_key=True)
    host_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    title = Column(String, default="Live on MusicSocial")
    status = Column(String, default="live")
    viewer_count = Column(Integer, default=0)
    room_key = Column(String, unique=True, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    ended_at = Column(DateTime, nullable=True)

class LiveViewer(Base):
    __tablename__ = "live_viewers"
    id = Column(Integer, primary_key=True)
    room_id = Column(Integer, ForeignKey("live_rooms.id"), nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    __table_args__ = (UniqueConstraint("room_id", "user_id", name="uq_live_viewer"),)

class LiveMessage(Base):
    __tablename__ = "live_messages"
    id = Column(Integer, primary_key=True)
    room_id = Column(Integer, ForeignKey("live_rooms.id"), nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    username = Column(String, default="")
    text = Column(String, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

class CreatorGift(Base):
    __tablename__ = "creator_gifts"
    id = Column(Integer, primary_key=True)
    sender_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    recipient_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    room_id = Column(Integer, ForeignKey("live_rooms.id"), nullable=True)
    coins = Column(Integer, nullable=False)
    withdrawable_coins = Column(Integer, default=0)
    gift_name = Column(String, default="Gift")
    created_at = Column(DateTime, default=datetime.utcnow)

class DeviceToken(Base):
    __tablename__ = "device_tokens"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    token = Column(String, unique=True, nullable=False, index=True)
    platform = Column(String, default="android")
    created_at = Column(DateTime, default=datetime.utcnow)
    last_seen_at = Column(DateTime, default=datetime.utcnow)

class Report(Base):
    __tablename__ = "reports"
    id = Column(Integer, primary_key=True)
    reporter_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    target_type = Column(String, nullable=False)
    target_id = Column(Integer, nullable=True)
    reason = Column(String, nullable=False)
    details = Column(String, default="")
    status = Column(String, default="open", index=True)
    created_at = Column(DateTime, default=datetime.utcnow)

class Block(Base):
    __tablename__ = "blocks"
    id = Column(Integer, primary_key=True)
    blocker_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    blocked_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    __table_args__ = (UniqueConstraint("blocker_id", "blocked_id", name="uq_block_pair"),)

class VerificationRequest(Base):
    __tablename__ = "verification_requests"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, unique=True, index=True)
    note = Column(String, default="")
    status = Column(String, default="pending", index=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    reviewed_at = Column(DateTime, nullable=True)

class Draft(Base):
    __tablename__ = "drafts"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    video_url = Column(String, default="")
    caption = Column(String, default="")
    music_name = Column(String, default="Original Sound")
    trim_start_ms = Column(Integer, default=0)
    trim_end_ms = Column(Integer, default=0)
    crop = Column(String, default="")
    overlay_text = Column(String, default="")
    status = Column(String, default="draft")
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

class TypingStatus(Base):
    __tablename__ = "typing_status"
    id = Column(Integer, primary_key=True)
    conversation_id = Column(Integer, ForeignKey("conversations.id"), nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    is_typing = Column(Integer, default=0)
    updated_at = Column(DateTime, default=datetime.utcnow)

class UserActivity(Base):
    __tablename__ = "user_activity"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    activity_type = Column(String, nullable=False)
    post_id = Column(Integer, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

def _ensure_col(table, column, definition):
    # PostgreSQL uses TIMESTAMP rather than DATETIME.
    if engine.dialect.name == "postgresql":
        definition = definition.replace("DATETIME", "TIMESTAMP")

    with engine.begin() as conn:
        existing = {c["name"] for c in inspect(engine).get_columns(table)}
        if column not in existing:
            conn.exec_driver_sql(
                f'ALTER TABLE "{table}" ADD COLUMN "{column}" {definition}'
            )

Base.metadata.create_all(bind=engine)
_ensure_col("posts", "views", "INTEGER DEFAULT 0")
_ensure_col("posts", "boost_score", "INTEGER DEFAULT 0")
_ensure_col("users", "referral_code", "VARCHAR")
_ensure_col("users", "referred_by_user_id", "INTEGER")
_ensure_col("users", "referral_coins", "INTEGER DEFAULT 0")
_ensure_col("sessions", "expires_at", "DATETIME")
_ensure_col("users", "is_verified", "INTEGER DEFAULT 0")
_ensure_col("users", "last_seen_at", "DATETIME")
_ensure_col("withdrawal_requests", "withdrawable_coins", "INTEGER DEFAULT 0")
_ensure_col("withdrawal_requests", "paid_at", "TIMESTAMP")
_ensure_col("withdrawal_requests", "owner_note", "VARCHAR DEFAULT ''")

# Backfill the coin amount for withdrawal requests created before the new manual-payout system.
_db_backfill = SessionLocal()
try:
    for _w in _db_backfill.query(WithdrawalRequest).filter(WithdrawalRequest.withdrawable_coins == 0).all():
        _w.withdrawable_coins = int(math.ceil((_w.amount_naira or 0) * WITHDRAWAL_COINS / WITHDRAWAL_NAIRA))
    _db_backfill.commit()
finally:
    _db_backfill.close()

def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 120000)
    return salt.hex() + ":" + digest.hex()

def verify_password(password: str, stored: str) -> bool:
    try:
        salt_hex, digest_hex = stored.split(":")
        digest = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt_hex), 120000)
        return secrets.compare_digest(digest.hex(), digest_hex)
    except Exception:
        return False

def is_owner(user) -> bool:
    if OWNER_EMAIL and (user.email or "").lower() == OWNER_EMAIL:
        return True
    if OWNER_USERNAME and user.username == OWNER_USERNAME:
        return True
    return False

def current_user(authorization: str | None) -> User:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "Please sign in to continue.")
    token = authorization[7:].strip()
    db = SessionLocal()
    try:
        session = db.query(SessionToken).filter(SessionToken.token == token).first()
        if not session:
            raise HTTPException(401, "Your session is invalid. Please sign in again.")
        # Sessions are intentionally not auto-expired. A user stays signed in
        # until the session is explicitly logged out/revoked.
        user = db.query(User).filter(User.id == session.user_id).first()
        if not user:
            raise HTTPException(401, "Account not found.")
        return user
    finally:
        db.close()

def public_credits(user: User) -> int:
    return OWNER_PROMO if is_owner(user) else (user.credits or 0) + (user.referral_coins or 0)

def user_json(user: User):
    return {
        "id": user.id, "username": user.username, "email": user.email,
        "bio": user.bio or "", "avatar_url": user.avatar_url or "",
        "credits": public_credits(user), "is_owner": is_owner(user), "is_verified": bool(getattr(user, "is_verified", 0)),
    }

def post_json(post: Post, liked=False, following=False, hashtags=None):
    return {
        "id": post.id, "user_id": post.user_id, "username": post.username,
        "caption": post.caption, "video_url": post.video_url, "music_name": post.music_name,
        "likes": post.likes or 0, "comments": post.comments or 0, "shares": post.shares or 0,
        "views": post.views or 0, "boost_score": post.boost_score or 0,
        "liked": liked, "following": following, "hashtags": hashtags or [],
        "created_at": post.created_at.isoformat() if post.created_at else None,
    }

def _spend_coins(user, amount: int):
    if is_owner(user):
        return 0, 0
    referral = min(user.referral_coins or 0, amount)
    paid = amount - referral
    if paid > (user.credits or 0):
        raise HTTPException(400, f"Not enough coins. You need {amount} coins. Buy Boost Credits first.")
    user.referral_coins = (user.referral_coins or 0) - referral
    user.credits = (user.credits or 0) - paid
    return referral, paid

def _uname(name: str) -> str:
    name = (name or "").strip()
    return name if name.startswith("@") else "@" + name

def _live_msgs(db, room_id: int):
    rows = db.query(LiveMessage).filter(LiveMessage.room_id == room_id).order_by(LiveMessage.id.asc()).limit(80).all()
    return [{"id": m.id, "username": m.username, "text": m.text,
             "created_at": m.created_at.isoformat() if m.created_at else ""} for m in rows]

def _room_json(db, room: LiveRoom):
    host = db.query(User).filter(User.id == room.host_id).first()
    viewers = db.query(LiveViewer).filter(LiveViewer.room_id == room.id).count()
    room.viewer_count = viewers
    return {
        "id": room.id, "title": room.title, "host_id": room.host_id,
        "host_username": host.username if host else "@creator",
        "room_key": room.room_key, "viewers": viewers, "viewer_count": viewers,
        "live": room.status == "live", "status": room.status,
        "messages": _live_msgs(db, room.id),
    }

def story_json(db, story: Story):
    user = db.query(User).filter(User.id == story.user_id).first()
    return {
        "id": story.id,
        "user_id": story.user_id,
        "username": user.username if user else "@creator",
        "avatar_url": user.avatar_url if user else "",
        "media_url": story.media_url,
        "media_type": story.media_type or "image",
        "caption": story.caption or "",
        "created_at": story.created_at.isoformat() if story.created_at else "",
        "expires_at": story.expires_at.isoformat() if story.expires_at else "",
    }

@app.post("/me/avatar")
async def upload_avatar(avatar: UploadFile = File(...), authorization: str | None = Header(default=None)):
    user = current_user(authorization)
    allowed = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"}
    mime = (avatar.content_type or "").lower()
    if mime not in allowed:
        raise HTTPException(400, "Use JPG, PNG, or WEBP for your avatar.")
    db = SessionLocal(); path = None
    try:
        name = f"avatar_{user.id}_{uuid.uuid4().hex}{allowed[mime]}"
        path = UPLOAD_DIR / name
        total = 0
        with open(path, "wb") as f:
            while True:
                chunk = await avatar.read(1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > 8 * 1024 * 1024:
                    raise HTTPException(413, "Avatar is too large (max 8 MB).")
                f.write(chunk)
        row = db.query(User).filter(User.id == user.id).first()
        row.avatar_url = f"/uploads/{name}"
        db.commit(); db.refresh(row)
        return {"success": True, "user": user_json(row)}
    except HTTPException:
        if path and path.exists(): path.unlink()
        raise
    except Exception as e:
        if path and path.exists(): path.unlink()
        db.rollback()
        raise HTTPException(500, f"Avatar upload failed: {e}")
    finally:
        await avatar.close(); db.close()

@app.get("/stories")
def get_stories(authorization: str | None = Header(default=None)):
    me = current_user(authorization)
    db = SessionLocal()
    try:
        now = datetime.utcnow()
        db.query(Story).filter(Story.expires_at <= now).delete(synchronize_session=False)
        db.commit()
        following_ids = {x.following_id for x in db.query(Follow).filter(Follow.follower_id == me.id).all()}
        rows = db.query(Story).filter(Story.expires_at > now).order_by(Story.created_at.desc()).limit(200).all()
        rows.sort(key=lambda x: (0 if x.user_id == me.id else 1 if x.user_id in following_ids else 2, -(x.created_at.timestamp() if x.created_at else 0)))
        return {"success": True, "stories": [story_json(db, x) for x in rows]}
    finally:
        db.close()

@app.post("/stories")
async def upload_story(media: UploadFile = File(...), caption: str = Form(""), authorization: str | None = Header(default=None)):
    user = current_user(authorization)
    allowed = {
        "image/jpeg": (".jpg", "image"), "image/png": (".png", "image"),
        "image/webp": (".webp", "image"), "video/mp4": (".mp4", "video"),
        "video/webm": (".webm", "video"), "video/quicktime": (".mov", "video"),
    }
    mime = (media.content_type or "").lower()
    if mime not in allowed:
        raise HTTPException(400, "Use JPG, PNG, WEBP, MP4, WEBM, or MOV for a story.")
    suffix, media_type = allowed[mime]
    db = SessionLocal(); path = None
    try:
        name = f"story_{user.id}_{uuid.uuid4().hex}{suffix}"
        path = UPLOAD_DIR / name
        total = 0
        limit = 30 * 1024 * 1024 if media_type == "image" else 100 * 1024 * 1024
        with open(path, "wb") as f:
            while True:
                chunk = await media.read(1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > limit:
                    raise HTTPException(413, "Story file is too large.")
                f.write(chunk)
        story = Story(user_id=user.id, media_url=f"/uploads/{name}", media_type=media_type,
                      caption=caption[:300], expires_at=datetime.utcnow() + timedelta(hours=24))
        db.add(story); db.commit(); db.refresh(story)
        return {"success": True, "story": story_json(db, story)}
    except HTTPException:
        if path and path.exists(): path.unlink()
        raise
    except Exception as e:
        if path and path.exists(): path.unlink()
        db.rollback(); raise HTTPException(500, f"Story upload failed: {e}")
    finally:
        await media.close(); db.close()

@app.get("/")
def root():
    return {"message": "MusicSocial is running", "status": "ok"}

@app.get("/health")
def health():
    return {"status": "healthy", "owner_configured": bool(OWNER_EMAIL or OWNER_USERNAME)}

@app.post("/auth/register")
def register(username: str = Form(...), email: str = Form(...), password: str = Form(...), referral_code: str = Form("")):
    username = _uname(username); email = email.strip().lower()
    if len(username) < 3 or len(username) > 30:
        raise HTTPException(400, "Username must be 3–30 characters.")
    if len(password) < 6:
        raise HTTPException(400, "Password must be at least 6 characters.")
    db = SessionLocal()
    try:
        if db.query(User).filter((User.username == username) | (User.email == email)).first():
            raise HTTPException(400, "That username or email is already in use.")
        referrer = None
        code = referral_code.strip().upper()
        if code:
            referrer = db.query(User).filter(User.referral_code == code).first()
            if not referrer:
                raise HTTPException(400, "That referral code is not valid.")
        user = User(username=username, email=email, password_hash=hash_password(password),
                    credits=100, referral_code=("MSC" + secrets.token_hex(5)).upper(),
                    referred_by_user_id=referrer.id if referrer else None)
        db.add(user); db.commit(); db.refresh(user)
        token = secrets.token_urlsafe(48)
        db.add(SessionToken(user_id=user.id, token=token, expires_at=datetime.utcnow() + timedelta(days=30)))
        if referrer and referrer.id != user.id:
            referrer.referral_coins = (referrer.referral_coins or 0) + 2
            db.add(Notification(user_id=referrer.id, text=f"Referral reward: +2 coins for {user.username}"))
            db.add(ReferralReward(referrer_id=referrer.id, referred_user_id=user.id, coins=2))
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
            raise HTTPException(401, "Incorrect email or password.")
        token = secrets.token_urlsafe(48)
        db.add(SessionToken(user_id=user.id, token=token, expires_at=datetime.utcnow() + timedelta(days=30))); db.commit()
        return {"success": True, "token": token, "user": user_json(user)}
    finally:
        db.close()

@app.post("/auth/logout")
def logout(authorization: str | None = Header(default=None)):
    if authorization and authorization.startswith("Bearer "):
        db = SessionLocal()
        try:
            db.query(SessionToken).filter(SessionToken.token == authorization[7:].strip()).delete()
            db.commit()
        finally:
            db.close()
    return {"success": True}

@app.get("/me")
def me(authorization: str | None = Header(default=None)):
    return {"success": True, "user": user_json(current_user(authorization))}

@app.put("/me")
def update_me(bio: str = Form(""), authorization: str | None = Header(default=None)):
    user = current_user(authorization)
    db = SessionLocal()
    try:
        row = db.query(User).filter(User.id == user.id).first()
        row.bio = bio[:160]; db.commit(); db.refresh(row)
        return {"success": True, "user": user_json(row)}
    finally:
        db.close()

@app.get("/users/suggestions")
def user_suggestions(authorization: str | None = Header(default=None)):
    me = current_user(authorization)
    db = SessionLocal()
    try:
        users = db.query(User).filter(User.id != me.id).order_by(User.id.desc()).limit(30).all()
        out = []
        for u in users:
            out.append({
                "id": u.id, "username": u.username, "email": u.email, "bio": u.bio or "",
                "avatar_url": u.avatar_url or "",
                "followers": db.query(Follow).filter(Follow.following_id == u.id).count(),
                "following": db.query(Follow).filter(Follow.follower_id == me.id, Follow.following_id == u.id).first() is not None,
            })
        return {"success": True, "users": out}
    finally:
        db.close()

@app.get("/users/{username}")
def profile(username: str):
    username = _uname(username)
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.username == username).first()
        if not user:
            raise HTTPException(404, "Creator not found.")
        return {
            "success": True, "user": user_json(user),
            "followers": db.query(Follow).filter(Follow.following_id == user.id).count(),
            "following": db.query(Follow).filter(Follow.follower_id == user.id).count(),
            "posts": [post_json(p) for p in db.query(Post).filter(Post.user_id == user.id).order_by(Post.id.desc()).all()],
        }
    finally:
        db.close()

@app.post("/users/{user_id}/follow")
def follow_user(user_id: int, authorization: str | None = Header(default=None)):
    me = current_user(authorization)
    if me.id == user_id:
        raise HTTPException(400, "You cannot follow yourself.")
    db = SessionLocal()
    try:
        if not db.query(User).filter(User.id == user_id).first():
            raise HTTPException(404, "Creator not found.")
        existing = db.query(Follow).filter(Follow.follower_id == me.id, Follow.following_id == user_id).first()
        if existing:
            db.delete(existing); following = False
        else:
            db.add(Follow(follower_id=me.id, following_id=user_id))
            _notify(db, user_id, f"{me.username} started following you")
            following = True
        db.commit()
        return {"success": True, "following": following,
                "followers": db.query(Follow).filter(Follow.following_id == user_id).count()}
    finally:
        db.close()

@app.get("/posts")
def get_posts(authorization: str | None = Header(default=None)):
    db = SessionLocal()
    try:
        me_id = None
        if authorization and authorization.startswith("Bearer "):
            s = db.query(SessionToken).filter(SessionToken.token == authorization[7:].strip()).first()
            if s: me_id = s.user_id
        posts = db.query(Post).order_by(Post.boost_score.desc(), Post.id.desc()).all()
        liked = {x.post_id for x in db.query(Like).filter(Like.user_id == me_id).all()} if me_id else set()
        following = {x.following_id for x in db.query(Follow).filter(Follow.follower_id == me_id).all()} if me_id else set()
        return {"success": True, "posts": [post_json(p, p.id in liked, p.user_id in following if p.user_id else False) for p in posts]}
    finally:
        db.close()

@app.post("/posts")
async def create_post(caption: str = Form(""), music_name: str = Form("Original Sound"),
                      video: UploadFile = File(...), authorization: str | None = Header(default=None)):
    user = current_user(authorization)
    db = SessionLocal(); path = None
    try:
        if not video.content_type or not video.content_type.startswith("video/"):
            raise HTTPException(400, "Please choose a video file.")
        ext = Path(video.filename or "video.mp4").suffix.lower() or ".mp4"
        name = f"{uuid.uuid4().hex}{ext}"; path = UPLOAD_DIR / name
        total = 0
        with open(path, "wb") as f:
            while True:
                chunk = await video.read(1024 * 1024)
                if not chunk: break
                total += len(chunk)
                if total > 150 * 1024 * 1024:
                    raise HTTPException(413, "That video is too large (max 150 MB).")
                f.write(chunk)
        post = Post(user_id=user.id, username=user.username, caption=caption,
                    video_url=f"/uploads/{name}", music_name=music_name)
        db.add(post); db.commit(); db.refresh(post)
        tags = sorted({t.lower() for t in re.findall(r"(?<!\w)#([A-Za-z0-9_]{1,40})", caption)})
        for tag in tags:
            db.add(PostHashtag(post_id=post.id, hashtag=tag))
        db.commit()
        return {"success": True, "post": post_json(post, hashtags=tags)}
    except HTTPException:
        if path and path.exists(): path.unlink()
        raise
    except Exception as e:
        if path and path.exists(): path.unlink()
        db.rollback(); raise HTTPException(500, f"Upload failed: {e}")
    finally:
        await video.close(); db.close()

@app.post("/posts/{post_id}/view")
def record_view(post_id: int, authorization: str | None = Header(default=None)):
    user = current_user(authorization)
    db = SessionLocal()
    try:
        post = db.query(Post).filter(Post.id == post_id).first()
        if not post: raise HTTPException(404, "Video not found.")
        if db.query(ViewEvent).filter(ViewEvent.post_id == post_id, ViewEvent.user_id == user.id).first():
            return {"success": True, "counted": False, "views": post.views or 0}
        db.add(ViewEvent(post_id=post_id, user_id=user.id))
        post.views = (post.views or 0) + 1; db.commit()
        return {"success": True, "counted": True, "views": post.views}
    finally:
        db.close()

@app.post("/posts/{post_id}/like")
def like_post(post_id: int, authorization: str | None = Header(default=None)):
    user = current_user(authorization)
    db = SessionLocal()
    try:
        post = db.query(Post).filter(Post.id == post_id).first()
        if not post: raise HTTPException(404, "Video not found.")
        existing = db.query(Like).filter(Like.user_id == user.id, Like.post_id == post_id).first()
        if existing:
            db.delete(existing); post.likes = max(0, (post.likes or 0) - 1); liked = False
        else:
            db.add(Like(user_id=user.id, post_id=post_id)); post.likes = (post.likes or 0) + 1; liked = True
            if post.user_id and post.user_id != user.id:
                _notify(db, post.user_id, f"{user.username} liked your video")
        db.commit()
        return {"success": True, "likes": post.likes, "liked": liked}
    finally:
        db.close()

@app.get("/posts/{post_id}/comments")
def get_comments(post_id: int):
    db = SessionLocal()
    try:
        if not db.query(Post).filter(Post.id == post_id).first():
            raise HTTPException(404, "Video not found.")
        rows = db.query(Comment).filter(Comment.post_id == post_id).order_by(Comment.id.asc()).all()
        return {"success": True, "comments": [
            {"id": c.id, "post_id": c.post_id, "username": c.username, "text": c.text,
             "created_at": c.created_at.isoformat() if c.created_at else None} for c in rows]}
    finally:
        db.close()

@app.post("/posts/{post_id}/comments")
def add_comment(post_id: int, text: str = Form(...), authorization: str | None = Header(default=None)):
    user = current_user(authorization); clean = text.strip()
    if not clean: raise HTTPException(400, "Write a comment first.")
    db = SessionLocal()
    try:
        post = db.query(Post).filter(Post.id == post_id).first()
        if not post: raise HTTPException(404, "Video not found.")
        c = Comment(post_id=post_id, user_id=user.id, username=user.username, text=clean[:1000])
        db.add(c); post.comments = (post.comments or 0) + 1
        if post.user_id and post.user_id != user.id:
            _notify(db, post.user_id, f"{user.username} commented on your video")
        db.commit(); db.refresh(c)
        return {"success": True, "comment": {"id": c.id, "post_id": c.post_id, "username": c.username,
                "text": c.text, "created_at": c.created_at.isoformat() if c.created_at else None},
                "comments": post.comments}
    finally:
        db.close()

@app.post("/posts/{post_id}/share")
def share_post(post_id: int, authorization: str | None = Header(default=None)):
    current_user(authorization)
    db = SessionLocal()
    try:
        post = db.query(Post).filter(Post.id == post_id).first()
        if not post: raise HTTPException(404, "Video not found.")
        post.shares = (post.shares or 0) + 1; db.commit()
        return {"success": True, "shares": post.shares}
    finally:
        db.close()

def _send_optional_push(user_id: int, title: str, body: str):
    # Optional FCM support. Configure FIREBASE_SERVICE_ACCOUNT_JSON on Railway
    # and install firebase-admin in requirements.txt to enable phone push.
    service_json = os.getenv("FIREBASE_SERVICE_ACCOUNT_JSON", "").strip()
    if not service_json:
        return
    try:
        import firebase_admin
        from firebase_admin import credentials, messaging
        try:
            firebase_admin.get_app()
        except ValueError:
            cred = credentials.Certificate(json.loads(service_json))
            firebase_admin.initialize_app(cred)
        db = SessionLocal()
        try:
            tokens = db.query(DeviceToken).filter(DeviceToken.user_id == user_id).all()
            for row in tokens:
                try:
                    messaging.send(messaging.Message(
                        notification=messaging.Notification(title=title[:80], body=body[:200]),
                        token=row.token,
                    ))
                except Exception:
                    pass
        finally:
            db.close()
    except Exception:
        pass

def _notify(db, user_id: int, text: str, title: str = "MusicSocial"):
    if user_id:
        clean = text[:500]
        db.add(Notification(user_id=user_id, text=clean))
        # Push is sent after commit by a lightweight best-effort call.
        db.flush()
        _send_optional_push(user_id, title, clean)

@app.get("/notifications")
def notifications(authorization: str | None = Header(default=None)):
    user = current_user(authorization)
    db = SessionLocal()
    try:
        rows = db.query(Notification).filter(Notification.user_id == user.id).order_by(Notification.id.desc()).limit(100).all()
        unread = sum(1 for n in rows if not n.is_read)
        return {"success": True, "unread": unread, "notifications": [
            {"id": n.id, "text": n.text, "read": bool(n.is_read),
             "created_at": n.created_at.isoformat() if n.created_at else None} for n in rows]}
    finally:
        db.close()

@app.post("/notifications/{notification_id}/read")
def mark_notification_read(notification_id: int, authorization: str | None = Header(default=None)):
    user = current_user(authorization); db = SessionLocal()
    try:
        row = db.query(Notification).filter(Notification.id == notification_id, Notification.user_id == user.id).first()
        if not row: raise HTTPException(404, "Notification not found.")
        row.is_read = 1; db.commit()
        return {"success": True}
    finally:
        db.close()

@app.post("/notifications/read-all")
def mark_all_notifications_read(authorization: str | None = Header(default=None)):
    user = current_user(authorization); db = SessionLocal()
    try:
        db.query(Notification).filter(Notification.user_id == user.id, Notification.is_read == 0).update({"is_read": 1})
        db.commit()
        return {"success": True}
    finally:
        db.close()

@app.get("/wallet")
def wallet(authorization: str | None = Header(default=None)):
    user = current_user(authorization)
    return {"success": True, "credits": public_credits(user), "purchased_credits": user.credits or 0,
            "referral_coins": user.referral_coins or 0, "owner": is_owner(user), "currency": "NGN"}

@app.get("/credits/packages")
def credit_packages():
    return {"success": True, "currency": "NGN", "packages": [{"id": k, **v} for k, v in CREDIT_PACKAGES.items()]}

def _paystack_headers():
    if not PAYSTACK_SECRET_KEY:
        raise HTTPException(503, "Payments are not configured yet.")
    return {"Authorization": f"Bearer {PAYSTACK_SECRET_KEY}", "Content-Type": "application/json"}

@app.post("/payments/paystack/initialize")
def initialize_paystack(package_id: str = Form(...), authorization: str | None = Header(default=None)):
    user = current_user(authorization)
    package = CREDIT_PACKAGES.get(package_id)
    if not package: raise HTTPException(400, "Choose a valid coin package.")
    reference = f"MSC-{uuid.uuid4().hex}"
    try:
        response = httpx.post(f"{PAYSTACK_BASE_URL}/transaction/initialize", headers=_paystack_headers(),
            json={"email": user.email, "amount": str(package["naira"] * 100), "currency": "NGN",
                  "reference": reference, "metadata": json.dumps({"user_id": user.id, "package_id": package_id, "credits": package["credits"]})},
            timeout=30.0)
        data = response.json()
    except Exception as exc:
        raise HTTPException(502, f"Could not reach Paystack: {exc}")
    if response.status_code >= 400 or not data.get("status"):
        raise HTTPException(502, data.get("message", "Payment could not start."))
    pay = data.get("data") or {}
    db = SessionLocal()
    try:
        db.add(PaymentTransaction(user_id=user.id, reference=reference, package_id=package_id,
                                  amount_naira=package["naira"], credits=package["credits"], status="initialized"))
        db.commit()
    finally:
        db.close()
    return {"success": True, "public_key": PAYSTACK_PUBLIC_KEY, "reference": reference,
            "access_code": pay.get("access_code"), "authorization_url": pay.get("authorization_url"),
            "package": {"id": package_id, **package}}

def _fulfill_payment(db, tx: PaymentTransaction, paystack_data: dict):
    if tx.status == "success": return False
    tx.status = "success"; tx.paystack_transaction_id = str(paystack_data.get("id", "")); tx.paid_at = datetime.utcnow()
    user = db.query(User).filter(User.id == tx.user_id).first()
    if not user: raise HTTPException(404, "User not found.")
    user.credits = (user.credits or 0) + tx.credits
    _notify(db, user.id, f"Payment successful. {tx.credits} boost coins were added.")
    return True

@app.get("/payments/paystack/verify/{reference}")
def verify_paystack(reference: str, authorization: str | None = Header(default=None)):
    user = current_user(authorization)
    db = SessionLocal()
    try:
        tx = db.query(PaymentTransaction).filter(PaymentTransaction.reference == reference, PaymentTransaction.user_id == user.id).first()
        if not tx: raise HTTPException(404, "Payment not found.")
        try:
            response = httpx.get(f"{PAYSTACK_BASE_URL}/transaction/verify/{reference}", headers=_paystack_headers(), timeout=30.0)
            data = response.json()
        except Exception as exc:
            raise HTTPException(502, f"Could not verify payment: {exc}")
        pay = data.get("data") or {}
        if response.status_code >= 400 or not data.get("status"):
            raise HTTPException(400, data.get("message", "Payment verification failed."))
        if pay.get("status") != "success":
            tx.status = pay.get("status", "pending"); db.commit()
            return {"success": True, "paid": False, "status": tx.status}
        if int(pay.get("amount") or 0) != tx.amount_naira * 100 or pay.get("currency") != "NGN":
            raise HTTPException(400, "Payment amount does not match this package.")
        added = _fulfill_payment(db, tx, pay); db.commit(); db.refresh(user)
        return {"success": True, "paid": True, "already_fulfilled": not added,
                "credits_added": tx.credits if added else 0, "credits": public_credits(user), "reference": reference}
    finally:
        db.close()

@app.post("/payments/paystack/webhook")
async def paystack_webhook(request: Request, x_paystack_signature: str | None = Header(default=None)):
    if not PAYSTACK_SECRET_KEY: raise HTTPException(503, "Paystack is not configured.")
    raw = await request.body()
    expected = hmac.new(PAYSTACK_SECRET_KEY.encode(), raw, hashlib.sha512).hexdigest()
    if not x_paystack_signature or not hmac.compare_digest(expected, x_paystack_signature):
        raise HTTPException(401, "Invalid Paystack signature.")
    event = json.loads(raw.decode())
    if event.get("event") != "charge.success": return {"received": True}
    pay = event.get("data") or {}; ref = pay.get("reference")
    if not ref: return {"received": True}
    db = SessionLocal()
    try:
        tx = db.query(PaymentTransaction).filter(PaymentTransaction.reference == ref).first()
        if tx and int(pay.get("amount") or 0) == tx.amount_naira * 100 and pay.get("currency") == "NGN":
            _fulfill_payment(db, tx, pay); db.commit()
        return {"received": True}
    finally:
        db.close()


@app.post("/notifications/device-token")
def register_device_token(device_token: str = Form(...), platform: str = Form("android"), authorization: str | None = Header(default=None)):
    user = current_user(authorization); clean = device_token.strip()
    if not clean or len(clean) > 4096:
        raise HTTPException(400, "Invalid device token.")
    db = SessionLocal()
    try:
        row = db.query(DeviceToken).filter(DeviceToken.token == clean).first()
        if row:
            row.user_id = user.id; row.platform = platform[:30]; row.last_seen_at = datetime.utcnow()
        else:
            db.add(DeviceToken(user_id=user.id, token=clean, platform=platform[:30]))
        db.commit()
        return {"success": True}
    finally:
        db.close()

@app.delete("/notifications/device-token")
def unregister_device_token(device_token: str, authorization: str | None = Header(default=None)):
    user = current_user(authorization); db = SessionLocal()
    try:
        db.query(DeviceToken).filter(DeviceToken.user_id == user.id, DeviceToken.token == device_token).delete()
        db.commit(); return {"success": True}
    finally: db.close()

@app.post("/reports")
def create_report(target_type: str = Form(...), target_id: int | None = Form(None),
                 reason: str = Form(...), details: str = Form(""), authorization: str | None = Header(default=None)):
    me = current_user(authorization); clean_reason = reason.strip()[:120]
    if not clean_reason: raise HTTPException(400, "Choose a reason.")
    allowed = {"user", "post", "comment", "message", "live"}
    if target_type not in allowed: raise HTTPException(400, "Invalid report type.")
    db = SessionLocal()
    try:
        row = Report(reporter_id=me.id, target_type=target_type, target_id=target_id,
                     reason=clean_reason, details=details.strip()[:1000])
        db.add(row); db.commit(); db.refresh(row)
        return {"success": True, "report_id": row.id, "status": row.status}
    finally: db.close()

@app.get("/admin/reports")
def admin_reports(status: str = "open", authorization: str | None = Header(default=None)):
    me = current_user(authorization)
    if not is_owner(me): raise HTTPException(403, "Owner access required.")
    db = SessionLocal()
    try:
        rows = db.query(Report).filter(Report.status == status).order_by(Report.id.desc()).limit(200).all()
        return {"success": True, "reports": [{"id": r.id, "reporter_id": r.reporter_id,
            "target_type": r.target_type, "target_id": r.target_id, "reason": r.reason,
            "details": r.details, "status": r.status,
            "created_at": r.created_at.isoformat() if r.created_at else None} for r in rows]}
    finally: db.close()

@app.post("/admin/reports/{report_id}/resolve")
def resolve_report(report_id: int, status: str = Form("resolved"), authorization: str | None = Header(default=None)):
    me = current_user(authorization)
    if not is_owner(me): raise HTTPException(403, "Owner access required.")
    if status not in {"resolved", "dismissed", "open"}: raise HTTPException(400, "Invalid status.")
    db = SessionLocal()
    try:
        row = db.query(Report).filter(Report.id == report_id).first()
        if not row: raise HTTPException(404, "Report not found.")
        row.status = status; db.commit(); return {"success": True, "status": row.status}
    finally: db.close()

@app.post("/blocks/{user_id}")
def block_user(user_id: int, authorization: str | None = Header(default=None)):
    me = current_user(authorization)
    if me.id == user_id: raise HTTPException(400, "You cannot block yourself.")
    db = SessionLocal()
    try:
        target = db.query(User).filter(User.id == user_id).first()
        if not target: raise HTTPException(404, "User not found.")
        if not db.query(Block).filter(Block.blocker_id == me.id, Block.blocked_id == user_id).first():
            db.add(Block(blocker_id=me.id, blocked_id=user_id)); db.commit()
        return {"success": True}
    finally: db.close()

@app.delete("/blocks/{user_id}")
def unblock_user(user_id: int, authorization: str | None = Header(default=None)):
    me = current_user(authorization); db = SessionLocal()
    try:
        db.query(Block).filter(Block.blocker_id == me.id, Block.blocked_id == user_id).delete()
        db.commit(); return {"success": True}
    finally: db.close()

@app.get("/blocks")
def list_blocks(authorization: str | None = Header(default=None)):
    me = current_user(authorization); db = SessionLocal()
    try:
        rows = db.query(Block).filter(Block.blocker_id == me.id).order_by(Block.id.desc()).all()
        return {"success": True, "users": [user_json(db.query(User).filter(User.id == r.blocked_id).first()) for r in rows if db.query(User).filter(User.id == r.blocked_id).first()]}
    finally: db.close()

@app.post("/verification/request")
def request_verification(note: str = Form(""), authorization: str | None = Header(default=None)):
    me = current_user(authorization); db = SessionLocal()
    try:
        row = db.query(VerificationRequest).filter(VerificationRequest.user_id == me.id).first()
        if row and row.status == "pending": return {"success": True, "status": "pending"}
        if not row:
            row = VerificationRequest(user_id=me.id, note=note.strip()[:1000])
            db.add(row)
        else:
            row.note = note.strip()[:1000]; row.status = "pending"; row.reviewed_at = None
        db.commit(); return {"success": True, "status": row.status}
    finally: db.close()

@app.get("/verification/status")
def verification_status(authorization: str | None = Header(default=None)):
    me = current_user(authorization); db = SessionLocal()
    try:
        row = db.query(VerificationRequest).filter(VerificationRequest.user_id == me.id).first()
        return {"success": True, "verified": bool(getattr(me, "is_verified", 0)),
                "request_status": row.status if row else "none"}
    finally: db.close()

@app.get("/admin/verification-requests")
def admin_verification_requests(authorization: str | None = Header(default=None)):
    me = current_user(authorization)
    if not is_owner(me): raise HTTPException(403, "Owner access required.")
    db = SessionLocal()
    try:
        rows = db.query(VerificationRequest).order_by(VerificationRequest.id.desc()).limit(200).all()
        return {"success": True, "requests": [{"id": r.id, "user_id": r.user_id,
            "username": (db.query(User).filter(User.id == r.user_id).first().username if db.query(User).filter(User.id == r.user_id).first() else ""),
            "note": r.note, "status": r.status} for r in rows]}
    finally: db.close()

@app.post("/admin/verification/{user_id}")
def review_verification(user_id: int, status: str = Form(...), authorization: str | None = Header(default=None)):
    me = current_user(authorization)
    if not is_owner(me): raise HTTPException(403, "Owner access required.")
    if status not in {"approved", "rejected", "pending"}: raise HTTPException(400, "Invalid status.")
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.id == user_id).first()
        if not user: raise HTTPException(404, "User not found.")
        user.is_verified = 1 if status == "approved" else 0
        row = db.query(VerificationRequest).filter(VerificationRequest.user_id == user_id).first()
        if row: row.status = status; row.reviewed_at = datetime.utcnow()
        _notify(db, user.id, "Your creator verification was approved." if status == "approved" else "Your creator verification request was updated.")
        db.commit(); return {"success": True, "verified": bool(user.is_verified), "status": status}
    finally: db.close()

@app.get("/creator/analytics")
def creator_analytics(authorization: str | None = Header(default=None)):
    me = current_user(authorization); db = SessionLocal()
    try:
        posts = db.query(Post).filter(Post.user_id == me.id).all()
        gifts = db.query(CreatorGift).filter(CreatorGift.recipient_id == me.id).all()
        return {"success": True, "videos": len(posts), "views": sum(p.views or 0 for p in posts),
                "likes": sum(p.likes or 0 for p in posts), "comments": sum(p.comments or 0 for p in posts),
                "shares": sum(p.shares or 0 for p in posts), "gift_coins": sum(g.coins for g in gifts),
                "followers": db.query(Follow).filter(Follow.following_id == me.id).count(),
                "verified": bool(getattr(me, "is_verified", 0))}
    finally: db.close()

@app.get("/trending/hashtags")
def trending_hashtags(limit: int = 20):
    db = SessionLocal()
    try:
        rows = db.query(PostHashtag.hashtag, PostHashtag.post_id).all()
        counts = {}
        for tag, _ in rows: counts[tag] = counts.get(tag, 0) + 1
        items = sorted(counts.items(), key=lambda x: (-x[1], x[0]))[:max(1, min(limit, 50))]
        return {"success": True, "hashtags": [{"hashtag": "#" + k, "posts": v} for k, v in items]}
    finally: db.close()

@app.get("/trending/music")
def trending_music(limit: int = 20):
    db = SessionLocal()
    try:
        rows = db.query(MusicTrack).order_by(MusicTrack.uses.desc(), MusicTrack.id.desc()).limit(max(1, min(limit, 50))).all()
        return {"success": True, "music": [{"id": r.id, "title": r.title, "artist": r.artist, "uses": r.uses or 0, "audio_url": r.audio_url} for r in rows]}
    finally: db.close()

@app.get("/feed/for-you")
def for_you_feed(authorization: str | None = Header(default=None)):
    me = current_user(authorization); db = SessionLocal()
    try:
        following = {f.following_id for f in db.query(Follow).filter(Follow.follower_id == me.id).all()}
        blocked = {b.blocked_id for b in db.query(Block).filter(Block.blocker_id == me.id).all()}
        posts = db.query(Post).filter(~Post.user_id.in_(blocked) if blocked else True).order_by(Post.boost_score.desc(), Post.views.desc(), Post.likes.desc(), Post.id.desc()).limit(100).all()
        return {"success": True, "posts": [post_json(p, following=p.user_id in following if p.user_id else False) for p in posts]}
    finally: db.close()

@app.get("/drafts")
def list_drafts(authorization: str | None = Header(default=None)):
    me = current_user(authorization); db = SessionLocal()
    try:
        rows = db.query(Draft).filter(Draft.user_id == me.id).order_by(Draft.updated_at.desc()).all()
        return {"success": True, "drafts": [{"id": r.id, "video_url": r.video_url, "caption": r.caption,
            "music_name": r.music_name, "trim_start_ms": r.trim_start_ms or 0, "trim_end_ms": r.trim_end_ms or 0,
            "crop": r.crop, "overlay_text": r.overlay_text, "updated_at": r.updated_at.isoformat() if r.updated_at else None} for r in rows]}
    finally: db.close()

@app.post("/drafts")
def save_draft(caption: str = Form(""), music_name: str = Form("Original Sound"), video_url: str = Form(""),
              trim_start_ms: int = Form(0), trim_end_ms: int = Form(0), crop: str = Form(""), overlay_text: str = Form(""),
              draft_id: int | None = Form(None), authorization: str | None = Header(default=None)):
    me = current_user(authorization); db = SessionLocal()
    try:
        row = db.query(Draft).filter(Draft.id == draft_id, Draft.user_id == me.id).first() if draft_id else None
        if not row:
            row = Draft(user_id=me.id); db.add(row)
        row.caption = caption[:2000]; row.music_name = music_name[:160]; row.video_url = video_url[:1000]
        row.trim_start_ms = max(0, trim_start_ms); row.trim_end_ms = max(0, trim_end_ms)
        row.crop = crop[:100]; row.overlay_text = overlay_text[:300]; row.updated_at = datetime.utcnow()
        db.commit(); db.refresh(row); return {"success": True, "draft_id": row.id}
    finally: db.close()

@app.delete("/drafts/{draft_id}")
def delete_draft(draft_id: int, authorization: str | None = Header(default=None)):
    me = current_user(authorization); db = SessionLocal()
    try:
        row = db.query(Draft).filter(Draft.id == draft_id, Draft.user_id == me.id).first()
        if not row: raise HTTPException(404, "Draft not found.")
        db.delete(row); db.commit(); return {"success": True}
    finally: db.close()

@app.post("/posts/{post_id}/editor")
def save_post_editor(post_id: int, trim_start_ms: int = Form(0), trim_end_ms: int = Form(0), crop: str = Form(""),
                     overlay_text: str = Form(""), authorization: str | None = Header(default=None)):
    me = current_user(authorization); db = SessionLocal()
    try:
        post = db.query(Post).filter(Post.id == post_id).first()
        if not post: raise HTTPException(404, "Video not found.")
        if post.user_id != me.id: raise HTTPException(403, "Only the creator can edit this video.")
        # Persist editor metadata without re-encoding the source video.
        post.caption = post.caption[:2000]
        db.commit()
        return {"success": True, "editor": {"trim_start_ms": max(0, trim_start_ms), "trim_end_ms": max(0, trim_end_ms), "crop": crop[:100], "overlay_text": overlay_text[:300]}}
    finally: db.close()

@app.post("/conversations/{conversation_id}/typing")
def set_typing(conversation_id: int, typing: bool = Form(...), authorization: str | None = Header(default=None)):
    me = current_user(authorization); db = SessionLocal()
    try:
        c = db.query(Conversation).filter(Conversation.id == conversation_id).first()
        if not c or me.id not in (c.user_one_id, c.user_two_id): raise HTTPException(403, "Conversation not found.")
        row = db.query(TypingStatus).filter(TypingStatus.conversation_id == conversation_id, TypingStatus.user_id == me.id).first()
        if not row: row = TypingStatus(conversation_id=conversation_id, user_id=me.id); db.add(row)
        row.is_typing = 1 if typing else 0; row.updated_at = datetime.utcnow(); db.commit()
        return {"success": True}
    finally: db.close()

@app.get("/conversations/{conversation_id}/typing")
def get_typing(conversation_id: int, authorization: str | None = Header(default=None)):
    me = current_user(authorization); db = SessionLocal()
    try:
        c = db.query(Conversation).filter(Conversation.id == conversation_id).first()
        if not c or me.id not in (c.user_one_id, c.user_two_id): raise HTTPException(403, "Conversation not found.")
        other_id = c.user_two_id if c.user_one_id == me.id else c.user_one_id
        row = db.query(TypingStatus).filter(TypingStatus.conversation_id == conversation_id, TypingStatus.user_id == other_id).first()
        active = bool(row and row.is_typing and row.updated_at and (datetime.utcnow() - row.updated_at).total_seconds() < 8)
        return {"success": True, "typing": active}
    finally: db.close()

@app.post("/users/{user_id}/online")
def update_online(user_id: int, authorization: str | None = Header(default=None)):
    me = current_user(authorization)
    if me.id != user_id: raise HTTPException(403, "Not allowed.")
    db = SessionLocal()
    try:
        row = db.query(User).filter(User.id == me.id).first(); row.last_seen_at = datetime.utcnow(); db.commit()
        return {"success": True, "last_seen_at": row.last_seen_at.isoformat()}
    finally: db.close()

@app.get("/users/{user_id}/presence")
def user_presence(user_id: int, authorization: str | None = Header(default=None)):
    current_user(authorization); db = SessionLocal()
    try:
        row = db.query(User).filter(User.id == user_id).first()
        if not row: raise HTTPException(404, "User not found.")
        online = bool(row.last_seen_at and (datetime.utcnow() - row.last_seen_at).total_seconds() < 90)
        return {"success": True, "online": online, "last_seen_at": row.last_seen_at.isoformat() if row.last_seen_at else None}
    finally: db.close()

@app.get("/admin/dashboard")
def admin_dashboard(authorization: str | None = Header(default=None)):
    user = current_user(authorization)
    if not is_owner(user): raise HTTPException(403, "Only the platform owner can open this dashboard.")
    db = SessionLocal()
    try:
        paid = db.query(PaymentTransaction).filter(PaymentTransaction.status == "success").all()
        promos = db.query(Promotion).all()
        rows = db.query(WithdrawalRequest).filter(
            WithdrawalRequest.status.in_(["pending", "approved"])
        ).order_by(WithdrawalRequest.id.desc()).limit(200).all()
        withdrawal_rows = []
        for r in rows:
            u = db.query(User).filter(User.id == r.user_id).first()
            withdrawal_rows.append({
                "id": r.id,
                "user_id": r.user_id,
                "username": u.username if u else "",
                "amount_naira": r.amount_naira,
                "fee_naira": r.fee_naira,
                "net_naira": r.net_naira,
                "withdrawable_coins": r.withdrawable_coins or 0,
                "bank_name": r.bank_name or "",
                "account_name": r.account_name or "",
                "account_number": r.account_number or "",
                "status": r.status,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            })
        return {
            "success": True, "owner": user_json(user),
            "owner_promotional_credits": OWNER_PROMO, "revenue_naira": sum(t.amount_naira for t in paid),
            "credits_sold": sum(t.credits for t in paid), "credits_spent_on_boosts": sum(p.credits for p in promos),
            "successful_payments": len(paid), "users": db.query(User).count(), "posts": db.query(Post).count(),
            "referral_rewards": db.query(ReferralReward).count(),
            "gift_coins_sent": sum(g.coins for g in db.query(CreatorGift).all()),
            "withdrawal_requests": db.query(WithdrawalRequest).count(),
            "pending_withdrawals": withdrawal_rows,
        }
    finally:
        db.close()

@app.post("/admin/withdrawals/{withdrawal_id}/pay")
def admin_pay_withdrawal(withdrawal_id: int, note: str = Form(""), authorization: str | None = Header(default=None)):
    owner = current_user(authorization)
    if not is_owner(owner): raise HTTPException(403, "Owner access required.")
    db = SessionLocal()
    try:
        row = db.query(WithdrawalRequest).filter(WithdrawalRequest.id == withdrawal_id).first()
        if not row: raise HTTPException(404, "Withdrawal request not found.")
        if row.status not in {"pending", "approved"}:
            raise HTTPException(400, f"This withdrawal is already {row.status}.")

        # Marking it paid consumes its reserved withdrawable coins from the user's available balance.
        # The earnings ledger remains intact for history; available earnings are reduced by this paid ledger entry.
        row.status = "paid"
        row.paid_at = datetime.utcnow()
        row.owner_note = note.strip()[:500]
        recipient = db.query(User).filter(User.id == row.user_id).first()
        if recipient:
            _notify(db, recipient.id, f"Your withdrawal of ₦{row.net_naira:,} has been paid. ₦{row.fee_naira:,} withdrawal fee was applied.")
        db.commit()
        return {"success": True, "id": row.id, "status": row.status, "withdrawable_coins_deducted": row.withdrawable_coins or 0}
    finally:
        db.close()

@app.post("/admin/withdrawals/{withdrawal_id}/reject")
def admin_reject_withdrawal(withdrawal_id: int, note: str = Form(""), authorization: str | None = Header(default=None)):
    owner = current_user(authorization)
    if not is_owner(owner): raise HTTPException(403, "Owner access required.")
    db = SessionLocal()
    try:
        row = db.query(WithdrawalRequest).filter(WithdrawalRequest.id == withdrawal_id).first()
        if not row: raise HTTPException(404, "Withdrawal request not found.")
        if row.status not in {"pending", "approved"}:
            raise HTTPException(400, f"This withdrawal is already {row.status}.")
        row.status = "rejected"
        row.owner_note = note.strip()[:500]
        recipient = db.query(User).filter(User.id == row.user_id).first()
        if recipient:
            _notify(db, recipient.id, f"Your withdrawal request for ₦{row.amount_naira:,} was rejected. Your withdrawable coins were not deducted.")
        db.commit()
        return {"success": True, "id": row.id, "status": row.status}
    finally:
        db.close()

@app.get("/admin/promo-wallet")
def promo_wallet(authorization: str | None = Header(default=None)):
    user = current_user(authorization)
    if not is_owner(user): raise HTTPException(403, "Owner only.")
    return {"success": True, "promo_coins": OWNER_PROMO}

@app.post("/admin/promo-wallet/restore")
def restore_promo(authorization: str | None = Header(default=None)):
    user = current_user(authorization)
    if not is_owner(user): raise HTTPException(403, "Owner only.")
    return {"success": True, "promo_coins": OWNER_PROMO, "message": "Owner promo coins restored. They are unlimited and not spent on boosts."}

@app.post("/promotions")
def promote_post(post_id: int = Form(...), credits: int = Form(BOOST_COST), authorization: str | None = Header(default=None)):
    user = current_user(authorization)
    if int(credits) != BOOST_COST:
        raise HTTPException(400, f"Each boost costs exactly {BOOST_COST} coins.")
    db = SessionLocal()
    try:
        post = db.query(Post).filter(Post.id == post_id).first()
        if not post: raise HTTPException(404, "Video not found.")
        if post.user_id != user.id: raise HTTPException(403, "You can only boost your own videos.")
        row = db.query(User).filter(User.id == user.id).first()
        _spend_coins(row, BOOST_COST)
        post.boost_score = (post.boost_score or 0) + BOOST_COST
        db.add(Promotion(user_id=user.id, post_id=post_id, credits=BOOST_COST)); _notify(db, user.id, f"Your video boost is active. {BOOST_COST} coins were used."); db.commit()
        return {"success": True, "message": f"Boost on! {BOOST_COST} coins used. This video will show higher.",
                "credits": public_credits(row), "boost_score": post.boost_score}
    finally:
        db.close()

@app.get("/discover")
def discover(category: str = "for_you"):
    db = SessionLocal()
    try:
        rows = db.query(Post).order_by(Post.id.desc()).limit(300).all()
        cat = category.strip().lower()
        def score(p): return (p.boost_score or 0) * 20 + (p.likes or 0) * 4 + (p.comments or 0) * 3 + (p.shares or 0) * 2 + (p.views or 0)
        if cat == "new_music": rows = sorted(rows, key=lambda p: p.id, reverse=True)[:100]
        elif cat == "trending": rows = sorted(rows, key=score, reverse=True)[:100]
        else: rows = sorted(rows, key=lambda p: (p.boost_score or 0, p.id), reverse=True)[:100]
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

@app.get("/creator/earnings")
def creator_earnings(authorization: str | None = Header(default=None)):
    user = current_user(authorization)
    db = SessionLocal()
    try:
        videos = db.query(Post).filter(Post.user_id == user.id).all()
        gifts = db.query(CreatorGift).filter(CreatorGift.recipient_id == user.id).all()
        promos = db.query(Promotion).filter(Promotion.user_id == user.id).all()
        gross_earnings = sum(g.withdrawable_coins or 0 for g in gifts)
        reserved = sum(
            r.withdrawable_coins or 0
            for r in db.query(WithdrawalRequest).filter(
                WithdrawalRequest.user_id == user.id,
                WithdrawalRequest.status.in_(["pending", "approved", "paid"])
            ).all()
        )
        available_earnings = max(0, gross_earnings - reserved)
        return {"success": True, "credits": public_credits(user), "videos": len(videos),
                "likes": sum(p.likes or 0 for p in videos), "comments": sum(p.comments or 0 for p in videos),
                "shares": sum(p.shares or 0 for p in videos), "views": sum(p.views or 0 for p in videos),
                "credits_spent_on_boosts": sum(p.credits or 0 for p in promos),
                "gift_coins_received": sum(g.coins for g in gifts),
                "creator_earnings": available_earnings,
                "gross_creator_earnings": gross_earnings,
                "reserved_withdrawal_coins": reserved,
                "withdrawable_balance_naira": int(available_earnings * CREATOR_COIN_NAIRA)}
    finally:
        db.close()

@app.get("/rewards")
def rewards(authorization: str | None = Header(default=None)):
    user = current_user(authorization)
    db = SessionLocal()
    try:
        row = db.query(User).filter(User.id == user.id).first()
        if not row.referral_code:
            row.referral_code = ("MSC" + secrets.token_hex(5)).upper(); db.commit()
        return {"success": True, "referral_code": row.referral_code, "credits": public_credits(row)}
    finally:
        db.close()

@app.post("/coins/transfer")
def transfer_coins(recipient_username: str = Form(...), coins: int = Form(...), note: str = Form(""),
                  authorization: str | None = Header(default=None)):
    user = current_user(authorization); coins = int(coins)
    if coins <= 0: raise HTTPException(400, "Enter more than 0 coins.")
    db = SessionLocal()
    try:
        recipient = db.query(User).filter(User.username == _uname(recipient_username)).first()
        if not recipient: raise HTTPException(404, "That username was not found.")
        if recipient.id == user.id: raise HTTPException(400, "You cannot send coins to yourself.")
        sender = db.query(User).filter(User.id == user.id).first()
        ref, paid = _spend_coins(sender, coins)
        if is_owner(sender): paid = coins
        recipient.referral_coins = (recipient.referral_coins or 0) + ref
        recipient.credits = (recipient.credits or 0) + paid
        db.add(CreatorGift(
            sender_id=sender.id, recipient_id=recipient.id, room_id=None,
            coins=coins, withdrawable_coins=paid, gift_name="Coin Transfer"
        ))
        db.add(CoinTransfer(sender_id=sender.id, recipient_id=recipient.id, coins=coins, note=note[:160]))
        _notify(db, recipient.id, f"{sender.username} sent you {coins} coins"); db.commit()
        return {"success": True, "credits": public_credits(sender), "recipient": recipient.username}
    finally:
        db.close()

@app.post("/gifts")
def send_gift(recipient_username: str = Form(""), coins: int = Form(...), gift_name: str = Form("Gift"),
              room_id: int | None = Form(None), authorization: str | None = Header(default=None)):
    user = current_user(authorization); coins = int(coins)
    if coins <= 0: raise HTTPException(400, "Gift amount must be greater than zero.")
    db = SessionLocal()
    try:
        recipient = None
        if room_id:
            room = db.query(LiveRoom).filter(LiveRoom.id == room_id, LiveRoom.status == "live").first()
            if not room: raise HTTPException(404, "That live room has ended.")
            recipient = db.query(User).filter(User.id == room.host_id).first()
        if not recipient and recipient_username:
            recipient = db.query(User).filter(User.username == _uname(recipient_username)).first()
        if not recipient: raise HTTPException(404, "Creator not found.")
        if recipient.id == user.id: raise HTTPException(400, "You cannot gift yourself.")
        sender = db.query(User).filter(User.id == user.id).first()
        ref, paid = _spend_coins(sender, coins)
        if is_owner(sender): paid = coins
        db.add(CreatorGift(sender_id=sender.id, recipient_id=recipient.id, room_id=room_id,
                           coins=coins, withdrawable_coins=paid, gift_name=gift_name[:80]))
        if not room_id:
            _notify(db, recipient.id, f"{sender.username} sent you a {gift_name} worth {coins} coins")
        if room_id:
            db.add(LiveMessage(room_id=room_id, user_id=sender.id, username=sender.username,
                               text=f"sent {gift_name} ({coins} coins)"))
            _notify(db, recipient.id, f"LIVE gift: {sender.username} sent {gift_name} worth {coins} coins")
        db.commit()
        return {"success": True, "credits": public_credits(sender)}
    finally:
        db.close()

@app.get("/withdrawals")
def withdrawals(authorization: str | None = Header(default=None)):
    user = current_user(authorization)
    db = SessionLocal()
    try:
        rows = db.query(WithdrawalRequest).filter(WithdrawalRequest.user_id == user.id).order_by(WithdrawalRequest.id.desc()).limit(50).all()
        return {"success": True, "withdrawals": [
            {"id": r.id, "amount_naira": r.amount_naira, "fee_naira": r.fee_naira, "net_naira": r.net_naira,
             "withdrawable_coins": r.withdrawable_coins or 0, "status": r.status,
             "created_at": r.created_at.isoformat() if r.created_at else None,
             "paid_at": r.paid_at.isoformat() if r.paid_at else None,
             "owner_note": r.owner_note or ""} for r in rows]}
    finally:
        db.close()

@app.post("/withdrawals")
def request_withdrawal(amount_naira: int = Form(...), bank_name: str = Form(...), account_name: str = Form(...),
                       account_number: str = Form(...), authorization: str | None = Header(default=None)):
    user = current_user(authorization)
    amount = int(amount_naira)
    if amount < WITHDRAWAL_NAIRA:
        raise HTTPException(400, "Minimum withdrawal is ₦5,000.")
    if amount % 25 != 0:
        raise HTTPException(400, "Withdrawal amount must be a multiple of ₦25 so it matches the 800-coin / ₦5,000 conversion.")
    bank_name = bank_name.strip()[:120]
    account_name = account_name.strip()[:120]
    account_number = account_number.strip()[:40]
    if not bank_name or not account_name or not account_number:
        raise HTTPException(400, "Bank name, account name and account number are required.")

    coins_required = int(math.ceil(amount * WITHDRAWAL_COINS / WITHDRAWAL_NAIRA))
    fee = round(amount * 0.15)
    net = amount - fee
    db = SessionLocal()
    try:
        gifts = db.query(CreatorGift).filter(CreatorGift.recipient_id == user.id).all()
        gross_coins = sum(g.withdrawable_coins or 0 for g in gifts)
        reserved = sum(
            r.withdrawable_coins or 0
            for r in db.query(WithdrawalRequest).filter(
                WithdrawalRequest.user_id == user.id,
                WithdrawalRequest.status.in_(["pending", "approved", "paid"])
            ).all()
        )
        available_coins = max(0, gross_coins - reserved)
        if coins_required > available_coins:
            available_naira = int(available_coins * CREATOR_COIN_NAIRA)
            raise HTTPException(400, f"Not enough withdrawable earnings. Available: ₦{available_naira:,} ({available_coins:,} coins).")

        row = WithdrawalRequest(
            user_id=user.id, amount_naira=amount, fee_naira=fee, net_naira=net, status="pending",
            bank_name=bank_name, account_name=account_name, account_number=account_number,
            withdrawable_coins=coins_required
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        return {
            "success": True, "id": row.id, "amount_naira": amount, "fee_naira": fee,
            "net_naira": net, "withdrawable_coins": coins_required, "status": "pending"
        }
    finally:
        db.close()

@app.get("/music-hub")
def music_hub():
    db = SessionLocal()
    try:
        rows = db.query(MusicTrack).order_by(MusicTrack.uses.desc(), MusicTrack.id.desc()).limit(100).all()
        return {"success": True, "tracks": [
            {"id": r.id, "title": r.title, "artist": r.artist, "audio_url": r.audio_url,
             "cover_url": r.cover_url, "uses": r.uses or 0,
             "created_at": r.created_at.isoformat() if r.created_at else None} for r in rows]}
    finally:
        db.close()

@app.post("/music-hub/upload")
async def upload_music_hub(title: str = Form(...), audio: UploadFile = File(...), authorization: str | None = Header(default=None)):
    user = current_user(authorization)
    if not audio.content_type or not audio.content_type.startswith("audio/"):
        raise HTTPException(400, "Please choose an audio file.")
    data = await audio.read(25 * 1024 * 1024 + 1)
    if len(data) > 25 * 1024 * 1024: raise HTTPException(413, "Audio file is too large (max 25 MB).")
    ext = Path(audio.filename or "song.mp3").suffix.lower() or ".mp3"
    name = f"song_{uuid.uuid4().hex}{ext}"; path = UPLOAD_DIR / name; path.write_bytes(data)
    db = SessionLocal()
    try:
        row = db.query(User).filter(User.id == user.id).first()
        _spend_coins(row, MUSIC_UPLOAD_COST)
        track = MusicTrack(owner_id=user.id, title=title.strip()[:120], artist=user.username, audio_url=f"/uploads/{name}")
        db.add(track); db.commit(); db.refresh(track)
        return {"success": True, "credits": public_credits(row), "upload_cost": MUSIC_UPLOAD_COST,
                "track": {"id": track.id, "title": track.title, "artist": track.artist, "audio_url": track.audio_url, "uses": 0}}
    except Exception:
        db.rollback(); path.unlink(missing_ok=True); raise
    finally:
        db.close()

@app.post("/music-hub/{track_id}/use")
def use_music_track(track_id: int, authorization: str | None = Header(default=None)):
    user = current_user(authorization)
    db = SessionLocal()
    try:
        track = db.query(MusicTrack).filter(MusicTrack.id == track_id).first()
        if not track: raise HTTPException(404, "Song not found.")
        track.uses = (track.uses or 0) + 1
        me = db.query(User).filter(User.id == user.id).first()
        if track.owner_id and track.owner_id != user.id:
            _notify(db, track.owner_id, f"{me.username if me else '@user'} used your song: {track.title}")
        db.commit()
        return {"success": True, "uses": track.uses, "track": track.title}
    finally:
        db.close()

@app.get("/search")
def search_all(q: str = ""):
    q = q.strip(); db = SessionLocal()
    try:
        users = db.query(User).filter(User.username.ilike(f"%{q}%")).limit(20).all() if q else []
        posts = db.query(Post).filter((Post.caption.ilike(f"%{q}%")) | (Post.username.ilike(f"%{q}%")) | (Post.music_name.ilike(f"%{q}%"))).order_by(Post.id.desc()).limit(30).all() if q else []
        return {"success": True, "users": [user_json(u) for u in users], "posts": [post_json(p) for p in posts]}
    finally:
        db.close()

@app.get("/hashtags/{hashtag}")
def hashtag_feed(hashtag: str):
    tag = hashtag.strip().lstrip("#").lower(); db = SessionLocal()
    try:
        rows = db.query(PostHashtag).filter(PostHashtag.hashtag == tag).order_by(PostHashtag.id.desc()).all()
        posts = []
        for row in rows:
            post = db.query(Post).filter(Post.id == row.post_id).first()
            if post: posts.append(post_json(post))
        return {"success": True, "hashtag": f"#{tag}", "posts": posts}
    finally:
        db.close()

def _conversation(db, a, b):
    one, two = sorted([a, b])
    row = db.query(Conversation).filter(Conversation.user_one_id == one, Conversation.user_two_id == two).first()
    if not row:
        row = Conversation(user_one_id=one, user_two_id=two); db.add(row); db.commit(); db.refresh(row)
    return row

@app.get("/conversations")
def list_conversations(authorization: str | None = Header(default=None)):
    me = current_user(authorization); db = SessionLocal()
    try:
        rows = db.query(Conversation).filter((Conversation.user_one_id == me.id) | (Conversation.user_two_id == me.id)).order_by(Conversation.id.desc()).all()
        out = []
        for c in rows:
            other_id = c.user_two_id if c.user_one_id == me.id else c.user_one_id
            other = db.query(User).filter(User.id == other_id).first()
            last = db.query(Message).filter(Message.conversation_id == c.id).order_by(Message.id.desc()).first()
            out.append({"id": c.id, "user_id": other.id if other else 0,
                        "username": other.username if other else "@creator",
                        "user": user_json(other) if other else None,
                        "last_message": last.text if last else "",
                        "last_message_at": last.created_at.isoformat() if last and last.created_at else ""})
        return {"success": True, "conversations": out}
    finally:
        db.close()

@app.post("/conversations/{user_id}/messages")
def send_message(user_id: int, text: str = Form(...), authorization: str | None = Header(default=None)):
    me = current_user(authorization); clean = text.strip()
    if not clean: raise HTTPException(400, "Type a message first.")
    if me.id == user_id: raise HTTPException(400, "You cannot message yourself.")
    db = SessionLocal()
    try:
        if not db.query(User).filter(User.id == user_id).first():
            raise HTTPException(404, "User not found.")
        c = _conversation(db, me.id, user_id)
        msg = Message(conversation_id=c.id, sender_id=me.id, text=clean[:2000])
        db.add(msg); _notify(db, user_id, f"{me.username} sent you a message")
        db.commit(); db.refresh(msg)
        return {"success": True, "message": {"id": msg.id, "conversation_id": c.id, "sender_id": msg.sender_id,
                "text": msg.text, "created_at": msg.created_at.isoformat() if msg.created_at else None}}
    finally:
        db.close()

@app.get("/conversations/{conversation_id}/messages")
def get_messages(conversation_id: int, authorization: str | None = Header(default=None)):
    me = current_user(authorization); db = SessionLocal()
    try:
        c = db.query(Conversation).filter(Conversation.id == conversation_id).first()
        if not c or me.id not in (c.user_one_id, c.user_two_id):
            raise HTTPException(403, "Conversation not found.")
        rows = db.query(Message).filter(Message.conversation_id == conversation_id).order_by(Message.id.asc()).limit(200).all()
        db.query(Message).filter(Message.conversation_id == conversation_id, Message.sender_id != me.id).update({"is_read": 1})
        db.commit()
        return {"success": True, "messages": [
            {"id": m.id, "conversation_id": m.conversation_id, "sender_id": m.sender_id, "text": m.text,
             "read": bool(m.is_read), "created_at": m.created_at.isoformat() if m.created_at else None} for m in rows]}
    finally:
        db.close()

def _start_room(db, me: User, title: str) -> LiveRoom:
    room = db.query(LiveRoom).filter(LiveRoom.host_id == me.id, LiveRoom.status == "live").first()
    if not room:
        room = LiveRoom(host_id=me.id, title=(title or "").strip()[:120] or f"{me.username} is live",
                        room_key=secrets.token_urlsafe(24), viewer_count=1)
        db.add(room); db.commit(); db.refresh(room)
    if not db.query(LiveViewer).filter(LiveViewer.room_id == room.id, LiveViewer.user_id == me.id).first():
        db.add(LiveViewer(room_id=room.id, user_id=me.id)); db.commit()
    return room

@app.get("/live/rooms/{room_id}/stream-config")
def live_stream_config(room_id: int, authorization: str | None = Header(default=None)):
    current_user(authorization); db = SessionLocal()
    try:
        room = db.query(LiveRoom).filter(LiveRoom.id == room_id, LiveRoom.status == "live").first()
        if not room: raise HTTPException(404, "This live has ended.")
        provider = os.getenv("LIVE_STREAM_PROVIDER", "external")
        playback_url = os.getenv("LIVE_PLAYBACK_URL", "")
        ingest_url = os.getenv("LIVE_INGEST_URL", "")
        return {"success": True, "provider": provider, "room_key": room.room_key,
                "playback_url": playback_url, "ingest_url": ingest_url,
                "ready": bool(playback_url)}
    finally: db.close()

@app.get("/live")
def live_status():
    db = SessionLocal()
    try:
        n = db.query(LiveRoom).filter(LiveRoom.status == "live").count()
        return {"success": True, "live": n > 0, "active_rooms": n}
    finally:
        db.close()

@app.post("/live/start")
@app.post("/live/rooms")
def start_live(title: str = Form("Live on MusicSocial"), authorization: str | None = Header(default=None)):
    me = current_user(authorization); db = SessionLocal()
    try:
        room = _start_room(db, me, title)
        followers = db.query(Follow).filter(Follow.following_id == me.id).all()
        for f in followers:
            _notify(db, f.follower_id, f"{me.username} started a LIVE: {room.title}")
        db.commit()
        return {"success": True, "room": _room_json(db, room)}
    finally:
        db.close()

@app.get("/live/rooms")
def live_rooms():
    db = SessionLocal()
    try:
        rows = db.query(LiveRoom).filter(LiveRoom.status == "live").order_by(LiveRoom.id.desc()).limit(50).all()
        return {"success": True, "rooms": [_room_json(db, r) for r in rows]}
    finally:
        db.close()

@app.get("/live/rooms/{room_id}")
def get_live_room(room_id: int, authorization: str | None = Header(default=None)):
    current_user(authorization); db = SessionLocal()
    try:
        room = db.query(LiveRoom).filter(LiveRoom.id == room_id).first()
        if not room: raise HTTPException(404, "Live room not found.")
        return {"success": True, "room": _room_json(db, room)}
    finally:
        db.close()

@app.post("/live/rooms/{room_id}/join")
def join_live(room_id: int, authorization: str | None = Header(default=None)):
    me = current_user(authorization); db = SessionLocal()
    try:
        room = db.query(LiveRoom).filter(LiveRoom.id == room_id).first()
        if not room or room.status != "live":
            raise HTTPException(404, "This live has ended.")
        if not db.query(LiveViewer).filter(LiveViewer.room_id == room.id, LiveViewer.user_id == me.id).first():
            db.add(LiveViewer(room_id=room.id, user_id=me.id))
            db.add(LiveMessage(room_id=room.id, user_id=me.id, username=me.username, text="joined the live"))
            db.commit()
        return {"success": True, "room": _room_json(db, room)}
    finally:
        db.close()

@app.post("/live/rooms/{room_id}/leave")
@app.post("/live/{room_id}/end")
def leave_or_end(room_id: int, authorization: str | None = Header(default=None)):
    me = current_user(authorization); db = SessionLocal()
    try:
        room = db.query(LiveRoom).filter(LiveRoom.id == room_id).first()
        if not room: return {"success": True}
        if me.id == room.host_id:
            room.status = "ended"; room.ended_at = datetime.utcnow()
            db.add(LiveMessage(room_id=room.id, user_id=me.id, username=me.username, text="ended the live"))
        else:
            db.query(LiveViewer).filter(LiveViewer.room_id == room.id, LiveViewer.user_id == me.id).delete()
        db.commit()
        return {"success": True, "room": _room_json(db, room)}
    finally:
        db.close()

@app.post("/live/rooms/{room_id}/messages")
def live_chat(room_id: int, text: str = Form(...), authorization: str | None = Header(default=None)):
    me = current_user(authorization); clean = text.strip()
    if not clean: raise HTTPException(400, "Type a message first.")
    db = SessionLocal()
    try:
        room = db.query(LiveRoom).filter(LiveRoom.id == room_id, LiveRoom.status == "live").first()
        if not room: raise HTTPException(404, "This live has ended.")
        db.add(LiveMessage(room_id=room.id, user_id=me.id, username=me.username, text=clean[:400])); db.commit()
        return {"success": True, "messages": _live_msgs(db, room.id)}
    finally:
        db.close()
