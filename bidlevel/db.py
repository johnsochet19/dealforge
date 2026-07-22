"""Database engine/session for BidLevel.

Separate database from the DealForge app so the two products never collide.
`BIDLEVEL_DATABASE_URL` (or `DATABASE_URL` when running BidLevel standalone in
production) selects Postgres; default is a local SQLite file.
"""
from __future__ import annotations

import os

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker


def _database_url() -> str:
    url = os.environ.get("BIDLEVEL_DATABASE_URL") or "sqlite:///./bidlevel.db"
    # Railway/Heroku style postgres:// -> postgresql://
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    return url


DATABASE_URL = _database_url()

engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {},
    pool_pre_ping=True,
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


class Base(DeclarativeBase):
    pass


def init_db() -> None:
    from bidlevel import models  # noqa: F401  (register mappings)

    Base.metadata.create_all(engine)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
