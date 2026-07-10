"""Notification delivery: dispatcher + channels, exercised end-to-end with the
network seams (_http_post / _smtp_send) monkeypatched so no real I/O happens."""
from datetime import datetime
from types import SimpleNamespace

import pytest

from app.services import notify
from app.services.notify import (
    WebhookChannel, EmailChannel, DeliveryError, dispatch, channel_kinds,
)
from app.models import NotificationChannel, Alert, AlertEvent, Product
from app.db import SessionLocal, Base, engine


# --- channel unit tests -----------------------------------------------------

def test_webhook_channel_posts(monkeypatch):
    captured = {}

    def fake_post(url, payload, timeout=5.0):
        captured["url"] = url
        captured["payload"] = payload
        return 200

    monkeypatch.setattr(notify, "_http_post", fake_post)
    WebhookChannel().send("https://hook.example/x", subject="s", body="b",
                          payload={"message": "hi"})
    assert captured["url"] == "https://hook.example/x"
    assert captured["payload"]["message"] == "hi"


def test_webhook_channel_wraps_errors(monkeypatch):
    def boom(url, payload, timeout=5.0):
        raise RuntimeError("connection refused")

    monkeypatch.setattr(notify, "_http_post", boom)
    with pytest.raises(DeliveryError):
        WebhookChannel().send("https://x", subject="s", body="b", payload={})


def test_email_channel_unconfigured_raises(monkeypatch):
    monkeypatch.delenv("SMTP_HOST", raising=False)
    with pytest.raises(DeliveryError):
        EmailChannel().send("a@b.com", subject="s", body="b", payload={})


def test_email_channel_sends_when_configured(monkeypatch):
    monkeypatch.setenv("SMTP_HOST", "smtp.example.com")
    monkeypatch.setenv("SMTP_FROM", "alerts@dealforge.test")
    sent = {}

    def fake_smtp(msg, **kw):
        sent["to"] = msg["To"]
        sent["subject"] = msg["Subject"]
        sent["host"] = kw["host"]

    monkeypatch.setattr(notify, "_smtp_send", fake_smtp)
    EmailChannel().send("buyer@b.com", subject="Deal!", body="cheap now",
                        payload={})
    assert sent["to"] == "buyer@b.com"
    assert sent["subject"] == "Deal!"
    assert sent["host"] == "smtp.example.com"


def test_registered_kinds():
    assert set(channel_kinds()) == {"webhook", "email"}


# --- dispatcher tests (against the DB) --------------------------------------

@pytest.fixture
def db():
    Base.metadata.create_all(bind=engine)
    s = SessionLocal()
    yield s
    s.rollback()
    s.close()


def _seed_alert_event(db, email="u@e.com"):
    p = Product(retailer="mockmart", external_id="X1", title="Widget")
    db.add(p); db.flush()
    a = Alert(user_email=email, product_id=p.id, rule_type="lowest_ever")
    db.add(a); db.flush()
    e = AlertEvent(alert_id=a.id, message="New low!", triggered_at=datetime.utcnow())
    db.add(e); db.flush()
    return a, e, p


def test_dispatch_no_channels(db):
    a, e, p = _seed_alert_event(db, "nochan@e.com")
    status = dispatch(db, a, e, product=p)
    assert status == "no_channels"
    assert e.delivery_status == "no_channels"


def test_dispatch_sent(db, monkeypatch):
    a, e, p = _seed_alert_event(db, "sent@e.com")
    db.add(NotificationChannel(user_email="sent@e.com", kind="webhook",
                               target="https://hook/x"))
    db.flush()
    posted = []
    monkeypatch.setattr(notify, "_http_post",
                        lambda url, payload, timeout=5.0: posted.append(url) or 200)
    status = dispatch(db, a, e, product=p)
    assert status == "sent"
    assert posted == ["https://hook/x"]
    assert e.delivery_status == "sent"


def test_dispatch_partial(db, monkeypatch):
    a, e, p = _seed_alert_event(db, "part@e.com")
    db.add(NotificationChannel(user_email="part@e.com", kind="webhook",
                               target="https://good/x"))
    db.add(NotificationChannel(user_email="part@e.com", kind="email",
                               target="x@y.com"))
    db.flush()
    monkeypatch.setattr(notify, "_http_post", lambda *a, **k: 200)
    monkeypatch.delenv("SMTP_HOST", raising=False)  # email will fail
    status = dispatch(db, a, e, product=p)
    assert status == "partial"
    import json
    detail = json.loads(e.delivery_detail)
    kinds = {d["kind"]: d["status"] for d in detail}
    assert kinds == {"webhook": "sent", "email": "failed"}


def test_dispatch_all_failed(db, monkeypatch):
    a, e, p = _seed_alert_event(db, "fail@e.com")
    db.add(NotificationChannel(user_email="fail@e.com", kind="webhook",
                               target="https://bad/x"))
    db.flush()

    def boom(*a, **k):
        raise DeliveryError("nope")

    monkeypatch.setattr(notify, "_http_post", boom)
    status = dispatch(db, a, e, product=p)
    assert status == "failed"
    assert e.delivery_status == "failed"


# --- API tests: channel CRUD + delivery inside the alert lifecycle -----------

from fastapi.testclient import TestClient
from app.main import app
from app.services.ingest import run_ingest

client = TestClient(app)


def _seed_products(rounds=3):
    s = SessionLocal()
    for _ in range(rounds):
        run_ingest(s)
    s.close()


def test_channel_crud():
    email = "crud@e.com"
    r = client.post("/api/v1/notifications/channels",
                    json={"user_email": email, "kind": "webhook",
                          "target": "https://hook/y"})
    assert r.status_code == 200
    cid = r.json()["id"]

    listed = client.get("/api/v1/notifications/channels",
                        params={"user_email": email}).json()
    assert any(c["id"] == cid for c in listed)

    d = client.delete(f"/api/v1/notifications/channels/{cid}")
    assert d.status_code == 200
    remaining = client.get("/api/v1/notifications/channels",
                           params={"user_email": email}).json()
    assert all(c["id"] != cid for c in remaining)


def test_channel_bad_kind_rejected():
    r = client.post("/api/v1/notifications/channels",
                    json={"user_email": "z@e.com", "kind": "carrier_pigeon",
                          "target": "nest"})
    assert r.status_code == 400


def test_alert_fires_and_delivers_via_webhook(monkeypatch):
    _seed_products(4)
    email = "deliver@e.com"
    pid = client.get("/api/v1/deals").json()[0]["id"]
    # a webhook channel + a guaranteed-firing alert
    client.post("/api/v1/notifications/channels",
                json={"user_email": email, "kind": "webhook",
                      "target": "https://hook/deliver"})
    alert_id = client.post("/api/v1/alerts", json={
        "user_email": email, "product_id": pid,
        "rule_type": "price_below", "threshold": 999999}).json()["id"]

    posted = []
    monkeypatch.setattr(notify, "_http_post",
                        lambda url, payload, timeout=5.0: posted.append(payload) or 200)
    fired = client.post("/api/v1/alerts/evaluate").json()["fired"]
    mine = [f for f in fired if f["alert_id"] == alert_id]
    assert mine and mine[0]["delivery_status"] == "sent"
    assert posted and posted[0]["event"] == "alert_fired"

    events = client.get("/api/v1/alerts/events",
                        params={"user_email": email}).json()
    assert events[0]["delivery_status"] == "sent"


def test_test_channel_endpoint(monkeypatch):
    r = client.post("/api/v1/notifications/channels",
                    json={"user_email": "t@e.com", "kind": "webhook",
                          "target": "https://hook/test"})
    cid = r.json()["id"]
    monkeypatch.setattr(notify, "_http_post", lambda *a, **k: 200)
    res = client.post(f"/api/v1/notifications/channels/{cid}/test").json()
    assert res["ok"] is True
