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

engine = create_engine(
    DATABASE_URL,
    pool_pre_ping=True,
    connect_args=connect_args,
)

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
