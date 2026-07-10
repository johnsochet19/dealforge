"""Price Prediction -- transparent, explainable forecasting over real history.

This is deliberately NOT a black-box model. Every output is a documented
function of the stored price series, in the same spirit as the explainable Deal
Score: a platform that tells you to "wait" should be able to say *why*. It needs
no training data and no external ML service, so it runs anywhere the app runs,
and it grows more confident as more history accumulates.

Method, in brief:
- fit a least-squares trend line over a recent window of observations;
- project it forward `horizon_days` to an expected price (clamped to a sane band
  around the historical range);
- estimate the probability of a lower price from how often the item has been
  cheaper than now, tilted by the trend direction;
- estimate the next likely sale from the historical cadence of price dips.
"""
from dataclasses import dataclass, field
from datetime import timedelta
from statistics import mean


@dataclass
class Forecast:
    recommendation: str            # buy_now | wait | consider
    expected_price: float          # projected price `horizon_days` out
    horizon_days: int
    probability_lower: float       # 0..1: chance of a lower price within horizon
    expected_savings_if_waiting: float
    next_sale_in_days: int | None  # from historical dip cadence, or None
    trend_per_day: float           # $/day slope of the recent window
    confidence: float              # 0..1: grows with history, shrinks with noise
    rationale: list[str] = field(default_factory=list)


def _clamp(x, lo, hi):
    return max(lo, min(hi, x))


def _linreg(xs, ys):
    """Ordinary least squares. Returns (slope, intercept)."""
    n = len(xs)
    if n < 2:
        return 0.0, (ys[-1] if ys else 0.0)
    mx, my = mean(xs), mean(ys)
    denom = sum((x - mx) ** 2 for x in xs)
    if denom == 0:
        return 0.0, my
    slope = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / denom
    return slope, my - slope * mx


def _count_dips(pairs):
    """Number of times the series crossed from >= trailing-average to a local
    discount (a price meaningfully below the running average). A rough but
    honest proxy for how often this item goes on sale."""
    prices = [p for _, p in pairs]
    if len(prices) < 3:
        return 0
    dips, below = 0, False
    running = []
    for p in prices:
        running.append(p)
        avg = mean(running)
        if p < avg * 0.97:
            if not below:
                dips += 1
            below = True
        else:
            below = False
    return dips


def forecast(observations, stats, horizon_days: int = 30, now=None) -> Forecast:
    """observations: objects with .observed_at and .price. stats: PriceStats."""
    pairs = sorted(((o.observed_at, o.price) for o in observations),
                   key=lambda x: x[0])
    if not pairs:
        raise ValueError("no observations")

    now = now or pairs[-1][0]
    current = pairs[-1][1]
    prices = [p for _, p in pairs]
    n = len(prices)

    # recent window: last 90 days of data (fall back to all we have)
    cutoff = now - timedelta(days=90)
    window = [(t, p) for t, p in pairs if t >= cutoff] or pairs
    t0 = window[0][0]
    xs = [(t - t0).total_seconds() / 86400.0 for t, _ in window]
    ys = [p for _, p in window]
    slope, intercept = _linreg(xs, ys)

    last_x = xs[-1]
    projected = intercept + slope * (last_x + horizon_days)
    # keep the projection inside a believable band around real history
    expected = round(_clamp(projected, stats.lowest * 0.85, stats.highest * 1.05), 2)

    below_frac = sum(1 for p in prices if p < current) / n
    proj_rel = (slope * horizon_days) / current if current else 0.0  # <0 = falling
    trend_prob = _clamp(0.5 - proj_rel * 2.5, 0.0, 1.0)
    # weight the trend a bit more than history so a clear downtrend can imply a
    # lower price even when the item is currently at an all-time low.
    probability_lower = round(_clamp(0.4 * below_frac + 0.6 * trend_prob, 0.0, 1.0), 3)

    # "still dropping right now" is a property of the latest step, not the
    # whole window -- a flat tail at the floor should read as stable, not falling.
    recent_delta = prices[-1] - prices[-2] if n >= 2 else 0.0
    still_falling = recent_delta < -0.003 * current

    expected_savings = round(max(0.0, current - expected), 2)

    # next likely sale from dip cadence
    dips = _count_dips(pairs)
    span_days = max(1.0, (pairs[-1][0] - pairs[0][0]).total_seconds() / 86400.0)
    next_sale = None
    if dips >= 2:
        cadence = span_days / dips
        # days since the most recent below-average observation
        avg = stats.avg_90d or stats.average
        since = 0.0
        for t, p in reversed(pairs):
            if p < avg * 0.97:
                since = (now - t).total_seconds() / 86400.0
                break
        next_sale = max(0, int(round(cadence - since)))

    confidence = round(_clamp(min(1.0, n / 30.0) * (1.0 - min(1.0, stats.volatility * 3)),
                              0.0, 1.0), 2)

    at_floor = current <= stats.lowest * 1.02
    baseline = stats.avg_90d or stats.average
    rationale = []
    if at_floor:
        rationale.append(f"At/near the historical low (${stats.lowest:.2f}).")
    if slope < 0:
        rationale.append(f"Recent trend is falling (~${abs(slope):.2f}/day).")
    elif slope > 0:
        rationale.append(f"Recent trend is rising (~${slope:.2f}/day).")
    rationale.append(f"Cheaper than now {below_frac*100:.0f}% of its history.")
    if next_sale is not None:
        rationale.append(f"Historically dips about every {span_days/max(1,dips):.0f} days.")

    if at_floor and not still_falling:
        rec = "buy_now"
        rationale.append("At the floor and not still dropping — good time to buy.")
    elif still_falling and expected_savings / current >= 0.02:
        rec = "wait"
        rationale.append(f"Still dropping; expected ~${expected_savings:.2f} "
                         f"saved by waiting ~{horizon_days}d.")
    elif probability_lower >= 0.6 and expected_savings / current >= 0.03:
        rec = "wait"
        rationale.append(f"Likely lower soon (~{probability_lower*100:.0f}%); "
                         f"expected ~${expected_savings:.2f} saved by waiting.")
    elif baseline and current <= baseline:
        rec = "consider"
        rationale.append("Around or below its recent average — a fair price.")
    else:
        rec = "wait" if probability_lower >= 0.5 else "consider"

    return Forecast(
        recommendation=rec,
        expected_price=expected,
        horizon_days=horizon_days,
        probability_lower=probability_lower,
        expected_savings_if_waiting=expected_savings,
        next_sale_in_days=next_sale,
        trend_per_day=round(slope, 4),
        confidence=confidence,
        rationale=rationale,
    )
