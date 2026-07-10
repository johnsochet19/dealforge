"""Compose history + scoring into product 'deal cards' the API/UI consume."""
from sqlalchemy import select
from ..models import Product, PriceObservation
from .history import compute_stats
from .scoring import deal_score, recommendation
from .predict import forecast


def product_card(db, product: Product) -> dict:
    obs = db.execute(
        select(PriceObservation)
        .where(PriceObservation.product_id == product.id)
        .order_by(PriceObservation.observed_at)
    ).scalars().all()
    if not obs:
        return None
    stats = compute_stats(obs)
    latest = obs[-1]
    score, breakdown = deal_score(
        stats, rating=product.rating, review_count=product.review_count,
        seller_reputation=product.seller_reputation, coupon=latest.coupon,
    )
    fc = forecast(obs, stats)
    return {
        "id": product.id,
        "retailer": product.retailer,
        "external_id": product.external_id,
        "title": product.title,
        "brand": product.brand,
        "category": product.category,
        "url": product.url,
        "image_url": product.image_url,
        "msrp": product.msrp,
        "rating": product.rating,
        "review_count": product.review_count,
        "in_stock": latest.in_stock,
        "inventory_level": latest.inventory_level,
        "coupon": latest.coupon,
        "deal_score": score,
        "score_breakdown": breakdown,
        "recommendation": recommendation(stats, score),
        "prediction": {
            "recommendation": fc.recommendation,
            "expected_price": fc.expected_price,
            "horizon_days": fc.horizon_days,
            "probability_lower": fc.probability_lower,
            "expected_savings_if_waiting": fc.expected_savings_if_waiting,
            "next_sale_in_days": fc.next_sale_in_days,
            "confidence": fc.confidence,
        },
        "stats": stats.__dict__,
    }


def product_forecast(db, product_id: int) -> dict | None:
    """Full explainable forecast (with rationale) for one product."""
    obs = db.execute(
        select(PriceObservation)
        .where(PriceObservation.product_id == product_id)
        .order_by(PriceObservation.observed_at)
    ).scalars().all()
    if not obs:
        return None
    stats = compute_stats(obs)
    fc = forecast(obs, stats)
    return {"product_id": product_id, **fc.__dict__}


def all_cards(db) -> list[dict]:
    prods = db.execute(select(Product)).scalars().all()
    cards = [c for p in prods if (c := product_card(db, p))]
    cards.sort(key=lambda c: c["deal_score"], reverse=True)
    return cards


def history_series(db, product_id: int) -> list[dict]:
    obs = db.execute(
        select(PriceObservation)
        .where(PriceObservation.product_id == product_id)
        .order_by(PriceObservation.observed_at)
    ).scalars().all()
    return [{"t": o.observed_at.isoformat(), "price": o.price,
             "in_stock": o.in_stock} for o in obs]
