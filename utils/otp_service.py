from __future__ import annotations

import os
import random
import time
from typing import Optional

try:
    import redis  # type: ignore
except Exception:  # pragma: no cover
    redis = None

from utils.brevo_email import send_email


OTP_EXP_MIN = int(os.getenv("OTP_EXP_MINUTES", "5"))
OTP_SUBJECT = os.getenv("OTP_SUBJECT", "Your login OTP")


_MEM: dict[str, tuple[str, float]] = {}


def _redis_client():
    url = os.getenv("REDIS_URL")
    if not url or not redis:
        return None
    return redis.Redis.from_url(url, decode_responses=True)


def _gen_otp() -> str:
    return f"{random.randint(100000, 999999)}"


def otp_issue(*, identifier: str, to_email: str) -> str:
    """
    Issues OTP for 'identifier' and sends it to email via Brevo.
    Stores OTP in Redis if REDIS_URL present; otherwise in-memory.
    """
    code = _gen_otp()
    ttl_seconds = max(60, OTP_EXP_MIN * 60)

    r = _redis_client()
    key = f"otp:{identifier}"
    if r:
        r.setex(key, ttl_seconds, code)
    else:
        _MEM[key] = (code, time.time() + ttl_seconds)

    html = f"""
    <div style="font-family:Arial,sans-serif">
      <h2>Login OTP</h2>
      <p>Your OTP is:</p>
      <div style="font-size:28px;font-weight:700;letter-spacing:2px">{code}</div>
      <p>This OTP expires in {OTP_EXP_MIN} minutes.</p>
    </div>
    """
    send_email(to_email=to_email, subject=OTP_SUBJECT, html=html, text=f"Your OTP is {code}")
    return code


def otp_verify(*, identifier: str, otp: str) -> bool:
    otp = (otp or "").strip()
    if not otp:
        return False

    r = _redis_client()
    key = f"otp:{identifier}"
    if r:
        stored = r.get(key)
        if stored and secrets_equal(stored, otp):
            r.delete(key)
            return True
        return False

    stored = _MEM.get(key)
    if not stored:
        return False
    code, exp = stored
    if time.time() > exp:
        _MEM.pop(key, None)
        return False
    if secrets_equal(code, otp):
        _MEM.pop(key, None)
        return True
    return False


def secrets_equal(a: str, b: str) -> bool:
    # Constant-time compare
    if len(a) != len(b):
        return False
    result = 0
    for x, y in zip(a.encode("utf-8"), b.encode("utf-8")):
        result |= x ^ y
    return result == 0

