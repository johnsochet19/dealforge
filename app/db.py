"""Database engine + session.

Uses DATABASE_URL if set (Postgres in prod, e.g. a Railway Postgres plugin),
otherwise a local SQLite file so the app runs with zero external services. The
schema is written to work on both.

Persistence on Railway: the default SQLite file lives on the container's
ephemeral disk, so it resets on every redeploy. Attach a Postgres plugin and
Railway injects DATABASE_URL -- the app then stores everything in Postgres,
which persists across deploys. init_db() converges the schema on boot so you
never have to run a migration by hand for this to work.
"""
import os
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import declarative_base, sessionmaker


def _normalize(url: str) -> str:
    """Railway/Heroku hand out `postgres://...`; SQLAlchemy 2.x needs the
    `postgresql://` scheme, and we pin the psycopg2 driver explicitly."""
    if url.startswith("postgres://"):
        return "postgresql+psycopg2://" + url[len("postgres://"):]
    if url.startswith("postgresql://") and "+" not in url.split("://", 1)[0]:
        return "postgresql+psycopg2://" + url[len("postgresql://"):]
    return url


DATABASE_URL = _normalize(os.getenv("DATABASE_URL", "sqlite:///./dealforge.db"))
_is_sqlite = DATABASE_URL.startswith("sqlite")

connect_args = {"check_same_thread": False} if _is_sqlite else {}
engine = create_engine(
    DATABASE_URL, connect_args=connect_args, future=True,
    # pre-ping avoids errors from Postgres connections dropped while idle
    # (common on managed Postgres); harmless and unnecessary on SQLite.
    pool_pre_ping=not _is_sqlite,
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# Columns added to existing tables after the initial schema shipped. init_db
# adds any that are missing so an old database converges without a manual
# migration. (table, column, SQL type valid on both SQLite and Postgres.)
_ADDED_COLUMNS = [
    ("alert_events", "delivery_status", "VARCHAR(16)"),
    ("alert_events", "delivery_detail", "TEXT"),
]


def init_db(bind=None) -> None:
    """Create missing tables and add missing columns so the database matches the
    current models. Idempotent and safe to run on every boot, on SQLite or
    Postgres. This is a lightweight stand-in for a full migration tool: it fixes
    the reset-on-redeploy problem (with a persistent Postgres) and closes the
    schema-evolution gap that plain create_all leaves (it never alters existing
    tables)."""
    eng = bind or engine
    import app.models  # noqa: F401  ensure tables are registered on Base
    Base.metadata.create_all(bind=eng)  # creates any missing tables

    insp = inspect(eng)
    existing = set(insp.get_table_names())
    pending = []
    for table, column, ddl_type in _ADDED_COLUMNS:
        if table in existing:
            cols = {c["name"] for c in insp.get_columns(table)}
            if column not in cols:
                pending.append((table, column, ddl_type))
    if pending:
        with eng.begin() as conn:
            for table, column, ddl_type in pending:
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {ddl_type}"))
