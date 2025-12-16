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
            "is_on_call": bool(candidate.is_on_call),
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
    - Paid users: respects preference (male|female|both).
    - Free users: forced to SAME gender.
    Returns a ChatSession(mode='video') plus Agora App ID from env.
    """
    pref = _norm_gender(payload.preference)
    if pref not in {"male", "female", "both"}:
        pref = "both"

    me = _norm_gender(user.gender)
    desired_gender: Optional[str] = None
    
    # Mark user as on call
    user.is_on_call = True
    db.add(user)
    db.commit()

    if user.is_subscribed:
        # Paid: respect preference
        if pref in {"male", "female"}:
            desired_gender = pref
        else:
            desired_gender = None
    else:
        # Free: same gender only
        if me in {"male", "female"}:
            desired_gender = me
        else:
            # cross/unknown -> random
            desired_gender = None

    q = db.query(User).filter(User.id != user.id, User.is_on_call == False)
    if desired_gender:
        q = q.filter(User.gender == desired_gender)
    
    other = q.order_by(func.random()).first()
    
    # Fallback if no one found with desired gender (only for paid users seeking specific gender, or free users if none same gender)
    if not other and user.is_subscribed and desired_gender:
         # Try finding ANY valid user if preference not met? Or just fail?
         # Usually better to fail or wait, but for MVP we might fallback to random.
         # For now, let's keep it strict.
         pass
         
    if not other:
         # Try wider search if allowed? 
         # For free users, must be same gender. If none, fail.
         if not user.is_subscribed and desired_gender:
              pass # Fail
         elif not user.is_subscribed and not desired_gender:
              # Fallback to anyone not on call
              other = db.query(User).filter(User.id != user.id, User.is_on_call == False).order_by(func.random()).first()

    if not other:
        # cleanup own status if failed
        user.is_on_call = False
        db.add(user)
        db.commit()
        raise HTTPException(404, "No available users found.")
    
    # Mark other as on call
    other.is_on_call = True
    db.add(other)

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
        "duration_seconds": 60, # Standard duration
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
            "is_on_call": True,
        },
    }


@router.post("/video/end")
def end_video_call(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    user.is_on_call = False
    db.add(user)
    db.commit()
    return {"ok": True}


@router.get("/sessions/history")
def get_chat_history(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """
    Returns a list of unique users the current user has chatted with.
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
            other = s.user_b if s.user_a_id == user.id else s.user_a
            if other:
                history_map[other_id] = {
                    "user_id": other.id,
                    "name": other.name,
                    "image_url": other.image_url,
                    "last_seen": s.created_at.isoformat(),
                    "session_id": s.id,
                    "mode": s.mode,
                    "is_on_call": bool(other.is_on_call),
                    "is_online": _is_online(other),
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
