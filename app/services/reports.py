"""Reporting -- export the catalog and analytics as CSV.

CSV is dependency-free and opens in Excel/Sheets, so it covers the spec's
export need without pulling in a PDF/spreadsheet stack. The rows are built from
the same deal cards and analytics the API serves, so an export always matches
what the user saw on screen.
"""
import csv
import io

from .deals import all_cards
from .analytics import analytics, _discount_pct


def deals_csv(db) -> str:
    cards = all_cards(db)
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["product_id", "title", "brand", "category", "retailer",
                "price", "msrp", "discount_pct", "deal_score",
                "recommendation", "in_stock", "url"])
    for c in cards:
        w.writerow([
            c["id"], c["title"], c["brand"], c["category"], c["retailer"],
            c["stats"]["current"], c["msrp"], round(_discount_pct(c), 1),
            c["deal_score"], c.get("prediction", {}).get("recommendation"),
            c["in_stock"], c.get("url"),
        ])
    return buf.getvalue()


def rankings_csv(db, dimension: str = "retailers") -> str:
    a = analytics(db)
    rows = a.get(dimension, [])
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow([dimension[:-1] if dimension.endswith("s") else dimension,
                "products", "avg_deal_score", "avg_discount_pct", "best_deal"])
    for r in rows:
        w.writerow([r["name"], r["products"], r["avg_deal_score"],
                    r["avg_discount_pct"], r["best_deal"]])
    return buf.getvalue()
