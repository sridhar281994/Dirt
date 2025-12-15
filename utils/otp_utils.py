"""
Legacy in-memory OTP helpers.

The app uses `utils/otp_service.py` (Redis + Brevo). This file remains so
older imports don't crash.
"""

from __future__ import annotations

import random
import time

OTP_STORE: dict[str, tuple[int, float]] = {}


def generate_otp(email: str, *, ttl_seconds: int = 300) -> int:
    otp = random.randint(100000, 999999)
    OTP_STORE[email] = (otp, time.time() + ttl_seconds)
    return otp


def verify_otp(email: str, otp: int) -> bool:
    rec = OTP_STORE.get(email)
    if not rec:
        return False
    code, exp = rec
    if time.time() > exp:
        OTP_STORE.pop(email, None)
        return False
    if code == otp:
        OTP_STORE.pop(email, None)
        return True
    return False
