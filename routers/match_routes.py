from __future__ import annotations

from datetime import datetime, timedelta
import json
import os
import random
import time
from typing import Optional

from sqlalchemy import or_, func
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from database import get_db
from models import ChatMessage, ChatSession, Swipe, User
from routers.auth import get_current_user
from models import WebRTCSignal
from utils.redis_client import get_async_redis
from utils.turn_credentials import build_ice_servers


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


def _video_build_payload(*, session: ChatSession, me: User, other: User, duration_seconds: int = 60) -> dict:
    try:
        duration_seconds = int(duration_seconds or 0)
    except Exception:
        duration_seconds = 60
    if duration_seconds <= 0:
        duration_seconds = 60

    # WebRTC config:
    # - STUN list is kept for backwards compatibility with older Android clients.
    # - New clients should prefer `ice_servers` (STUN + optional TURN REST creds).
    stun_urls = [
        "stun:stun.l.google.com:19302",
        "stun:stun1.l.google.com:19302",
    ]
    ice_servers = build_ice_servers(user_id=int(getattr(me, "id", 0) or 0))

    return {
        "ok": True,
        # Backwards compatible keys (kept so older clients don't crash). New clients ignore these.
        "agora_app_id": "",
        "channel": "",
        "agora_uid": int(getattr(me, "id", 0) or 0),
        "agora_token": "",
        "agora_token_expire_ts": 0,
        # New: WebRTC signaling session.
        "webrtc": {
            "session_id": int(session.id),
            "stun_urls": stun_urls,
            "ice_servers": ice_servers,
            # Offerer is user_a by convention (deterministic, no race).
            "role": "offerer" if int(session.user_a_id) == int(me.id) else "answerer",
        },
        "duration_seconds": int(duration_seconds),
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


def _ws_url_from_request(request: Request) -> str:
    """
    Build websocket base URL for clients.
    Returned value does NOT include the token query param.
    """
    try:
        base = str(request.base_url).rstrip("/")
    except Exception:
        base = ""
    if base.startswith("https://"):
        base = "wss://" + base[len("https://") :]
    elif base.startswith("http://"):
        base = "ws://" + base[len("http://") :]
    if not base:
        # Fallback for local/dev use.
        base = "ws://localhost:8000"
    return f"{base}/api/ws"


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
async def video_match(
    payload: VideoMatchIn,
    request: Request,
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

    ws_url = _ws_url_from_request(request)

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
                out = _video_build_payload(session=sess, me=user, other=other)
                try:
                    out.setdefault("webrtc", {})
                    out["webrtc"]["ws_url"] = ws_url
                except Exception:
                    pass
                return out
        # Stale state: reset and continue as searching.
        _video_reset_state(db, user)
        db.commit()

    # Mark user as actively searching (best-effort; throttled).
    try:
        should_write = (getattr(user, "video_state", None) != "searching") or not getattr(user, "video_state_updated_at", None)
        if not should_write and user.video_state_updated_at:
            should_write = (datetime.utcnow() - user.video_state_updated_at).total_seconds() >= 30
        if should_write:
            _video_set_searching(db, user)
            db.commit()
    except Exception:
        db.rollback()

    me = _norm_gender(user.gender)
    desired_gender: Optional[str] = None

    # For free users:
    # - Allow EXACTLY ONE opposite-gender match as a 40s trial (first time only).
    # - After that, restrict to same gender (legacy behavior).
    wants_opposite_trial = False
    try:
        free_opposite_used = int(getattr(user, "free_video_opposite_count", 0) or 0)
    except Exception:
        free_opposite_used = 0

    if not bool(user.is_subscribed) and me in {"male", "female"} and free_opposite_used <= 0:
        wants_opposite_trial = True
        desired_gender = "female" if me == "male" else "male"
    elif user.is_subscribed:
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
    
    # Prefer Redis matchmaking queue for scale (no random DB scans).
    r = get_async_redis()
    if r is not None:
        try:
            # Queue by "desired partner gender" (what the user wants to receive).
            desired_bucket = (desired_gender or "any").strip().lower()
            if desired_bucket not in {"male", "female", "any"}:
                desired_bucket = "any"

            my_id = int(user.id)
            my_gender = me or "any"
            if my_gender not in {"male", "female"}:
                my_gender = "any"

            # Users who can accept *me* are waiting in:
            # - q:<my_gender> (they want my gender)
            # - q:any         (they'll accept anyone)
            candidate_queues = []
            if my_gender in {"male", "female"}:
                candidate_queues.append(f"mm:video:q:{my_gender}")
            candidate_queues.append("mm:video:q:any")

            my_queue = f"mm:video:q:{desired_bucket}"
            meta_key = f"mm:video:meta:{my_id}"
            now = int(time.time())

            # Store lightweight matching metadata (TTL means stale users drop out naturally).
            await r.hset(
                meta_key,
                mapping={
                    "gender": my_gender,
                    "desired": desired_bucket,
                    "queue": my_queue,
                    "is_sub": "1" if bool(user.is_subscribed) else "0",
                    "free_opp_used": str(int(getattr(user, "free_video_opposite_count", 0) or 0)),
                    "wants_opp_trial": "1" if bool(wants_opposite_trial) else "0",
                    "ts": str(now),
                },
            )
            await r.expire(meta_key, 120)
            await r.zadd(my_queue, {str(my_id): float(now)})
            await r.expire(my_queue, 180)

            async def _try_match_from_queue(qkey: str) -> Optional[int]:
                # Clean very old entries (best-effort).
                try:
                    await r.zremrangebyscore(qkey, 0, float(now - 180))
                except Exception:
                    pass
                ids = await r.zrange(qkey, 0, 30)
                for cid_raw in ids:
                    try:
                        cid = int(cid_raw)
                    except Exception:
                        continue
                    if cid <= 0 or cid == my_id:
                        continue
                    cmeta_key = f"mm:video:meta:{cid}"
                    cmeta = await r.hgetall(cmeta_key)
                    if not cmeta:
                        # Stale entry; remove from queue.
                        try:
                            await r.zrem(qkey, str(cid))
                        except Exception:
                            pass
                        continue

                    c_gender = (cmeta.get("gender") or "any").strip().lower()
                    c_desired = (cmeta.get("desired") or "any").strip().lower()
                    c_queue = (cmeta.get("queue") or f"mm:video:q:{c_desired}").strip()
                    c_is_sub = (cmeta.get("is_sub") or "0") == "1"
                    try:
                        c_free_opp_used = int(cmeta.get("free_opp_used") or 0)
                    except Exception:
                        c_free_opp_used = 0

                    # Two-way compatibility:
                    # - candidate's gender must match what I want (or I accept any)
                    if desired_bucket in {"male", "female"} and c_gender != desired_bucket:
                        continue
                    # - candidate must accept my gender (or accept any)
                    if my_gender in {"male", "female"} and c_desired not in {my_gender, "any"}:
                        continue

                    # Free-user opposite-gender trial constraint (best-effort).
                    if wants_opposite_trial and (not c_is_sub) and c_free_opp_used > 0:
                        continue

                    # Acquire a short lock so only one node matches this pair.
                    a, b = (my_id, cid) if my_id < cid else (cid, my_id)
                    lock_key = f"mm:video:pairlock:{a}:{b}"
                    got = await r.set(lock_key, "1", nx=True, ex=10)
                    if not got:
                        continue

                    # Remove both from their queues (best-effort).
                    rem_me = await r.zrem(my_queue, str(my_id))
                    rem_c = await r.zrem(c_queue, str(cid))
                    if (rem_me or 0) <= 0 or (rem_c or 0) <= 0:
                        # Race: put ourselves back, let lock expire.
                        try:
                            await r.zadd(my_queue, {str(my_id): float(now)})
                        except Exception:
                            pass
                        continue

                    return cid
                return None

            # Prefer loop-safe queues by skipping recently seen partners (best-effort).
            # We still do the final "loop" constraint in DB after we pick a candidate.
            other_id: Optional[int] = None
            for qkey in candidate_queues:
                other_id = await _try_match_from_queue(qkey)
                if other_id:
                    break

            if not other_id:
                return {"ok": True, "match": None, "webrtc": {"ws_url": ws_url}}

            other = db.get(User, int(other_id))
            if not other or other.id == user.id:
                # Candidate vanished; let caller retry.
                return {"ok": True, "match": None, "webrtc": {"ws_url": ws_url}}

            # Create session in DB (durable record).
            session = ChatSession(mode="video", user_a_id=user.id, user_b_id=other.id)
            db.add(session)
            db.commit()
            db.refresh(session)

            # Mark both in_call (legacy client behavior relies on this).
            _video_set_in_call(db, u=user, session_id=session.id, partner_id=other.id)
            _video_set_in_call(db, u=other, session_id=session.id, partner_id=user.id)
            db.commit()

            # Cache call membership for websocket path.
            try:
                await r.set(
                    f"webrtc:call:{int(session.id)}",
                    json.dumps({"a": int(user.id), "b": int(other.id)}, separators=(",", ":")),
                    ex=2 * 60 * 60,
                )
            except Exception:
                pass

            out = _video_build_payload(session=session, me=user, other=other, duration_seconds=60)
            # Add ws_url for websocket signaling clients.
            try:
                out.setdefault("webrtc", {})
                out["webrtc"]["ws_url"] = ws_url
            except Exception:
                pass

            # Notify both peers over websocket channel (best-effort).
            try:
                evt = json.dumps(
                    {
                        "type": "matched",
                        "call_id": int(session.id),
                        "role_a": "offerer",
                        "a": int(session.user_a_id),
                        "b": int(session.user_b_id),
                        "ts": datetime.utcnow().isoformat(),
                    },
                    separators=(",", ":"),
                )
                await r.publish(f"ws:{int(user.id)}", evt)
                await r.publish(f"ws:{int(other.id)}", evt)
            except Exception:
                pass

            return out
        except Exception:
            # If Redis matchmaking fails for any reason, fall back to DB matcher below.
            pass

    # --- DB fallback (legacy; not scalable) ---
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
        # If we're attempting the "first opposite-gender trial" for a free user,
        # ensure we do not match another free user who already used their opposite trial.
        # (Subscribed users are always eligible.)
        if wants_opposite_trial:
            q = q.filter(or_(User.is_subscribed == True, User.free_video_opposite_count <= 0))
        if exclude_ids:
            q = q.filter(~User.id.in_(exclude_ids))
        return q.order_by(func.random()).first()

    # 1. Try with exclusions (Loop)
    other = get_candidate(exclude_ids=excluded_ids)

    # 2. If not found and we had exclusions, Reset Loop (try without exclusions)
    if not other and excluded_ids:
        other = get_candidate(exclude_ids=None)

    if not other:
        # No candidate currently searching. Keep caller in searching state.
        # Return 200 so clients can poll/retry without treating it as an error.
        return {"ok": True, "match": None, "webrtc": {"ws_url": ws_url}}
    
    session = ChatSession(mode="video", user_a_id=user.id, user_b_id=other.id)
    db.add(session)
    db.commit()
    db.refresh(session)

    # Transition BOTH users into "in_call" state so the other device can poll and receive the same session.
    _video_set_in_call(db, u=user, session_id=session.id, partner_id=other.id)
    _video_set_in_call(db, u=other, session_id=session.id, partner_id=user.id)

    # Update free-user counters (best-effort).
    is_opposite = _is_opposite_or_cross(me_gender=user.gender, other_gender=other.gender)
    # Duration rule:
    # - Opposite-gender trial: 40s, but only for the *first* opposite match of any free participant.
    # - Otherwise: standard 60s.
    duration_seconds = 60
    if bool(is_opposite):
        try:
            u_used = int(getattr(user, "free_video_opposite_count", 0) or 0)
        except Exception:
            u_used = 0
        try:
            o_used = int(getattr(other, "free_video_opposite_count", 0) or 0)
        except Exception:
            o_used = 0
        if (not bool(user.is_subscribed) and u_used <= 0) or (not bool(other.is_subscribed) and o_used <= 0):
            duration_seconds = 40

    for a, b in ((user, other), (other, user)):
        try:
            if not bool(getattr(a, "is_subscribed", False)):
                a.free_video_total_count = int(getattr(a, "free_video_total_count", 0) or 0) + 1
                if _is_opposite_or_cross(me_gender=getattr(a, "gender", ""), other_gender=getattr(b, "gender", "")):
                    a.free_video_opposite_count = int(getattr(a, "free_video_opposite_count", 0) or 0) + 1
                db.add(a)
        except Exception:
            # Never block matching due to counter updates.
            pass
    db.commit()

    out = _video_build_payload(session=session, me=user, other=other, duration_seconds=duration_seconds)
    try:
        out.setdefault("webrtc", {})
        out["webrtc"]["ws_url"] = ws_url
    except Exception:
        pass
    return out


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
    # Deprecated: kept for backwards compatibility with older clients.
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
    return {
        "ok": True,
        "agora_app_id": "",
        "channel": "",
        "agora_uid": int(getattr(user, "id", 0) or 0),
        "agora_token": "",
        "agora_token_expire_ts": 0,
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
            # Cleanup WebRTC signaling messages for this session.
            try:
                db.query(WebRTCSignal).filter(WebRTCSignal.session_id == sid).delete()
            except Exception:
                pass
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
