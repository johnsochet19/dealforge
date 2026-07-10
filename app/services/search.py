"""Search + advanced filtering over scored deal cards.

Filters run in Python over the composed cards so they can span both stored
product fields (brand, category, retailer, rating) and *derived* fields
(deal_score, current price, discount-vs-90d, in_stock) in one pass. At scale
the text match and the stored-field filters would push down into a search index
(the spec's "search index for fast product queries") while derived fields stay
here; the filter semantics below are the single source of truth either way.
"""
from .deals import all_cards


def _discount_pct(card) -> float:
    s = card["stats"]
    base = s.get("avg_90d") or s.get("average")
    if not base:
        return 0.0
    return (base - s["current"]) / base * 100


def search(db, *, q: str | None = None, category: str | None = None,
           retailer: str | None = None, brand: str | None = None,
           min_price: float | None = None, max_price: float | None = None,
           min_score: int = 0, min_discount: float | None = None,
           min_rating: float | None = None, in_stock: bool | None = None,
           sort: str = "deal_score", limit: int = 100, offset: int = 0) -> dict:
    cards = all_cards(db)

    if q:
        needle = q.lower()
        cards = [c for c in cards if needle in (
            f"{c['title']} {c['brand']} {c['category']} "
            f"{c['retailer']} {c['external_id']}").lower()]
    if category:
        cards = [c for c in cards if c["category"].lower() == category.lower()]
    if retailer:
        cards = [c for c in cards if c["retailer"].lower() == retailer.lower()]
    if brand:
        cards = [c for c in cards if (c["brand"] or "").lower() == brand.lower()]
    if min_price is not None:
        cards = [c for c in cards if c["stats"]["current"] >= min_price]
    if max_price is not None:
        cards = [c for c in cards if c["stats"]["current"] <= max_price]
    if min_score:
        cards = [c for c in cards if c["deal_score"] >= min_score]
    if min_rating is not None:
        cards = [c for c in cards if (c["rating"] or 0) >= min_rating]
    if in_stock is not None:
        cards = [c for c in cards if c["in_stock"] is in_stock]
    if min_discount is not None:
        cards = [c for c in cards if _discount_pct(c) >= min_discount]

    keys = {
        "deal_score": lambda c: -c["deal_score"],
        "price_low": lambda c: c["stats"]["current"],
        "price_high": lambda c: -c["stats"]["current"],
        "discount": lambda c: -_discount_pct(c),
        "rating": lambda c: -(c["rating"] or 0),
    }
    cards.sort(key=keys.get(sort, keys["deal_score"]))

    total = len(cards)
    page = cards[offset:offset + limit]
    for c in page:
        c["discount_pct"] = round(_discount_pct(c), 1)
    return {"total": total, "count": len(page), "offset": offset, "results": page}


def facets(db) -> dict:
    """Distinct filter values, for populating dropdowns in the UI."""
    cards = all_cards(db)
    return {
        "categories": sorted({c["category"] for c in cards}),
        "retailers": sorted({c["retailer"] for c in cards}),
        "brands": sorted({c["brand"] for c in cards if c["brand"]}),
    }
