from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from database import get_db
from models import User
from utils.otp_utils import generate_otp, verify_otp
from datetime import datetime, timedelta


router = APIRouter()


@router.post("/send-otp")
def send_otp(email: str):
otp = generate_otp(email)
return {"message": "OTP sent"}




@router.post("/verify-otp")
def verify(email: str, otp: int, db: Session = Depends(get_db)):
if not verify_otp(email, otp):
raise HTTPException(status_code=401, detail="Invalid OTP")
return {"message": "Login successful"}




@router.post("/match")
def match_user(user_id: int, preference: str, db: Session = Depends(get_db)):
user = db.query(User).filter(User.id == user_id).first()
if not user:
raise HTTPException(status_code=404, detail="User not found")


if preference != "Both" and not user.is_subscribed:
raise HTTPException(status_code=403, detail="Subscription required")


# Dummy match logic
return {"matched": True, "chat_type": preference}




@router.delete("/cleanup-chats")
def cleanup_old_chats(db: Session = Depends(get_db)):
expiry = datetime.utcnow() - timedelta(hours=48)
deleted = db.query(ChatHistory).filter(ChatHistory.created_at < expiry).delete()
db.commit()
return {"deleted": deleted}
