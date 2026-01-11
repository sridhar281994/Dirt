from __future__ import annotations

import asyncio
import json
from datetime import datetime
from typing import Any, Dict, Optional, Tuple

from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect
from jose import jwt
from sqlalchemy.orm import Session

from database import get_db
from models import ChatSession, User
from routers.auth import JWT_ALG, JWT_SECRET
from utils.redis_client import get_async_redis


router = APIRouter(tags=["webrtc-ws"])


def _extract_token(ws: WebSocket) -> Optional[str]:
    # Prefer query param for mobile clients.
    tok = (ws.query_params.get("token") or "").strip()
    if tok:
        return tok
    auth = (ws.headers.get("authorization") or "").strip()
    if not auth:
        return None
    if auth.lower().startswith("bearer "):
        return auth.split(" ", 1)[1].strip() or None
    return auth or None


def _ws_auth_user(*, ws: WebSocket, db: Session) -> User:
    tok = _extract_token(ws)
    if not tok:
        raise HTTPException(401, "Missing token")
    try:
        payload = jwt.decode(tok, JWT_SECRET, algorithms=[JWT_ALG])
    except Exception:
        raise HTTPException(401, "Invalid token")
    sub = payload.get("sub")
    if not sub:
        raise HTTPException(401, "Invalid token")
    user = db.get(User, int(sub))
    if not user:
        raise HTTPException(401, "User not found")
    return user


def _call_participants_from_db(*, db: Session, call_id: int) -> Tuple[int, int]:
    sess = db.query(ChatSession).filter(ChatSession.id == int(call_id), ChatSession.mode == "video").first()
    if not sess:
        raise HTTPException(404, "Call not found")
    return int(sess.user_a_id), int(sess.user_b_id)


async def _call_participants(*, db: Session, call_id: int) -> Tuple[int, int]:
    """
    Prefer Redis cache (for scale), fallback to DB.
    Cache key: webrtc:call:<id> -> JSON {"a":<id>,"b":<id>}
    """
    r = get_async_redis()
    key = f"webrtc:call:{int(call_id)}"
    if r is not None:
        try:
            raw = await r.get(key)
            if raw:
                obj = json.loads(raw)
                a = int(obj.get("a") or 0)
                b = int(obj.get("b") or 0)
                if a > 0 and b > 0:
                    return a, b
        except Exception:
            pass

    a, b = _call_participants_from_db(db=db, call_id=int(call_id))
    if r is not None:
        try:
            # Keep for 2 hours; enough for typical call/session lifetimes.
            await r.set(key, json.dumps({"a": a, "b": b}, separators=(",", ":")), ex=2 * 60 * 60)
        except Exception:
            pass
    return a, b


@router.websocket("/ws")
async def ws_signaling(ws: WebSocket, db: Session = Depends(get_db)):
    """
    WebRTC signaling over WebSocket (Redis fanout).

    Client connects:
      wss://<host>/api/ws?token=<JWT>

    Client sends JSON messages:
      {"type":"signal","call_id":123,"kind":"offer"|"answer"|"ice"|"bye","payload":{...}}
    Server relays to the other peer (validated as a participant).
    """

    # Authenticate before accept (so we can close with proper code).
    try:
        user = _ws_auth_user(ws=ws, db=db)
    except HTTPException as exc:
        await ws.close(code=4401)
        return

    await ws.accept()

    r = get_async_redis()
    pubsub = None
    sub_task: asyncio.Task | None = None
    channel = f"ws:{int(user.id)}"

    async def _sub_loop() -> None:
        assert pubsub is not None
        try:
            async for msg in pubsub.listen():
                if not msg:
                    continue
                if msg.get("type") != "message":
                    continue
                data = msg.get("data")
                if data is None:
                    continue
                try:
                    await ws.send_text(str(data))
                except Exception:
                    return
        except Exception:
            return

    # Subscribe to per-user channel so other API nodes can deliver signaling to this socket.
    if r is not None:
        try:
            pubsub = r.pubsub()
            await pubsub.subscribe(channel)
            sub_task = asyncio.create_task(_sub_loop())
        except Exception:
            pubsub = None
            sub_task = None

    # Tell client it’s connected.
    try:
        await ws.send_json(
            {
                "type": "hello",
                "user_id": int(user.id),
                "ts": datetime.utcnow().isoformat(),
                "ws": True,
            }
        )
    except Exception:
        await ws.close()
        return

    try:
        while True:
            raw = await ws.receive_text()
            try:
                msg = json.loads(raw or "{}")
            except Exception:
                continue

            mtype = str(msg.get("type") or "").strip().lower()
            if mtype in {"ping"}:
                await ws.send_json({"type": "pong", "ts": datetime.utcnow().isoformat()})
                continue

            if mtype != "signal":
                # Ignore unknown messages to keep protocol forward-compatible.
                continue

            try:
                call_id = int(msg.get("call_id") or 0)
            except Exception:
                call_id = 0
            if call_id <= 0:
                continue

            kind = str(msg.get("kind") or "").strip().lower()
            if kind not in {"offer", "answer", "ice", "bye"}:
                continue

            payload = msg.get("payload")
            if payload is None:
                payload = {}

            a, b = await _call_participants(db=db, call_id=call_id)
            if int(user.id) not in {a, b}:
                # Don’t leak call existence or allow cross-call relay.
                continue
            other_id = b if int(user.id) == a else a

            out = {
                "type": "signal",
                "call_id": int(call_id),
                "kind": kind,
                "from_user_id": int(user.id),
                "payload": payload,
                "ts": datetime.utcnow().isoformat(),
            }
            out_raw = json.dumps(out, separators=(",", ":"), ensure_ascii=False)

            if r is not None:
                try:
                    await r.publish(f"ws:{int(other_id)}", out_raw)
                except Exception:
                    pass
            # Best-effort ack to the sender.
            try:
                await ws.send_json({"type": "ack", "call_id": int(call_id), "kind": kind})
            except Exception:
                pass

    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        try:
            if sub_task:
                sub_task.cancel()
        except Exception:
            pass
        try:
            if pubsub is not None:
                await pubsub.unsubscribe(channel)
                await pubsub.close()
        except Exception:
            pass
        try:
            await ws.close()
        except Exception:
            pass

