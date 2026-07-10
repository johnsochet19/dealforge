import os, tempfile
os.environ["DATABASE_URL"] = "sqlite:///" + tempfile.mktemp(suffix=".db")

from fastapi.testclient import TestClient
from app.main import app
from app.db import SessionLocal
from app.services.ingest import run_ingest

client = TestClient(app)


def _seed(rounds=5):
    db = SessionLocal()
    for _ in range(rounds):
        run_ingest(db)
    db.close()


def test_health():
    assert client.get("/health").json()["status"] == "ok"


def test_ingest_and_deals():
    _seed()
    r = client.get("/api/v1/deals")
    assert r.status_code == 200
    cards = r.json()
    assert len(cards) > 0
    top = cards[0]
    assert 0 <= top["deal_score"] <= 100
    assert "score_breakdown" in top
    # sorted descending
    scores = [c["deal_score"] for c in cards]
    assert scores == sorted(scores, reverse=True)


def test_history_endpoint():
    _seed(3)
    pid = client.get("/api/v1/deals").json()[0]["id"]
    hist = client.get(f"/api/v1/products/{pid}/history").json()
    assert len(hist) >= 3
    assert "price" in hist[0]


def test_alert_lifecycle_fires():
    _seed(4)
    pid = client.get("/api/v1/deals").json()[0]["id"]
    # price_below with a huge threshold guarantees a fire
    res = client.post("/api/v1/alerts", json={
        "user_email": "a@b.com", "product_id": pid,
        "rule_type": "price_below", "threshold": 999999})
    assert res.status_code == 200
    fired = client.post("/api/v1/alerts/evaluate").json()["fired"]
    assert any(f["product_id"] == pid for f in fired)
    events = client.get("/api/v1/alerts/events", params={"user_email": "a@b.com"}).json()
    assert len(events) >= 1


def test_alert_bad_rule_rejected():
    _seed(1)
    pid = client.get("/api/v1/deals").json()[0]["id"]
    res = client.post("/api/v1/alerts", json={
        "user_email": "x@y.com", "product_id": pid,
        "rule_type": "nonsense", "threshold": 1})
    assert res.status_code == 400


def test_missing_product_404():
    assert client.get("/api/v1/products/999999").status_code == 404
