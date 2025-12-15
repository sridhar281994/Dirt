from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from database import get_db
from models import User
from routers.auth import get_current_user

router = APIRouter(tags=["subscription"])


class SubscriptionVerifyIn(BaseModel):
    purchase_token: str
    plan_key: str


@router.post("/subscription/verify")
def verify_subscription(
    payload: SubscriptionVerifyIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """
    Verifies subscription token. 
    In a real app, this would validate against Google Play Developer API.
    For this demo/task, we accept any non-empty token.
    """
    if not payload.purchase_token:
        raise HTTPException(400, "Purchase token required")
    
    if not payload.plan_key:
        raise HTTPException(400, "Plan key required")

    # Mock validation:
    # Google Play validation logic would go here.
    
    user.is_subscribed = True
    db.add(user)
    db.commit()
    
    return {"ok": True, "valid": True}
