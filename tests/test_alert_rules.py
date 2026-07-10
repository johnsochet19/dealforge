from datetime import datetime, timedelta
from types import SimpleNamespace
from app.services.alerts import _evaluate
from app.services.history import compute_stats


def _stats(prices):
    now = datetime.utcnow()
    obs = [SimpleNamespace(price=p, observed_at=now - timedelta(days=len(prices)-i))
           for i, p in enumerate(prices)]
    return compute_stats(obs)


def _latest(price, **kw):
    d = dict(price=price, in_stock=True, coupon=0.0, inventory_level=None)
    d.update(kw)
    return SimpleNamespace(**d)


def test_price_below_fires_and_not():
    s = _stats([100, 90, 80])
    assert _evaluate("price_below", 85, _latest(80), s)
    assert _evaluate("price_below", 70, _latest(80), s) is None


def test_percent_off():
    s = _stats([100, 100, 100])  # avg 100
    assert _evaluate("percent_off", 30, _latest(60), s)   # 40% off
    assert _evaluate("percent_off", 50, _latest(60), s) is None


def test_lowest_ever():
    s = _stats([100, 90, 80])
    assert _evaluate("lowest_ever", None, _latest(80), s)
    assert _evaluate("lowest_ever", None, _latest(95), s) is None


def test_back_in_stock():
    s = _stats([50])
    assert _evaluate("back_in_stock", None, _latest(50, in_stock=True), s)
    assert _evaluate("back_in_stock", None, _latest(50, in_stock=False), s) is None


def test_coupon_appears():
    s = _stats([50])
    assert _evaluate("coupon_appears", None, _latest(50, coupon=5), s)
    assert _evaluate("coupon_appears", None, _latest(50, coupon=0), s) is None


def test_low_inventory():
    s = _stats([50])
    assert _evaluate("low_inventory", 10, _latest(50, inventory_level=3), s)
    assert _evaluate("low_inventory", 10, _latest(50, inventory_level=40), s) is None
    assert _evaluate("low_inventory", 10, _latest(50, inventory_level=None), s) is None


def test_unknown_rule_returns_none():
    s = _stats([50])
    assert _evaluate("bogus", 1, _latest(50), s) is None
