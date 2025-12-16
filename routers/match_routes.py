from __future__ import annotations

from datetime import datetime, timedelta
import os
import random
from typing import Optional

from sqlalchemy import or_, func
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from database import get_db
from models import ChatMessage, ChatSession, Swipe, User
from routers.auth import get_current_user


router = APIRouter(tags=["match"])


class SwipeIn(BaseModel):
    target_user_id: int
    direction: str  # "left" | "right"


class StartSessionIn(BaseModel):
    target_user_id: int
    mode: str  # "text" | "voice" | "video"


class VideoMatchIn(BaseModel):
    preference: str = "both"  # male|female|both


class MessageIn(BaseModel):
    session_id: int
    message: str


def _norm_gender(value: str) -> str:
    return (value or "").strip().lower()


def _is_opposite_or_cross(*, me_gender: str, other_gender: str) -> bool:
    me = _norm_gender(me_gender)
    other = _norm_gender(other_gender)
    if other == "cross":
        return True
    if me in {"male", "female"} and other in {"male", "female"}:
        return me != other
    return False


def _is_online(u: User, *, window_seconds: int = 120) -> bool:
    if not getattr(u, "last_active_at", None):
        return False
    try:
        return (datetime.utcnow() - u.last_active_at).total_seconds() <= window_seconds
    except Exception:
        return False


@router.get("/profiles/next")
def get_next_profile(
    preference: str = "both",
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """
    Returns a single profile card for swipe UI.
    Filters by preference ('male'|'female'|'both'), excludes self, and excludes already swiped.
    """
    pref = _norm_gender(preference)
    if pref not in {"male", "female", "both"}:
        raise HTTPException(400, "Invalid preference.")

    swiped_ids = [r[0] for r in db.query(Swipe.target_user_id).filter(Swipe.user_id == user.id).all()]

    q = db.query(User).filter(User.id != user.id)
    
    # Task 3: "validity" of 10 hours for online status
    # We interpret this as: only show users created (or active) in the last 10 hours.
    # since = datetime.utcnow() - timedelta(hours=10)
    # q = q.filter(User.created_at >= since)

    if swiped_ids:
        q = q.filter(~User.id.in_(swiped_ids))
    if pref != "both":
        q = q.filter(User.gender == pref)

    # Prefer unswiped first; if exhausted, loop by falling back to already-swiped users (randomly).
    candidate: Optional[User] = q.order_by(User.created_at.desc()).first()
    if not candidate:
        q2 = db.query(User).filter(User.id != user.id)
        if pref != "both":
            q2 = q2.filter(User.gender == pref)
        candidate = q2.order_by(func.random()).first()
    if not candidate:
        return {"ok": True, "profile": None}

    return {
        "ok": True,
        "profile": {
            "id": candidate.id,
            "username": candidate.username or "",
            "name": candidate.name,
            "country": candidate.country,
            "gender": candidate.gender,
            "description": candidate.description or "",
            "image_url": candidate.image_url or "",
            "is_online": _is_online(candidate),
        },
    }


@router.post("/profiles/swipe")
def swipe_profile(
    payload: SwipeIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    direction = (payload.direction or "").lower().strip()
    if direction not in {"left", "right"}:
        raise HTTPException(400, "direction must be 'left' or 'right'")

    target = db.get(User, payload.target_user_id)
    if not target or target.id == user.id:
        raise HTTPException(404, "Target not found")

    rec = Swipe(user_id=user.id, target_user_id=target.id, direction=direction)
    db.add(rec)
    db.commit()
    return {"ok": True}


@router.post("/sessions/start")
def start_session(
    payload: StartSessionIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    mode = (payload.mode or "").strip().lower()
    if mode not in {"text", "voice", "video"}:
        raise HTTPException(400, "mode must be 'text', 'voice', or 'video'")

    other = db.get(User, payload.target_user_id)
    if not other or other.id == user.id:
        raise HTTPException(404, "Target not found")

    # Chat is subscription-only (text/voice). Video is handled separately via /video/match.
    if mode in {"text", "voice"} and not user.is_subscribed:
        raise HTTPException(403, "Subscription required for chat.")

    session = ChatSession(mode=mode, user_a_id=user.id, user_b_id=other.id)
    db.add(session)
    db.commit()
    db.refresh(session)

    return {
        "ok": True,
        "session": {
            "id": session.id,
            "mode": session.mode,
            "user_a_id": session.user_a_id,
            "user_b_id": session.user_b_id,
            "created_at": session.created_at.isoformat(),
        },
    }


@router.post("/video/match")
def video_match(
    payload: VideoMatchIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """
    Random video matchmaking.
    - If subscribed: respects preference (male|female|both)
    - If not subscribed: connects randomly, with bias rules for opposite gender:
        - first 3 matches: opposite gender (when possible)
        - afterwards: opposite gender is rare
    Returns a ChatSession(mode='video') plus Agora App ID from env.
    """
    pref = _norm_gender(payload.preference)
    if pref not in {"male", "female", "both"}:
        pref = "both"

    me = _norm_gender(user.gender)
    desired_gender: Optional[str] = None
    
    # Duration logic: first 3 matches 40s, then 5s
    # usage count is roughly free_video_total_count
    match_count = user.free_video_total_count or 0
    duration_seconds = 40 if match_count < 3 else 5

    if user.is_subscribed:
        if pref in {"male", "female"}:
            desired_gender = pref
        else:
            desired_gender = None
    else:
        # Free users: ignore requested preference (connect both genders randomly)
        if me in {"male", "female"}:
            opposite = "female" if me == "male" else "male"
            if (user.free_video_opposite_count or 0) < 3:
                desired_gender = opposite
                user.free_video_opposite_count = int((user.free_video_opposite_count or 0) + 1)
            else:
                # "Very rare" opposite gender after first 3
                desired_gender = opposite if random.random() < 0.10 else me
            user.free_video_total_count = int((user.free_video_total_count or 0) + 1)
        else:
            # 'cross' or unknown: just random
            desired_gender = None
            user.free_video_total_count = int((user.free_video_total_count or 0) + 1)

        db.add(user)
        db.commit()

    q = db.query(User).filter(User.id != user.id)
    if desired_gender:
        q = q.filter(User.gender == desired_gender)
    other = q.order_by(func.random()).first()
    if not other:
        other = db.query(User).filter(User.id != user.id).order_by(func.random()).first()
    if not other:
        raise HTTPException(404, "No users available for video match.")

    session = ChatSession(mode="video", user_a_id=user.id, user_b_id=other.id)
    db.add(session)
    db.commit()
    db.refresh(session)

    agora_app_id = os.getenv("AGORA_ID") or os.getenv("AGORA_APP_ID") or ""
    channel = f"video_{session.id}"

    return {
        "ok": True,
        "agora_app_id": agora_app_id,
        "channel": channel,
        "duration_seconds": duration_seconds,
        "session": {
            "id": session.id,
            "mode": session.mode,
            "user_a_id": session.user_a_id,
            "user_b_id": session.user_b_id,
            "created_at": session.created_at.isoformat(),
        },
        "match": {
            "id": other.id,
            "username": other.username or "",
            "name": other.name,
            "country": other.country,
            "gender": other.gender,
            "description": other.description or "",
            "image_url": other.image_url or "",
            "is_online": _is_online(other),
        },
    }


@router.get("/sessions/history")
def get_chat_history(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """
    Returns a list of unique users the current user has chatted with,
    ordered by most recent session.
    """
    sessions = (
        db.query(ChatSession)
        .filter(or_(ChatSession.user_a_id == user.id, ChatSession.user_b_id == user.id))
        .order_by(ChatSession.created_at.desc())
        .all()
    )

    history_map = {}
    for s in sessions:
        other_id = s.user_b_id if s.user_a_id == user.id else s.user_a_id
        if other_id not in history_map:
            # Determine "other" user object
            other = s.user_b if s.user_a_id == user.id else s.user_a
            if other:
                history_map[other_id] = {
                    "user_id": other.id,
                    "name": other.name,
                    "image_url": other.image_url,
                    "last_seen": s.created_at.isoformat(),
                    "session_id": s.id,
                    "mode": s.mode,
                }
    
    return {
        "ok": True,
        "history": list(history_map.values())
    }


@router.post("/messages")
def post_message(
    payload: MessageIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    session = db.get(ChatSession, payload.session_id)
    if not session:
        raise HTTPException(404, "Session not found")
    if user.id not in {session.user_a_id, session.user_b_id}:
        raise HTTPException(403, "Not a participant")

    # Chat is subscription-only.
    if session.mode in {"text", "voice"} and not user.is_subscribed:
        raise HTTPException(403, "Subscription required to send message.")
    
    if not payload.message or not payload.message.strip():
        raise HTTPException(400, "Message required")

    rec = ChatMessage(session_id=session.id, sender_id=user.id, message=payload.message.strip())
    db.add(rec)
    db.commit()
    return {"ok": True}


@router.get("/messages")
def get_messages(
    session_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    session = db.get(ChatSession, session_id)
    if not session:
        raise HTTPException(404, "Session not found")
    if user.id not in {session.user_a_id, session.user_b_id}:
        raise HTTPException(403, "Not a participant")

    # Chat is subscription-only.
    if session.mode in {"text", "voice"} and not user.is_subscribed:
        raise HTTPException(403, "Subscription required to view messages.")

    msgs = (
        db.query(ChatMessage)
        .filter(ChatMessage.session_id == session.id)
        .order_by(ChatMessage.created_at.asc())
        .all()
    )
    return {
        "ok": True,
        "messages": [
            {
                "id": m.id,
                "sender_id": m.sender_id,
                "message": m.message,
                "created_at": m.created_at.isoformat(),
            }
            for m in msgs
        ],
    }


@router.post("/subscription/demo-activate")
def demo_activate_subscription(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """
    Demo-only endpoint: marks user as subscribed.
    Replace with Google Play subscription verification later.
    """
    user.is_subscribed = True
    db.add(user)
    db.commit()
    return {"ok": True, "is_subscribed": True}


@router.delete("/cleanup-chats")
def cleanup_old_chats(db: Session = Depends(get_db)):
    expiry = datetime.utcnow() - timedelta(hours=48)
    deleted = db.query(ChatMessage).filter(ChatMessage.created_at < expiry).delete()
    db.commit()
    return {"ok": True, "deleted": int(deleted or 0)}
