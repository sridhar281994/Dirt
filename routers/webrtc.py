from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import and_
from sqlalchemy.orm import Session

from database import get_db
from models import ChatSession, User, WebRTCSignal
from routers.auth import get_current_user


router = APIRouter(tags=["webrtc"])


class _SignalIn(BaseModel):
    session_id: int
    payload: Dict[str, Any]


class IceIn(BaseModel):
    session_id: int
    candidate: Dict[str, Any]


def _assert_video_participant(*, db: Session, session_id: int, user: User) -> ChatSession:
    sess = db.query(ChatSession).filter(ChatSession.id == session_id, ChatSession.mode == "video").first()
    if not sess:
        raise HTTPException(404, "Session not found")
    if user.id not in {sess.user_a_id, sess.user_b_id}:
        raise HTTPException(403, "Not a participant")
    return sess


def _cleanup_old_signals(*, db: Session, session_id: int) -> None:
    """
    Best-effort retention: drop very old signaling messages.
    """
    cutoff = datetime.utcnow() - timedelta(minutes=30)
    try:
        db.query(WebRTCSignal).filter(WebRTCSignal.session_id == session_id, WebRTCSignal.created_at < cutoff).delete()
    except Exception:
        pass


def _upsert_latest(*, db: Session, session_id: int, sender_id: int, kind: str, payload: Dict[str, Any]) -> int:
    # Keep only the latest offer/answer per sender to avoid unbounded growth.
    try:
        db.query(WebRTCSignal).filter(
            WebRTCSignal.session_id == session_id,
            WebRTCSignal.sender_id == sender_id,
            WebRTCSignal.kind == kind,
        ).delete()
    except Exception:
        pass
    rec = WebRTCSignal(
        session_id=int(session_id),
        sender_id=int(sender_id),
        kind=str(kind),
        payload=json.dumps(payload or {}, separators=(",", ":"), ensure_ascii=False),
    )
    db.add(rec)
    db.commit()
    db.refresh(rec)
    return int(rec.id)


@router.post("/video/webrtc/offer")
def post_offer(payload: _SignalIn, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    sid = int(payload.session_id or 0)
    if sid <= 0:
        raise HTTPException(400, "session_id required")
    _assert_video_participant(db=db, session_id=sid, user=user)
    _cleanup_old_signals(db=db, session_id=sid)

    rec_id = _upsert_latest(db=db, session_id=sid, sender_id=user.id, kind="offer", payload=payload.payload)
    return {"ok": True, "id": rec_id}


@router.get("/video/webrtc/offer")
def get_offer(session_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    sid = int(session_id or 0)
    if sid <= 0:
        raise HTTPException(400, "session_id required")
    _assert_video_participant(db=db, session_id=sid, user=user)

    rec = (
        db.query(WebRTCSignal)
        .filter(
            WebRTCSignal.session_id == sid,
            WebRTCSignal.kind == "offer",
            WebRTCSignal.sender_id != user.id,
        )
        .order_by(WebRTCSignal.id.desc())
        .first()
    )
    if not rec:
        return {"ok": True, "offer": None}
    try:
        pl = json.loads(rec.payload or "{}")
    except Exception:
        pl = {}
    return {"ok": True, "offer": {"id": rec.id, "payload": pl}}


@router.post("/video/webrtc/answer")
def post_answer(payload: _SignalIn, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    sid = int(payload.session_id or 0)
    if sid <= 0:
        raise HTTPException(400, "session_id required")
    _assert_video_participant(db=db, session_id=sid, user=user)
    _cleanup_old_signals(db=db, session_id=sid)

    rec_id = _upsert_latest(db=db, session_id=sid, sender_id=user.id, kind="answer", payload=payload.payload)
    return {"ok": True, "id": rec_id}


@router.get("/video/webrtc/answer")
def get_answer(session_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    sid = int(session_id or 0)
    if sid <= 0:
        raise HTTPException(400, "session_id required")
    _assert_video_participant(db=db, session_id=sid, user=user)

    rec = (
        db.query(WebRTCSignal)
        .filter(
            WebRTCSignal.session_id == sid,
            WebRTCSignal.kind == "answer",
            WebRTCSignal.sender_id != user.id,
        )
        .order_by(WebRTCSignal.id.desc())
        .first()
    )
    if not rec:
        return {"ok": True, "answer": None}
    try:
        pl = json.loads(rec.payload or "{}")
    except Exception:
        pl = {}
    return {"ok": True, "answer": {"id": rec.id, "payload": pl}}


@router.post("/video/webrtc/ice")
def post_ice(payload: IceIn, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    sid = int(payload.session_id or 0)
    if sid <= 0:
        raise HTTPException(400, "session_id required")
    _assert_video_participant(db=db, session_id=sid, user=user)
    _cleanup_old_signals(db=db, session_id=sid)

    rec = WebRTCSignal(
        session_id=int(sid),
        sender_id=int(user.id),
        kind="ice",
        payload=json.dumps(payload.candidate or {}, separators=(",", ":"), ensure_ascii=False),
    )
    db.add(rec)
    db.commit()
    db.refresh(rec)
    return {"ok": True, "id": int(rec.id)}


@router.get("/video/webrtc/ice")
def get_ice(
    session_id: int,
    since_id: int = 0,
    limit: int = 50,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    sid = int(session_id or 0)
    if sid <= 0:
        raise HTTPException(400, "session_id required")
    _assert_video_participant(db=db, session_id=sid, user=user)
    try:
        since = int(since_id or 0)
    except Exception:
        since = 0
    try:
        limit = int(limit or 0)
    except Exception:
        limit = 50
    if limit <= 0:
        limit = 50
    if limit > 200:
        limit = 200

    rows = (
        db.query(WebRTCSignal)
        .filter(
            WebRTCSignal.session_id == sid,
            WebRTCSignal.kind == "ice",
            WebRTCSignal.sender_id != user.id,
            WebRTCSignal.id > since,
        )
        .order_by(WebRTCSignal.id.asc())
        .limit(limit)
        .all()
    )
    out: List[Dict[str, Any]] = []
    for r in rows:
        try:
            pl = json.loads(r.payload or "{}")
        except Exception:
            pl = {}
        out.append({"id": int(r.id), "payload": pl})
    return {"ok": True, "candidates": out}

