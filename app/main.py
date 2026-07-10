"""DealForge AI -- API entrypoint (v1).

Real, working endpoints over the core platform: ingestion, deal cards with
explainable scores, price history, and CRUD + evaluation for alerts.
"""
import json
import os
from fastapi import FastAPI, Depends, HTTPException, Header
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from .db import Base, engine, get_db, init_db
from . import models  # noqa: F401  (register tables)
from . import connectors  # noqa: F401  (register connectors)
from .connectors.base import get_connectors
from .services.ingest import run_ingest
from .services.deals import all_cards, product_card, history_series
from .services.alerts import evaluate_alerts
from .services.notify import dispatch, channel_kinds
from .services.search import search as run_search, facets as get_facets
from .services import auth
from .models import Alert, Product, AlertEvent, NotificationChannel, User

# When true, every data endpoint that touches a user's alerts/channels demands a
# valid bearer token. Default off so the zero-friction demo (and the test suite)
# work without auth; set REQUIRE_AUTH=true to lock the app down in production.
REQUIRE_AUTH = os.getenv("REQUIRE_AUTH", "false").lower() == "true"

if auth.using_default_secret():
    import logging
    logging.getLogger("uvicorn.error").warning(
        "SECRET_KEY is unset -- using an insecure default. Set SECRET_KEY in "
        "the environment before deploying; tokens signed with the default are "
        "not secure and reset on every process using the default.")

init_db()  # create/converge schema (works on SQLite and Postgres)

app = FastAPI(title="DealForge AI", version="1.0.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"],
                   allow_headers=["*"])


@app.get("/health")
def health():
    return {"status": "ok"}


# --- authentication ---------------------------------------------------------

def get_optional_user(authorization: str | None = Header(None),
                      db: Session = Depends(get_db)) -> User | None:
    """Resolve the caller from a `Authorization: Bearer <token>` header, or None
    if absent/invalid. Endpoints choose whether a user is required."""
    if not authorization or not authorization.lower().startswith("bearer "):
        return None
    token = authorization.split(" ", 1)[1].strip()
    try:
        payload = auth.decode_token(token)
    except auth.TokenError:
        return None
    user = db.get(User, payload.get("sub"))
    return user if (user and user.is_active) else None


def get_current_user(user: User | None = Depends(get_optional_user)) -> User:
    if user is None:
        raise HTTPException(401, "authentication required")
    return user


def _resolve_email(provided: str | None, user: User | None) -> str:
    """The email a write/read operation acts on. An authenticated user always
    acts as themselves (a supplied user_email can't override the token). Without
    a token we fall back to the supplied email -- unless REQUIRE_AUTH is on."""
    if user is not None:
        return user.email
    if REQUIRE_AUTH:
        raise HTTPException(401, "authentication required")
    if not provided:
        raise HTTPException(400, "user_email is required (or send a bearer token)")
    return provided


class Credentials(BaseModel):
    email: str
    password: str


def _user_dict(u: User) -> dict:
    return {"id": u.id, "email": u.email, "created_at": u.created_at.isoformat()
            if u.created_at else None}


@app.post("/api/v1/auth/register")
def register(creds: Credentials, db: Session = Depends(get_db)):
    email = creds.email.strip().lower()
    if not email or "@" not in email:
        raise HTTPException(400, "a valid email is required")
    if len(creds.password) < 8:
        raise HTTPException(400, "password must be at least 8 characters")
    exists = db.execute(select(User).where(User.email == email)).scalar_one_or_none()
    if exists:
        raise HTTPException(409, "an account with this email already exists")
    u = User(email=email, password_hash=auth.hash_password(creds.password))
    db.add(u); db.commit(); db.refresh(u)
    return {"token": auth.create_token(u.id), "user": _user_dict(u)}


@app.post("/api/v1/auth/login")
def login(creds: Credentials, db: Session = Depends(get_db)):
    email = creds.email.strip().lower()
    u = db.execute(select(User).where(User.email == email)).scalar_one_or_none()
    if not u or not u.is_active or not auth.verify_password(creds.password, u.password_hash):
        raise HTTPException(401, "invalid email or password")
    return {"token": auth.create_token(u.id), "user": _user_dict(u)}


@app.get("/api/v1/auth/me")
def me(user: User = Depends(get_current_user)):
    return _user_dict(user)


@app.post("/api/v1/ingest")
def ingest(db: Session = Depends(get_db)):
    return {"ingested": run_ingest(db)}


@app.get("/api/v1/connectors")
def connectors_status():
    """Which retailer connectors are live. The mock feed is always present; a
    real connector (e.g. eBay) appears only when its credentials are configured,
    so the UI can show whether live data is flowing."""
    active = sorted(get_connectors().keys())
    return {"active": active, "ebay_enabled": "ebay" in active,
            "live_data": any(name != "mockmart" for name in active)}


@app.get("/api/v1/deals")
def deals(min_score: int = 0, category: str | None = None,
          db: Session = Depends(get_db)):
    cards = all_cards(db)
    if category:
        cards = [c for c in cards if c["category"] == category]
    return [c for c in cards if c["deal_score"] >= min_score]


@app.get("/api/v1/products/{product_id}")
def product(product_id: int, db: Session = Depends(get_db)):
    p = db.get(Product, product_id)
    if not p:
        raise HTTPException(404, "product not found")
    return product_card(db, p)


@app.get("/api/v1/products/{product_id}/history")
def history(product_id: int, db: Session = Depends(get_db)):
    if not db.get(Product, product_id):
        raise HTTPException(404, "product not found")
    return history_series(db, product_id)


class AlertIn(BaseModel):
    user_email: str | None = None  # ignored when authenticated (token wins)
    product_id: int
    rule_type: str
    threshold: float | None = None


@app.post("/api/v1/alerts")
def create_alert(payload: AlertIn, db: Session = Depends(get_db),
                 user: User | None = Depends(get_optional_user)):
    valid = {"price_below", "percent_off", "lowest_ever", "back_in_stock",
             "coupon_appears", "low_inventory"}
    if payload.rule_type not in valid:
        raise HTTPException(400, f"rule_type must be one of {sorted(valid)}")
    if not db.get(Product, payload.product_id):
        raise HTTPException(404, "product not found")
    email = _resolve_email(payload.user_email, user)
    data = payload.model_dump()
    data["user_email"] = email
    a = Alert(**data)
    db.add(a); db.commit(); db.refresh(a)
    return {"id": a.id, "active": a.active}


@app.get("/api/v1/alerts")
def list_alerts(user_email: str | None = None, db: Session = Depends(get_db),
                user: User | None = Depends(get_optional_user)):
    email = _resolve_email(user_email, user)
    rows = db.execute(select(Alert).where(Alert.user_email == email)).scalars().all()
    return [{"id": a.id, "product_id": a.product_id, "rule_type": a.rule_type,
             "threshold": a.threshold, "active": a.active,
             "last_triggered_at": a.last_triggered_at} for a in rows]


@app.post("/api/v1/alerts/evaluate")
def run_alert_eval(db: Session = Depends(get_db)):
    return {"fired": evaluate_alerts(db)}


@app.get("/api/v1/alerts/events")
def alert_events(user_email: str | None = None, db: Session = Depends(get_db),
                 user: User | None = Depends(get_optional_user)):
    email = _resolve_email(user_email, user)
    rows = db.execute(
        select(AlertEvent).join(Alert).where(Alert.user_email == email)
        .order_by(AlertEvent.triggered_at.desc())
    ).scalars().all()
    return [{"alert_id": e.alert_id, "message": e.message,
             "triggered_at": e.triggered_at.isoformat(),
             "delivery_status": e.delivery_status,
             "delivery_detail": json.loads(e.delivery_detail) if e.delivery_detail else None}
            for e in rows]


# --- notification channels: where a user's fired alerts get delivered -------

class ChannelIn(BaseModel):
    user_email: str | None = None  # ignored when authenticated (token wins)
    kind: str
    target: str


def _channel_dict(c: NotificationChannel) -> dict:
    return {"id": c.id, "user_email": c.user_email, "kind": c.kind,
            "target": c.target, "active": c.active}


def _owned_channel(channel_id: int, db: Session, user: User | None) -> NotificationChannel:
    """Load a channel, enforcing ownership when a user is authenticated."""
    c = db.get(NotificationChannel, channel_id)
    if not c:
        raise HTTPException(404, "channel not found")
    if user is not None and c.user_email != user.email:
        raise HTTPException(403, "not your channel")
    if user is None and REQUIRE_AUTH:
        raise HTTPException(401, "authentication required")
    return c


@app.get("/api/v1/notifications/kinds")
def notification_kinds():
    return {"kinds": channel_kinds()}


@app.post("/api/v1/notifications/channels")
def create_channel(payload: ChannelIn, db: Session = Depends(get_db),
                   user: User | None = Depends(get_optional_user)):
    if payload.kind not in channel_kinds():
        raise HTTPException(400, f"kind must be one of {channel_kinds()}")
    if not payload.target.strip():
        raise HTTPException(400, "target is required")
    email = _resolve_email(payload.user_email, user)
    c = NotificationChannel(user_email=email, kind=payload.kind,
                            target=payload.target)
    db.add(c); db.commit(); db.refresh(c)
    return _channel_dict(c)


@app.get("/api/v1/notifications/channels")
def list_channels(user_email: str | None = None, db: Session = Depends(get_db),
                  user: User | None = Depends(get_optional_user)):
    email = _resolve_email(user_email, user)
    rows = db.execute(
        select(NotificationChannel).where(
            NotificationChannel.user_email == email)
    ).scalars().all()
    return [_channel_dict(c) for c in rows]


@app.delete("/api/v1/notifications/channels/{channel_id}")
def delete_channel(channel_id: int, db: Session = Depends(get_db),
                   user: User | None = Depends(get_optional_user)):
    c = _owned_channel(channel_id, db, user)
    db.delete(c); db.commit()
    return {"deleted": channel_id}


@app.post("/api/v1/notifications/channels/{channel_id}/test")
def test_channel(channel_id: int, db: Session = Depends(get_db),
                 user: User | None = Depends(get_optional_user)):
    """Send a synthetic notification through one channel so a user can confirm
    delivery works before relying on it for real alerts."""
    c = _owned_channel(channel_id, db, user)
    from .services.notify import get_channel
    impl = get_channel(c.kind)
    if impl is None:
        raise HTTPException(400, f"unknown channel kind {c.kind}")
    try:
        impl.send(c.target, subject="DealForge test notification",
                  body="This is a test notification from DealForge.",
                  payload={"event": "test", "channel_id": c.id})
    except Exception as exc:
        return {"ok": False, "detail": str(exc)}
    return {"ok": True}

@app.get("/api/v1/search")
def search(q: str | None = None, category: str | None = None,
           retailer: str | None = None, brand: str | None = None,
           min_price: float | None = None, max_price: float | None = None,
           min_score: int = 0, min_discount: float | None = None,
           min_rating: float | None = None, in_stock: bool | None = None,
           sort: str = "deal_score", limit: int = 100, offset: int = 0,
           db: Session = Depends(get_db)):
    return run_search(db, q=q, category=category, retailer=retailer, brand=brand,
                      min_price=min_price, max_price=max_price, min_score=min_score,
                      min_discount=min_discount, min_rating=min_rating,
                      in_stock=in_stock, sort=sort, limit=limit, offset=offset)


@app.get("/api/v1/facets")
def facets(db: Session = Depends(get_db)):
    return get_facets(db)


# --- serve the dashboard from the same origin (so it can call the API) ---
from fastapi.staticfiles import StaticFiles
import os as _os
_fe = _os.path.join(_os.path.dirname(_os.path.dirname(__file__)), "frontend")
if _os.path.isdir(_fe):
    app.mount("/", StaticFiles(directory=_fe, html=True), name="frontend")

if __name__ == "__main__":
    import uvicorn, os
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))

if __name__ == "__main__":
    import uvicorn, os
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))
