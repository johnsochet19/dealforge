"""Deal Score 0-100.

This is an explainable heuristic, NOT a black-box "AI" that hides a random
number. Every point is traceable to real price history. That is a feature: a
deal platform that can't explain WHY something scores 92 is not trustworthy.

Components (weights sum to 100):
  price_position   35  -- how low current price sits within its historical range
  vs_recent_avg    25  -- discount vs 90d (fallback all-time) average
  discount_rarity  15  -- rare discounts are worth more (low discount_frequency)
  at_or_near_low   10  -- bonus for matching the historical lowest
  quality          10  -- rating * review-count confidence * seller reputation
  coupon           5   -- extra stackable coupon present

Returns (score, breakdown dict) so the UI/API can show the reasoning.
"""
from math import log10
from .history import PriceStats


def _price_position(stats: PriceStats) -> float:
    rng = stats.highest - stats.lowest
    if rng <= 0:
        return 0.5  # flat history: neutral
    # 1.0 when at the historical low, 0.0 at the high
    return max(0.0, min(1.0, (stats.highest - stats.current) / rng))


def _vs_recent(stats: PriceStats) -> float:
    baseline = stats.avg_90d or stats.average
    if not baseline:
        return 0.0
    drop = (baseline - stats.current) / baseline
    # map a 0-40% drop onto 0-1, clamp
    return max(0.0, min(1.0, drop / 0.40))


def _quality(rating, review_count, seller_rep) -> float:
    r = (rating or 0) / 5.0
    # review-count confidence: log-scaled, saturates ~10k reviews
    conf = min(1.0, log10((review_count or 0) + 1) / 4.0)
    rep = seller_rep if seller_rep is not None else 0.7
    return max(0.0, min(1.0, 0.5 * r + 0.3 * conf + 0.2 * rep))


def deal_score(stats: PriceStats, *, rating=None, review_count=0,
               seller_reputation=None, coupon=0.0) -> tuple[int, dict]:
    pos = _price_position(stats)
    recent = _vs_recent(stats)
    rarity = 1.0 - stats.discount_frequency   # rarer discount => higher
    near_low = 1.0 if stats.current <= stats.lowest * 1.01 else 0.0
    quality = _quality(rating, review_count, seller_reputation)
    has_coupon = 1.0 if coupon and coupon > 0 else 0.0

    parts = {
        "price_position": pos * 35,
        "vs_recent_avg": recent * 25,
        "discount_rarity": rarity * 15,
        "at_or_near_low": near_low * 10,
        "quality": quality * 10,
        "coupon": has_coupon * 5,
    }
    score = round(sum(parts.values()))
    breakdown = {k: round(v, 1) for k, v in parts.items()}
    return max(0, min(100, score)), breakdown


def recommendation(stats: PriceStats, score: int) -> str:
    """Simple, honest buy/wait guidance derived from position + score.
    Not a price 'prediction model' -- it's a transparent rule."""
    if score >= 80 and stats.current <= (stats.avg_90d or stats.average):
        return "buy_now"
    if _price_position(stats) < 0.35:
        return "wait"  # price is high within its range
    return "consider"
