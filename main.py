from __future__ import annotations

import os
from datetime import datetime, timedelta

from apscheduler.schedulers.background import BackgroundScheduler
from fastapi import FastAPI
from sqlalchemy.orm import Session

from database import Base, SessionLocal, engine
from models import ChatMessage
from routers.auth import router as auth_router
from routers.match_routes import router as match_router
from routers.public_chat import router as public_router
from routers.subscription import router as sub_router


app = FastAPI(title="Chat App Backend")

# Create tables (simple projects; for production use migrations).
Base.metadata.create_all(bind=engine)

app.include_router(auth_router, prefix="/api")
app.include_router(match_router, prefix="/api")
app.include_router(public_router, prefix="/api")
app.include_router(sub_router, prefix="/api")


def _cleanup_old_messages() -> int:
    """Delete chat history older than 48 hours."""
    cutoff = datetime.utcnow() - timedelta(hours=48)
    db: Session = SessionLocal()
    try:
        deleted = db.query(ChatMessage).filter(ChatMessage.created_at < cutoff).delete()
        db.commit()
        return int(deleted or 0)
    finally:
        db.close()


@app.on_event("startup")
def _start_scheduler():
    # Run periodic cleanup (48h retention).
    sched = BackgroundScheduler(timezone=os.getenv("TZ", "UTC"))
    sched.add_job(_cleanup_old_messages, "interval", minutes=30, id="cleanup_old_messages", replace_existing=True)
    sched.start()
    app.state._scheduler = sched


@app.on_event("shutdown")
def _stop_scheduler():
    sched = getattr(app.state, "_scheduler", None)
    if sched:
        sched.shutdown(wait=False)


@app.get("/")
def root():
    return {"status": "Backend running"}

