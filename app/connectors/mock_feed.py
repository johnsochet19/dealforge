"""A documented mock feed connector.

It produces deterministic-but-realistic product catalogs and, on each fetch,
a fresh price sampled from a random walk with occasional promotional dips.
This lets the whole platform (history aggregation, deal scoring, alerts) run
end-to-end with no external API keys. Swap this out for a real connector by
implementing RetailerConnector against an official retailer API.
"""
import random
from .base import RetailerConnector, ProductRecord, register

_CATALOG = [
    # (external_id, title, brand, category, msrp, rating, reviews, base_price)
    ("MK-LAPTOP-1", "UltraBook 14 (16GB/512GB)", "Acme", "Computers", 999, 4.4, 2100, 899),
    ("MK-TV-1", "55\" 4K OLED TV", "Vizonic", "Electronics", 1299, 4.6, 5400, 1099),
    ("MK-PHONE-1", "Nova Phone 12 (128GB)", "Nova", "Phones", 799, 4.3, 8800, 749),
    ("MK-HEADPH-1", "Noise-Cancel Headphones", "Sonus", "Electronics", 349, 4.5, 3300, 279),
    ("MK-GPU-1", "RTX-style GPU 12GB", "GraphCore", "Gaming", 599, 4.2, 1500, 549),
    ("MK-VACUUM-1", "Robot Vacuum X", "CleanCo", "Home", 499, 4.1, 990, 399),
    ("MK-WATCH-1", "SmartWatch S2", "Nova", "Electronics", 399, 4.4, 4100, 329),
    ("MK-MONITOR-1", "27\" 144Hz Monitor", "Vizonic", "Computers", 329, 4.5, 2700, 279),
]


class MockFeedConnector(RetailerConnector):
    name = "mockmart"

    def __init__(self, seed: int | None = None):
        self._rng = random.Random(seed)
        # persistent "current price" per product so successive fetches walk
        self._state = {row[0]: row[7] for row in _CATALOG}

    def fetch(self):
        for ext_id, title, brand, category, msrp, rating, reviews, base in _CATALOG:
            cur = self._state[ext_id]
            # random walk +/- up to 3%
            cur *= 1 + self._rng.uniform(-0.03, 0.03)
            # ~12% chance of a promo dip
            coupon = 0.0
            if self._rng.random() < 0.12:
                cur *= self._rng.uniform(0.80, 0.92)
                if self._rng.random() < 0.5:
                    coupon = round(cur * 0.05, 2)
            # keep within sane bounds relative to msrp
            cur = max(base * 0.55, min(cur, msrp * 1.02))
            self._state[ext_id] = cur
            in_stock = self._rng.random() > 0.05
            inventory = 0 if not in_stock else self._rng.randint(1, 200)
            yield ProductRecord(
                external_id=ext_id, title=title, brand=brand, category=category,
                url=f"https://example.com/{ext_id}", image_url="",
                msrp=msrp, rating=rating, review_count=reviews,
                seller_reputation=round(self._rng.uniform(0.7, 0.99), 2),
                price=round(cur, 2), in_stock=in_stock, inventory_level=inventory,
                coupon=coupon,
            )


register(MockFeedConnector(seed=42))
