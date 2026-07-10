"""Shopping Assistant: grounded answers over seeded catalog data."""
from fastapi.testclient import TestClient
from app.main import app
from app.db import SessionLocal
from app.services.ingest import run_ingest
from app.services.assistant import _budget, _pick_terms

client = TestClient(app)


def _seed(rounds=6):
    s = SessionLocal()
    for _ in range(rounds):
        run_ingest(s)
    s.close()


# --- intent parsing ---------------------------------------------------------

def test_budget_parsing():
    assert _budget("best laptop under $900") == 900.0
    assert _budget("tv below 1,500") == 1500.0
    assert _budget("cheapest headphones") is None


def test_category_and_term_parsing():
    cat, term = _pick_terms("what is the best laptop under $900?")
    assert cat == "Computers"
    cat2, _ = _pick_terms("what tv should i buy")
    assert cat2 == "Electronics"


# --- API grounded answers ---------------------------------------------------

def test_best_under_budget_returns_picks():
    _seed()
    r = client.post("/api/v1/assistant",
                    json={"question": "what is the best laptop under $1200?"})
    body = r.json()
    assert body["intent"] == "recommend"
    assert body["picks"]
    # every pick is a real product with a score, priced within budget
    for p in body["picks"]:
        assert p["price"] <= 1200
        assert 0 <= p["deal_score"] <= 100


def test_is_this_a_good_deal():
    _seed()
    pid = client.get("/api/v1/deals").json()[0]["id"]
    r = client.post("/api/v1/assistant",
                    json={"question": "is this a good deal?", "product_id": pid})
    body = r.json()
    assert body["intent"] == "evaluate_product"
    assert body["answer"]["product_id"] == pid
    assert body["answer"]["verdict"]


def test_likely_to_go_on_sale():
    _seed()
    r = client.post("/api/v1/assistant",
                    json={"question": "what products are likely to go on sale?"})
    body = r.json()
    assert body["intent"] == "likely_sales"
    assert "picks" in body


def test_unknown_question_is_helpful():
    _seed(1)
    r = client.post("/api/v1/assistant", json={"question": "hello there"})
    assert r.json()["intent"] in ("unknown", "recommend")
