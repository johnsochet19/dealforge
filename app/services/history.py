"""Price-history aggregation. Pure functions over observation lists so they are
trivially unit-testable and independent of the ORM.

In Postgres these aggregates would be served by windowed SQL / continuous
aggregates (e.g. TimescaleDB) rather than pulling rows into Python; the math is
identical and lives here as the single source of truth.
"""
from dataclasses import dataclass
from datetime import datetime, timedelta
from statistics import median, pstdev


@dataclass
class PriceStats:
    current: float
    lowest: float
    highest: float
    average: float
    median: float
    avg_30d: float | None
    avg_90d: float | None
    avg_365d: float | None
    volatility: float          # coefficient of variation, 0+
    discount_frequency: float  # fraction of observations below trailing avg
    n: int


def _window_avg(pairs, now, days):
    cutoff = now - timedelta(days=days)
    vals = [p for t, p in pairs if t >= cutoff]
    return round(sum(vals) / len(vals), 2) if vals else None


def compute_stats(observations, now: datetime | None = None) -> PriceStats:
    """observations: iterable of objects with .price and .observed_at."""
    now = now or datetime.utcnow()
    pairs = sorted(((o.observed_at, o.price) for o in observations),
                   key=lambda x: x[0])
    if not pairs:
        raise ValueError("no observations")
    prices = [p for _, p in pairs]
    avg = sum(prices) / len(prices)
    vol = round(pstdev(prices) / avg, 4) if avg and len(prices) > 1 else 0.0
    below = sum(1 for p in prices if p < avg)
    return PriceStats(
        current=prices[-1],
        lowest=min(prices),
        highest=max(prices),
        average=round(avg, 2),
        median=round(median(prices), 2),
        avg_30d=_window_avg(pairs, now, 30),
        avg_90d=_window_avg(pairs, now, 90),
        avg_365d=_window_avg(pairs, now, 365),
        volatility=vol,
        discount_frequency=round(below / len(prices), 4),
        n=len(prices),
    )
