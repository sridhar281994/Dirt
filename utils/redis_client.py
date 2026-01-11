from __future__ import annotations

import os
from functools import lru_cache
from typing import Optional

import redis
import redis.asyncio as redis_async


def redis_url() -> Optional[str]:
    """
    Return Redis URL (or None if not configured).

    In production, set REDIS_URL (e.g. on Render / Fly / AWS).
    """

    u = (os.getenv("REDIS_URL") or "").strip()
    return u or None


@lru_cache(maxsize=1)
def get_redis() -> Optional[redis.Redis]:
    """
    Synchronous Redis client (useful for background jobs / sync endpoints).
    Returns None if REDIS_URL isn't configured.
    """

    u = redis_url()
    if not u:
        return None
    return redis.Redis.from_url(u, decode_responses=True)


@lru_cache(maxsize=1)
def get_async_redis() -> Optional[redis_async.Redis]:
    """
    Async Redis client (preferred for WebSockets / async endpoints).
    Returns None if REDIS_URL isn't configured.
    """

    u = redis_url()
    if not u:
        return None
    return redis_async.Redis.from_url(u, decode_responses=True)

