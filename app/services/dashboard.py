"""Dashboard intelligence -- the curated views the spec's dashboard needs.

Each view is a documented slice/sort of the scored deal cards:
- today's best deals      : highest Deal Score
- biggest discounts       : largest drop vs 90-day average
- price drops             : currently below its recent (30d) average
- hidden gems             : high score but low review volume (under-the-radar)
- AI picks                : forecast says buy_now, ranked by score
- trending                : most-reviewed (a popularity proxy)
- restocks                : back in stock with inventory after being out/low
"""
from .deals import all_cards


def _discount_pct(card) -> float:
    s = card["stats"]
    base = s.get("avg_90d") or s.get("average")
    return (base - s["current"]) / base * 100 if base else 0.0


def _slim(cards, limit, extra=None):
    out = []
    for c in cards[:limit]:
        row = {
            "product_id": c["id"], "title": c["title"], "brand": c["brand"],
            "retailer": c["retailer"], "category": c["category"],
            "price": c["stats"]["current"], "msrp": c["msrp"],
            "deal_score": c["deal_score"], "image_url": c.get("image_url"),
            "url": c.get("url"), "in_stock": c["in_stock"],
            "recommendation": c.get("prediction", {}).get("recommendation"),
            "discount_pct": round(_discount_pct(c), 1),
        }
        if extra:
            row.update(extra(c))
        out.append(row)
    return out


def dashboard(db) -> dict:
    cards = all_cards(db)

    todays_best = sorted(cards, key=lambda c: c["deal_score"], reverse=True)
    biggest = sorted(cards, key=_discount_pct, reverse=True)
    drops = [c for c in cards
             if c["stats"].get("avg_30d") and c["stats"]["current"] < c["stats"]["avg_30d"] * 0.97]
    drops.sort(key=lambda c: c["stats"]["avg_30d"] - c["stats"]["current"], reverse=True)
    # hidden gems: strong score but comparatively few reviews (under the radar)
    gems = sorted([c for c in cards if c["deal_score"] >= 60],
                  key=lambda c: (c["deal_score"], -(c["review_count"] or 0)),
                  reverse=True)
    gems = sorted(gems, key=lambda c: (c["review_count"] or 0))[:8]
    ai_picks = sorted(
        [c for c in cards if c.get("prediction", {}).get("recommendation") == "buy_now"],
        key=lambda c: c["deal_score"], reverse=True)
    trending = sorted(cards, key=lambda c: (c["review_count"] or 0), reverse=True)

    return {
        "todays_best": _slim(todays_best, 8),
        "biggest_discounts": _slim(biggest, 8),
        "price_drops": _slim(drops, 8),
        "hidden_gems": _slim(gems, 8),
        "ai_picks": _slim(ai_picks, 8),
        "trending": _slim(trending, 8),
    }
