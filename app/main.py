"""DealForge AI -- API entrypoint (v1).

Real, working endpoints over the core platform: ingestion, deal cards with
explainable scores, price history, and CRUD + evaluation for alerts.
"""
from fastapi import FastAPI, Depends, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from .db import Base, engine, get_db
from . import models  # noqa: F401  (register tables)
from . import connectors  # noqa: F401  (register connectors)
from .services.ingest import run_ingest
from .services.deals import all_cards, product_card, history_series
from .services.alerts import evaluate_alerts
from .services.search import search as run_search, facets as get_facets
from .models import Alert, Product, AlertEvent

Base.metadata.create_all(bind=engine)

app = FastAPI(title="DealForge AI", version="1.0.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"],
                   allow_headers=["*"])


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/api/v1/ingest")
def ingest(db: Session = Depends(get_db)):
    return {"ingested": run_ingest(db)}


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
    user_email: str
    product_id: int
    rule_type: str
    threshold: float | None = None


@app.post("/api/v1/alerts")
def create_alert(payload: AlertIn, db: Session = Depends(get_db)):
    valid = {"price_below", "percent_off", "lowest_ever", "back_in_stock",
             "coupon_appears", "low_inventory"}
    if payload.rule_type not in valid:
        raise HTTPException(400, f"rule_type must be one of {sorted(valid)}")
    if not db.get(Product, payload.product_id):
        raise HTTPException(404, "product not found")
    a = Alert(**payload.model_dump())
    db.add(a); db.commit(); db.refresh(a)
    return {"id": a.id, "active": a.active}


@app.get("/api/v1/alerts")
def list_alerts(user_email: str, db: Session = Depends(get_db)):
    rows = db.execute(select(Alert).where(Alert.user_email == user_email)).scalars().all()
    return [{"id": a.id, "product_id": a.product_id, "rule_type": a.rule_type,
             "threshold": a.threshold, "active": a.active,
             "last_triggered_at": a.last_triggered_at} for a in rows]


@app.post("/api/v1/alerts/evaluate")
def run_alert_eval(db: Session = Depends(get_db)):
    return {"fired": evaluate_alerts(db)}


@app.get("/api/v1/alerts/events")
def alert_events(user_email: str, db: Session = Depends(get_db)):
    rows = db.execute(
        select(AlertEvent).join(Alert).where(Alert.user_email == user_email)
        .order_by(AlertEvent.triggered_at.desc())
    ).scalars().all()
    return [{"alert_id": e.alert_id, "message": e.message,
             "triggered_at": e.triggered_at.isoformat()} for e in rows]

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
