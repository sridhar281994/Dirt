from __future__ import annotations

import base64
import hmac
import os
import time
from hashlib import sha1
from typing import Any, Dict, List, Optional


def _split_urls(v: str) -> list[str]:
    urls: list[str] = []
    for part in (v or "").split(","):
        p = part.strip()
        if p:
            urls.append(p)
    return urls


def build_ice_servers(*, user_id: int) -> List[Dict[str, Any]]:
    """
    Build RTCPeerConnection iceServers list.

    Env vars:
    - STUN_URLS: comma-separated stun: URLs (optional)
    - TURN_URLS: comma-separated turn:/turns: URLs (optional)
    - TURN_SECRET: coturn "static auth secret" (optional; enables REST auth)
    - TURN_TTL_SECONDS: int (default 600)

    If TURN_SECRET is set, we generate coturn REST credentials:
      username = "<expiry_unix_ts>:<user_id>"
      credential = base64(hmac-sha1(secret, username))
    """

    stun_urls = _split_urls(os.getenv("STUN_URLS", "")) or [
        "stun:stun.l.google.com:19302",
        "stun:stun1.l.google.com:19302",
    ]
    turn_urls = _split_urls(os.getenv("TURN_URLS", ""))
    turn_secret = (os.getenv("TURN_SECRET") or "").strip() or None

    try:
        ttl = int(os.getenv("TURN_TTL_SECONDS", "600"))
    except Exception:
        ttl = 600
    if ttl <= 60:
        ttl = 60

    ice: list[dict[str, Any]] = []
    if stun_urls:
        ice.append({"urls": stun_urls})

    if turn_urls and turn_secret:
        expiry = int(time.time()) + ttl
        username = f"{expiry}:{int(user_id)}"
        digest = hmac.new(turn_secret.encode("utf-8"), username.encode("utf-8"), sha1).digest()
        credential = base64.b64encode(digest).decode("utf-8")
        ice.append(
            {
                "urls": turn_urls,
                "username": username,
                "credential": credential,
            }
        )

    return ice

