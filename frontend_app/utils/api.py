from __future__ import annotations

import os
from typing import Any, Dict
import requests
import urllib3

from frontend_app.utils.storage import get_token


class ApiError(RuntimeError):
    pass


def _base_url() -> str:
    return os.getenv("BACKEND_URL", "https://dirt-0atr.onrender.com").rstrip("/")


def _headers(auth: bool = False) -> Dict[str, str]:
    h = {"content-type": "application/json"}
    if auth:
        tok = get_token()
        if tok:
            h["authorization"] = f"Bearer {tok}"
    return h


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
    r = requests.post(
        f"{_base_url()}/api/auth/register",
        json=payload,
        headers=_headers(),
        timeout=20,
        verify=False,
    )
    _raise(r)
    return r.json()


def api_login_request_otp(*, identifier: str, password: str) -> Dict[str, Any]:
    r = requests.post(
        f"{_base_url()}/api/auth/login/request-otp",
        json={"identifier": identifier, "password": password},
        headers=_headers(),
        timeout=20,
        verify=False,
    )
    _raise(r)
    return r.json()


def api_login_verify_otp(*, identifier: str, password: str, otp: str) -> Dict[str, Any]:
    r = requests.post(
        f"{_base_url()}/api/auth/login/verify-otp",
        json={"identifier": identifier, "password": password, "otp": otp},
        headers=_headers(),
        timeout=20,
        verify=False,
    )
    _raise(r)
    return r.json()


def api_forgot_password_request_otp(*, identifier: str) -> Dict[str, Any]:
    r = requests.post(
        f"{_base_url()}/api/auth/forgot-password/request-otp",
        json={"identifier": identifier},
        headers=_headers(),
        timeout=20,
        verify=False,
    )
    _raise(r)
    return r.json()


def api_forgot_password_reset(*, identifier: str, otp: str, new_password: str) -> Dict[str, Any]:
    r = requests.post(
        f"{_base_url()}/api/auth/forgot-password/reset",
        json={"identifier": identifier, "otp": otp, "new_password": new_password},
        headers=_headers(),
        timeout=20,
        verify=False,
    )
    _raise(r)
    return r.json()


def api_guest() -> Dict[str, Any]:
    r = requests.post(
        f"{_base_url()}/api/auth/guest",
        json={},
        headers=_headers(),
        timeout=20,
        verify=False,
    )
    _raise(r)
    return r.json()


def api_next_profile(*, preference: str) -> Dict[str, Any]:
    r = requests.get(
        f"{_base_url()}/api/profiles/next",
        params={"preference": preference},
        headers=_headers(auth=True),
        timeout=20,
        verify=False,
    )
    _raise(r)
    return r.json()


def api_swipe(*, target_user_id: int, direction: str) -> Dict[str, Any]:
    r = requests.post(
        f"{_base_url()}/api/profiles/swipe",
        json={"target_user_id": target_user_id, "direction": direction},
        headers=_headers(auth=True),
        timeout=20,
        verify=False,
    )
    _raise(r)
    return r.json()


def api_start_session(*, target_user_id: int, mode: str) -> Dict[str, Any]:
    r = requests.post(
        f"{_base_url()}/api/sessions/start",
        json={"target_user_id": target_user_id, "mode": mode},
        headers=_headers(auth=True),
        timeout=20,
        verify=False,
    )
    _raise(r)
    return r.json()


def api_get_messages(*, session_id: int) -> Dict[str, Any]:
    r = requests.get(
        f"{_base_url()}/api/messages",
        params={"session_id": session_id},
        headers=_headers(auth=True),
        timeout=20,
        verify=False,
    )
    _raise(r)
    return r.json()


def api_post_message(*, session_id: int, message: str) -> Dict[str, Any]:
    r = requests.post(
        f"{_base_url()}/api/messages",
        json={"session_id": session_id, "message": message},
        headers=_headers(auth=True),
        timeout=20,
        verify=False,
    )
    _raise(r)
    return r.json()


def api_demo_subscribe() -> Dict[str, Any]:
    r = requests.post(
        f"{_base_url()}/api/subscription/demo-activate",
        json={},
        headers=_headers(auth=True),
        timeout=20,
        verify=False,
    )
    _raise(r)
    return r.json()


def api_verify_subscription(*, purchase_token: str, plan_key: str) -> bool:
    r = requests.post(
        f"{_base_url()}/api/subscription/verify",
        json={"purchase_token": purchase_token, "plan_key": plan_key},
        headers=_headers(auth=True),
        timeout=20,
        verify=False,
    )
    _raise(r)
    return r.json().get("valid", False)


def api_video_match(*, preference: str = "both") -> Dict[str, Any]:
    r = requests.post(
        f"{_base_url()}/api/video/match",
        json={"preference": preference},
        headers=_headers(auth=True),
        timeout=20,
        verify=False,
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

    r = requests.post(
        f"{_base_url()}/api/video/end",
        json=payload,
        headers=_headers(auth=True),
        timeout=20,
        verify=False,
    )
    _raise(r)
    return r.json()


def api_get_public_messages(*, limit: int = 500) -> Dict[str, Any]:
    r = requests.get(
        f"{_base_url()}/api/public/messages",
        params={"limit": limit},
        headers=_headers(auth=True),
        timeout=20,
        verify=False,
    )
    _raise(r)
    return r.json()


def api_post_public_message(*, message: str, image_url: str = None) -> Dict[str, Any]:
    r = requests.post(
        f"{_base_url()}/api/public/messages",
        json={"message": message, "image_url": image_url},
        headers=_headers(auth=True),
        timeout=20,
        verify=False,
    )
    _raise(r)
    return r.json()


def api_get_history() -> Dict[str, Any]:
    r = requests.get(
        f"{_base_url()}/api/sessions/history",
        headers=_headers(auth=True),
        timeout=20,
        verify=False,
    )
    _raise(r)
    return r.json()


def api_report_user(*, reported_user_id: int | None = None, reason: str, details: str | None = None, context: str | None = None) -> Dict[str, Any]:
    r = requests.post(
        f"{_base_url()}/api/reports",
        json={
            "reported_user_id": reported_user_id,
            "reason": reason,
            "details": details,
            "context": context
        },
        headers=_headers(auth=True),
        timeout=20,
        verify=False,
    )
    _raise(r)
    return r.json()


def api_update_profile(name: str | None = None, image_url: str | None = None) -> Dict[str, Any]:
    payload = {}
    if name is not None:
        payload["name"] = name
    if image_url is not None:
        payload["image_url"] = image_url

    r = requests.put(
        f"{_base_url()}/api/auth/profile",
        json=payload,
        headers=_headers(auth=True),
        timeout=20,
        verify=False,
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
            r = requests.post(url, headers=headers, files=files, timeout=40, verify=False)
    except FileNotFoundError:
        raise ApiError("Selected file not found.")

    _raise(r)
    return r.json()
