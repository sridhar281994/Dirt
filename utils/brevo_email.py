from __future__ import annotations

import os
from typing import Optional

import requests


def send_email(*, to_email: str, subject: str, html: str, text: Optional[str] = None) -> None:
    """
    Sends email using Brevo Transactional Email API.
    Requires:
      - BREVO_API_KEY
      - BREVO_FROM (email) OR EMAIL_FROM/SMTP_FROM
    """
    api_key = os.getenv("BREVO_API_KEY")
    if not api_key:
        raise RuntimeError("BREVO_API_KEY is not set")

    from_email = (
        os.getenv("BREVO_FROM")
        or os.getenv("EMAIL_FROM")
        or os.getenv("SMTP_FROM")
    )
    if not from_email:
        raise RuntimeError("BREVO_FROM (or EMAIL_FROM/SMTP_FROM) is not set")

    payload = {
        "sender": {"email": from_email, "name": "Chat App"},
        "to": [{"email": to_email}],
        "subject": subject,
        "htmlContent": html,
    }
    if text:
        payload["textContent"] = text

    resp = requests.post(
        "https://api.brevo.com/v3/smtp/email",
        headers={
            "accept": "application/json",
            "api-key": api_key,
            "content-type": "application/json",
        },
        json=payload,
        timeout=15,
    )
    if resp.status_code >= 300:
        raise RuntimeError(f"Brevo send failed ({resp.status_code}): {resp.text}")

