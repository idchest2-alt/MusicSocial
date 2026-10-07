from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Header, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import create_engine, Column, Integer, String, DateTime, ForeignKey, UniqueConstraint, inspect, or_, and_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import declarative_base, sessionmaker
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional
import hashlib
import secrets
import uuid
import os
import re
import hmac
import json
import httpx
from dotenv import load_dotenv

load_dotenv()
app = FastAPI(title="MusicSocial API", version="2.0.0")

PAYSTACK_SECRET_KEY = os.getenv("PAYSTACK_SECRET_KEY", "")
PAYSTACK_PUBLIC_KEY = os.getenv("PAYSTACK_PUBLIC_KEY", "")
PAYSTACK_BASE_URL = "https://api.paystack.co"
OWNER_EMAIL = os.getenv("MUSICSOCIAL_OWNER_EMAIL", "").strip().lower()
OWNER_USERNAME = os.getenv("MUSICSOCIAL_OWNER_USERNAME", "@idchest").strip()
if OWNER_USERNAME and not OWNER_USERNAME.startswith("@"):
    OWNER_USERNAME = "@" + OWNER_USERNAME

CREATOR_COIN_NAIRA = 10
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

origins = [x.strip() for x in os.getenv("MUSICSOCIAL_ALLOWED_ORIGINS", "*").split(",") if x.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
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
    is_verified = Column(Integer, default=0)
    last_seen_at = Column(DateTime, nullable=True)
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
    repost_of_id = Column(Integer, nullable=True, index=True)
    repost_count = Column(Integer, default=0)
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


class Repost(Base):
    __tablename__ = "reposts"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    post_id = Column(Integer, ForeignKey("posts.id"), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    __table_args__ = (UniqueConstraint("user_id", "post_id", name="uq_user_post_repost"),)


class Bookmark(Base):
    __tablename__ = "bookmarks"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    post_id = Column(Integer, ForeignKey("posts.id"), nullable=False, index=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    __table_args__ = (UniqueConstraint("user_id", "post_id", name="uq_user_post_bookmark"),)


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
    isrc = Column(String, default="")
    ownership_confirmed = Column(Integer, default=0)
    verification_status = Column(String, default="pending")
    verification_notes = Column(String, default="")
    identity_match = Column(Integer, default=0)
    proof_url = Column(String, default="")
    audio_sha256 = Column(String, default="")
    fingerprint_match_id = Column(Integer, nullable=True)
    verified_at = Column(DateTime, nullable=True)
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
    reporter_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    target_type = Column(String, nullable=False)
    target_id = Column(Integer, nullable=True)
    reason = Column(String, nullable=False)
    details = Column(String, default="")
    status = Column(String, default="open")
    created_at = Column(DateTime, default=datetime.utcnow)


class Block(Base):
    __tablename__ = "blocks"
    id = Column(Integer, primary_key=True)
    blocker_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    blocked_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    __table_args__ = (UniqueConstraint("blocker_id", "blocked_id", name="uq_block"),)


class VerificationRequest(Base):
    __tablename__ = "verification_requests"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, unique=True)
    note = Column(String, default="")
    status = Column(String, default="pending")
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
    conversation_id = Column(Integer, ForeignKey("conversations.id"), nullable=False)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    is_typing = Column(Integer, default=0)
    updated_at = Column(DateTime, default=datetime.utcnow)
    __table_args__ = (UniqueConstraint("conversation_id", "user_id", name="uq_typing"),)


class UserActivity(Base):
    __tablename__ = "user_activities"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    activity_type = Column(String, nullable=False)
    post_id = Column(Integer, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


Base.metadata.create_all(bind=engine)


def ensure_col(table, column, definition):
    inspector = inspect(engine)
    if table not in inspector.get_table_names():
        return
    existing = {c["name"] for c in inspector.get_columns(table)}
    if column in existing:
        return
    ddl = definition.replace("DATETIME", "TIMESTAMP") if engine.dialect.name == "postgresql" else definition
    with engine.begin() as conn:
        conn.exec_driver_sql(f'ALTER TABLE "{table}" ADD COLUMN "{column}" {ddl}')


# Safe compatibility migrations for databases created by the older backend.
for _table, _column, _definition in [
    ("posts", "views", "INTEGER DEFAULT 0"),
    ("posts", "boost_score", "INTEGER DEFAULT 0"),
    ("posts", "repost_of_id", "INTEGER"),
    ("posts", "repost_count", "INTEGER DEFAULT 0"),
    ("users", "referral_code", "VARCHAR"),
    ("users", "referred_by_user_id", "INTEGER"),
    ("users", "referral_coins", "INTEGER DEFAULT 0"),
    ("users", "is_verified", "INTEGER DEFAULT 0"),
    ("users", "last_seen_at", "DATETIME"),
    ("sessions", "expires_at", "DATETIME"),
    ("music_tracks", "cover_url", "VARCHAR DEFAULT ''"),
    ("music_tracks", "uses", "INTEGER DEFAULT 0"),
    ("music_tracks", "isrc", "VARCHAR DEFAULT ''"),
    ("music_tracks", "ownership_confirmed", "INTEGER DEFAULT 0"),
    ("music_tracks", "verification_status", "VARCHAR DEFAULT 'pending'"),
    ("music_tracks", "verification_notes", "VARCHAR DEFAULT ''"),
    ("music_tracks", "identity_match", "INTEGER DEFAULT 0"),
    ("music_tracks", "proof_url", "VARCHAR DEFAULT ''"),
    ("music_tracks", "audio_sha256", "VARCHAR DEFAULT ''"),
    ("music_tracks", "fingerprint_match_id", "INTEGER"),
    ("music_tracks", "verified_at", "DATETIME"),
]:
    try:
        ensure_col(_table, _column, _definition)
    except Exception as e:
        print(f"Migration warning for {_table}.{_column}: {e}")

# Existing sessions also become persistent. They are still revoked by /auth/logout.
try:
    with engine.begin() as _conn:
        _conn.exec_driver_sql('UPDATE sessions SET expires_at = NULL')
except Exception as _e:
    print(f"Session persistence migration warning: {_e}")


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


def is_owner(user: User) -> bool:
    return bool((OWNER_EMAIL and (user.email or "").lower() == OWNER_EMAIL) or (OWNER_USERNAME and user.username == OWNER_USERNAME))


def current_user(authorization: str | None) -> User:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "Please sign in to continue.")
    token = authorization[7:].strip()
    db = SessionLocal()
    try:
        session = db.query(SessionToken).filter(SessionToken.token == token).first()
        if not session:
            raise HTTPException(401, "Your session is invalid. Please sign in again.")
        # MusicSocial sessions are persistent until the user explicitly logs out.
        # Keep this check intentionally disabled so closing the app does not sign users out.
        user = db.query(User).filter(User.id == session.user_id).first()
        if not user:
            raise HTTPException(401, "Account not found.")
        db.expunge(user)
        return user
    finally:
        db.close()


def public_credits(user: User) -> int:
    return OWNER_PROMO if is_owner(user) else (user.credits or 0) + (user.referral_coins or 0)


def user_json(user: User):
    return {"id": user.id, "username": user.username, "email": user.email, "bio": user.bio or "", "avatar_url": user.avatar_url or "", "credits": public_credits(user), "is_owner": is_owner(user), "is_verified": bool(getattr(user, "is_verified", 0))}


def public_user_json(user: User):
    return {"id": user.id, "username": user.username, "bio": user.bio or "", "avatar_url": user.avatar_url or "", "is_verified": bool(getattr(user, "is_verified", 0))}


def post_json(post: Post, liked=False, following=False, hashtags=None, reposted=False, bookmarked=False, bookmark_count=0):
    return {"id": post.id, "user_id": post.user_id, "username": post.username, "caption": post.caption or "", "video_url": post.video_url, "music_name": post.music_name or "Original Sound", "likes": post.likes or 0, "comments": post.comments or 0, "shares": post.shares or 0, "views": post.views or 0, "boost_score": post.boost_score or 0, "liked": liked, "following": following, "hashtags": hashtags or [], "repost_of_id": post.repost_of_id, "is_repost": bool(post.repost_of_id), "reposted": reposted, "repost_count": post.repost_count or 0, "bookmarked": bookmarked, "bookmark_count": bookmark_count, "created_at": post.created_at.isoformat() if post.created_at else None}


def _spend_coins(user: User, amount: int):
    if is_owner(user):
        return 0, 0
    if amount <= 0:
        raise HTTPException(400, "Coin amount must be positive.")
    referral = min(user.referral_coins or 0, amount)
    paid = amount - referral
    if paid > (user.credits or 0):
        raise HTTPException(400, f"Not enough coins. You need {amount} coins.")
    user.referral_coins = (user.referral_coins or 0) - referral
    user.credits = (user.credits or 0) - paid
    return referral, paid


def _uname(name: str) -> str:
    name = (name or "").strip()
    return name if name.startswith("@") else "@" + name


def _notify(db, user_id: int, text: str):
    if user_id:
        db.add(Notification(user_id=user_id, text=text[:300]))


def _hashtags(db, post: Post):
    return [r.hashtag for r in db.query(PostHashtag).filter(PostHashtag.post_id == post.id).all()]


def _save_hashtags(db, post: Post, caption: str):
    tags = {w[1:].lower() for w in (caption or "").split() if w.startswith("#") and len(w) > 1}
    for tag in tags:
        db.add(PostHashtag(post_id=post.id, hashtag=tag[:60]))


def _issue_token(db, user: User):
    token = secrets.token_urlsafe(32)
    db.add(SessionToken(user_id=user.id, token=token, expires_at=None))
    db.commit()
    return token


def _blocked_ids(db, user_id: int):
    return {x.blocked_id for x in db.query(Block).filter(Block.blocker_id == user_id).all()}


@app.get("/")
def root():
    return {"message": "MusicSocial is running", "status": "ok", "version": "2.0.0"}


@app.get("/health")
def health():
    return {"status": "healthy", "owner_configured": bool(OWNER_EMAIL or OWNER_USERNAME), "database": engine.dialect.name, "uploads": str(UPLOAD_DIR)}


@app.get("/privacy-policy", response_class=HTMLResponse)
def privacy_policy():
    return """<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>MusicSocial Privacy Policy</title><style>body{font-family:Arial,sans-serif;line-height:1.6;margin:0;background:#090B1A;color:#f5f7ff}main{max-width:900px;margin:auto;padding:28px 20px 60px}h1,h2{color:#fff}.card{background:#12162b;border-radius:16px;padding:22px;margin:18px 0}small{color:#a9afc3}</style></head><body><main><h1>MusicSocial Privacy Policy</h1><small>Effective date: October 7, 2026</small><div class="card"><p>MusicSocial ("MusicSocial", "we", "us", or "our") is a social platform for sharing and discovering music, videos and creator content. This Privacy Policy explains what information we collect, how we use it, how we protect it, and the choices available to you.</p></div><h2>1. Information We Collect</h2><div class="card"><p><strong>Account information:</strong> username, email address, password information (stored as a secure password hash), profile biography, profile picture and account-related settings.</p><p><strong>User content:</strong> videos, stories, music/audio uploads, captions, comments, messages and other content you choose to upload or send through MusicSocial.</p><p><strong>Social activity:</strong> likes, follows, reposts, bookmarks, shares, views, notifications, reports, blocks and related activity.</p><p><strong>Payments and monetization:</strong> payment references, purchased credit packages, payment status, creator earnings and withdrawal information that you submit. Card payment processing is handled by our payment provider, Paystack; MusicSocial does not store your full card number.</p><p><strong>Device information:</strong> device notification tokens when you enable or use push notifications.</p><p><strong>Creator verification information:</strong> information and proof you submit when requesting creator or music verification.</p><p><strong>Camera and microphone:</strong> the Android app may request camera and microphone permissions so you can record or create content. We use the resulting media only for the features you choose to use.</p></div><h2>2. How We Use Information</h2><div class="card"><p>We use information to create and manage accounts, provide MusicSocial social and creator features, store and display content you choose to publish, process payments and monetization requests, provide notifications and messaging, prevent abuse and fraud, enforce our rules, improve reliability, and respond to support or verification requests.</p></div><h2>3. Sharing of Information</h2><div class="card"><p>We do not sell your personal information. Information may be shared when necessary to provide the service, including with service providers such as payment processors and hosting/infrastructure providers, or when required by law, to protect users, or to enforce our rights.</p><p>Content you choose to make public, such as your username, profile information, posts, comments and public creator content, may be visible to other MusicSocial users.</p></div><h2>4. Payments</h2><div class="card"><p>Payments for MusicSocial credit packages may be processed by Paystack. Payment providers may collect and process payment information according to their own privacy policies and terms. MusicSocial receives transaction information needed to confirm and manage your purchase.</p></div><h2>5. Data Security</h2><div class="card"><p>We use reasonable technical and organizational measures to protect account information, including secure password hashing and authenticated sessions. No online service can guarantee absolute security.</p></div><h2>6. Data Retention</h2><div class="card"><p>We retain information for as long as reasonably necessary to provide MusicSocial, maintain security and records, process transactions, resolve disputes, comply with legal obligations, and enforce our policies. Public content and account information may remain until you delete it or your account, subject to legitimate retention requirements.</p></div><h2>7. Your Choices and Account Deletion</h2><div class="card"><p>You can manage information through available MusicSocial account features. You may request deletion of your account and associated personal information by contacting the developer through the contact information provided on the MusicSocial Google Play listing. Some information may need to be retained where required by law or for legitimate security, fraud-prevention or transaction-record purposes.</p></div><h2>8. Children's Privacy</h2><div class="card"><p>MusicSocial is not intended for children under 13. We do not knowingly collect personal information from children under 13. If you believe a child has provided personal information, please contact the developer so appropriate action can be taken.</p></div><h2>9. Changes to This Policy</h2><div class="card"><p>We may update this Privacy Policy when our services or legal requirements change. The updated version will be published on this page with a new effective date.</p></div><h2>10. Contact</h2><div class="card"><p>For privacy questions or account-deletion requests, contact the MusicSocial developer using the developer contact information shown on the MusicSocial Google Play listing.</p></div></main></body></html>"""


@app.post("/auth/register")
def register(username: str = Form(...), email: str = Form(...), password: str = Form(...), referral_code: str = Form("")):
    username, email = _uname(username), email.strip().lower()
    if len(username) < 3 or len(username) > 30:
        raise HTTPException(400, "Username must be 3–30 characters.")
    if len(password) < 6:
        raise HTTPException(400, "Password must be at least 6 characters.")
    db = SessionLocal()
    try:
        if db.query(User).filter(User.username == username).first():
            raise HTTPException(400, "That username is taken.")
        if db.query(User).filter(User.email == email).first():
            raise HTTPException(400, "That email is already registered.")
        referrer = db.query(User).filter(User.referral_code == referral_code.strip().upper()).first() if referral_code.strip() else None
        user = User(username=username, email=email, password_hash=hash_password(password), referral_code=("MSC" + secrets.token_hex(5)).upper(), referred_by_user_id=referrer.id if referrer else None, credits=100)
        db.add(user); db.commit(); db.refresh(user)
        if referrer and referrer.id != user.id:
            db.add(ReferralReward(referrer_id=referrer.id, referred_user_id=user.id, coins=2))
            referrer.referral_coins = (referrer.referral_coins or 0) + 2
            _notify(db, referrer.id, f"{user.username} joined with your code. +2 coins")
            db.commit()
        token = _issue_token(db, user)
        return {"token": token, "user": user_json(user)}
    finally:
        db.close()


@app.post("/auth/login")
def login(email: str = Form(...), password: str = Form(...)):
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.email == email.strip().lower()).first()
        if not user or not verify_password(password, user.password_hash):
            raise HTTPException(401, "Email or password is incorrect.")
        token = _issue_token(db, user)
        return {"token": token, "user": user_json(user)}
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
    me_user = current_user(authorization); db = SessionLocal()
    try:
        row = db.query(User).filter(User.id == me_user.id).first(); row.bio = bio[:160]; db.commit(); db.refresh(row)
        return {"success": True, "user": user_json(row)}
    finally: db.close()


@app.post("/me/avatar")
async def upload_avatar(avatar: UploadFile = File(...), authorization: str | None = Header(default=None)):
    user = current_user(authorization)
    allowed = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"}; mime = (avatar.content_type or "").lower()
    if mime not in allowed: raise HTTPException(400, "Use JPG, PNG, or WEBP for your avatar.")
    path = None; db = SessionLocal()
    try:
        name = f"avatar_{user.id}_{uuid.uuid4().hex}{allowed[mime]}"; path = UPLOAD_DIR / name; total = 0
        with open(path, "wb") as f:
            while True:
                chunk = await avatar.read(1024 * 1024)
                if not chunk: break
                total += len(chunk)
                if total > 8 * 1024 * 1024: raise HTTPException(413, "Avatar is too large (max 8 MB).")
                f.write(chunk)
        row = db.query(User).filter(User.id == user.id).first(); row.avatar_url = f"/uploads/{name}"; db.commit(); db.refresh(row)
        return {"success": True, "user": user_json(row)}
    except HTTPException:
        if path and path.exists(): path.unlink()
        raise
    finally:
        await avatar.close(); db.close()


@app.get("/wallet")
def wallet(authorization: str | None = Header(default=None)):
    user = current_user(authorization); db = SessionLocal()
    try:
        row = db.query(User).filter(User.id == user.id).first()
        return {"success": True, "credits": public_credits(row), "referral_coins": row.referral_coins or 0}
    finally: db.close()


@app.get("/users/suggestions")
def user_suggestions(authorization: str | None = Header(default=None)):
    me_user = current_user(authorization); db = SessionLocal()
    try:
        following = {x.following_id for x in db.query(Follow).filter(Follow.follower_id == me_user.id).all()}
        blocked = _blocked_ids(db, me_user.id)
        rows = db.query(User).filter(User.id != me_user.id).order_by(User.id.desc()).limit(50).all()
        out = []
        for u in rows:
            if u.id in blocked: continue
            item = public_user_json(u); item["followers"] = db.query(Follow).filter(Follow.following_id == u.id).count(); item["following"] = u.id in following; out.append(item)
        return {"success": True, "users": out}
    finally: db.close()


@app.get("/users/{username}")
def profile(username: str, authorization: str | None = Header(default=None)):
    viewer = None
    if authorization:
        try: viewer = current_user(authorization)
        except HTTPException: pass
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.username == _uname(username)).first()
        if not user: raise HTTPException(404, "User not found.")
        data = user_json(user) if viewer and viewer.id == user.id else public_user_json(user)
        followers = db.query(Follow).filter(Follow.following_id == user.id).count(); following = db.query(Follow).filter(Follow.follower_id == user.id).count()
        posts = db.query(Post).filter(Post.user_id == user.id).order_by(Post.id.desc()).limit(60).all()
        return {"success": True, "user": data, "followers": followers, "following": following, "posts": [post_json(p) for p in posts]}
    finally: db.close()


@app.post("/users/{user_id}/follow")
def follow_user(user_id: int, authorization: str | None = Header(default=None)):
    me_user = current_user(authorization)
    if me_user.id == user_id: raise HTTPException(400, "You cannot follow yourself.")
    db = SessionLocal()
    try:
        if not db.query(User).filter(User.id == user_id).first(): raise HTTPException(404, "User not found.")
        row = db.query(Follow).filter(Follow.follower_id == me_user.id, Follow.following_id == user_id).first()
        if row: db.delete(row); following = False
        else: db.add(Follow(follower_id=me_user.id, following_id=user_id)); _notify(db, user_id, f"{me_user.username} followed you"); following = True
        db.commit(); return {"success": True, "following": following}
    finally: db.close()


@app.post("/blocks/{user_id}")
def block_user(user_id: int, authorization: str | None = Header(default=None)):
    me = current_user(authorization)
    if me.id == user_id: raise HTTPException(400, "You cannot block yourself.")
    db = SessionLocal()
    try:
        if not db.query(User).filter(User.id == user_id).first(): raise HTTPException(404, "User not found.")
        if not db.query(Block).filter(Block.blocker_id == me.id, Block.blocked_id == user_id).first(): db.add(Block(blocker_id=me.id, blocked_id=user_id))
        db.query(Follow).filter(or_(and_(Follow.follower_id == me.id, Follow.following_id == user_id), and_(Follow.follower_id == user_id, Follow.following_id == me.id))).delete(synchronize_session=False)
        db.commit(); return {"success": True, "blocked": True}
    finally: db.close()


@app.delete("/blocks/{user_id}")
def unblock_user(user_id: int, authorization: str | None = Header(default=None)):
    me = current_user(authorization); db = SessionLocal()
    try:
        db.query(Block).filter(Block.blocker_id == me.id, Block.blocked_id == user_id).delete(); db.commit(); return {"success": True, "blocked": False}
    finally: db.close()


@app.get("/blocks")
def list_blocks(authorization: str | None = Header(default=None)):
    me = current_user(authorization); db = SessionLocal()
    try:
        rows = db.query(Block).filter(Block.blocker_id == me.id).order_by(Block.id.desc()).all()
        users = [db.query(User).filter(User.id == x.blocked_id).first() for x in rows]
        return {"success": True, "users": [public_user_json(u) for u in users if u]}
    finally: db.close()


@app.post("/stories")
async def upload_story(media: UploadFile = File(...), caption: str = Form(""), authorization: str | None = Header(default=None)):
    user = current_user(authorization)
    allowed = {"image/jpeg": (".jpg", "image"), "image/png": (".png", "image"), "image/webp": (".webp", "image"), "video/mp4": (".mp4", "video"), "video/webm": (".webm", "video"), "video/quicktime": (".mov", "video")}
    mime = (media.content_type or "").lower()
    if mime not in allowed: raise HTTPException(400, "Use JPG, PNG, WEBP, MP4, WEBM, or MOV for a story.")
    suffix, media_type = allowed[mime]; path = None; db = SessionLocal()
    try:
        name = f"story_{user.id}_{uuid.uuid4().hex}{suffix}"; path = UPLOAD_DIR / name; total = 0; limit = 30*1024*1024 if media_type == "image" else 100*1024*1024
        with open(path, "wb") as f:
            while True:
                chunk = await media.read(1024*1024)
                if not chunk: break
                total += len(chunk)
                if total > limit: raise HTTPException(413, "Story file is too large.")
                f.write(chunk)
        story = Story(user_id=user.id, media_url=f"/uploads/{name}", media_type=media_type, caption=caption[:300], expires_at=datetime.utcnow()+timedelta(hours=24)); db.add(story); db.commit(); db.refresh(story)
        return {"success": True, "story": story_json(db, story)}
    except HTTPException:
        if path and path.exists(): path.unlink()
        raise
    finally: await media.close(); db.close()


def story_json(db, story):
    u = db.query(User).filter(User.id == story.user_id).first()
    return {"id": story.id, "user_id": story.user_id, "username": u.username if u else "@creator", "avatar_url": u.avatar_url if u else "", "media_url": story.media_url, "media_type": story.media_type or "image", "caption": story.caption or "", "created_at": story.created_at.isoformat() if story.created_at else "", "expires_at": story.expires_at.isoformat() if story.expires_at else ""}


@app.get("/stories")
def get_stories(authorization: str | None = Header(default=None)):
    me = current_user(authorization); db = SessionLocal()
    try:
        now = datetime.utcnow(); db.query(Story).filter(Story.expires_at <= now).delete(synchronize_session=False); db.commit()
        following_ids = {x.following_id for x in db.query(Follow).filter(Follow.follower_id == me.id).all()}
        rows = db.query(Story).filter(Story.expires_at > now).order_by(Story.created_at.desc()).limit(200).all()
        rows.sort(key=lambda x:(0 if x.user_id == me.id else 1 if x.user_id in following_ids else 2, -(x.created_at.timestamp() if x.created_at else 0)))
        return {"success": True, "stories": [story_json(db,x) for x in rows]}
    finally: db.close()


@app.get("/posts")
def get_posts(authorization: str | None = Header(default=None)):
    viewer = None
    if authorization:
        try: viewer = current_user(authorization)
        except HTTPException: pass
    db = SessionLocal()
    try:
        blocked = _blocked_ids(db, viewer.id) if viewer else set()
        rows = db.query(Post).order_by(Post.boost_score.desc(), Post.id.desc()).limit(200).all()
        rows = [p for p in rows if p.user_id not in blocked]
        liked_ids = {x.post_id for x in db.query(Like).filter(Like.user_id == viewer.id).all()} if viewer else set()
        reposted_ids = {x.post_id for x in db.query(Repost).filter(Repost.user_id == viewer.id).all()} if viewer else set()
        bookmarked_ids = {x.post_id for x in db.query(Bookmark).filter(Bookmark.user_id == viewer.id).all()} if viewer else set()
        following_ids = {x.following_id for x in db.query(Follow).filter(Follow.follower_id == viewer.id).all()} if viewer else set()
        def _pj(p):
            source_id = p.repost_of_id or p.id
            return post_json(p, p.id in liked_ids, p.user_id in following_ids, _hashtags(db,p), source_id in reposted_ids, source_id in bookmarked_ids, db.query(Bookmark).filter(Bookmark.post_id == source_id).count())
        return {"success": True, "posts": [_pj(p) for p in rows]}
    finally: db.close()


@app.post("/posts")
async def create_post(video: UploadFile = File(...), caption: str = Form(""), music_name: str = Form("Original Sound"), authorization: str | None = Header(default=None)):
    user = current_user(authorization); mime = (video.content_type or "").lower()
    if not mime.startswith("video/"): raise HTTPException(400, "Please select a video file.")
    ext = Path(video.filename or "video.mp4").suffix.lower() or ".mp4"; name = f"video_{user.id}_{uuid.uuid4().hex}{ext}"; path = UPLOAD_DIR/name; total = 0
    try:
        with open(path,"wb") as f:
            while True:
                chunk = await video.read(1024*1024)
                if not chunk: break
                total += len(chunk)
                if total > 150*1024*1024: raise HTTPException(413,"Video is too large. Maximum size is 150 MB.")
                f.write(chunk)
    except HTTPException:
        path.unlink(missing_ok=True); raise
    finally: await video.close()
    db = SessionLocal()
    try:
        post = Post(user_id=user.id,username=user.username,caption=caption[:500],video_url=f"/uploads/{name}",music_name=(music_name or "Original Sound")[:120]); db.add(post); db.commit(); db.refresh(post); _save_hashtags(db,post,caption); db.commit()
        return {"success": True, "post": post_json(post)}
    except Exception:
        db.rollback(); path.unlink(missing_ok=True); raise
    finally: db.close()


@app.delete("/posts/{post_id}")
def delete_post(post_id: int, authorization: str | None = Header(default=None)):
    me = current_user(authorization); db = SessionLocal()
    try:
        post = db.query(Post).filter(Post.id == post_id).first()
        if not post: raise HTTPException(404,"Video not found.")
        if post.user_id != me.id and not is_owner(me): raise HTTPException(403,"You can only delete your own video.")
        file_path = UPLOAD_DIR / Path(post.video_url or "").name
        repost_copies = db.query(Post).filter(Post.repost_of_id == post.id).all()
        for copy in repost_copies:
            db.query(Comment).filter(Comment.post_id == copy.id).delete(synchronize_session=False)
            db.query(Like).filter(Like.post_id == copy.id).delete(synchronize_session=False)
            db.query(Repost).filter(Repost.post_id == copy.id).delete(synchronize_session=False)
            db.query(ViewEvent).filter(ViewEvent.post_id == copy.id).delete(synchronize_session=False)
            db.query(PostHashtag).filter(PostHashtag.post_id == copy.id).delete(synchronize_session=False)
            db.query(Bookmark).filter(Bookmark.post_id == copy.id).delete(synchronize_session=False)
            db.query(Promotion).filter(Promotion.post_id == copy.id).delete(synchronize_session=False)
            db.delete(copy)
        db.query(Comment).filter(Comment.post_id==post.id).delete(synchronize_session=False)
        db.query(Like).filter(Like.post_id==post.id).delete(synchronize_session=False)
        db.query(Repost).filter(Repost.post_id==post.id).delete(synchronize_session=False)
        db.query(ViewEvent).filter(ViewEvent.post_id==post.id).delete(synchronize_session=False)
        db.query(PostHashtag).filter(PostHashtag.post_id==post.id).delete(synchronize_session=False)
        db.query(Bookmark).filter(Bookmark.post_id==post.id).delete(synchronize_session=False)
        db.query(Promotion).filter(Promotion.post_id==post.id).delete(synchronize_session=False)
        db.delete(post)
        db.commit()
        file_path.unlink(missing_ok=True)
        return {"success": True,"post_id":post_id}
    except HTTPException: db.rollback(); raise
    finally: db.close()


@app.post("/posts/{post_id}/view")
def record_view(post_id:int,authorization:str|None=Header(default=None)):
    me=current_user(authorization); db=SessionLocal()
    try:
        post=db.query(Post).filter(Post.id==post_id).first()
        if not post: raise HTTPException(404,"Video not found.")
        if not db.query(ViewEvent).filter(ViewEvent.post_id==post_id,ViewEvent.user_id==me.id).first(): db.add(ViewEvent(post_id=post_id,user_id=me.id)); post.views=(post.views or 0)+1; db.commit()
        return {"success":True,"views":post.views or 0}
    finally: db.close()


@app.post("/posts/{post_id}/like")
def like_post(post_id:int,authorization:str|None=Header(default=None)):
    me=current_user(authorization); db=SessionLocal()
    try:
        post=db.query(Post).filter(Post.id==post_id).first()
        if not post: raise HTTPException(404,"Video not found.")
        row=db.query(Like).filter(Like.user_id==me.id,Like.post_id==post_id).first()
        if row: db.delete(row); post.likes=max(0,(post.likes or 0)-1); liked=False
        else: db.add(Like(user_id=me.id,post_id=post_id)); post.likes=(post.likes or 0)+1; liked=True; _notify(db,post.user_id,f"{me.username} liked your video") if post.user_id and post.user_id!=me.id else None
        db.commit(); return {"success":True,"liked":liked,"likes":post.likes}
    finally: db.close()


@app.get("/posts/{post_id}/comments")
def get_comments(post_id:int):
    db=SessionLocal()
    try:
        rows=db.query(Comment).filter(Comment.post_id==post_id).order_by(Comment.id.asc()).limit(200).all(); return {"success":True,"comments":[{"id":c.id,"post_id":c.post_id,"username":c.username,"text":c.text,"created_at":c.created_at.isoformat() if c.created_at else ""} for c in rows]}
    finally: db.close()


@app.post("/posts/{post_id}/comments")
def add_comment(post_id:int,text:str=Form(...),authorization:str|None=Header(default=None)):
    me=current_user(authorization); clean=text.strip()
    if not clean: raise HTTPException(400,"Type a comment first.")
    db=SessionLocal()
    try:
        post=db.query(Post).filter(Post.id==post_id).first()
        if not post: raise HTTPException(404,"Video not found.")
        db.add(Comment(post_id=post_id,user_id=me.id,username=me.username,text=clean[:500])); post.comments=(post.comments or 0)+1; _notify(db,post.user_id,f"{me.username} commented on your video") if post.user_id and post.user_id!=me.id else None; db.commit(); return {"success":True}
    finally: db.close()


@app.post("/posts/{post_id}/share")
def share_post(post_id:int,authorization:str|None=Header(default=None)):
    current_user(authorization); db=SessionLocal()
    try:
        post=db.query(Post).filter(Post.id==post_id).first()
        if not post: raise HTTPException(404,"Video not found.")
        post.shares=(post.shares or 0)+1; db.commit(); return {"success":True,"shares":post.shares,"url":f"/shared/posts/{post.id}"}
    finally: db.close()


@app.post("/posts/{post_id}/repost")
def repost_post(post_id:int,authorization:str|None=Header(default=None)):
    me=current_user(authorization); db=SessionLocal()
    try:
        original=db.query(Post).filter(Post.id==post_id).first()
        if not original: raise HTTPException(404,"Video not found.")
        source_id=original.repost_of_id or original.id; source=db.query(Post).filter(Post.id==source_id).first() or original
        existing=db.query(Repost).filter(Repost.user_id==me.id,Repost.post_id==source.id).first()
        if existing: return {"success":True,"already_reposted":True,"repost_count":source.repost_count or 0}
        db.add(Repost(user_id=me.id,post_id=source.id)); source.repost_count=(source.repost_count or 0)+1; source.shares=(source.shares or 0)+1
        db.add(Post(user_id=me.id,username=me.username,caption=source.caption or "",video_url=source.video_url,music_name=source.music_name or "Original Sound",repost_of_id=source.id)); _notify(db,source.user_id,f"{me.username} reposted your video") if source.user_id and source.user_id!=me.id else None; db.commit()
        return {"success":True,"already_reposted":False,"repost_count":source.repost_count}
    finally: db.close()


@app.post("/posts/{post_id}/bookmark")
def bookmark_post(post_id:int,authorization:str|None=Header(default=None)):
    me=current_user(authorization); db=SessionLocal()
    try:
        post=db.query(Post).filter(Post.id==post_id).first()
        if not post: raise HTTPException(404,"Video not found.")
        source_id=post.repost_of_id or post.id
        exists=db.query(Bookmark).filter(Bookmark.user_id==me.id,Bookmark.post_id==source_id).first()
        if exists:
            db.delete(exists); bookmarked=False
        else:
            db.add(Bookmark(user_id=me.id,post_id=source_id)); bookmarked=True
        db.commit(); count=db.query(Bookmark).filter(Bookmark.post_id==source_id).count()
        return {"success":True,"bookmarked":bookmarked,"bookmark_count":count}
    finally: db.close()

@app.get("/bookmarks")
def get_bookmarks(authorization:str|None=Header(default=None)):
    me=current_user(authorization); db=SessionLocal()
    try:
        rows=db.query(Bookmark).filter(Bookmark.user_id==me.id).order_by(Bookmark.id.desc()).limit(200).all()
        out=[]
        for b in rows:
            p=db.query(Post).filter(Post.id==b.post_id).first()
            if not p: continue
            out.append(post_json(p,db.query(Like).filter(Like.user_id==me.id,Like.post_id==p.id).first() is not None,False,_hashtags(db,p),db.query(Repost).filter(Repost.user_id==me.id,Repost.post_id==(p.repost_of_id or p.id)).first() is not None,True,db.query(Bookmark).filter(Bookmark.post_id==p.id).count()))
        return {"success":True,"posts":out}
    finally: db.close()

@app.get("/shared/posts/{post_id}",response_class=HTMLResponse)
def shared_post(post_id:int):
    db=SessionLocal()
    try:
        post=db.query(Post).filter(Post.id==post_id).first()
        if not post: raise HTTPException(404,"Video not found.")
        video=post.video_url
        if video.startswith("/"): video=video
        return f"""<!doctype html><meta name="viewport" content="width=device-width,initial-scale=1"><body style="margin:0;background:#090B1A;color:#fff;font-family:sans-serif"><video src="{video}" controls autoplay style="width:100%;max-height:80vh;background:#000"></video><p style="padding:16px">{post.username}<br>{post.caption or ""}</p></body>"""
    finally: db.close()


@app.get("/feed/for-you")
def for_you_feed(authorization:str|None=Header(default=None)):
    viewer=current_user(authorization); db=SessionLocal()
    try:
        blocked=_blocked_ids(db,viewer.id); following={x.following_id for x in db.query(Follow).filter(Follow.follower_id==viewer.id).all()}
        rows=db.query(Post).order_by(Post.boost_score.desc(),Post.views.desc(),Post.likes.desc(),Post.id.desc()).limit(300).all(); rows=[p for p in rows if p.user_id not in blocked]
        liked={x.post_id for x in db.query(Like).filter(Like.user_id==viewer.id).all()}; reposted={x.post_id for x in db.query(Repost).filter(Repost.user_id==viewer.id).all()}
        return {"success":True,"posts":[post_json(p,p.id in liked,p.user_id in following,_hashtags(db,p),(p.repost_of_id or p.id) in reposted) for p in rows]}
    finally: db.close()


@app.get("/feed/following")
def following_feed(authorization:str|None=Header(default=None)):
    viewer=current_user(authorization); db=SessionLocal()
    try:
        ids={x.following_id for x in db.query(Follow).filter(Follow.follower_id==viewer.id).all()}; ids.add(viewer.id); blocked=_blocked_ids(db,viewer.id)
        rows=db.query(Post).filter(Post.user_id.in_(ids)).order_by(Post.id.desc()).limit(200).all(); rows=[p for p in rows if p.user_id not in blocked]
        liked={x.post_id for x in db.query(Like).filter(Like.user_id==viewer.id).all()}; reposted={x.post_id for x in db.query(Repost).filter(Repost.user_id==viewer.id).all()}
        return {"success":True,"posts":[post_json(p,p.id in liked,True,_hashtags(db,p),(p.repost_of_id or p.id) in reposted) for p in rows]}
    finally: db.close()


@app.get("/notifications")
def notifications(authorization:str|None=Header(default=None)):
    me=current_user(authorization); db=SessionLocal()
    try:
        rows=db.query(Notification).filter(Notification.user_id==me.id).order_by(Notification.id.desc()).limit(80).all(); return {"success":True,"notifications":[{"id":n.id,"text":n.text,"read":bool(n.is_read),"created_at":n.created_at.isoformat() if n.created_at else ""} for n in rows]}
    finally: db.close()


@app.post("/notifications/{notification_id}/read")
def mark_notification_read(notification_id:int,authorization:str|None=Header(default=None)):
    me=current_user(authorization); db=SessionLocal()
    try:
        row=db.query(Notification).filter(Notification.id==notification_id,Notification.user_id==me.id).first()
        if row: row.is_read=1; db.commit()
        return {"success":True}
    finally: db.close()


@app.post("/notifications/read-all")
def mark_all_notifications_read(authorization:str|None=Header(default=None)):
    me=current_user(authorization); db=SessionLocal()
    try: db.query(Notification).filter(Notification.user_id==me.id).update({"is_read":1}); db.commit(); return {"success":True}
    finally: db.close()


@app.post("/notifications/device-token")
def register_device_token(token:str=Form(...),platform:str=Form("android"),authorization:str|None=Header(default=None)):
    me=current_user(authorization); token=token.strip()
    if not token: raise HTTPException(400,"Device token is required.")
    db=SessionLocal()
    try:
        row=db.query(DeviceToken).filter(DeviceToken.token==token).first()
        if row: row.user_id=me.id; row.platform=platform[:30]; row.last_seen_at=datetime.utcnow()
        else: db.add(DeviceToken(user_id=me.id,token=token,platform=platform[:30]))
        db.commit(); return {"success":True}
    finally: db.close()


@app.delete("/notifications/device-token")
def delete_device_token(token:str=Form(...),authorization:str|None=Header(default=None)):
    me=current_user(authorization); db=SessionLocal()
    try: db.query(DeviceToken).filter(DeviceToken.user_id==me.id,DeviceToken.token==token.strip()).delete(); db.commit(); return {"success":True}
    finally: db.close()


@app.get("/credits/packages")
def credit_packages(): return {"success":True,"packages":[{"id":k,**v} for k,v in CREDIT_PACKAGES.items()]}


def _paystack_headers(): return {"Authorization":f"Bearer {PAYSTACK_SECRET_KEY}","Content-Type":"application/json"}


def _fulfill_payment(db, row, data=None):
    if row.status=="success": return 0
    row.status="success"; row.paid_at=datetime.utcnow(); row.paystack_transaction_id=str((data or {}).get("id") or "")
    user=db.query(User).filter(User.id==row.user_id).first(); user.credits=(user.credits or 0)+row.credits; return row.credits


@app.post("/payments/paystack/initialize")
def initialize_paystack(package_id:str=Form(...),authorization:str|None=Header(default=None)):
    user=current_user(authorization); pack=CREDIT_PACKAGES.get(package_id)
    if not pack: raise HTTPException(400,"Unknown package.")
    if not PAYSTACK_SECRET_KEY: raise HTTPException(500,"Payments are not configured.")
    reference="msc_"+uuid.uuid4().hex; db=SessionLocal()
    try:
        db.add(PaymentTransaction(user_id=user.id,reference=reference,package_id=package_id,amount_naira=pack["naira"],credits=pack["credits"])); db.commit()
        with httpx.Client(timeout=30) as client: res=client.post(f"{PAYSTACK_BASE_URL}/transaction/initialize",headers=_paystack_headers(),json={"email":user.email,"amount":pack["naira"]*100,"reference":reference})
        data=res.json()
        if not data.get("status"): raise HTTPException(400,data.get("message","Could not start payment."))
        return {"success":True,"reference":reference,"authorization_url":data["data"]["authorization_url"],"credits":pack["credits"],"naira":pack["naira"]}
    finally: db.close()


@app.get("/payments/paystack/verify/{reference}")
def verify_paystack(reference:str,authorization:str|None=Header(default=None)):
    user=current_user(authorization)
    if not PAYSTACK_SECRET_KEY: raise HTTPException(500,"Payments are not configured.")
    db=SessionLocal()
    try:
        row=db.query(PaymentTransaction).filter(PaymentTransaction.reference==reference,PaymentTransaction.user_id==user.id).first()
        if not row: raise HTTPException(404,"Payment not found.")
        if row.status=="success": return {"paid":True,"credits_added":0,"credits":public_credits(db.query(User).filter(User.id==user.id).first())}
        with httpx.Client(timeout=30) as client: res=client.get(f"{PAYSTACK_BASE_URL}/transaction/verify/{reference}",headers=_paystack_headers())
        data=res.json(); paid=bool(data.get("status") and data.get("data",{}).get("status")=="success")
        added=0
        if paid: added=_fulfill_payment(db,row,data.get("data",{})); db.commit()
        current=db.query(User).filter(User.id==user.id).first(); return {"paid":paid,"credits_added":added,"credits":public_credits(current)}
    finally: db.close()


@app.post("/payments/paystack/webhook")
async def paystack_webhook(request:Request):
    body=await request.body(); signature=request.headers.get("x-paystack-signature","")
    if not PAYSTACK_SECRET_KEY or not signature or not hmac.compare_digest(signature,hmac.new(PAYSTACK_SECRET_KEY.encode(),body,hashlib.sha512).hexdigest()): raise HTTPException(401,"Invalid signature.")
    payload=json.loads(body.decode("utf-8")); data=payload.get("data",{}); reference=data.get("reference")
    if payload.get("event")=="charge.success" and reference:
        db=SessionLocal()
        try:
            row=db.query(PaymentTransaction).filter(PaymentTransaction.reference==reference).first()
            if row: _fulfill_payment(db,row,data); db.commit()
        finally: db.close()
    return {"received":True}


@app.get("/discover")
def discover(category:str="for_you",authorization:str|None=Header(default=None)):
    viewer=None
    if authorization:
        try: viewer=current_user(authorization)
        except HTTPException: pass
    db=SessionLocal()
    try:
        blocked=_blocked_ids(db,viewer.id) if viewer else set(); rows=db.query(Post).order_by(Post.id.desc()).limit(300).all(); rows=[p for p in rows if p.user_id not in blocked]
        cat=category.strip().lower()
        score=lambda p:(p.boost_score or 0)*20+(p.likes or 0)*4+(p.comments or 0)*3+(p.shares or 0)*2+(p.views or 0)
        if cat=="new_music": rows=sorted(rows,key=lambda p:p.id,reverse=True)[:100]
        elif cat=="trending": rows=sorted(rows,key=score,reverse=True)[:100]
        else: rows=sorted(rows,key=lambda p:(p.boost_score or 0,p.id),reverse=True)[:100]
        return {"success":True,"category":cat,"posts":[post_json(p) for p in rows]}
    finally: db.close()


@app.get("/music/{title}")
def music_feed(title:str):
    db=SessionLocal()
    try:
        rows=db.query(Post).filter(Post.music_name.ilike(f"%{title.strip()}%")).order_by(Post.id.desc()).limit(100).all(); return {"success":True,"music":title,"posts":[post_json(p) for p in rows]}
    finally: db.close()


@app.get("/search")
def search_all(q:str="", authorization:str|None=Header(default=None)):
    q=q.strip(); db=SessionLocal()
    try:
        viewer=None
        if authorization:
            try: viewer=current_user(authorization)
            except HTTPException: pass
        if not q:
            return {"success":True,"users":[],"posts":[]}
        term=q.lstrip("#").strip()
        like=f"%{term}%"
        users=db.query(User).filter(or_(User.username.ilike(like),User.bio.ilike(like))).order_by(User.id.desc()).limit(30).all()
        posts=db.query(Post).filter(or_(Post.caption.ilike(like),Post.username.ilike(like),Post.music_name.ilike(like))).order_by(Post.id.desc()).limit(50).all()
        if q.startswith("#"):
            tagged=db.query(PostHashtag).filter(PostHashtag.hashtag==term.lower()).all()
            tagged_ids={x.post_id for x in tagged}
            posts=[p for p in db.query(Post).filter(Post.id.in_(tagged_ids)).order_by(Post.id.desc()).limit(50).all()] if tagged_ids else []
        blocked=_blocked_ids(db,viewer.id) if viewer else set()
        posts=[p for p in posts if p.user_id not in blocked]
        liked_ids={x.post_id for x in db.query(Like).filter(Like.user_id==viewer.id).all()} if viewer else set()
        reposted_ids={x.post_id for x in db.query(Repost).filter(Repost.user_id==viewer.id).all()} if viewer else set()
        bookmarked_ids={x.post_id for x in db.query(Bookmark).filter(Bookmark.user_id==viewer.id).all()} if viewer else set()
        following_ids={x.following_id for x in db.query(Follow).filter(Follow.follower_id==viewer.id).all()} if viewer else set()
        result=[]
        for p in posts:
            source_id=p.repost_of_id or p.id
            result.append(post_json(p,p.id in liked_ids,p.user_id in following_ids,_hashtags(db,p),source_id in reposted_ids,source_id in bookmarked_ids,db.query(Bookmark).filter(Bookmark.post_id==source_id).count()))
        user_results=[]
        for u in users:
            item=public_user_json(u); item["followers"]=db.query(Follow).filter(Follow.following_id==u.id).count(); user_results.append(item)
        return {"success":True,"users":user_results,"posts":result}
    finally: db.close()


@app.get("/hashtags/{hashtag}")
def hashtag_feed(hashtag:str):
    tag=hashtag.strip().lstrip("#").lower(); db=SessionLocal()
    try:
        rows=db.query(PostHashtag).filter(PostHashtag.hashtag==tag).order_by(PostHashtag.id.desc()).all(); posts=[]
        for row in rows:
            p=db.query(Post).filter(Post.id==row.post_id).first()
            if p: posts.append(post_json(p))
        return {"success":True,"hashtag":f"#{tag}","posts":posts}
    finally: db.close()


@app.get("/trending/hashtags")
def trending_hashtags():
    db=SessionLocal()
    try:
        rows=db.query(PostHashtag).all(); counts={}
        for r in rows: counts[r.hashtag]=counts.get(r.hashtag,0)+1
        return {"success":True,"hashtags":[{"hashtag":f"#{k}","count":v} for k,v in sorted(counts.items(),key=lambda x:x[1],reverse=True)[:50]]}
    finally: db.close()


@app.get("/trending/music")
def trending_music():
    db=SessionLocal()
    try:
        rows=db.query(Post.music_name).all(); counts={}
        for (name,) in rows:
            name=name or "Original Sound"; counts[name]=counts.get(name,0)+1
        return {"success":True,"music":[{"music_name":k,"uses":v} for k,v in sorted(counts.items(),key=lambda x:x[1],reverse=True)[:50]]}
    finally: db.close()


@app.get("/creator/earnings")
def creator_earnings(authorization:str|None=Header(default=None)):
    user=current_user(authorization); db=SessionLocal()
    try:
        videos=db.query(Post).filter(Post.user_id==user.id).all(); gifts=db.query(CreatorGift).filter(CreatorGift.recipient_id==user.id).all(); promos=db.query(Promotion).filter(Promotion.user_id==user.id).all(); row=db.query(User).filter(User.id==user.id).first()
        return {"success":True,"credits":public_credits(row),"videos":len(videos),"likes":sum(p.likes or 0 for p in videos),"comments":sum(p.comments or 0 for p in videos),"shares":sum(p.shares or 0 for p in videos),"views":sum(p.views or 0 for p in videos),"credits_spent_on_boosts":sum(p.credits or 0 for p in promos),"gift_coins_received":sum(g.coins for g in gifts),"creator_earnings":sum(g.withdrawable_coins or 0 for g in gifts)}
    finally: db.close()


@app.get("/rewards")
def rewards(authorization:str|None=Header(default=None)):
    user=current_user(authorization); db=SessionLocal()
    try:
        row=db.query(User).filter(User.id==user.id).first()
        if not row.referral_code: row.referral_code=("MSC"+secrets.token_hex(5)).upper(); db.commit()
        return {"success":True,"referral_code":row.referral_code,"credits":public_credits(row)}
    finally: db.close()


@app.post("/coins/transfer")
def transfer_coins(recipient_username:str=Form(...),coins:int=Form(...),note:str=Form(""),authorization:str|None=Header(default=None)):
    user=current_user(authorization); coins=int(coins)
    if coins<=0: raise HTTPException(400,"Enter more than 0 coins.")
    db=SessionLocal()
    try:
        recipient=db.query(User).filter(User.username==_uname(recipient_username)).first()
        if not recipient: raise HTTPException(404,"That username was not found.")
        if recipient.id==user.id: raise HTTPException(400,"You cannot send coins to yourself.")
        sender=db.query(User).filter(User.id==user.id).first(); ref,paid=_spend_coins(sender,coins)
        if is_owner(sender):
            paid = coins
        recipient.referral_coins=(recipient.referral_coins or 0)+ref; recipient.credits=(recipient.credits or 0)+paid
        db.add(CoinTransfer(sender_id=sender.id,recipient_id=recipient.id,coins=coins,note=note[:160])); _notify(db,recipient.id,f"{sender.username} sent you {coins} coins"); db.commit()
        return {"success":True,"credits":public_credits(sender),"recipient":recipient.username}
    finally: db.close()


@app.post("/gifts")
def send_gift(recipient_username:str=Form(""),coins:int=Form(...),gift_name:str=Form("Gift"),room_id:Optional[int]=Form(None),authorization:str|None=Header(default=None)):
    user=current_user(authorization); coins=int(coins)
    if coins<=0: raise HTTPException(400,"Gift amount must be greater than zero.")
    db=SessionLocal()
    try:
        recipient=None
        if room_id:
            room=db.query(LiveRoom).filter(LiveRoom.id==room_id,LiveRoom.status=="live").first()
            if not room: raise HTTPException(404,"That live room has ended.")
            recipient=db.query(User).filter(User.id==room.host_id).first()
        if not recipient and recipient_username: recipient=db.query(User).filter(User.username==_uname(recipient_username)).first()
        if not recipient: raise HTTPException(404,"Creator not found.")
        if recipient.id==user.id: raise HTTPException(400,"You cannot gift yourself.")
        sender=db.query(User).filter(User.id==user.id).first(); ref,paid=_spend_coins(sender,coins)
        db.add(CreatorGift(sender_id=sender.id,recipient_id=recipient.id,room_id=room_id,coins=coins,withdrawable_coins=coins if is_owner(sender) else paid,gift_name=gift_name[:80])); _notify(db,recipient.id,f"{sender.username} sent you a {gift_name} worth {coins} coins"); db.commit()
        return {"success":True,"credits":public_credits(sender)}
    finally: db.close()


@app.get("/withdrawals")
def withdrawals(authorization:str|None=Header(default=None)):
    user=current_user(authorization); db=SessionLocal()
    try:
        rows=db.query(WithdrawalRequest).filter(WithdrawalRequest.user_id==user.id).order_by(WithdrawalRequest.id.desc()).limit(50).all(); return {"success":True,"withdrawals":[{"id":r.id,"amount_naira":r.amount_naira,"fee_naira":r.fee_naira,"net_naira":r.net_naira,"reserved_coins":r.amount_naira//CREATOR_COIN_NAIRA,"status":r.status,"bank_name":r.bank_name,"account_name":r.account_name,"account_number":r.account_number,"created_at":r.created_at.isoformat() if r.created_at else ""} for r in rows]}
    finally: db.close()


@app.post("/withdrawals")
def request_withdrawal(amount_naira:int=Form(...),bank_name:str=Form(...),account_name:str=Form(...),account_number:str=Form(...),authorization:str|None=Header(default=None)):
    user=current_user(authorization); amount=int(amount_naira)
    if amount<5000: raise HTTPException(400,"Minimum withdrawal is ₦5,000.")
    fee=round(amount*0.15); net=amount-fee; db=SessionLocal()
    try:
        already=db.query(WithdrawalRequest).filter(WithdrawalRequest.user_id==user.id,WithdrawalRequest.status.in_(["pending","approved","paid"])).all(); withdrawn=sum(x.amount_naira for x in already); gifts=db.query(CreatorGift).filter(CreatorGift.recipient_id==user.id).all(); eligible=sum((g.withdrawable_coins or 0) for g in gifts)*CREATOR_COIN_NAIRA-withdrawn
        if amount>eligible: raise HTTPException(400,f"Not enough withdrawable earnings. Available: ₦{max(0,eligible):,}")
        row=WithdrawalRequest(user_id=user.id,amount_naira=amount,fee_naira=fee,net_naira=net,status="pending",bank_name=bank_name[:120],account_name=account_name[:120],account_number=account_number[:40]); db.add(row); db.commit(); db.refresh(row); return {"success":True,"id":row.id,"amount_naira":amount,"fee_naira":fee,"net_naira":net,"status":"pending"}
    finally: db.close()


@app.post("/promotions")
def promote_post(post_id:int=Form(...),credits:int=Form(BOOST_COST),authorization:str|None=Header(default=None)):
    user=current_user(authorization)
    if int(credits)!=BOOST_COST: raise HTTPException(400,f"Each boost costs exactly {BOOST_COST} coins.")
    db=SessionLocal()
    try:
        post=db.query(Post).filter(Post.id==post_id).first()
        if not post: raise HTTPException(404,"Video not found.")
        if post.user_id!=user.id: raise HTTPException(403,"You can only boost your own videos.")
        row=db.query(User).filter(User.id==user.id).first(); _spend_coins(row,BOOST_COST); post.boost_score=(post.boost_score or 0)+BOOST_COST; db.add(Promotion(user_id=user.id,post_id=post_id,credits=BOOST_COST)); _notify(db,user.id,f"Your video boost is active. {BOOST_COST} coins were used."); db.commit(); return {"success":True,"credits":public_credits(row),"boost_score":post.boost_score}
    finally: db.close()


@app.get("/music-hub")
def music_hub(authorization:str|None=Header(default=None)):
    viewer=None
    if authorization:
        try: viewer=current_user(authorization)
        except HTTPException: pass
    db=SessionLocal()
    try:
        if viewer:
            rows=db.query(MusicTrack).filter(or_(MusicTrack.verification_status=="verified",MusicTrack.owner_id==viewer.id)).order_by(MusicTrack.uses.desc(),MusicTrack.id.desc()).limit(100).all()
        else:
            rows=db.query(MusicTrack).filter(MusicTrack.verification_status=="verified").order_by(MusicTrack.uses.desc(),MusicTrack.id.desc()).limit(100).all()
        return {"success":True,"tracks":[_music_track_json(r) | {"audio_available":(UPLOAD_DIR/Path(r.audio_url or "").name).is_file()} for r in rows]}
    finally: db.close()


@app.get("/music-hub/{track_id}/audio")
def music_track_audio(track_id:int):
    db=SessionLocal()
    try:
        track=db.query(MusicTrack).filter(MusicTrack.id==track_id).first()
        if not track: raise HTTPException(404,"This sound is no longer available.")
        path=UPLOAD_DIR/Path(track.audio_url or "").name
        if not path.is_file(): raise HTTPException(404,"Audio file missing on server.")
        media="audio/mpeg" if path.suffix.lower()==".mp3" else "application/octet-stream"; return FileResponse(path,media_type=media,filename=path.name)
    finally: db.close()


@app.post("/music-hub/upload")
async def upload_music_hub(
    title:str=Form(...), artist:str=Form(""), isrc:str=Form(""), owner_confirmed:bool=Form(False),
    audio:UploadFile=File(...), cover:UploadFile=File(...), proof:UploadFile|None=File(None),
    authorization:str|None=Header(default=None)
):
    user=current_user(authorization)
    if not owner_confirmed: raise HTTPException(400,"Confirm song ownership before uploading.")
    if not audio.content_type or not audio.content_type.startswith("audio/"): raise HTTPException(400,"Please choose an audio file.")
    if not cover.content_type or not cover.content_type.startswith("image/"): raise HTTPException(400,"Please choose a cover image.")
    db=SessionLocal(); created=[]
    try:
        audio_data=await audio.read(25*1024*1024+1)
        if len(audio_data)>25*1024*1024: raise HTTPException(413,"Audio file is too large (max 25 MB).")
        cover_data=await cover.read(8*1024*1024+1)
        if len(cover_data)>8*1024*1024: raise HTTPException(413,"Cover is too large (max 8 MB).")
        proof_data=await proof.read(15*1024*1024+1) if proof else b""
        if len(proof_data)>15*1024*1024: raise HTTPException(413,"Proof file is too large (max 15 MB).")
        def save_blob(prefix, upload, data, default_ext):
            ext=Path(upload.filename or default_ext).suffix.lower() or default_ext
            name=f"{prefix}_{uuid.uuid4().hex}{ext}"; path=UPLOAD_DIR/name; path.write_bytes(data); created.append(path); return f"/uploads/{name}"
        audio_url=save_blob("song",audio,audio_data,".mp3")
        cover_url=save_blob("cover",cover,cover_data,".jpg")
        proof_url=save_blob("proof",proof,proof_data,".bin") if proof and proof_data else ""
        digest=hashlib.sha256(audio_data).hexdigest()
        duplicate=db.query(MusicTrack).filter(MusicTrack.audio_sha256==digest).first()
        owner_is_platform_owner=is_owner(user)
        initial_status="verified" if owner_is_platform_owner else "pending"
        initial_notes=("Verified automatically for the platform owner." if owner_is_platform_owner else "Pending platform owner review.")
        row=MusicTrack(owner_id=user.id,title=title.strip()[:120],artist=artist.strip()[:120] or user.username,audio_url=audio_url,cover_url=cover_url,uses=0,isrc=isrc.strip()[:40],ownership_confirmed=1,verification_status=initial_status,verification_notes=initial_notes,identity_match=1 if artist.strip().lower()==user.username.strip().lower() else 0,proof_url=proof_url,audio_sha256=digest,fingerprint_match_id=duplicate.id if duplicate else None,verified_at=datetime.utcnow() if owner_is_platform_owner else None)
        db.add(row); db.commit(); db.refresh(row)
        return {"success":True,"message":"Song submitted for ownership verification.","track":{"id":row.id,"title":row.title,"artist":row.artist,"audio_url":row.audio_url,"cover_url":row.cover_url,"uses":0,"verification_status":row.verification_status}}
    except HTTPException:
        db.rollback()
        for path in created:
            path.unlink(missing_ok=True)
        raise
    except Exception:
        db.rollback()
        for path in created:
            path.unlink(missing_ok=True)
        raise
    finally:
        await audio.close(); await cover.close()
        if proof: await proof.close()
        db.close()


@app.delete("/music-hub/{track_id}")
def delete_music_track(track_id:int,authorization:str|None=Header(default=None)):
    user=current_user(authorization); db=SessionLocal()
    try:
        track=db.query(MusicTrack).filter(MusicTrack.id==track_id).first()
        if not track: raise HTTPException(404,"Song not found.")
        if track.owner_id!=user.id and not is_owner(user): raise HTTPException(403,"You can only delete your own uploaded songs.")
        locals_to_delete=[UPLOAD_DIR/Path(x or "").name for x in (track.audio_url,track.cover_url,track.proof_url)]
        db.delete(track); db.commit()
        for local in locals_to_delete: local.unlink(missing_ok=True)
        return {"success":True,"track_id":track_id}
    finally: db.close()


@app.post("/music-hub/{track_id}/use")
def use_music_track(track_id:int,authorization:str|None=Header(default=None)):
    user=current_user(authorization); db=SessionLocal()
    try:
        track=db.query(MusicTrack).filter(MusicTrack.id==track_id).first()
        if not track: raise HTTPException(404,"Song not found.")
        if (track.verification_status or "pending").lower() != "verified":
            raise HTTPException(403,"This song is still awaiting ownership verification.")
        track.uses=(track.uses or 0)+1
        if track.owner_id and track.owner_id!=user.id: _notify(db,track.owner_id,f"{user.username} used your song: {track.title}")
        db.commit(); return {"success":True,"uses":track.uses,"track":track.title,"audio_url":track.audio_url}
    finally: db.close()


def _music_track_json(r):
    return {"id":r.id,"owner_id":r.owner_id,"title":r.title,"artist":r.artist,"audio_url":r.audio_url,"cover_url":r.cover_url or "","uses":r.uses or 0,"isrc":r.isrc or "","ownership_confirmed":bool(r.ownership_confirmed),"verification_status":r.verification_status or "pending","verification_notes":r.verification_notes or "","identity_match":bool(r.identity_match),"proof_submitted":bool(r.proof_url),"fingerprint_match_id":r.fingerprint_match_id,"verified_at":r.verified_at.isoformat() if r.verified_at else None,"proof_url":r.proof_url or "","audio_sha256":r.audio_sha256 or "","created_at":r.created_at.isoformat() if r.created_at else None}

@app.get("/owner/music-verifications")
def owner_music_verifications(authorization:str|None=Header(default=None)):
    me=current_user(authorization)
    if not is_owner(me): raise HTTPException(403,"Only the platform owner can review music ownership.")
    db=SessionLocal()
    try:
        rows=db.query(MusicTrack).order_by(MusicTrack.id.desc()).limit(500).all()
        summary={"pending":sum(1 for r in rows if (r.verification_status or "pending")=="pending"),"verified":sum(1 for r in rows if (r.verification_status or "").lower()=="verified"),"rejected":sum(1 for r in rows if (r.verification_status or "").lower()=="rejected"),"total":len(rows)}
        return {"success":True,"summary":summary,"tracks":[_music_track_json(r) for r in rows]}
    finally: db.close()

@app.post("/owner/music-verifications/{track_id}/confirm")
def confirm_owner_music(track_id:int,authorization:str|None=Header(default=None)):
    me=current_user(authorization)
    if not is_owner(me): raise HTTPException(403,"Only the platform owner can verify music.")
    db=SessionLocal()
    try:
        row=db.query(MusicTrack).filter(MusicTrack.id==track_id).first()
        if not row: raise HTTPException(404,"Song not found.")
        row.verification_status="verified"; row.verification_notes="Verified by the MusicSocial platform owner."; row.verified_at=datetime.utcnow(); row.identity_match=1 if row.identity_match else 0
        db.commit(); return {"success":True,"track":_music_track_json(row)}
    finally: db.close()

@app.post("/owner/music-verifications/{track_id}/reject")
def reject_owner_music(track_id:int,reason:str=Form("Rejected by platform owner."),authorization:str|None=Header(default=None)):
    me=current_user(authorization)
    if not is_owner(me): raise HTTPException(403,"Only the platform owner can reject music.")
    db=SessionLocal()
    try:
        row=db.query(MusicTrack).filter(MusicTrack.id==track_id).first()
        if not row: raise HTTPException(404,"Song not found.")
        row.verification_status="rejected"; row.verification_notes=reason[:500]; row.verified_at=None; db.commit(); return {"success":True,"track":_music_track_json(row)}
    finally: db.close()

@app.get("/drafts")
def list_drafts(authorization:str|None=Header(default=None)):
    user=current_user(authorization); db=SessionLocal()
    try:
        rows=db.query(Draft).filter(Draft.user_id==user.id).order_by(Draft.id.desc()).limit(100).all(); return {"success":True,"drafts":[{"id":d.id,"video_url":d.video_url,"caption":d.caption,"music_name":d.music_name,"trim_start_ms":d.trim_start_ms,"trim_end_ms":d.trim_end_ms,"crop":d.crop,"overlay_text":d.overlay_text,"status":d.status,"created_at":d.created_at.isoformat() if d.created_at else None,"updated_at":d.updated_at.isoformat() if d.updated_at else None} for d in rows]}
    finally: db.close()


@app.post("/drafts")
def create_draft(video_url:str=Form(""),caption:str=Form(""),music_name:str=Form("Original Sound"),trim_start_ms:int=Form(0),trim_end_ms:int=Form(0),crop:str=Form(""),overlay_text:str=Form(""),authorization:str|None=Header(default=None)):
    user=current_user(authorization); db=SessionLocal()
    try:
        d=Draft(user_id=user.id,video_url=video_url[:1000],caption=caption[:500],music_name=music_name[:120],trim_start_ms=trim_start_ms,trim_end_ms=trim_end_ms,crop=crop[:200],overlay_text=overlay_text[:300]); db.add(d); db.commit(); db.refresh(d); return {"success":True,"draft_id":d.id}
    finally: db.close()


@app.delete("/drafts/{draft_id}")
def delete_draft(draft_id:int,authorization:str|None=Header(default=None)):
    user=current_user(authorization); db=SessionLocal()
    try:
        d=db.query(Draft).filter(Draft.id==draft_id,Draft.user_id==user.id).first()
        if not d: raise HTTPException(404,"Draft not found.")
        db.delete(d); db.commit(); return {"success":True}
    finally: db.close()


@app.post("/posts/{post_id}/editor")
def edit_post(post_id:int,caption:str=Form(""),music_name:str=Form(""),authorization:str|None=Header(default=None)):
    user=current_user(authorization); db=SessionLocal()
    try:
        p=db.query(Post).filter(Post.id==post_id).first()
        if not p: raise HTTPException(404,"Video not found.")
        if p.user_id!=user.id and not is_owner(user): raise HTTPException(403,"You can only edit your own video.")
        if caption: p.caption=caption[:500]
        if music_name: p.music_name=music_name[:120]
        db.query(PostHashtag).filter(PostHashtag.post_id==p.id).delete(synchronize_session=False); _save_hashtags(db,p,p.caption); db.commit(); return {"success":True,"post":post_json(p)}
    finally: db.close()


def _conversation(db,a,b):
    one,two=sorted([a,b]); row=db.query(Conversation).filter(Conversation.user_one_id==one,Conversation.user_two_id==two).first()
    if not row: row=Conversation(user_one_id=one,user_two_id=two); db.add(row); db.commit(); db.refresh(row)
    return row


@app.get("/conversations")
def list_conversations(authorization:str|None=Header(default=None)):
    me=current_user(authorization); db=SessionLocal()
    try:
        rows=db.query(Conversation).filter(or_(Conversation.user_one_id==me.id,Conversation.user_two_id==me.id)).order_by(Conversation.id.desc()).all(); out=[]
        for c in rows:
            other_id=c.user_two_id if c.user_one_id==me.id else c.user_one_id; other=db.query(User).filter(User.id==other_id).first(); last=db.query(Message).filter(Message.conversation_id==c.id).order_by(Message.id.desc()).first(); out.append({"id":c.id,"user_id":other.id if other else 0,"username":other.username if other else "@creator","user":public_user_json(other) if other else None,"last_message":last.text if last else "","last_message_at":last.created_at.isoformat() if last and last.created_at else ""})
        return {"success":True,"conversations":out}
    finally: db.close()


@app.post("/conversations/{user_id}/messages")
def send_message(user_id:int,text:str=Form(...),authorization:str|None=Header(default=None)):
    me=current_user(authorization); clean=text.strip()
    if not clean: raise HTTPException(400,"Type a message first.")
    if me.id==user_id: raise HTTPException(400,"You cannot message yourself.")
    db=SessionLocal()
    try:
        if not db.query(User).filter(User.id==user_id).first(): raise HTTPException(404,"User not found.")
        c=_conversation(db,me.id,user_id); msg=Message(conversation_id=c.id,sender_id=me.id,text=clean[:2000]); db.add(msg); _notify(db,user_id,f"{me.username} sent you a message"); db.commit(); db.refresh(msg); return {"success":True,"message":{"id":msg.id,"conversation_id":c.id,"sender_id":msg.sender_id,"text":msg.text}}
    finally: db.close()


@app.get("/conversations/{conversation_id}/messages")
def get_messages(conversation_id:int,authorization:str|None=Header(default=None)):
    me=current_user(authorization); db=SessionLocal()
    try:
        c=db.query(Conversation).filter(Conversation.id==conversation_id).first()
        if not c or me.id not in (c.user_one_id,c.user_two_id): raise HTTPException(403,"Conversation not found.")
        rows=db.query(Message).filter(Message.conversation_id==conversation_id).order_by(Message.id.asc()).limit(200).all(); db.query(Message).filter(Message.conversation_id==conversation_id,Message.sender_id!=me.id).update({"is_read":1}); db.commit()
        return {"success":True,"messages":[{"id":m.id,"conversation_id":m.conversation_id,"sender_id":m.sender_id,"text":m.text,"read":bool(m.is_read),"created_at":m.created_at.isoformat() if m.created_at else None} for m in rows]}
    finally: db.close()


@app.post("/conversations/{conversation_id}/typing")
def set_typing(conversation_id:int,is_typing:int=Form(...),authorization:str|None=Header(default=None)):
    me=current_user(authorization); db=SessionLocal()
    try:
        c=db.query(Conversation).filter(Conversation.id==conversation_id).first()
        if not c or me.id not in (c.user_one_id,c.user_two_id): raise HTTPException(403,"Conversation not found.")
        row=db.query(TypingStatus).filter(TypingStatus.conversation_id==conversation_id,TypingStatus.user_id==me.id).first()
        if not row: row=TypingStatus(conversation_id=conversation_id,user_id=me.id,is_typing=1 if is_typing else 0); db.add(row)
        else: row.is_typing=1 if is_typing else 0; row.updated_at=datetime.utcnow()
        db.commit(); return {"success":True}
    finally: db.close()


@app.get("/conversations/{conversation_id}/typing")
def get_typing(conversation_id:int,authorization:str|None=Header(default=None)):
    me=current_user(authorization); db=SessionLocal()
    try:
        c=db.query(Conversation).filter(Conversation.id==conversation_id).first()
        if not c or me.id not in (c.user_one_id,c.user_two_id): raise HTTPException(403,"Conversation not found.")
        rows=db.query(TypingStatus).filter(TypingStatus.conversation_id==conversation_id,TypingStatus.user_id!=me.id).all(); return {"success":True,"typing":[{"user_id":r.user_id,"is_typing":bool(r.is_typing)} for r in rows if r.updated_at and r.updated_at>=datetime.utcnow()-timedelta(seconds=10)]}
    finally: db.close()


@app.post("/users/{user_id}/online")
def online(user_id:int,authorization:str|None=Header(default=None)):
    me=current_user(authorization)
    if me.id!=user_id: raise HTTPException(403,"You can only update your own presence.")
    db=SessionLocal()
    try: row=db.query(User).filter(User.id==me.id).first(); row.last_seen_at=datetime.utcnow(); db.commit(); return {"success":True,"online":True}
    finally: db.close()


@app.get("/users/{user_id}/presence")
def presence(user_id:int):
    db=SessionLocal()
    try:
        u=db.query(User).filter(User.id==user_id).first()
        if not u: raise HTTPException(404,"User not found.")
        online=bool(u.last_seen_at and u.last_seen_at>=datetime.utcnow()-timedelta(minutes=2)); return {"success":True,"online":online,"last_seen_at":u.last_seen_at.isoformat() if u.last_seen_at else None}
    finally: db.close()


def _live_msgs(db,room_id):
    rows=db.query(LiveMessage).filter(LiveMessage.room_id==room_id).order_by(LiveMessage.id.asc()).limit(80).all(); return [{"id":m.id,"username":m.username,"text":m.text,"created_at":m.created_at.isoformat() if m.created_at else ""} for m in rows]


def _room_json(db,room):
    host=db.query(User).filter(User.id==room.host_id).first(); viewers=db.query(LiveViewer).filter(LiveViewer.room_id==room.id).count(); room.viewer_count=viewers
    return {"id":room.id,"title":room.title,"host_id":room.host_id,"host_username":host.username if host else "@creator","room_key":room.room_key,"viewers":viewers,"viewer_count":viewers,"live":room.status=="live","status":room.status,"messages":_live_msgs(db,room.id)}


def _start_room(db,me,title):
    room=db.query(LiveRoom).filter(LiveRoom.host_id==me.id,LiveRoom.status=="live").first()
    if not room:
        room=LiveRoom(host_id=me.id,title=(title or "").strip()[:120] or f"{me.username} is live",room_key=secrets.token_urlsafe(24),viewer_count=1); db.add(room); db.commit(); db.refresh(room)
    if not db.query(LiveViewer).filter(LiveViewer.room_id==room.id,LiveViewer.user_id==me.id).first(): db.add(LiveViewer(room_id=room.id,user_id=me.id)); db.commit()
    return room


@app.get("/live")
def live(): return live_rooms()


@app.get("/live/rooms")
def live_rooms():
    db=SessionLocal()
    try: rows=db.query(LiveRoom).filter(LiveRoom.status=="live").order_by(LiveRoom.id.desc()).limit(50).all(); return {"success":True,"rooms":[_room_json(db,r) for r in rows]}
    finally: db.close()


@app.post("/live/start")
@app.post("/live/rooms")
def start_live(title:str=Form("Live on MusicSocial"),authorization:str|None=Header(default=None)):
    me=current_user(authorization); db=SessionLocal()
    try:
        room=_start_room(db,me,title)
        for f in db.query(Follow).filter(Follow.following_id==me.id).all(): _notify(db,f.follower_id,f"{me.username} started a LIVE: {room.title}")
        db.commit(); return {"success":True,"room":_room_json(db,room)}
    finally: db.close()


@app.get("/live/rooms/{room_id}")
def get_live_room(room_id:int):
    db=SessionLocal()
    try:
        room=db.query(LiveRoom).filter(LiveRoom.id==room_id).first()
        if not room: raise HTTPException(404,"Live room not found.")
        return {"success":True,"room":_room_json(db,room)}
    finally: db.close()


@app.get("/live/rooms/{room_id}/stream-config")
def live_stream_config(room_id:int):
    db=SessionLocal()
    try:
        room=db.query(LiveRoom).filter(LiveRoom.id==room_id).first()
        if not room: raise HTTPException(404,"Live room not found.")
        return {"success":True,"room_id":room_id,"provider":os.getenv("LIVE_STREAM_PROVIDER","none"),"playback_url":os.getenv("LIVE_PLAYBACK_URL",""),"ingest_url":os.getenv("LIVE_INGEST_URL","")}
    finally: db.close()


@app.post("/live/rooms/{room_id}/join")
def join_live(room_id:int,authorization:str|None=Header(default=None)):
    me=current_user(authorization); db=SessionLocal()
    try:
        room=db.query(LiveRoom).filter(LiveRoom.id==room_id,LiveRoom.status=="live").first()
        if not room: raise HTTPException(404,"That live room has ended.")
        if not db.query(LiveViewer).filter(LiveViewer.room_id==room_id,LiveViewer.user_id==me.id).first(): db.add(LiveViewer(room_id=room_id,user_id=me.id)); db.commit()
        return {"success":True,"room":_room_json(db,room)}
    finally: db.close()


@app.post("/live/rooms/{room_id}/leave")
def leave_live(room_id:int,authorization:str|None=Header(default=None)):
    me=current_user(authorization); db=SessionLocal()
    try:
        db.query(LiveViewer).filter(LiveViewer.room_id==room_id,LiveViewer.user_id==me.id).delete(); db.commit(); room=db.query(LiveRoom).filter(LiveRoom.id==room_id).first(); return {"success":True,"room":_room_json(db,room) if room else None}
    finally: db.close()


@app.post("/live/{room_id}/end")
def end_live(room_id:int,authorization:str|None=Header(default=None)):
    me=current_user(authorization); db=SessionLocal()
    try:
        room=db.query(LiveRoom).filter(LiveRoom.id==room_id).first()
        if not room: return {"success":True}
        if me.id!=room.host_id and not is_owner(me): raise HTTPException(403,"Only the host can end this live.")
        room.status="ended"; room.ended_at=datetime.utcnow(); db.commit(); return {"success":True}
    finally: db.close()


@app.post("/live/rooms/{room_id}/messages")
def live_chat(room_id:int,text:str=Form(...),authorization:str|None=Header(default=None)):
    me=current_user(authorization); clean=text.strip()
    if not clean: raise HTTPException(400,"Type a message first.")
    db=SessionLocal()
    try:
        room=db.query(LiveRoom).filter(LiveRoom.id==room_id,LiveRoom.status=="live").first()
        if not room: raise HTTPException(404,"Live room not found.")
        msg=LiveMessage(room_id=room_id,user_id=me.id,username=me.username,text=clean[:300]); db.add(msg); db.commit(); db.refresh(msg); return {"success":True,"message":{"id":msg.id,"username":msg.username,"text":msg.text,"created_at":msg.created_at.isoformat()}}
    finally: db.close()


@app.post("/reports")
def report(target_type:str=Form(...),target_id:Optional[int]=Form(None),reason:str=Form(...),details:str=Form(""),authorization:str|None=Header(default=None)):
    me=current_user(authorization); db=SessionLocal()
    try: db.add(Report(reporter_id=me.id,target_type=target_type[:50],target_id=target_id,reason=reason[:100],details=details[:1000])); db.commit(); return {"success":True}
    finally: db.close()


@app.get("/admin/reports")
def admin_reports(authorization:str|None=Header(default=None)):
    me=current_user(authorization)
    if not is_owner(me): raise HTTPException(403,"Only the platform owner can open this dashboard.")
    db=SessionLocal()
    try:
        rows=db.query(Report).order_by(Report.id.desc()).limit(200).all(); return {"success":True,"reports":[{"id":r.id,"reporter_id":r.reporter_id,"target_type":r.target_type,"target_id":r.target_id,"reason":r.reason,"details":r.details,"status":r.status,"created_at":r.created_at.isoformat() if r.created_at else None} for r in rows]}
    finally: db.close()


@app.post("/admin/reports/{report_id}/resolve")
def resolve_report(report_id:int,status:str=Form("resolved"),authorization:str|None=Header(default=None)):
    me=current_user(authorization)
    if not is_owner(me): raise HTTPException(403,"Only the platform owner can resolve reports.")
    db=SessionLocal()
    try:
        row=db.query(Report).filter(Report.id==report_id).first()
        if not row: raise HTTPException(404,"Report not found.")
        row.status=status[:30]; db.commit(); return {"success":True}
    finally: db.close()


@app.post("/verification/request")
def verification_request(note:str=Form(""),authorization:str|None=Header(default=None)):
    me=current_user(authorization); db=SessionLocal()
    try:
        row=db.query(VerificationRequest).filter(VerificationRequest.user_id==me.id).first()
        if row: row.note=note[:1000]; row.status="pending"; row.reviewed_at=None
        else: db.add(VerificationRequest(user_id=me.id,note=note[:1000]))
        db.commit(); return {"success":True,"status":"pending"}
    finally: db.close()


@app.get("/verification/status")
def verification_status(authorization:str|None=Header(default=None)):
    me=current_user(authorization); db=SessionLocal()
    try:
        u=db.query(User).filter(User.id==me.id).first(); r=db.query(VerificationRequest).filter(VerificationRequest.user_id==me.id).first(); return {"success":True,"is_verified":bool(u.is_verified),"status":r.status if r else "none","note":r.note if r else ""}
    finally: db.close()


@app.get("/admin/verification-requests")
def admin_verification_requests(authorization:str|None=Header(default=None)):
    me=current_user(authorization)
    if not is_owner(me): raise HTTPException(403,"Only the platform owner can open this dashboard.")
    db=SessionLocal()
    try:
        rows=db.query(VerificationRequest).order_by(VerificationRequest.id.desc()).limit(200).all(); return {"success":True,"requests":[{"id":r.id,"user_id":r.user_id,"note":r.note,"status":r.status,"created_at":r.created_at.isoformat() if r.created_at else None} for r in rows]}
    finally: db.close()


@app.post("/admin/verification/{user_id}")
def review_verification(user_id:int,status:str=Form(...),authorization:str|None=Header(default=None)):
    me=current_user(authorization)
    if not is_owner(me): raise HTTPException(403,"Only the platform owner can review verification.")
    db=SessionLocal()
    try:
        u=db.query(User).filter(User.id==user_id).first(); r=db.query(VerificationRequest).filter(VerificationRequest.user_id==user_id).first()
        if not u: raise HTTPException(404,"User not found.")
        approved=status.lower() in {"approved","approve","verified"}; u.is_verified=1 if approved else 0
        if r: r.status="approved" if approved else status[:30]; r.reviewed_at=datetime.utcnow()
        db.commit(); return {"success":True,"is_verified":bool(u.is_verified)}
    finally: db.close()


@app.get("/creator/analytics")
def creator_analytics(authorization:str|None=Header(default=None)):
    me=current_user(authorization); db=SessionLocal()
    try:
        posts=db.query(Post).filter(Post.user_id==me.id).all(); return {"success":True,"videos":len(posts),"views":sum(p.views or 0 for p in posts),"likes":sum(p.likes or 0 for p in posts),"comments":sum(p.comments or 0 for p in posts),"shares":sum(p.shares or 0 for p in posts),"followers":db.query(Follow).filter(Follow.following_id==me.id).count()}
    finally: db.close()


@app.get("/admin/dashboard")
def admin_dashboard(authorization:str|None=Header(default=None)):
    me=current_user(authorization)
    if not is_owner(me): raise HTTPException(403,"Only the platform owner can open this dashboard.")
    db=SessionLocal()
    try:
        paid=db.query(PaymentTransaction).filter(PaymentTransaction.status=="success").all()
        withdrawal_rows=db.query(WithdrawalRequest).order_by(WithdrawalRequest.id.desc()).limit(200).all()
        withdrawals=[{"id":w.id,"username":(db.query(User).filter(User.id==w.user_id).first().username if db.query(User).filter(User.id==w.user_id).first() else ""),"amount_naira":w.amount_naira,"fee_naira":w.fee_naira,"net_naira":w.net_naira,"reserved_coins":int(w.amount_naira/CREATOR_COIN_NAIRA),"status":w.status,"bank_name":w.bank_name,"account_name":w.account_name,"account_number":w.account_number,"rejection_reason":getattr(w,"rejection_reason","") or "","created_at":w.created_at.isoformat() if w.created_at else ""} for w in withdrawal_rows]
        return {"success":True,"revenue_naira":sum(t.amount_naira for t in paid),"credits_sold":sum(t.credits for t in paid),"credits_spent_on_boosts":sum((p.credits or 0) for p in db.query(Promotion).all()),"successful_payments":len(paid),"users":db.query(User).count(),"posts":db.query(Post).count(),"referral_rewards":db.query(ReferralReward).count(),"gift_coins_sent":sum(g.coins for g in db.query(CreatorGift).all()),"withdrawal_requests":len(withdrawal_rows),"withdrawals":withdrawals,"reports":db.query(Report).count(),"verification_requests":db.query(VerificationRequest).count()}
    finally: db.close()


@app.get("/admin/promo-wallet")
def admin_promo_wallet(authorization:str|None=Header(default=None)):
    me=current_user(authorization)
    if not is_owner(me): raise HTTPException(403,"Only the platform owner can open this wallet.")
    return {"success":True,"promo_credits":OWNER_PROMO}


@app.post("/admin/promo-wallet/restore")
def admin_promo_restore(authorization:str|None=Header(default=None)):
    me=current_user(authorization)
    if not is_owner(me): raise HTTPException(403,"Only the platform owner can restore promo credits.")
    return {"success":True,"promo_credits":OWNER_PROMO}


@app.get("/owner/dashboard")
def owner_dashboard(authorization:str|None=Header(default=None)):
    return admin_dashboard(authorization)


@app.get("/owner/promo-wallet")
def owner_promo_wallet(authorization:str|None=Header(default=None)):
    return admin_promo_wallet(authorization)
