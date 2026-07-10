"""Database URL normalization and schema-converging init_db()."""
from sqlalchemy import create_engine, inspect, text

from app.db import _normalize, init_db


# --- URL normalization ------------------------------------------------------

def test_normalize_railway_postgres_scheme():
    assert _normalize("postgres://u:p@host:5432/db") == \
        "postgresql+psycopg2://u:p@host:5432/db"


def test_normalize_postgresql_scheme_gets_driver():
    assert _normalize("postgresql://u:p@host/db") == \
        "postgresql+psycopg2://u:p@host/db"


def test_normalize_leaves_explicit_driver_alone():
    url = "postgresql+psycopg2://u:p@host/db"
    assert _normalize(url) == url


def test_normalize_leaves_sqlite_alone():
    assert _normalize("sqlite:///./dealforge.db") == "sqlite:///./dealforge.db"


# --- init_db creates the full schema ----------------------------------------

def test_init_db_creates_all_tables():
    eng = create_engine("sqlite:///:memory:")
    init_db(bind=eng)
    tables = set(inspect(eng).get_table_names())
    assert {"users", "products", "price_observations", "alerts",
            "alert_events", "notification_channels"} <= tables


def test_init_db_is_idempotent():
    eng = create_engine("sqlite:///:memory:")
    init_db(bind=eng)
    init_db(bind=eng)  # must not raise on a second run
    assert "users" in set(inspect(eng).get_table_names())


def test_init_db_adds_missing_columns_to_old_table():
    """Simulate a database created before delivery tracking existed: an
    alert_events table without the delivery_* columns. init_db should add them
    rather than leaving the schema stale (which plain create_all would do)."""
    eng = create_engine("sqlite:///:memory:")
    with eng.begin() as conn:
        conn.execute(text(
            "CREATE TABLE alert_events ("
            "id INTEGER PRIMARY KEY, alert_id INTEGER, message TEXT, "
            "triggered_at DATETIME)"))
    init_db(bind=eng)
    cols = {c["name"] for c in inspect(eng).get_columns("alert_events")}
    assert "delivery_status" in cols
    assert "delivery_detail" in cols
