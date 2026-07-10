"""A real retailer connector: eBay Browse API.

Turns the mock feed into live prices using eBay's *official* Browse API
(``/buy/browse/v1/item_summary/search``), which is ToS-compliant -- exactly the
kind of licensed access the connector seam exists for.

Auth is OAuth2 client-credentials: the connector mints an application access
token from ``EBAY_CLIENT_ID`` / ``EBAY_CLIENT_SECRET`` (or you can supply a
pre-minted ``EBAY_OAUTH_TOKEN``) and caches it until it expires. Browse search
needs a query, so the connector pulls one page of results per configured query
term and maps each item summary onto a ``ProductRecord``.

Graceful fallback: if no credentials are configured the connector is simply not
registered, so ``run_ingest`` keeps running the mock feed and nothing breaks.
Network failures during a fetch are logged and skipped rather than crashing
ingestion.

The HTTP calls go through the ``_http_get_json`` / ``_http_post_form`` seams so
the mapping and token logic are unit-tested without a network or real keys.
"""
import base64
import logging
import os
import time
from typing import Iterable

import httpx

from .base import RetailerConnector, ProductRecord, register

log = logging.getLogger("uvicorn.error")

_PROD_BASE = "https://api.ebay.com"
_SANDBOX_BASE = "https://api.sandbox.ebay.com"
_SCOPE = "https://api.ebay.com/oauth/api_scope"
_DEFAULT_QUERIES = ["laptop", "4k tv", "wireless headphones", "graphics card"]


# --- network seams (monkeypatchable in tests) -------------------------------

def _http_post_form(url: str, *, data: dict, headers: dict, timeout: float = 10.0) -> dict:
    resp = httpx.post(url, data=data, headers=headers, timeout=timeout)
    resp.raise_for_status()
    return resp.json()


def _http_get_json(url: str, *, params: dict, headers: dict, timeout: float = 10.0) -> dict:
    resp = httpx.get(url, params=params, headers=headers, timeout=timeout)
    resp.raise_for_status()
    return resp.json()


# --- mapping ----------------------------------------------------------------

def _to_float(value) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def map_item(item: dict) -> ProductRecord | None:
    """Map one eBay Browse item summary to a ProductRecord. Returns None if the
    item lacks the essentials (id, title, price)."""
    ext_id = item.get("itemId") or item.get("legacyItemId")
    title = item.get("title")
    price = _to_float((item.get("price") or {}).get("value"))
    if not ext_id or not title or price is None:
        return None

    categories = item.get("categories") or []
    category = categories[0].get("categoryName", "") if categories else ""

    seller = item.get("seller") or {}
    rep = _to_float(seller.get("feedbackPercentage"))
    seller_reputation = max(0.0, min(1.0, rep / 100.0)) if rep is not None else None

    marketing = item.get("marketingPrice") or {}
    msrp = _to_float((marketing.get("originalPrice") or {}).get("value"))

    image = (item.get("image") or {}).get("imageUrl", "")

    return ProductRecord(
        external_id=str(ext_id),
        title=title,
        price=price,
        brand=item.get("brand", "") or "",
        category=category,
        url=item.get("itemWebUrl", "") or "",
        image_url=image,
        msrp=msrp,
        rating=None,          # Browse item summaries don't carry buyer ratings
        review_count=0,
        seller_reputation=seller_reputation,
        in_stock=True,        # search only returns currently-available items
        inventory_level=None,
        coupon=0.0,
    )


# --- connector --------------------------------------------------------------

class EbayBrowseConnector(RetailerConnector):
    name = "ebay"

    def __init__(self, *, client_id: str | None = None, client_secret: str | None = None,
                 token: str | None = None, queries: list[str] | None = None,
                 marketplace: str = "EBAY_US", env: str = "production",
                 limit: int = 10):
        self._client_id = client_id
        self._client_secret = client_secret
        self._static_token = token
        self.queries = queries or list(_DEFAULT_QUERIES)
        self.marketplace = marketplace
        self.limit = limit
        self._base = _SANDBOX_BASE if env.lower() == "sandbox" else _PROD_BASE
        self._token: str | None = None
        self._token_expiry: float = 0.0

    # -- auth --
    def _fetch_token(self, now: float | None = None) -> str:
        now = now if now is not None else time.time()
        if self._static_token:
            return self._static_token
        if self._token and now < self._token_expiry:
            return self._token
        if not (self._client_id and self._client_secret):
            raise RuntimeError("eBay connector missing client_id/client_secret")
        basic = base64.b64encode(
            f"{self._client_id}:{self._client_secret}".encode()).decode()
        body = _http_post_form(
            f"{self._base}/identity/v1/oauth2/token",
            data={"grant_type": "client_credentials", "scope": _SCOPE},
            headers={"Authorization": f"Basic {basic}",
                     "Content-Type": "application/x-www-form-urlencoded"},
        )
        self._token = body["access_token"]
        # refresh a minute early to avoid edge-of-expiry failures
        self._token_expiry = now + max(0, int(body.get("expires_in", 7200)) - 60)
        return self._token

    # -- search --
    def _search(self, token: str, query: str) -> list[dict]:
        body = _http_get_json(
            f"{self._base}/buy/browse/v1/item_summary/search",
            params={"q": query, "limit": self.limit},
            headers={"Authorization": f"Bearer {token}",
                     "X-EBAY-C-MARKETPLACE-ID": self.marketplace},
        )
        return body.get("itemSummaries", []) or []

    def fetch(self) -> Iterable[ProductRecord]:
        try:
            token = self._fetch_token()
        except Exception as exc:
            log.warning("eBay connector: token fetch failed (%s); skipping", exc)
            return
        seen = set()
        for query in self.queries:
            try:
                items = self._search(token, query)
            except Exception as exc:
                log.warning("eBay connector: search '%s' failed (%s); skipping",
                            query, exc)
                continue
            for item in items:
                rec = map_item(item)
                if rec is None or rec.external_id in seen:
                    continue
                seen.add(rec.external_id)
                yield rec


def make_from_env() -> EbayBrowseConnector | None:
    """Build a connector from environment config, or None if no credentials are
    present (in which case ingestion falls back to the mock feed)."""
    client_id = os.getenv("EBAY_CLIENT_ID")
    client_secret = os.getenv("EBAY_CLIENT_SECRET")
    token = os.getenv("EBAY_OAUTH_TOKEN")
    if not token and not (client_id and client_secret):
        return None
    queries_env = os.getenv("EBAY_QUERIES", "")
    queries = [q.strip() for q in queries_env.split(",") if q.strip()] or None
    return EbayBrowseConnector(
        client_id=client_id, client_secret=client_secret, token=token,
        queries=queries, marketplace=os.getenv("EBAY_MARKETPLACE", "EBAY_US"),
        env=os.getenv("EBAY_ENV", "production"),
        limit=int(os.getenv("EBAY_LIMIT", "10")),
    )


_connector = make_from_env()
if _connector is not None:
    register(_connector)
    log.info("eBay connector registered (marketplace=%s, %d queries)",
             _connector.marketplace, len(_connector.queries))
