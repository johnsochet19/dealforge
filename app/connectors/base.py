"""Pluggable connector architecture.

Every retailer connector implements RetailerConnector. The core platform never
imports a specific retailer; it discovers connectors through the registry. To
add a retailer you drop in a subclass and register it -- no core changes.

In production, a connector wraps an OFFICIAL retailer API or a licensed data
provider (Amazon PA-API, eBay Browse, Best Buy, Walmart Affiliate, etc.).
Scraping most retailers violates their Terms of Service, so connectors are the
seam where compliant data access lives.
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Iterable


@dataclass
class ProductRecord:
    external_id: str
    title: str
    price: float
    brand: str = ""
    category: str = ""
    url: str = ""
    image_url: str = ""
    msrp: float | None = None
    rating: float | None = None
    review_count: int = 0
    seller_reputation: float | None = None
    in_stock: bool = True
    inventory_level: int | None = None
    coupon: float = 0.0
    extra: dict = field(default_factory=dict)


class RetailerConnector(ABC):
    name: str = "base"

    @abstractmethod
    def fetch(self) -> Iterable[ProductRecord]:
        """Yield current product/price records for this retailer."""
        raise NotImplementedError


_REGISTRY: dict[str, RetailerConnector] = {}


def register(connector: RetailerConnector) -> None:
    _REGISTRY[connector.name] = connector


def get_connectors() -> dict[str, RetailerConnector]:
    return dict(_REGISTRY)
