"""Slack/Discord/Telegram channels, CSV reports, and rate limiting."""
import csv
import io

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.db import SessionLocal
from app.services.ingest import run_ingest
from app.services import notify
from app.services.notify import (
    SlackChannel, DiscordChannel, TelegramChannel, DeliveryError, channel_kinds,
)
from app import ratelimit

client = TestClient(app)


def _seed(rounds=4):
    s = SessionLocal()
    for _ in range(rounds):
        run_ingest(s)
    s.close()


# --- new channels -----------------------------------------------------------

def test_all_channel_kinds_registered():
    assert set(channel_kinds()) == {"webhook", "email", "slack", "discord", "telegram"}


def test_slack_channel_formats_text(monkeypatch):
    cap = {}
    monkeypatch.setattr(notify, "_http_post",
                        lambda url, payload, timeout=5.0: cap.update(url=url, payload=payload) or 200)
    SlackChannel().send("https://hooks.slack.com/x", subject="Deal", body="cheap",
                        payload={})
    assert cap["url"].startswith("https://hooks.slack.com")
    assert "Deal" in cap["payload"]["text"]


def test_discord_channel_uses_content(monkeypatch):
    cap = {}
    monkeypatch.setattr(notify, "_http_post",
                        lambda url, payload, timeout=5.0: cap.update(payload=payload) or 200)
    DiscordChannel().send("https://discord.com/api/webhooks/x", subject="Deal",
                          body="cheap", payload={})
    assert "content" in cap["payload"]


def test_telegram_requires_token(monkeypatch):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    with pytest.raises(DeliveryError):
        TelegramChannel().send("12345", subject="s", body="b", payload={})


def test_telegram_builds_api_url(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "TOK")
    cap = {}
    monkeypatch.setattr(notify, "_http_post",
                        lambda url, payload, timeout=5.0: cap.update(url=url, payload=payload) or 200)
    TelegramChannel().send("999", subject="Deal", body="cheap", payload={})
    assert cap["url"] == "https://api.telegram.org/botTOK/sendMessage"
    assert cap["payload"]["chat_id"] == "999"


def test_slack_channel_via_api_create():
    r = client.post("/api/v1/notifications/channels",
                    json={"user_email": "slack@e.com", "kind": "slack",
                          "target": "https://hooks.slack.com/services/x"})
    assert r.status_code == 200


# --- CSV reports ------------------------------------------------------------

def test_deals_csv_report():
    _seed()
    r = client.get("/api/v1/reports/deals.csv")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/csv")
    rows = list(csv.reader(io.StringIO(r.text)))
    assert rows[0][:4] == ["product_id", "title", "brand", "category"]
    assert len(rows) > 1  # header + data


def test_rankings_csv_report():
    _seed()
    r = client.get("/api/v1/reports/rankings.csv", params={"dimension": "categories"})
    assert r.status_code == 200
    assert "avg_deal_score" in r.text.splitlines()[0]


def test_rankings_csv_bad_dimension():
    assert client.get("/api/v1/reports/rankings.csv",
                      params={"dimension": "bogus"}).status_code == 400


# --- rate limiting ----------------------------------------------------------

def test_rate_limit_enforced():
    ratelimit.reset()
    ratelimit.set_limit(3)
    try:
        codes = [client.get("/health").status_code for _ in range(5)]
    finally:
        ratelimit.set_limit(0)
        ratelimit.reset()
    assert codes[:3] == [200, 200, 200]
    assert 429 in codes[3:]


def test_rate_limit_disabled_by_default():
    ratelimit.reset()
    ratelimit.set_limit(0)
    codes = [client.get("/health").status_code for _ in range(10)]
    assert all(c == 200 for c in codes)
