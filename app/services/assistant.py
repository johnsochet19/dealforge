"""Shopping Assistant -- grounded, deterministic answers over the catalog.

Every answer is computed from the platform's own data (deal cards, scores,
forecasts) and cites the products it used, so it can't hallucinate a product or
price that doesn't exist. This is intentional: a shopping assistant that invents
listings is worse than none. It needs no LLM key to work.

Supported intents (matched by simple, transparent keyword rules):
- "is this a good deal?" for a named/served product        -> verdict + why
- "best <category> under $<budget>"                         -> ranked picks
- "cheapest / best <query>"                                 -> ranked picks
- "what's likely to go on sale?"                            -> wait-rated items

An optional LLM layer can phrase these more naturally when an API key is set,
but the grounded facts (the picks, scores, forecasts) always come from here.
"""
import re

from .deals import all_cards
from .search import search as run_search


_BUDGET_RE = re.compile(r"(?:under|below|less than|<)\s*\$?\s*(\d[\d,]*)")
_CATEGORY_WORDS = {
    "laptop": "Computers", "laptops": "Computers", "computer": "Computers",
    "tv": "Electronics", "television": "Electronics",
    "phone": "Phones", "phones": "Phones", "smartphone": "Phones",
    "headphone": "Electronics", "headphones": "Electronics",
    "gpu": "Gaming", "graphics": "Gaming",
    "monitor": "Computers", "vacuum": "Home", "watch": "Electronics",
}


def _budget(q: str) -> float | None:
    m = _BUDGET_RE.search(q)
    return float(m.group(1).replace(",", "")) if m else None


def _pick_terms(q: str) -> tuple[str | None, str | None]:
    """Return (category, free-text query) inferred from the question."""
    ql = q.lower()
    category = next((cat for word, cat in _CATEGORY_WORDS.items() if word in ql), None)
    # a rough free-text term: the noun after "best/cheapest ... " if present
    m = re.search(r"(?:best|cheapest|good|recommend)\s+([a-z0-9 ]+?)(?:\s+under|\s+below|\?|$)", ql)
    term = m.group(1).strip() if m else None
    if term in ("deal", "deals", "product", "products", "thing", "one"):
        term = None
    return category, term


def _verdict_for_card(card: dict) -> dict:
    score = card["deal_score"]
    pred = card.get("prediction", {})
    if score >= 80 and pred.get("recommendation") == "buy_now":
        verdict = "Yes — a strong deal, buy now."
    elif score >= 65:
        verdict = "Decent deal." + (" Consider waiting a bit." if pred.get("recommendation") == "wait" else "")
    elif pred.get("recommendation") == "wait":
        verdict = "Not right now — likely to get cheaper."
    else:
        verdict = "Middling — not a standout deal today."
    return {
        "question": "is this a good deal?",
        "product_id": card["id"],
        "title": card["title"],
        "verdict": verdict,
        "deal_score": score,
        "price": card["stats"]["current"],
        "recommendation": pred.get("recommendation"),
        "expected_price": pred.get("expected_price"),
        "why": card.get("score_breakdown"),
    }


def _rank(cards, limit=5):
    return [{
        "product_id": c["id"], "title": c["title"], "brand": c["brand"],
        "retailer": c["retailer"], "price": c["stats"]["current"],
        "deal_score": c["deal_score"],
        "recommendation": c.get("prediction", {}).get("recommendation"),
        "url": c.get("url"),
    } for c in cards[:limit]]


def ask(db, question: str, product_id: int | None = None) -> dict:
    q = (question or "").strip()
    ql = q.lower()

    # 1) "is this a good deal?" about a specific served product
    if product_id is not None and ("good deal" in ql or "should i buy" in ql
                                   or "worth it" in ql or not q):
        from .deals import product_card
        from ..models import Product
        p = db.get(Product, product_id)
        card = product_card(db, p) if p else None
        if card:
            return {"intent": "evaluate_product", "answer": _verdict_for_card(card)}

    # 2) "what's likely to go on sale?" -> items forecast to drop
    if "on sale" in ql or "go on sale" in ql or "drop" in ql or "wait" in ql:
        cards = [c for c in all_cards(db)
                 if c.get("prediction", {}).get("recommendation") == "wait"]
        cards.sort(key=lambda c: c["prediction"]["probability_lower"], reverse=True)
        return {"intent": "likely_sales",
                "answer": f"{len(cards)} items look likely to get cheaper soon.",
                "picks": _rank(cards)}

    # 3) "best/cheapest <thing> [under $X]" -> ranked picks
    budget = _budget(ql)
    category, term = _pick_terms(ql)
    sort = "price_low" if ("cheapest" in ql or "cheap" in ql) else "deal_score"
    res = run_search(db, q=term, category=category, max_price=budget,
                     sort=sort, limit=5)
    picks = res["results"]
    if picks:
        bits = ["Top pick:" if len(picks) == 1 else "Top picks:"]
        label = category or term or "deal"
        headline = f"Best {label}" + (f" under ${budget:.0f}" if budget else "") + \
                   f": {picks[0]['title']} (score {picks[0]['deal_score']})."
        return {"intent": "recommend", "answer": headline,
                "criteria": {"category": category, "query": term,
                             "budget": budget, "sort": sort},
                "picks": _rank(picks)}

    return {"intent": "unknown",
            "answer": "I can help with: 'is this a good deal?', "
                      "'best laptop under $900', 'cheapest 4k tv', or "
                      "'what's likely to go on sale?'.",
            "picks": []}
