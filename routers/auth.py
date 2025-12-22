from __future__ import annotations

import os
import re
import secrets
import time
from pathlib import Path
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import jwt
import bcrypt
from pydantic import BaseModel, EmailStr
from sqlalchemy.orm import Session

from database import get_db
from models import User
from utils.otp_service import otp_issue, otp_verify


router = APIRouter(prefix="/auth", tags=["auth"])
bearer = HTTPBearer(auto_error=False)

JWT_SECRET = os.getenv("JWT_SECRET", "change_me")
JWT_ALG = os.getenv("JWT_ALG", "HS256")
JWT_EXP_MIN = int(os.getenv("JWT_EXP_MIN", "43200"))  # 30 days default


def _now() -> datetime:
    return datetime.utcnow()


def _create_token(*, user_id: int, is_guest: bool = False) -> str:
    payload = {
        "sub": str(user_id),
        "is_guest": bool(is_guest),
        "iat": int(_now().timestamp()),
        "exp": int((_now() + timedelta(minutes=JWT_EXP_MIN)).timestamp()),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALG)


def get_current_user(
    creds: Optional[HTTPAuthorizationCredentials] = Depends(bearer),
    db: Session = Depends(get_db),
) -> User:
    if not creds or not creds.credentials:
        raise HTTPException(401, "Missing Authorization token")
    token = creds.credentials
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALG])
    except Exception:
        raise HTTPException(401, "Invalid token")
    sub = payload.get("sub")
    if not sub:
        raise HTTPException(401, "Invalid token")
    user = db.get(User, int(sub))
    if not user:
        raise HTTPException(401, "User not found")

    # Update presence (throttled) so profiles can show "online" status.
    now = _now()
    try:
        if (user.last_active_at is None) or ((now - user.last_active_at).total_seconds() >= 30):
            user.last_active_at = now
            db.add(user)
            db.commit()
    except Exception:
        # Presence updates should never block the request.
        db.rollback()
    return user


def _norm_gender(v: str) -> str:
    return (v or "").strip().lower()


class RegisterIn(BaseModel):
    email: EmailStr
    username: Optional[str] = None
    password: str
    name: str
    country: str
    gender: str  # male|female|cross
    description: Optional[str] = None
    image_url: Optional[str] = None


@router.post("/register")
def register(payload: RegisterIn, db: Session = Depends(get_db)):
    username = (payload.username or "").strip().lower() or None
    email = (str(payload.email).strip().lower() if payload.email else None)

    if username and len(username) < 3:
        raise HTTPException(400, "Username must be at least 3 characters.")
    if len(payload.password.strip()) < 6:
        raise HTTPException(400, "Password must be at least 6 characters.")
    if not payload.name.strip():
        raise HTTPException(400, "Name is required.")
    if not payload.country.strip():
        raise HTTPException(400, "Country is required.")

    gender = _norm_gender(payload.gender)
    if gender not in {"male", "female", "cross"}:
        raise HTTPException(400, "Gender must be male, female, or cross.")

    if email and db.query(User).filter(User.email == email).first():
        raise HTTPException(400, "entry already available")
    if username and db.query(User).filter(User.username == username).first():
        raise HTTPException(400, "entry already available")

    # Multi-byte safe password truncation for bcrypt (max 72 bytes)
    safe_password = payload.password.strip().encode("utf-8")[:72].decode("utf-8", errors="ignore")

    pwd_bytes = safe_password.encode('utf-8')
    salt = bcrypt.gensalt()
    hashed = bcrypt.hashpw(pwd_bytes, salt)
    password_hash = hashed.decode('utf-8')

    user = User(
        email=email,
        username=username,
        password_hash=password_hash,
        name=payload.name.strip(),
        country=payload.country.strip(),
        gender=gender,
        description=(payload.description or "").strip() or None,
        image_url=(payload.image_url or "").strip() or None,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return {"ok": True, "user_id": user.id}


class LoginOtpRequestIn(BaseModel):
    identifier: str  # email or username
    password: str


@router.post("/login/request-otp")
def login_request_otp(payload: LoginOtpRequestIn, db: Session = Depends(get_db)):
    ident = payload.identifier.strip().lower()
    if not ident:
        raise HTTPException(400, "Identifier required")

    if "@" in ident:
        user = db.query(User).filter(User.email == ident).first()
    else:
        user = db.query(User).filter(User.username == ident).first()

    if not user:
        raise HTTPException(404, "Account not found.")

    # Truncate input password to 72 bytes for bcrypt verification
    login_password = payload.password.strip().encode("utf-8")[:72].decode("utf-8", errors="ignore")
    if not bcrypt.checkpw(login_password.encode('utf-8'), user.password_hash.encode('utf-8')):
        raise HTTPException(401, "Incorrect password.")

    if not user.email:
        raise HTTPException(400, "This account has no email; OTP cannot be delivered.")

    otp_issue(identifier=f"user:{user.id}", to_email=user.email)
    return {"ok": True, "message": "OTP sent to your email."}


class LoginVerifyOtpIn(BaseModel):
    identifier: str
    password: str
    otp: str


@router.post("/login/verify-otp")
def login_verify_otp(payload: LoginVerifyOtpIn, db: Session = Depends(get_db)):
    ident = payload.identifier.strip().lower()

    if "@" in ident:
        user = db.query(User).filter(User.email == ident).first()
    else:
        user = db.query(User).filter(User.username == ident).first()

    if not user:
        raise HTTPException(404, "Account not found.")

    # Truncate input password to 72 bytes for bcrypt verification
    login_password = payload.password.strip().encode("utf-8")[:72].decode("utf-8", errors="ignore")
    if not bcrypt.checkpw(login_password.encode('utf-8'), user.password_hash.encode('utf-8')):
        raise HTTPException(401, "Incorrect password.")

    ok = otp_verify(identifier=f"user:{user.id}", otp=payload.otp.strip())
    if not ok:
        raise HTTPException(401, "Invalid/expired OTP.")

    token = _create_token(user_id=user.id, is_guest=False)
    # Mark online immediately on login.
    try:
        user.last_active_at = _now()
        db.add(user)
        db.commit()
    except Exception:
        db.rollback()
    return {
        "ok": True,
        "access_token": token,
        "token_type": "bearer",
        "user": {
            "id": user.id,
            "email": user.email,
            "username": user.username,
            "name": user.name,
            "gender": user.gender,
            "country": user.country,
            "description": user.description or "",
            "image_url": user.image_url or "",
            "is_subscribed": bool(user.is_subscribed),
            "last_active_at": user.last_active_at.isoformat() if user.last_active_at else None,
        },
    }


@router.post("/guest")
def guest_login(db: Session = Depends(get_db)):
    """Creates a lightweight guest user for 'login as guest'."""
    # Generate a safe random password under 72 bytes
    random_pass = secrets.token_hex(16)[:16]
    safe_password = random_pass.encode("utf-8")[:72].decode("utf-8", errors="ignore")

    pwd_bytes = safe_password.encode('utf-8')
    salt = bcrypt.gensalt()
    hashed = bcrypt.hashpw(pwd_bytes, salt)
    password_hash = hashed.decode('utf-8')

    # Determine next guest number
    existing_guests = db.query(User.username).filter(User.username.like("guest_%")).all()
    max_num = 0
    for row in existing_guests:
        uname = row[0]
        m = re.match(r"^guest_(\d+)$", uname)
        if m:
            try:
                num = int(m.group(1))
                if num > max_num:
                    max_num = num
            except ValueError:
                pass
    
    new_num = max_num + 1
    new_username = f"guest_{new_num}"
    new_name = f"Guest {new_num}"

    guest = User(
        email=None,
        username=new_username,
        password_hash=password_hash,
        name=new_name,
        gender="male",
        country="",
        description="",
        image_url="",
        is_subscribed=False,
    )
    db.add(guest)
    db.commit()
    db.refresh(guest)
    # Mark online immediately on guest creation/login.
    try:
        guest.last_active_at = _now()
        db.add(guest)
        db.commit()
    except Exception:
        db.rollback()

    token = _create_token(user_id=guest.id, is_guest=True)
    return {
        "ok": True,
        "access_token": token,
        "token_type": "bearer",
        "user": {
            "id": guest.id,
            "username": guest.username,
            "name": guest.name,
            "gender": guest.gender,
            "country": guest.country,
            "description": guest.description or "",
            "image_url": guest.image_url or "",
            "is_subscribed": False,
            "is_guest": True,
            "last_active_at": guest.last_active_at.isoformat() if guest.last_active_at else None,
        },
    }


class ForgotPasswordRequestIn(BaseModel):
    identifier: str

@router.post("/forgot-password/request-otp")
def forgot_password_request_otp(payload: ForgotPasswordRequestIn, db: Session = Depends(get_db)):
    ident = payload.identifier.strip().lower()
    if not ident:
        raise HTTPException(400, "Identifier required")

    if "@" in ident:
        user = db.query(User).filter(User.email == ident).first()
    else:
        user = db.query(User).filter(User.username == ident).first()

    if not user:
        raise HTTPException(404, "Account not found.")

    if not user.email:
        raise HTTPException(400, "This account has no email; OTP cannot be delivered.")

    otp_issue(identifier=f"reset:{user.id}", to_email=user.email)
    return {"ok": True, "message": "OTP sent to your email."}


class ForgotPasswordResetIn(BaseModel):
    identifier: str
    otp: str
    new_password: str

@router.post("/forgot-password/reset")
def forgot_password_reset(payload: ForgotPasswordResetIn, db: Session = Depends(get_db)):
    ident = payload.identifier.strip().lower()
    
    if "@" in ident:
        user = db.query(User).filter(User.email == ident).first()
    else:
        user = db.query(User).filter(User.username == ident).first()

    if not user:
        raise HTTPException(404, "Account not found.")

    # Verify OTP
    ok = otp_verify(identifier=f"reset:{user.id}", otp=payload.otp.strip())
    if not ok:
        raise HTTPException(401, "Invalid/expired OTP.")

    if len(payload.new_password.strip()) < 6:
        raise HTTPException(400, "Password must be at least 6 characters.")

    # Update Password
    safe_password = payload.new_password.strip().encode("utf-8")[:72].decode("utf-8", errors="ignore")
    pwd_bytes = safe_password.encode('utf-8')
    salt = bcrypt.gensalt()
    hashed = bcrypt.hashpw(pwd_bytes, salt)
    user.password_hash = hashed.decode('utf-8')
    
    db.commit()
    return {"ok": True, "message": "Password updated successfully."}


class UpdateProfileIn(BaseModel):
    name: Optional[str] = None
    image_url: Optional[str] = None


@router.put("/profile")
def update_profile(
    payload: UpdateProfileIn,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    changed = False
    if payload.name is not None:
        new_name = payload.name.strip()
        if not new_name:
            raise HTTPException(400, "Name cannot be empty.")
        current_user.name = new_name
        changed = True

    if payload.image_url is not None:
        new_url = payload.image_url.strip()
        current_user.image_url = new_url
        changed = True

    if changed:
        db.add(current_user)
        db.commit()
        db.refresh(current_user)

    return {
        "ok": True,
        "user": {
            "id": current_user.id,
            "email": current_user.email,
            "username": current_user.username,
            "name": current_user.name,
            "gender": current_user.gender,
            "country": current_user.country,
            "description": current_user.description or "",
            "image_url": current_user.image_url or "",
            "is_subscribed": bool(current_user.is_subscribed),
            "last_active_at": current_user.last_active_at.isoformat() if current_user.last_active_at else None,
        },
    }


@router.post("/profile/image")
async def upload_profile_image(
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Upload profile image and store its served path in DB.

    Saves to: {UPLOAD_DIR}/profile/<user>_<ts>_<rand>.<ext>
    Served at: /static/profile/<...>
    """
    # Basic validation
    content_type = (file.content_type or "").lower().strip()
    allowed = {
        "image/jpeg": "jpg",
        "image/jpg": "jpg",
        "image/png": "png",
        "image/webp": "webp",
    }
    if content_type not in allowed:
        raise HTTPException(400, "Unsupported image type. Use JPG/PNG/WEBP.")

    # Limit upload size (best-effort; FastAPI also allows server-side limits)
    max_bytes = int(os.getenv("PROFILE_IMAGE_MAX_BYTES", str(5 * 1024 * 1024)))
    data = await file.read()
    if not data:
        raise HTTPException(400, "Empty file.")
    if len(data) > max_bytes:
        raise HTTPException(400, f"Image too large (max {max_bytes} bytes).")

    upload_root = Path(os.getenv("UPLOAD_DIR", "uploads"))
    profile_dir = upload_root / "profile"
    try:
        profile_dir.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass

    ext = allowed[content_type]
    nonce = secrets.token_hex(4)
    ts = int(time.time())
    fname = f"user_{current_user.id}_{ts}_{nonce}.{ext}"
    dst = profile_dir / fname

    try:
        dst.write_bytes(data)
    except Exception:
        raise HTTPException(500, "Failed to save uploaded image.")

    # Store served path
    current_user.image_url = f"/static/profile/{fname}"
    db.add(current_user)
    db.commit()
    db.refresh(current_user)

    return {
        "ok": True,
        "user": {
            "id": current_user.id,
            "email": current_user.email,
            "username": current_user.username,
            "name": current_user.name,
            "gender": current_user.gender,
            "country": current_user.country,
            "description": current_user.description or "",
            "image_url": current_user.image_url or "",
            "is_subscribed": bool(current_user.is_subscribed),
            "last_active_at": current_user.last_active_at.isoformat() if current_user.last_active_at else None,
        },
    }
