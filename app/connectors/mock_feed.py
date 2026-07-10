"""A documented mock feed connector.

It produces deterministic-but-realistic product catalogs and, on each fetch,
a fresh price sampled from a random walk with occasional promotional dips.
This lets the whole platform (history aggregation, deal scoring, alerts) run
end-to-end with no external API keys. Swap this out for a real connector by
implementing RetailerConnector against an official retailer API.
"""
import random
from urllib.parse import quote_plus
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
    ("MK-DESKTOP-1", "Gaming Desktop Ryzen 7", "Acme", "Computers", 1499, 4.5, 1200, 1299),
    ("MK-CHROMEBOOK-1", "2-in-1 Chromebook 14", "Acme", "Computers", 499, 4.2, 3100, 429),
    ("MK-KEYB-1", "Mechanical Keyboard RGB", "Sonus", "Computers", 129, 4.6, 6200, 99),
    ("MK-SOUNDBAR-1", "Soundbar 3.1 Dolby", "Vizonic", "Electronics", 299, 4.3, 2400, 229),
    ("MK-SPEAKER-1", "Portable Bluetooth Speaker", "Sonus", "Electronics", 149, 4.5, 9100, 119),
    ("MK-CAM-1", "4K Action Camera", "Nova", "Electronics", 349, 4.2, 1800, 299),
    ("MK-STREAM-1", "Streaming Stick 4K", "Vizonic", "Electronics", 49, 4.6, 15200, 39),
    ("MK-PHONE-2", "Nova Phone 12 Pro (256GB)", "Nova", "Phones", 1099, 4.5, 5200, 999),
    ("MK-PHONE-3", "Budget Phone A3 (64GB)", "Acme", "Phones", 249, 4.0, 4700, 199),
    ("MK-CONSOLE-1", "Game Console X", "GraphCore", "Gaming", 499, 4.7, 22000, 499),
    ("MK-CTRL-1", "Wireless Controller", "GraphCore", "Gaming", 69, 4.4, 8300, 54),
    ("MK-SSD-1", "1TB NVMe Game SSD", "Acme", "Gaming", 129, 4.7, 11500, 89),
    ("MK-AIRP-1", "HEPA Air Purifier", "CleanCo", "Home", 279, 4.3, 1600, 219),
    ("MK-CORDVAC-1", "Cordless Stick Vacuum", "CleanCo", "Home", 399, 4.2, 2050, 299),
    ("MK-AIRFRY-1", "Air Fryer XL 6qt", "CleanCo", "Kitchen", 149, 4.6, 8800, 99),
    ("MK-BLENDER-1", "High-Speed Blender Pro", "CleanCo", "Kitchen", 199, 4.4, 3400, 149),
    ("MK-ESPRESSO-1", "Espresso Machine", "Sonus", "Kitchen", 449, 4.3, 1900, 379),
    ("MK-TREAD-1", "Smart Folding Treadmill", "Nova", "Sports", 799, 4.1, 870, 649),
    ("MK-BAND-1", "Fitness Band 5", "Nova", "Sports", 99, 4.3, 12400, 69),
    ("MK-DESK-1", "Electric Standing Desk", "Acme", "Office", 429, 4.5, 2600, 349),
    ("MK-CHAIR-1", "Ergonomic Office Chair", "Sonus", "Office", 349, 4.4, 4300, 279),
    ("MK-TABLET-1", "10\" Tablet (128GB)", "Nova", "Electronics", 329, 4.3, 5600, 269),
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
                # Simulated catalog has no real product page; point "View" at a
                # real eBay search for the item so the link goes somewhere useful.
                # Real connectors supply the actual listing URL + photo instead.
                url=f"https://www.ebay.com/sch/i.html?_nkw={quote_plus(title)}",
                image_url="",
                msrp=msrp, rating=rating, review_count=reviews,
                seller_reputation=round(self._rng.uniform(0.7, 0.99), 2),
                price=round(cur, 2), in_stock=in_stock, inventory_level=inventory,
                coupon=coupon,
            )


register(MockFeedConnector(seed=42))
