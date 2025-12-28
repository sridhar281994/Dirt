from __future__ import annotations

import os
import time
from urllib.parse import urlsplit
from typing import Any, Dict, Tuple
import requests
import urllib3

from frontend_app.utils.storage import get_token


class ApiError(RuntimeError):
    pass


urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

_DEFAULT_TIMEOUT: Tuple[float, float] = (10.0, 30.0)  # (connect, read)
_UPLOAD_TIMEOUT: Tuple[float, float] = (10.0, 40.0)
_PING_PATH = "/ping"
_WARMUP_MIN_INTERVAL_S = 5 * 60  # don't ping on every request
_last_warmup_monotonic: float = 0.0


def _base_url() -> str:
    return os.getenv("BACKEND_URL", "https://dirt-0atr.onrender.com").rstrip("/")


def _headers(auth: bool = False) -> Dict[str, str]:
    h = {"content-type": "application/json"}
    if auth:
        tok = get_token()
        if tok:
            h["authorization"] = f"Bearer {tok}"
    return h


def _friendly_network_error(exc: BaseException, *, url: str) -> str:
    """
    Convert low-level requests/urllib3/socket errors into a human-friendly message.
    This is especially important on Android where DNS / captive portal / no-data
    situations are common and would otherwise crash background threads.
    """
    try:
        host = urlsplit(url).netloc or url
    except Exception:
        host = url

    # Import here to avoid typing-only dependency differences on some platforms.
    try:
        from requests import exceptions as req_exc  # type: ignore
    except Exception:
        req_exc = None

    if req_exc:
        if isinstance(exc, req_exc.Timeout):
            # Render free/starter services often cold-start after inactivity.
            # Mobile apps also tend to use short default timeouts, which makes this look like "server down".
            if str(host).endswith("onrender.com"):
                return f"Server waking up. Please wait a moment and try again. ({host})"
            return f"Request timed out. Check your internet and try again. ({host})"
        if isinstance(exc, req_exc.SSLError):
            return f"SSL error while contacting server. ({host})"
        if isinstance(exc, req_exc.ConnectionError):
            # Common: DNS failure (UnknownHost / gaierror), airplane mode, no data, captive portal.
            return f"Can't reach server. Check internet/DNS and try again. ({host})"

    # Fallback: keep it short, but include host so we know which backend is used.
    return f"Network error contacting server. ({host})"


def _maybe_warmup(*, force: bool = False) -> None:
    """
    Wake up the backend before doing real API calls.
    On Render free/starter tiers, the first request after inactivity may take 10–40s.
    """
    global _last_warmup_monotonic

    now = time.monotonic()
    if not force and _last_warmup_monotonic and (now - _last_warmup_monotonic) < _WARMUP_MIN_INTERVAL_S:
        return

    url = f"{_base_url()}{_PING_PATH}"
    # Best-effort warmup: if it fails, the subsequent call will surface the error.
    try:
        requests.get(url, timeout=_DEFAULT_TIMEOUT, verify=False)
        _last_warmup_monotonic = now
    except Exception:
        # Don't raise here; let the main request's retry/timeout handling decide messaging.
        pass


def _request(method: str, url: str, **kwargs: Any) -> requests.Response:
    """
    requests.request wrapper that always raises ApiError on network failures.
    """
    # Always use explicit timeouts (connect, read). Mobile defaults are often too short.
    timeout = kwargs.get("timeout", _DEFAULT_TIMEOUT)
    if isinstance(timeout, (int, float)):
        # If a single value was provided, treat it as the read timeout with a safe connect timeout.
        timeout = (float(_DEFAULT_TIMEOUT[0]), float(timeout))
    kwargs["timeout"] = timeout

    # Default to verify=False to avoid platform CA issues in packaged mobile builds.
    # (For production, prefer verify=True and include proper CA certs.)
    kwargs.setdefault("verify", False)

    # Warm up Render/PAAS backend before the "real" call.
    try:
        if not str(url).endswith(_PING_PATH):
            _maybe_warmup()
    except Exception:
        pass

    # Retry once on Timeout (common with cold starts).
    for attempt in range(2):
        try:
            return requests.request(method, url, **kwargs)
        except requests.exceptions.Timeout as exc:
            if attempt == 0:
                # Force a warmup ping then retry once.
                _maybe_warmup(force=True)
                continue
            raise ApiError(_friendly_network_error(exc, url=url)) from exc
        except requests.RequestException as exc:
            raise ApiError(_friendly_network_error(exc, url=url)) from exc


def _raise(resp: requests.Response) -> None:
    try:
        data = resp.json()
    except Exception:
        data = None
    if resp.status_code >= 300:
        msg = None
        if isinstance(data, dict):
            msg = data.get("detail") or data.get("message")
        raise ApiError(msg or f"Request failed ({resp.status_code})")


def api_register(**payload: Any) -> Dict[str, Any]:
    r = _request(
        "POST",
        f"{_base_url()}/api/auth/register",
        json=payload,
        headers=_headers(),
        timeout=_DEFAULT_TIMEOUT,
    )
    _raise(r)
    return r.json()


def api_login_request_otp(*, identifier: str, password: str) -> Dict[str, Any]:
    r = _request(
        "POST",
        f"{_base_url()}/api/auth/login/request-otp",
        json={"identifier": identifier, "password": password},
        headers=_headers(),
        timeout=_DEFAULT_TIMEOUT,
    )
    _raise(r)
    return r.json()


def api_login_verify_otp(*, identifier: str, password: str, otp: str) -> Dict[str, Any]:
    r = _request(
        "POST",
        f"{_base_url()}/api/auth/login/verify-otp",
        json={"identifier": identifier, "password": password, "otp": otp},
        headers=_headers(),
        timeout=_DEFAULT_TIMEOUT,
    )
    _raise(r)
    return r.json()


def api_forgot_password_request_otp(*, identifier: str) -> Dict[str, Any]:
    r = _request(
        "POST",
        f"{_base_url()}/api/auth/forgot-password/request-otp",
        json={"identifier": identifier},
        headers=_headers(),
        timeout=_DEFAULT_TIMEOUT,
    )
    _raise(r)
    return r.json()


def api_forgot_password_reset(*, identifier: str, otp: str, new_password: str) -> Dict[str, Any]:
    r = _request(
        "POST",
        f"{_base_url()}/api/auth/forgot-password/reset",
        json={"identifier": identifier, "otp": otp, "new_password": new_password},
        headers=_headers(),
        timeout=_DEFAULT_TIMEOUT,
    )
    _raise(r)
    return r.json()


def api_guest() -> Dict[str, Any]:
    r = _request(
        "POST",
        f"{_base_url()}/api/auth/guest",
        json={},
        headers=_headers(),
        timeout=_DEFAULT_TIMEOUT,
    )
    _raise(r)
    return r.json()


def api_next_profile(*, preference: str) -> Dict[str, Any]:
    r = _request(
        "GET",
        f"{_base_url()}/api/profiles/next",
        params={"preference": preference},
        headers=_headers(auth=True),
        timeout=_DEFAULT_TIMEOUT,
    )
    _raise(r)
    return r.json()


def api_swipe(*, target_user_id: int, direction: str) -> Dict[str, Any]:
    r = _request(
        "POST",
        f"{_base_url()}/api/profiles/swipe",
        json={"target_user_id": target_user_id, "direction": direction},
        headers=_headers(auth=True),
        timeout=_DEFAULT_TIMEOUT,
    )
    _raise(r)
    return r.json()


def api_start_session(*, target_user_id: int, mode: str) -> Dict[str, Any]:
    r = _request(
        "POST",
        f"{_base_url()}/api/sessions/start",
        json={"target_user_id": target_user_id, "mode": mode},
        headers=_headers(auth=True),
        timeout=_DEFAULT_TIMEOUT,
    )
    _raise(r)
    return r.json()


def api_get_messages(*, session_id: int) -> Dict[str, Any]:
    r = _request(
        "GET",
        f"{_base_url()}/api/messages",
        params={"session_id": session_id},
        headers=_headers(auth=True),
        timeout=_DEFAULT_TIMEOUT,
    )
    _raise(r)
    return r.json()


def api_post_message(*, session_id: int, message: str) -> Dict[str, Any]:
    r = _request(
        "POST",
        f"{_base_url()}/api/messages",
        json={"session_id": session_id, "message": message},
        headers=_headers(auth=True),
        timeout=_DEFAULT_TIMEOUT,
    )
    _raise(r)
    return r.json()


def api_demo_subscribe() -> Dict[str, Any]:
    r = _request(
        "POST",
        f"{_base_url()}/api/subscription/demo-activate",
        json={},
        headers=_headers(auth=True),
        timeout=_DEFAULT_TIMEOUT,
    )
    _raise(r)
    return r.json()


def api_verify_subscription(*, purchase_token: str, plan_key: str) -> bool:
    r = _request(
        "POST",
        f"{_base_url()}/api/subscription/verify",
        json={"purchase_token": purchase_token, "plan_key": plan_key},
        headers=_headers(auth=True),
        timeout=_DEFAULT_TIMEOUT,
    )
    _raise(r)
    return r.json().get("valid", False)


def api_video_match(*, preference: str = "both") -> Dict[str, Any]:
    r = _request(
        "POST",
        f"{_base_url()}/api/video/match",
        json={"preference": preference},
        headers=_headers(auth=True),
        timeout=_DEFAULT_TIMEOUT,
    )
    _raise(r)
    return r.json()


def api_video_end(*, session_id: int | None = None) -> Dict[str, Any]:
    payload: Dict[str, Any] = {}
    if session_id is not None:
        try:
            sid = int(session_id)
        except Exception:
            sid = 0
        if sid > 0:
            payload["session_id"] = sid

    r = _request(
        "POST",
        f"{_base_url()}/api/video/end",
        json=payload,
        headers=_headers(auth=True),
        timeout=_DEFAULT_TIMEOUT,
    )
    _raise(r)
    return r.json()


def api_get_public_messages(*, limit: int = 500) -> Dict[str, Any]:
    r = _request(
        "GET",
        f"{_base_url()}/api/public/messages",
        params={"limit": limit},
        headers=_headers(auth=True),
        timeout=_DEFAULT_TIMEOUT,
    )
    _raise(r)
    return r.json()


def api_post_public_message(*, message: str, image_url: str = None) -> Dict[str, Any]:
    r = _request(
        "POST",
        f"{_base_url()}/api/public/messages",
        json={"message": message, "image_url": image_url},
        headers=_headers(auth=True),
        timeout=_DEFAULT_TIMEOUT,
    )
    _raise(r)
    return r.json()


def api_get_history() -> Dict[str, Any]:
    r = _request(
        "GET",
        f"{_base_url()}/api/sessions/history",
        headers=_headers(auth=True),
        timeout=_DEFAULT_TIMEOUT,
    )
    _raise(r)
    return r.json()


def api_report_user(*, reported_user_id: int | None = None, reason: str, details: str | None = None, context: str | None = None) -> Dict[str, Any]:
    r = _request(
        "POST",
        f"{_base_url()}/api/reports",
        json={
            "reported_user_id": reported_user_id,
            "reason": reason,
            "details": details,
            "context": context
        },
        headers=_headers(auth=True),
        timeout=_DEFAULT_TIMEOUT,
    )
    _raise(r)
    return r.json()


def api_update_profile(name: str | None = None, image_url: str | None = None) -> Dict[str, Any]:
    payload = {}
    if name is not None:
        payload["name"] = name
    if image_url is not None:
        payload["image_url"] = image_url

    r = _request(
        "PUT",
        f"{_base_url()}/api/auth/profile",
        json=payload,
        headers=_headers(auth=True),
        timeout=_DEFAULT_TIMEOUT,
    )
    _raise(r)
    return r.json()


def api_upload_profile_image(*, file_path: str) -> Dict[str, Any]:
    """
    Upload a profile image file as multipart/form-data.
    Backend stores and returns updated user dict with image_url like /static/...
    """
    tok = get_token()
    if not tok:
        raise ApiError("Not authenticated")

    url = f"{_base_url()}/api/auth/profile/image"
    headers = {"authorization": f"Bearer {tok}"}

    try:
        with open(file_path, "rb") as f:
            files = {"file": (os.path.basename(file_path), f, "application/octet-stream")}
            r = _request("POST", url, headers=headers, files=files, timeout=_UPLOAD_TIMEOUT)
    except FileNotFoundError:
        raise ApiError("Selected file not found.")

    _raise(r)
    return r.json()
