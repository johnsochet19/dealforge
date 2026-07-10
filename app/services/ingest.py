"""Ingestion: run all registered connectors, upsert products, append price
observations. In production this is a scheduled background job (Celery/RQ/
APScheduler) fanned out per connector; here it's a callable the API can invoke.
"""
from datetime import datetime
from sqlalchemy import select
from ..models import Product, PriceObservation
from ..connectors.base import get_connectors


def run_ingest(db) -> dict:
    counts = {}
    for name, connector in get_connectors().items():
        n = 0
        for rec in connector.fetch():
            prod = db.execute(
                select(Product).where(
                    Product.retailer == name,
                    Product.external_id == rec.external_id,
                )
            ).scalar_one_or_none()
            if prod is None:
                prod = Product(
                    retailer=name, external_id=rec.external_id, title=rec.title,
                    brand=rec.brand, category=rec.category, url=rec.url,
                    image_url=rec.image_url, msrp=rec.msrp, rating=rec.rating,
                    review_count=rec.review_count,
                    seller_reputation=rec.seller_reputation,
                )
                db.add(prod)
                db.flush()
            else:
                # refresh mutable fields
                prod.rating = rec.rating
                prod.review_count = rec.review_count
                prod.seller_reputation = rec.seller_reputation
            db.add(PriceObservation(
                product_id=prod.id, price=rec.price, in_stock=rec.in_stock,
                inventory_level=rec.inventory_level, coupon=rec.coupon,
                observed_at=datetime.utcnow(),
            ))
            n += 1
        counts[name] = n
    db.commit()
    return counts
