import os, tempfile
os.environ["DATABASE_URL"] = "sqlite:///" + tempfile.mktemp(suffix=".db")

from fastapi.testclient import TestClient
from app.main import app
from app.db import SessionLocal
from app.services.ingest import run_ingest

client = TestClient(app)


def _seed(rounds=6):
    db = SessionLocal()
    for _ in range(rounds):
        run_ingest(db)
    db.close()


def test_search_returns_envelope():
    _seed()
    r = client.get("/api/v1/search").json()
    assert set(r) == {"total", "count", "offset", "results"}
    assert r["total"] == len(client.get("/api/v1/deals").json())


def test_text_query_filters():
    _seed()
    r = client.get("/api/v1/search", params={"q": "laptop"}).json()
    assert r["count"] >= 1
    assert all("laptop" in x["title"].lower() or "laptop" in x["external_id"].lower()
               for x in r["results"])


def test_price_bounds():
    _seed()
    r = client.get("/api/v1/search", params={"max_price": 300}).json()
    assert all(x["stats"]["current"] <= 300 for x in r["results"])


def test_category_filter():
    _seed()
    r = client.get("/api/v1/search", params={"category": "Computers"}).json()
    assert all(x["category"] == "Computers" for x in r["results"])


def test_sort_price_low_ascending():
    _seed()
    r = client.get("/api/v1/search", params={"sort": "price_low"}).json()
    prices = [x["stats"]["current"] for x in r["results"]]
    assert prices == sorted(prices)


def test_pagination():
    _seed()
    full = client.get("/api/v1/search").json()
    page = client.get("/api/v1/search", params={"limit": 3, "offset": 0}).json()
    assert page["count"] <= 3
    assert page["total"] == full["total"]


def test_facets_populated():
    _seed()
    f = client.get("/api/v1/facets").json()
    assert "Computers" in f["categories"]
    assert "mockmart" in f["retailers"]
    assert len(f["brands"]) > 0


def test_discount_field_present():
    _seed()
    r = client.get("/api/v1/search").json()
    assert "discount_pct" in r["results"][0]
