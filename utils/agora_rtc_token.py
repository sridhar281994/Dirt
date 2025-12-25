from __future__ import annotations

"""
Minimal Agora RTC token builder (AccessToken "006").

This is vendored to avoid adding a third-party dependency just for token
generation.
"""

import base64
import hashlib
import hmac
import os
import random
import struct
import time
import zlib

# Agora AccessToken version
_VERSION = "006"


class AgoraTokenError(RuntimeError):
    pass


def _pack_uint16(x: int) -> bytes:
    return struct.pack("<H", int(x))


def _pack_uint32(x: int) -> bytes:
    return struct.pack("<I", int(x))


def _pack_bytes(b: bytes) -> bytes:
    return _pack_uint16(len(b)) + b


def _pack_string(s: str) -> bytes:
    b = (s or "").encode("utf-8")
    return _pack_uint16(len(b)) + b


def _crc32(s: str) -> int:
    return zlib.crc32((s or "").encode("utf-8")) & 0xFFFFFFFF


class _AccessToken:
    """
    Agora AccessToken builder (version 006).
    """

    # Privileges
    _PRIV_JOIN_CHANNEL = 1
    _PRIV_PUBLISH_AUDIO = 2
    _PRIV_PUBLISH_VIDEO = 3
    _PRIV_PUBLISH_DATA = 4

    def __init__(self, *, app_id: str, app_certificate: str, channel_name: str, uid: str):
        self.app_id = (app_id or "").strip()
        self.app_certificate = (app_certificate or "").strip()
        self.channel_name = (channel_name or "").strip()
        self.uid = (uid or "").strip()

        # These fields are embedded in the token and used for signature payload.
        self.salt = random.randint(1, 99999999)
        self.ts = int(time.time()) + 24 * 60 * 60
        self.messages: dict[int, int] = {}

    def add_privilege(self, privilege: int, expire_ts: int) -> None:
        self.messages[int(privilege)] = int(expire_ts)

    def build(self) -> str:
        if not (self.app_id and self.app_certificate and self.channel_name and self.uid):
            raise AgoraTokenError("Missing required fields to build Agora token.")
        if len(self.app_id) != 32:
            # Agora app id is typically 32 chars; warn early to avoid silent failures.
            raise AgoraTokenError("Invalid AGORA_APP_ID length (expected 32).")

        # Message body (salt, ts, privileges map)
        msg = _pack_uint32(self.salt) + _pack_uint32(self.ts) + _pack_uint16(len(self.messages))
        for k, v in self.messages.items():
            msg += _pack_uint16(k) + _pack_uint32(v)

        # Signature payload
        val = _pack_string(self.app_id) + _pack_string(self.channel_name) + _pack_string(self.uid) + msg
        sig = hmac.new(self.app_certificate.encode("utf-8"), val, hashlib.sha256).digest()

        crc_channel = _crc32(self.channel_name)
        crc_uid = _crc32(self.uid)

        content = _pack_bytes(sig) + _pack_uint32(crc_channel) + _pack_uint32(crc_uid) + _pack_bytes(msg)
        b64 = base64.b64encode(content).decode("utf-8")
        return f"{_VERSION}{self.app_id}{b64}"


def build_rtc_token_with_uid(
    *,
    app_id: str,
    app_certificate: str,
    channel_name: str,
    uid: int,
    expire_ts: int,
    publish: bool = True,
) -> str:
    """
    Build an Agora RTC token for a numeric uid.
    """
    try:
        uid_int = int(uid)
    except Exception as e:
        raise AgoraTokenError("uid must be an int") from e
    if uid_int < 0:
        raise AgoraTokenError("uid must be >= 0")

    t = _AccessToken(
        app_id=app_id,
        app_certificate=app_certificate,
        channel_name=channel_name,
        uid=str(uid_int),
    )
    # Everyone must be allowed to join.
    t.add_privilege(_AccessToken._PRIV_JOIN_CHANNEL, int(expire_ts))
    if publish:
        t.add_privilege(_AccessToken._PRIV_PUBLISH_AUDIO, int(expire_ts))
        t.add_privilege(_AccessToken._PRIV_PUBLISH_VIDEO, int(expire_ts))
        t.add_privilege(_AccessToken._PRIV_PUBLISH_DATA, int(expire_ts))
    return t.build()


def build_rtc_token_from_env(*, channel_name: str, uid: int, ttl_seconds: int = 3600) -> tuple[str, int]:
    """
    Convenience helper: reads credentials from env and returns (token, expire_ts).

    Uses:
    - AGORA_APP_ID or AGORA_ID
    - AGORA_APP_CERTIFICATE or AGORA_CERTIFICATE
    """
    app_id = os.getenv("AGORA_APP_ID") or os.getenv("AGORA_ID") or ""
    cert = os.getenv("AGORA_APP_CERTIFICATE") or os.getenv("AGORA_CERTIFICATE") or ""
    app_id = (app_id or "").strip()
    cert = (cert or "").strip()
    if not (app_id and cert):
        return ("", 0)
    expire_ts = int(time.time()) + int(ttl_seconds or 3600)
    token = build_rtc_token_with_uid(
        app_id=app_id,
        app_certificate=cert,
        channel_name=channel_name,
        uid=int(uid),
        expire_ts=expire_ts,
        publish=True,
    )
    return (token, expire_ts)

