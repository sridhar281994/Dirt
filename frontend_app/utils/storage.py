from __future__ import annotations

from typing import Any, Dict, Optional


_TOKEN: str = ""
_USER: Dict[str, Any] = {}


def set_token(token: str) -> None:
    global _TOKEN
    _TOKEN = token or ""


def get_token() -> str:
    return _TOKEN


def set_user(user: Dict[str, Any]) -> None:
    global _USER
    _USER = user or {}


def get_user() -> Dict[str, Any]:
    return _USER


def clear() -> None:
    set_token("")
    set_user({})

