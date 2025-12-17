from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from database import get_db
from models import Report, User
from routers.auth import get_current_user

router = APIRouter(tags=["Report"])

class ReportCreate(BaseModel):
    reported_user_id: int | None = None
    reason: str
    details: str | None = None
    context: str | None = None # e.g. "video", "chat", "public_chat"

@router.post("/reports")
def create_report(
    report_in: ReportCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Report a user or content.
    """
    # Verify reported user exists if ID provided
    if report_in.reported_user_id:
        target = db.query(User).filter(User.id == report_in.reported_user_id).first()
        if not target:
            raise HTTPException(status_code=404, detail="Reported user not found")

    new_report = Report(
        reporter_id=current_user.id,
        reported_user_id=report_in.reported_user_id,
        reason=report_in.reason,
        details=report_in.details,
        context=report_in.context
    )
    db.add(new_report)
    db.commit()
    db.refresh(new_report)
    
    return {"status": "success", "report_id": new_report.id}
