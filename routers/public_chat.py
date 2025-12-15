from __future__ import annotations

from datetime import datetime
from typing import List

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from database import get_db
from models import PublicMessage, User
from routers.auth import get_current_user

router = APIRouter(tags=["public"])


class PublicMessageIn(BaseModel):
    message: str
    image_url: str = None


@router.get("/public/messages")
def get_public_messages(
    limit: int = 500,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """
    Returns the latest public messages, capped at 500.
    """
    msgs = (
        db.query(PublicMessage)
        .order_by(PublicMessage.created_at.desc())
        .limit(limit)
        .all()
    )
    # Return in chronological order
    msgs.reverse()
    
    return {
        "ok": True,
        "messages": [
            {
                "id": m.id,
                "sender_id": m.sender_id,
                "sender_name": m.sender.name,
                "message": m.message,
                "image_url": m.image_url,
                "created_at": m.created_at.isoformat(),
            }
            for m in msgs
        ],
    }


@router.post("/public/messages")
def post_public_message(
    payload: PublicMessageIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    if not payload.message or not payload.message.strip():
        raise HTTPException(400, "Message required")

    rec = PublicMessage(
        sender_id=user.id,
        message=payload.message.strip(),
        image_url=payload.image_url,
    )
    db.add(rec)
    db.commit()

    # Maintenance: Keep only last 500 messages
    # This is a naive implementation; for high scale, use a background job or partition.
    count = db.query(PublicMessage).count()
    if count > 500:
        # Delete oldest
        # Find the 500th newest message date
        limit_msg = (
            db.query(PublicMessage)
            .order_by(PublicMessage.created_at.desc())
            .offset(500)
            .first()
        )
        if limit_msg:
            db.query(PublicMessage).filter(PublicMessage.created_at <= limit_msg.created_at).delete()
            db.commit()

    return {"ok": True}
