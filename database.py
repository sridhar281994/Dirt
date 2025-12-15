from __future__ import annotations

import os

from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker


def _database_url() -> str:
    # Prefer the platform-provided env var (Render sets DATABASE_URL).
    url = os.getenv("DATABASE_URL")
    if url:
        # SQLAlchemy defaults to psycopg2 for "postgresql://". On Python 3.13,
        # psycopg2 wheels can fail to import. Prefer psycopg (v3) instead.
        #
        # Render commonly provides "postgres://..."; normalize and select driver.
        if "://" in url and "+" not in url.split("://", 1)[0]:
            if url.startswith("postgres://"):
                return url.replace("postgres://", "postgresql+psycopg://", 1)
            if url.startswith("postgresql://"):
                return url.replace("postgresql://", "postgresql+psycopg://", 1)
        return url
    # Local/dev fallback (keeps repo runnable without Postgres).
    return "sqlite:///./app.db"


DATABASE_URL = _database_url()

connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}
engine = create_engine(DATABASE_URL, connect_args=connect_args, pool_pre_ping=True)

SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
