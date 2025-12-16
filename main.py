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

# Lightweight SQLite schema patching for local dev (Render uses Postgres + scripts/db_update.sql).
def _sqlite_ensure_columns() -> None:
    try:
        if not str(engine.url).startswith("sqlite"):
            return
        from sqlalchemy import text

        with engine.begin() as conn:
            cols = conn.execute(text("PRAGMA table_info(users)")).fetchall()
            existing = {str(r[1]) for r in cols}  # (cid, name, type, notnull, dflt_value, pk)

            if "last_active_at" not in existing:
                conn.execute(text("ALTER TABLE users ADD COLUMN last_active_at DATETIME"))
            if "free_video_total_count" not in existing:
                conn.execute(text("ALTER TABLE users ADD COLUMN free_video_total_count INTEGER DEFAULT 0"))
            if "free_video_opposite_count" not in existing:
                conn.execute(text("ALTER TABLE users ADD COLUMN free_video_opposite_count INTEGER DEFAULT 0"))
    except Exception:
        # Never block app startup for local dev migrations.
        pass


_sqlite_ensure_columns()

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

