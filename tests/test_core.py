from datetime import datetime, timedelta
from types import SimpleNamespace
from app.services.history import compute_stats
from app.services.scoring import deal_score, recommendation


def _obs(prices, start_days_ago=100):
    now = datetime.utcnow()
    n = len(prices)
    return [SimpleNamespace(price=p,
            observed_at=now - timedelta(days=start_days_ago * (n - i) / n))
            for i, p in enumerate(prices)]


def test_stats_basic():
    s = compute_stats(_obs([100, 90, 80, 120, 100]))
    assert s.lowest == 80 and s.highest == 120
    assert s.n == 5
    assert 90 <= s.average <= 100
    assert s.current == 100


def test_stats_single_obs_zero_volatility():
    s = compute_stats(_obs([50]))
    assert s.volatility == 0.0
    assert s.lowest == s.highest == 50


def test_stats_empty_raises():
    import pytest
    with pytest.raises(ValueError):
        compute_stats([])


def test_score_at_historical_low_is_high():
    # current sits at the historical low, rare discounts, good quality
    s = compute_stats(_obs([200, 190, 180, 210, 150]))
    score, bd = deal_score(s, rating=4.7, review_count=5000,
                           seller_reputation=0.95, coupon=10)
    assert score >= 70
    assert set(bd) == {"price_position", "vs_recent_avg", "discount_rarity",
                       "at_or_near_low", "quality", "coupon"}


def test_score_at_historical_high_is_low():
    s = compute_stats(_obs([100, 105, 110, 108, 120]))  # current = high
    score, _ = deal_score(s, rating=4.0, review_count=100)
    assert score <= 45


def test_score_bounds():
    s = compute_stats(_obs([100, 100, 100]))
    score, _ = deal_score(s)
    assert 0 <= score <= 100


def test_recommendation_rules():
    low = compute_stats(_obs([200, 180, 150]))
    assert recommendation(low, 85) == "buy_now"
    high = compute_stats(_obs([100, 110, 130]))
    assert recommendation(high, 30) == "wait"
