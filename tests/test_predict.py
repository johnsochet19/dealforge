"""Price Prediction AI: explainable forecast over synthetic-but-real series."""
from datetime import datetime, timedelta
from types import SimpleNamespace

from app.services.predict import forecast, _linreg, _count_dips
from app.services.history import compute_stats


def _series(prices, start_days_ago=None):
    n = len(prices)
    start = start_days_ago or n
    now = datetime(2026, 1, 1)
    obs = [SimpleNamespace(price=p, observed_at=now - timedelta(days=start - i))
           for i, p in enumerate(prices)]
    return obs, compute_stats(obs, now=now), now


def test_linreg_slope():
    slope, intercept = _linreg([0, 1, 2, 3], [10, 12, 14, 16])
    assert round(slope, 3) == 2.0
    assert round(intercept, 3) == 10.0


def test_falling_prices_predict_wait():
    prices = [200, 195, 190, 185, 180, 175, 170]
    obs, stats, now = _series(prices)
    fc = forecast(obs, stats, now=now)
    assert fc.trend_per_day < 0
    assert fc.probability_lower > 0.5
    assert fc.recommendation in ("wait", "buy_now")  # falling & near low
    assert fc.expected_price <= prices[-1]


def test_at_historical_low_flat_predicts_buy():
    prices = [90, 88, 85, 85, 85, 85]  # settled flat at the historical low
    obs, stats, now = _series(prices)
    fc = forecast(obs, stats, now=now)
    assert fc.recommendation == "buy_now"
    assert any("floor" in r.lower() or "low" in r.lower() for r in fc.rationale)


def test_probability_bounds_and_confidence():
    prices = [50 + (i % 5) for i in range(40)]
    obs, stats, now = _series(prices)
    fc = forecast(obs, stats, now=now)
    assert 0.0 <= fc.probability_lower <= 1.0
    assert 0.0 <= fc.confidence <= 1.0
    assert fc.expected_savings_if_waiting >= 0.0


def test_expected_price_clamped_to_band():
    # steep decline shouldn't project a negative/absurd price
    prices = [500, 400, 300, 200, 100]
    obs, stats, now = _series(prices)
    fc = forecast(obs, stats, now=now)
    assert fc.expected_price >= stats.lowest * 0.85


def test_next_sale_from_dip_cadence():
    # regular dips every ~4 points -> a cadence estimate should appear
    prices = []
    for _ in range(6):
        prices += [100, 100, 100, 80]
    obs, stats, now = _series(prices)
    fc = forecast(obs, stats, now=now)
    assert fc.next_sale_in_days is not None


def test_count_dips_counts_transitions():
    pairs = [(i, p) for i, p in enumerate([100, 100, 80, 100, 100, 80, 100])]
    assert _count_dips(pairs) == 2


def test_forecast_endpoint_and_card_prediction():
    from fastapi.testclient import TestClient
    from app.main import app
    from app.db import SessionLocal
    from app.services.ingest import run_ingest
    client = TestClient(app)
    s = SessionLocal()
    for _ in range(6):
        run_ingest(s)
    s.close()
    card = client.get("/api/v1/deals").json()[0]
    assert "prediction" in card
    assert card["prediction"]["recommendation"] in ("buy_now", "wait", "consider")
    fc = client.get(f"/api/v1/products/{card['id']}/forecast").json()
    assert "rationale" in fc and isinstance(fc["rationale"], list)
    assert 0.0 <= fc["probability_lower"] <= 1.0
