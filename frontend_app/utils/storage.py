from __future__ import annotations

import os
from typing import Any, Dict

from kivy.app import App
from kivy.storage.jsonstore import JsonStore


_TOKEN: str = ""
_USER: Dict[str, Any] = {}
_REMEMBER_ME: bool = False
_STORE: JsonStore | None = None
_CHAT_READ_KEY = "chat_read"


def _store_path() -> str:
    """
    Return a writable path for persistent storage.

    - Android: use App.user_data_dir
    - Desktop/dev: store alongside this module
    """
    try:
        app = App.get_running_app()
        if app and getattr(app, "user_data_dir", None):
            return os.path.join(app.user_data_dir, "buddymeet_store.json")
    except Exception:
        pass
    return os.path.join(os.path.dirname(__file__), "buddymeet_store.json")


def _get_store() -> JsonStore:
    global _STORE
    if _STORE is None:
        path = _store_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        _STORE = JsonStore(path)
    return _STORE


def _load_persisted() -> None:
    """
    Load persisted auth/user into memory (if remember_me is enabled).
    Safe to call repeatedly.
    """
    global _TOKEN, _USER, _REMEMBER_ME
    try:
        store = _get_store()
        if not store.exists("auth"):
            return
        data = store.get("auth") or {}
        _REMEMBER_ME = bool(data.get("remember_me") or False)
        if _REMEMBER_ME:
            _TOKEN = str(data.get("token") or "")
            _USER = dict(data.get("user") or {})
    except Exception:
        # If store is corrupted/unreadable, fail closed (do not persist).
        return


def set_remember_me(value: bool) -> None:
    global _REMEMBER_ME
    _REMEMBER_ME = bool(value)


def get_remember_me() -> bool:
    _load_persisted()
    return bool(_REMEMBER_ME)


def set_token(token: str) -> None:
    global _TOKEN
    _TOKEN = token or ""
    if _REMEMBER_ME:
        try:
            store = _get_store()
            store.put("auth", token=_TOKEN, user=_USER, remember_me=True)
        except Exception:
            pass


def get_token() -> str:
    _load_persisted()
    return _TOKEN


def set_user(user: Dict[str, Any]) -> None:
    global _USER
    _USER = user or {}
    if _REMEMBER_ME:
        try:
            store = _get_store()
            store.put("auth", token=_TOKEN, user=_USER, remember_me=True)
        except Exception:
            pass


def get_user() -> Dict[str, Any]:
    _load_persisted()
    return _USER


def set_session(*, token: str, user: Dict[str, Any], remember: bool) -> None:
    """
    Set current in-memory session and optionally persist it.
    """
    global _TOKEN, _USER, _REMEMBER_ME
    _TOKEN = token or ""
    _USER = user or {}
    _REMEMBER_ME = bool(remember)

    try:
        store = _get_store()
        store.put("auth", token=_TOKEN, user=_USER, remember_me=_REMEMBER_ME)
        if not _REMEMBER_ME:
            # If user opted out, remove sensitive session data.
            store.delete("auth")
            store.put("auth", token="", user={}, remember_me=False)
    except Exception:
        # Persistence failures should not block login.
        return


def clear() -> None:
    global _TOKEN, _USER, _REMEMBER_ME
    _TOKEN = ""
    _USER = {}
    _REMEMBER_ME = False
    try:
        store = _get_store()
        if store.exists("auth"):
            store.delete("auth")
        if store.exists(_CHAT_READ_KEY):
            store.delete(_CHAT_READ_KEY)
    except Exception:
        pass


def should_auto_login() -> bool:
    """
    True when a persisted token/user should skip login UI.
    """
    _load_persisted()
    return bool(_REMEMBER_ME and _TOKEN and _USER)


def get_last_read_message_id(*, session_id: int) -> int:
    """
    Local-only read tracking: last message id the user has seen for a session.

    Backend does not store per-user read state, so we persist it on-device.
    """
    try:
        sid = int(session_id or 0)
    except Exception:
        sid = 0
    if sid <= 0:
        return 0

    try:
        store = _get_store()
        if not store.exists(_CHAT_READ_KEY):
            return 0
        data = store.get(_CHAT_READ_KEY) or {}
        by_session = dict(data.get("by_session") or {})
        v = by_session.get(str(sid))
        return int(v or 0)
    except Exception:
        return 0


def set_last_read_message_id(*, session_id: int, message_id: int) -> None:
    try:
        sid = int(session_id or 0)
    except Exception:
        sid = 0
    try:
        mid = int(message_id or 0)
    except Exception:
        mid = 0
    if sid <= 0 or mid <= 0:
        return

    try:
        store = _get_store()
        by_session: Dict[str, Any] = {}
        if store.exists(_CHAT_READ_KEY):
            try:
                data = store.get(_CHAT_READ_KEY) or {}
                by_session = dict(data.get("by_session") or {})
            except Exception:
                by_session = {}

        # Only move forward (never decrease).
        prev = 0
        try:
            prev = int(by_session.get(str(sid)) or 0)
        except Exception:
            prev = 0
        if mid <= prev:
            return

        by_session[str(sid)] = int(mid)
        store.put(_CHAT_READ_KEY, by_session=by_session)
    except Exception:
        pass

