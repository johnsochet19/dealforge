"""eBay Browse connector: item mapping, OAuth token handling, search/fetch,
and env-driven registration -- exercised with the HTTP seams monkeypatched so
no network or real credentials are needed."""
import pytest

from app.connectors import ebay_browse as eb
from app.connectors.ebay_browse import EbayBrowseConnector, map_item, make_from_env


SAMPLE_ITEM = {
    "itemId": "v1|123456789|0",
    "title": "Dell XPS 13 Laptop 16GB/512GB",
    "price": {"value": "899.99", "currency": "USD"},
    "categories": [{"categoryId": "177", "categoryName": "PC Laptops & Netbooks"}],
    "seller": {"username": "topseller", "feedbackPercentage": "99.4",
               "feedbackScore": 12000},
    "marketingPrice": {"originalPrice": {"value": "1099.99", "currency": "USD"},
                       "discountPercentage": "18"},
    "image": {"imageUrl": "https://i.ebayimg.com/images/g/x/s-l500.jpg"},
    "itemWebUrl": "https://www.ebay.com/itm/123456789",
    "brand": "Dell",
}


# --- mapping ----------------------------------------------------------------

def test_map_item_full():
    rec = map_item(SAMPLE_ITEM)
    assert rec.external_id == "v1|123456789|0"
    assert rec.title.startswith("Dell XPS")
    assert rec.price == 899.99
    assert rec.brand == "Dell"
    assert rec.category == "PC Laptops & Netbooks"
    assert rec.url == "https://www.ebay.com/itm/123456789"
    assert rec.image_url.endswith("s-l500.jpg")
    assert rec.msrp == 1099.99
    assert abs(rec.seller_reputation - 0.994) < 1e-6
    assert rec.in_stock is True


def test_map_item_minimal():
    rec = map_item({"itemId": "x", "title": "Thing",
                    "price": {"value": "10.00"}})
    assert rec.price == 10.0
    assert rec.brand == ""
    assert rec.category == ""
    assert rec.msrp is None
    assert rec.seller_reputation is None


@pytest.mark.parametrize("bad", [
    {"title": "no id", "price": {"value": "1"}},
    {"itemId": "x", "price": {"value": "1"}},               # no title
    {"itemId": "x", "title": "y"},                          # no price
    {"itemId": "x", "title": "y", "price": {"value": "n/a"}},  # unparseable
])
def test_map_item_rejects_incomplete(bad):
    assert map_item(bad) is None


# --- token handling ---------------------------------------------------------

def test_static_token_used_directly(monkeypatch):
    called = []
    monkeypatch.setattr(eb, "_http_post_form",
                        lambda *a, **k: called.append(1) or {})
    c = EbayBrowseConnector(token="STATIC")
    assert c._fetch_token() == "STATIC"
    assert not called  # never hit the token endpoint


def test_token_minted_and_cached(monkeypatch):
    calls = []

    def fake_post(url, *, data, headers, timeout=10.0):
        calls.append(url)
        return {"access_token": "TOK", "expires_in": 7200}

    monkeypatch.setattr(eb, "_http_post_form", fake_post)
    c = EbayBrowseConnector(client_id="id", client_secret="secret")
    assert c._fetch_token(now=1000) == "TOK"
    assert c._fetch_token(now=1100) == "TOK"     # cached, no second call
    assert len(calls) == 1
    # after expiry it re-mints
    assert c._fetch_token(now=1000 + 7200) == "TOK"
    assert len(calls) == 2


def test_token_requires_credentials():
    c = EbayBrowseConnector()
    with pytest.raises(RuntimeError):
        c._fetch_token()


# --- fetch ------------------------------------------------------------------

def test_fetch_maps_and_dedupes(monkeypatch):
    monkeypatch.setattr(eb, "_http_post_form",
                        lambda *a, **k: {"access_token": "T", "expires_in": 7200})

    dup = dict(SAMPLE_ITEM)
    other = dict(SAMPLE_ITEM, itemId="v1|999|0", title="Other Laptop")

    def fake_get(url, *, params, headers, timeout=10.0):
        # same item appears for both queries -> should be deduped
        return {"itemSummaries": [SAMPLE_ITEM, dup, other]}

    monkeypatch.setattr(eb, "_http_get_json", fake_get)
    c = EbayBrowseConnector(client_id="id", client_secret="s",
                            queries=["laptop", "notebook"])
    recs = list(c.fetch())
    ids = [r.external_id for r in recs]
    assert ids.count("v1|123456789|0") == 1     # deduped across queries
    assert "v1|999|0" in ids


def test_fetch_survives_token_failure(monkeypatch):
    def boom(*a, **k):
        raise httpx_error()

    monkeypatch.setattr(eb, "_http_post_form", boom)
    c = EbayBrowseConnector(client_id="id", client_secret="s")
    assert list(c.fetch()) == []               # no crash, just empty


def test_fetch_survives_one_bad_query(monkeypatch):
    monkeypatch.setattr(eb, "_http_post_form",
                        lambda *a, **k: {"access_token": "T", "expires_in": 7200})
    calls = {"n": 0}

    def flaky_get(url, *, params, headers, timeout=10.0):
        calls["n"] += 1
        if params["q"] == "bad":
            raise httpx_error()
        return {"itemSummaries": [SAMPLE_ITEM]}

    monkeypatch.setattr(eb, "_http_get_json", flaky_get)
    c = EbayBrowseConnector(client_id="id", client_secret="s",
                            queries=["bad", "good"])
    recs = list(c.fetch())
    assert calls["n"] == 2                      # both queries attempted
    assert len(recs) == 1                       # good query still yielded


def httpx_error():
    return RuntimeError("network down")


# --- env registration -------------------------------------------------------

def test_make_from_env_none_without_creds(monkeypatch):
    for k in ("EBAY_CLIENT_ID", "EBAY_CLIENT_SECRET", "EBAY_OAUTH_TOKEN"):
        monkeypatch.delenv(k, raising=False)
    assert make_from_env() is None


def test_make_from_env_builds_with_creds(monkeypatch):
    monkeypatch.setenv("EBAY_CLIENT_ID", "id")
    monkeypatch.setenv("EBAY_CLIENT_SECRET", "secret")
    monkeypatch.setenv("EBAY_QUERIES", "phone, tablet ,")
    monkeypatch.setenv("EBAY_MARKETPLACE", "EBAY_GB")
    c = make_from_env()
    assert isinstance(c, EbayBrowseConnector)
    assert c.queries == ["phone", "tablet"]
    assert c.marketplace == "EBAY_GB"


def test_ebay_not_registered_by_default():
    # with no eBay env configured, only the mock feed is registered
    from app.connectors.base import get_connectors
    assert "ebay" not in get_connectors()
    assert "mockmart" in get_connectors()


def test_connectors_status_endpoint():
    from fastapi.testclient import TestClient
    from app.main import app
    body = TestClient(app).get("/api/v1/connectors").json()
    assert "mockmart" in body["active"]
    # no eBay creds in the test env -> not enabled, no live data
    assert body["ebay_enabled"] is False
    assert body["live_data"] is False


def test_ebay_connector_integrates_with_ingest(monkeypatch):
    """End-to-end through run_ingest: a registered eBay connector's items are
    upserted as products under the 'ebay' retailer with mapped fields."""
    from sqlalchemy import create_engine, select
    from sqlalchemy.orm import sessionmaker
    from app.db import Base
    from app.models import Product, PriceObservation
    from app.connectors.base import register, _REGISTRY
    from app.services.ingest import run_ingest

    monkeypatch.setattr(eb, "_http_get_json",
                        lambda url, **k: {"itemSummaries": [SAMPLE_ITEM]})
    conn = EbayBrowseConnector(token="T", queries=["laptop"])

    # isolated in-memory DB so we don't touch the shared file DB
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    db = Session()

    # temporarily register only our connector for this ingest
    saved = dict(_REGISTRY)
    _REGISTRY.clear()
    register(conn)
    try:
        counts = run_ingest(db)
    finally:
        _REGISTRY.clear()
        _REGISTRY.update(saved)

    assert counts["ebay"] == 1
    p = db.execute(select(Product).where(Product.retailer == "ebay")).scalar_one()
    assert p.title.startswith("Dell XPS")
    assert p.external_id == "v1|123456789|0"
    obs = db.execute(select(PriceObservation)
                     .where(PriceObservation.product_id == p.id)).scalars().all()
    assert obs and obs[0].price == 899.99
    db.close()
