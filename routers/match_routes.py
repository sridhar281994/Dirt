from __future__ import annotations

from datetime import datetime, timedelta
import os
import random
import time
from typing import Optional

from sqlalchemy import or_, func
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from database import get_db
from models import ChatMessage, ChatSession, Swipe, User
from routers.auth import get_current_user
from utils.agora_rtc_token import build_rtc_token_from_env


router = APIRouter(tags=["match"])


class SwipeIn(BaseModel):
    target_user_id: int
    direction: str  # "left" | "right"


class StartSessionIn(BaseModel):
    target_user_id: int
    mode: str  # "text" | "voice" | "video"


class VideoMatchIn(BaseModel):
    preference: str = "both"  # male|female|both


class VideoEndIn(BaseModel):
    # Optional: if provided, clear BOTH participants' busy status.
    session_id: int | None = None


class VideoTokenOut(BaseModel):
    ok: bool = True
    agora_app_id: str
    channel: str
    agora_uid: int
    agora_token: str
    agora_token_expire_ts: int


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


def _video_reset_state(db: Session, u: User) -> None:
    """
    Best-effort: clear any video matchmaking state for a user.
    """
    u.is_on_call = False
    u.video_state = "idle"
    u.video_state_updated_at = datetime.utcnow()
    u.video_session_id = None
    u.video_partner_id = None
    db.add(u)


def _video_set_searching(db: Session, u: User) -> None:
    u.video_state = "searching"
    u.video_state_updated_at = datetime.utcnow()
    # searching is not "on call"
    u.is_on_call = False
    u.video_session_id = None
    u.video_partner_id = None
    db.add(u)


def _video_set_in_call(db: Session, *, u: User, session_id: int, partner_id: int) -> None:
    u.video_state = "in_call"
    u.video_state_updated_at = datetime.utcnow()
    u.is_on_call = True
    u.video_session_id = int(session_id)
    u.video_partner_id = int(partner_id)
    db.add(u)


def _video_build_payload(*, session: ChatSession, me: User, other: User) -> dict:
    agora_app_id = os.getenv("AGORA_ID") or os.getenv("AGORA_APP_ID") or ""
    channel = f"video_{session.id}"
    try:
        agora_uid = int(getattr(me, "id", 0) or 0)
    except Exception:
        agora_uid = 0

    # Token is required when Agora project has certificate enabled.
    # If credentials are missing, token will be "" and client may still join
    # only if the Agora project is configured for "App ID" (no token).
    try:
        ttl = int(os.getenv("AGORA_TOKEN_TTL_SECONDS") or 3600)
    except Exception:
        ttl = 3600
    token, expire_ts = build_rtc_token_from_env(channel_name=channel, uid=agora_uid, ttl_seconds=ttl)
    return {
        "ok": True,
        "agora_app_id": agora_app_id,
        "channel": channel,
        "agora_uid": agora_uid,
        "agora_token": token,
        "agora_token_expire_ts": int(expire_ts or (int(time.time()) + ttl)),
        "duration_seconds": 60,  # Standard duration
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
    IMPORTANT: Only matches users who are ALSO actively searching (video_state='searching').
    This prevents creating sessions against random "online" users who didn't request video.

    - Paid users: respects preference (male|female|both).
    - Free users: forced to SAME gender.
    - Loop: excludes users matched in the last hour.
    - Online: only matches users active in last 2 mins.
    """
    pref = _norm_gender(payload.preference)
    if pref not in {"male", "female", "both"}:
        pref = "both"

    # If this user was already matched (e.g. the OTHER side created the session),
    # return the assigned session so both devices get the same session_id/channel.
    try:
        current_sid = int(getattr(user, "video_session_id", None) or 0)
    except Exception:
        current_sid = 0
    if (getattr(user, "video_state", None) == "in_call") and current_sid > 0:
        sess = db.query(ChatSession).filter(ChatSession.id == current_sid, ChatSession.mode == "video").first()
        if sess:
            other_id = sess.user_b_id if sess.user_a_id == user.id else sess.user_a_id
            other = db.get(User, other_id)
            if other:
                return _video_build_payload(session=sess, me=user, other=other)
        # Stale state: reset and continue as searching.
        _video_reset_state(db, user)
        db.commit()

    # Mark user as actively searching (opt-in queue).
    _video_set_searching(db, user)
    db.commit()

    me = _norm_gender(user.gender)
    desired_gender: Optional[str] = None

    if user.is_subscribed:
        # Paid: respect preference
        if pref in {"male", "female"}:
            desired_gender = pref
    else:
        # Free: same gender only
        if me in {"male", "female"}:
            desired_gender = me
        # If me is cross/unknown, desired_gender remains None -> matches anyone

    # --- Loop Logic: Get recent partners ---
    one_hour_ago = datetime.utcnow() - timedelta(hours=1)
    recent_sessions = (
        db.query(ChatSession)
        .filter(
            ChatSession.created_at >= one_hour_ago,
            ChatSession.mode == "video",
            or_(ChatSession.user_a_id == user.id, ChatSession.user_b_id == user.id)
        )
        .all()
    )
    excluded_ids = set()
    for s in recent_sessions:
        excluded_ids.add(s.user_b_id if s.user_a_id == user.id else s.user_a_id)
    
    # --- Query ---
    online_threshold = datetime.utcnow() - timedelta(minutes=2)
    # Consider only users who are actively searching recently.
    searching_fresh = datetime.utcnow() - timedelta(seconds=35)
    
    def get_candidate(exclude_ids=None):
        q = db.query(User).filter(
            User.id != user.id,
            User.is_on_call == False,
            User.last_active_at >= online_threshold,
            User.video_state == "searching",
            User.video_state_updated_at >= searching_fresh,
        )
        if desired_gender:
            q = q.filter(User.gender == desired_gender)
        if exclude_ids:
            q = q.filter(~User.id.in_(exclude_ids))
        return q.order_by(func.random()).first()

    # 1. Try with exclusions (Loop)
    other = get_candidate(exclude_ids=excluded_ids)

    # 2. If not found and we had exclusions, Reset Loop (try without exclusions)
    if not other and excluded_ids:
        other = get_candidate(exclude_ids=None)

    # 3. Fallback for free users (if they have no gender set, they match anyone - handled by desired_gender=None)
    # If still no other, fail.

    if not other:
        # No candidate currently searching. Keep caller in searching state.
        # Return 200 so clients can poll/retry without treating it as an error.
        return {"ok": True, "match": None}
    
    session = ChatSession(mode="video", user_a_id=user.id, user_b_id=other.id)
    db.add(session)
    db.commit()
    db.refresh(session)

    # Transition BOTH users into "in_call" state so the other device can poll and receive the same session.
    _video_set_in_call(db, u=user, session_id=session.id, partner_id=other.id)
    _video_set_in_call(db, u=other, session_id=session.id, partner_id=user.id)
    db.commit()

    return _video_build_payload(session=session, me=user, other=other)


@router.get("/video/token", response_model=VideoTokenOut)
def video_token(
    session_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """
    Return a fresh Agora RTC token for an existing video session.

    Useful if the client needs to re-join the channel (app resumed, token expired, etc.).
    """
    sess = (
        db.query(ChatSession)
        .filter(
            ChatSession.id == session_id,
            ChatSession.mode == "video",
            or_(ChatSession.user_a_id == user.id, ChatSession.user_b_id == user.id),
        )
        .first()
    )
    if not sess:
        raise HTTPException(404, "Session not found")

    agora_app_id = os.getenv("AGORA_ID") or os.getenv("AGORA_APP_ID") or ""
    channel = f"video_{sess.id}"
    try:
        agora_uid = int(getattr(user, "id", 0) or 0)
    except Exception:
        agora_uid = 0
    try:
        ttl = int(os.getenv("AGORA_TOKEN_TTL_SECONDS") or 3600)
    except Exception:
        ttl = 3600
    token, expire_ts = build_rtc_token_from_env(channel_name=channel, uid=agora_uid, ttl_seconds=ttl)
    return {
        "ok": True,
        "agora_app_id": agora_app_id,
        "channel": channel,
        "agora_uid": agora_uid,
        "agora_token": token,
        "agora_token_expire_ts": int(expire_ts or (int(time.time()) + ttl)),
    }


@router.post("/video/end")
def end_video_call(
    payload: VideoEndIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    # If the client knows the session_id, clear BOTH users' video state.
    # This is safe because matching now requires explicit opt-in (video_state='searching'),
    # so an ended user won't be re-matched unless they actively search again.
    try:
        sid = int(payload.session_id or 0)
    except Exception:
        sid = 0

    if sid > 0:
        sess = (
            db.query(ChatSession)
            .filter(
                ChatSession.id == sid,
                ChatSession.mode == "video",
                or_(ChatSession.user_a_id == user.id, ChatSession.user_b_id == user.id),
            )
            .first()
        )
        if sess:
            # Mark session as ended (best-effort; does not change chat message history).
            try:
                sess.ended_at = datetime.utcnow()
                sess.ended_by_id = user.id
                db.add(sess)
            except Exception:
                pass

            for uid in (sess.user_a_id, sess.user_b_id):
                u = db.query(User).filter(User.id == uid).first()
                if u:
                    _video_reset_state(db, u)
            db.commit()
            return {"ok": True}

    # Fallback (backwards compatible): clear only the current user.
    _video_reset_state(db, user)
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
                # Lightweight "last message" summary for unread indicators on the client.
                last_message_id = 0
                last_message_sender_id = 0
                last_message_text = ""
                last_message_at = None
                try:
                    last_msg = (
                        db.query(ChatMessage)
                        .filter(ChatMessage.session_id == s.id)
                        .order_by(ChatMessage.id.desc())
                        .first()
                    )
                    if last_msg:
                        last_message_id = int(last_msg.id or 0)
                        last_message_sender_id = int(last_msg.sender_id or 0)
                        last_message_text = str(last_msg.message or "")
                        try:
                            last_message_at = last_msg.created_at.isoformat() if last_msg.created_at else None
                        except Exception:
                            last_message_at = None
                except Exception:
                    # Best-effort only; never block history.
                    pass

                history_map[other_id] = {
                    "user_id": other.id,
                    "name": other.name,
                    "image_url": other.image_url,
                    "last_seen": s.created_at.isoformat(),
                    "session_id": s.id,
                    "mode": s.mode,
                    "is_on_call": bool(other.is_on_call),
                    "is_online": _is_online(other),
                    "last_message_id": last_message_id,
                    "last_message_sender_id": last_message_sender_id,
                    "last_message_text": last_message_text,
                    "last_message_at": last_message_at,
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
