from __future__ import annotations

import os
from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker


def get_database_url() -> str:
    """
    Force SQLAlchemy to use psycopg (v3).
    Render may provide DATABASE_URL as:
      - postgres://...
      - postgresql://...

    Both MUST be rewritten to:
      postgresql+psycopg://...
    """
    url = os.getenv("DATABASE_URL")

    if not url:
        # Local fallback for dev/testing
        return "sqlite:///./app.db"

    if url.startswith("postgres://"):
        return url.replace("postgres://", "postgresql+psycopg://", 1)

    if url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgresql+psycopg://", 1)

    return url


DATABASE_URL = get_database_url()

# SQLite needs check_same_thread, Postgres does NOT
connect_args = (
    {"check_same_thread": False}
    if DATABASE_URL.startswith("sqlite")
    else {}
)

_engine_kwargs = {
    "pool_pre_ping": True,
    "connect_args": connect_args,
}
# Production tuning (Postgres only). Keep SQLite defaults.
if not DATABASE_URL.startswith("sqlite"):
    try:
        _engine_kwargs["pool_size"] = int(os.getenv("DB_POOL_SIZE", "5"))
    except Exception:
        pass
    try:
        _engine_kwargs["max_overflow"] = int(os.getenv("DB_MAX_OVERFLOW", "10"))
    except Exception:
        pass
    try:
        _engine_kwargs["pool_recycle"] = int(os.getenv("DB_POOL_RECYCLE", "1800"))
    except Exception:
        pass

engine = create_engine(DATABASE_URL, **_engine_kwargs)

SessionLocal = sessionmaker(
    bind=engine,
    autocommit=False,
    autoflush=False,
)

Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
