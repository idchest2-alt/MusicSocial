from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Header
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from sqlalchemy import create_engine, Column, Integer, String, DateTime, ForeignKey, UniqueConstraint
from sqlalchemy.orm import declarative_base, sessionmaker, Session
from datetime import datetime
from pathlib import Path
import hashlib
import secrets
import shutil
import uuid
import re

app = FastAPI(title="MusicSocial API V2")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

BASE_DIR = Path(__file__).resolve().parent
DATABASE_URL = f"sqlite:///{BASE_DIR / 'musicsocial.db'}"
engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

UPLOAD_DIR = BASE_DIR / "uploads"
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


Base.metadata.create_all(bind=engine)


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
        "comments": post.comments or 0, "shares": post.shares or 0,
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
def register(username: str = Form(...), email: str = Form(...), password: str = Form(...)):
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
        user = User(
            username=username,
            email=email,
            password_hash=hash_password(password),
            credits=100,
        )
        db.add(user)
        db.commit()
        db.refresh(user)
        token = secrets.token_urlsafe(48)
        db.add(SessionToken(user_id=user.id, token=token))
        db.commit()
        return {"success": True, "token": token, "user": user_json(user)}
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
        with open(file_path, "wb") as buffer:
            shutil.copyfileobj(video.file, buffer)
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
        tags = sorted({t.lower() for t in re.findall(r"(?<!\w)#([A-Za-z0-9_]{1,40})", clean_caption)})
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
    return {"success": True, "credits": user.credits or 0}


@app.post("/promotions")
def promote_post(
    post_id: int = Form(...),
    credits: int = Form(...),
    authorization: str | None = Header(default=None),
):
    user = current_user(authorization)
    if credits < 10:
        raise HTTPException(400, "Minimum boost is 10 credits")
    db = SessionLocal()
    try:
        post = db.query(Post).filter(Post.id == post_id).first()
        if not post:
            raise HTTPException(404, "Post not found")
        if post.user_id != user.id:
            raise HTTPException(403, "You can only boost your own videos")
        row = db.query(User).filter(User.id == user.id).first()
        if (row.credits or 0) < credits:
            raise HTTPException(400, "Not enough credits")
        row.credits -= credits
        promotion = Promotion(user_id=user.id, post_id=post_id, credits=credits)
        db.add(promotion)
        db.commit()
        return {"success": True, "message": "Video boost activated", "credits": row.credits}
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
        promos = db.query(Promotion).filter(Promotion.user_id == user.id).all()
        videos = db.query(Post).filter(Post.user_id == user.id).all()
        return {
            "success": True, "credits": user.credits or 0,
            "videos": len(videos),
            "likes": sum(p.likes or 0 for p in videos),
            "comments": sum(p.comments or 0 for p in videos),
            "shares": sum(p.shares or 0 for p in videos),
            "credits_spent_on_boosts": sum(p.credits or 0 for p in promos),
            "creator_earnings": 0
        }
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

Base.metadata.create_all(bind=engine)

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
