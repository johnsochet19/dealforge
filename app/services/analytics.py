"""Analytics -- aggregate insight over the scored catalog.

Rankings and trend summaries computed from the same deal cards the rest of the
app serves, so the numbers always agree with what a user sees on a product.
Pure aggregation over `all_cards`; at scale these become materialized/rolled-up
queries, but the definitions here are the single source of truth.
"""
from collections import defaultdict
from statistics import mean

from .deals import all_cards


def _discount_pct(card) -> float:
    s = card["stats"]
    base = s.get("avg_90d") or s.get("average")
    return (base - s["current"]) / base * 100 if base else 0.0


def _rank_by(cards, key_fn):
    groups = defaultdict(list)
    for c in cards:
        k = key_fn(c)
        if k:
            groups[k].append(c)
    rows = []
    for name, items in groups.items():
        rows.append({
            "name": name,
            "products": len(items),
            "avg_deal_score": round(mean(c["deal_score"] for c in items), 1),
            "avg_discount_pct": round(mean(_discount_pct(c) for c in items), 1),
            "best_deal": max(items, key=lambda c: c["deal_score"])["title"],
        })
    rows.sort(key=lambda r: r["avg_deal_score"], reverse=True)
    return rows


def analytics(db) -> dict:
    cards = all_cards(db)
    if not cards:
        return {"products": 0, "retailers": [], "brands": [], "categories": [],
                "avg_deal_score": 0, "avg_discount_pct": 0}
    discounts = [_discount_pct(c) for c in cards]
    on_sale = [c for c in cards if _discount_pct(c) >= 5]
    buy_now = [c for c in cards if c.get("prediction", {}).get("recommendation") == "buy_now"]
    return {
        "products": len(cards),
        "avg_deal_score": round(mean(c["deal_score"] for c in cards), 1),
        "avg_discount_pct": round(mean(discounts), 1),
        "on_sale_now": len(on_sale),
        "buy_now_count": len(buy_now),
        "retailers": _rank_by(cards, lambda c: c["retailer"]),
        "brands": _rank_by(cards, lambda c: c.get("brand")),
        "categories": _rank_by(cards, lambda c: c["category"]),
        "price_trends": [{
            "product_id": c["id"], "title": c["title"],
            "current": c["stats"]["current"],
            "avg_90d": c["stats"]["avg_90d"],
            "discount_pct": round(_discount_pct(c), 1),
            "trend_per_day": c.get("prediction", {}).get("expected_price"),
        } for c in sorted(cards, key=lambda c: _discount_pct(c), reverse=True)[:10]],
    }
