"""Simple per-IP rate limiting.

A fixed-window request counter keyed by client IP. In-process (fine for a single
worker; a multi-worker deployment would back this with Redis). Disabled by
default so the zero-config demo and the test suite are unaffected; set the
RATE_LIMIT env var (requests per minute per IP) to enable it.
"""
import os
import time
from collections import defaultdict

from fastapi import Request
from fastapi.responses import JSONResponse

WINDOW_SECONDS = 60.0
_limit = int(os.getenv("RATE_LIMIT", "0"))  # 0 = disabled
_hits: dict[str, list[float]] = defaultdict(list)


def set_limit(n: int) -> None:
    """Set the per-minute limit at runtime (used by tests/config)."""
    global _limit
    _limit = n


def reset() -> None:
    _hits.clear()


def _now() -> float:
    return time.monotonic()


async def rate_limit_middleware(request: Request, call_next):
    if _limit > 0:
        ip = request.client.host if request.client else "unknown"
        now = _now()
        cutoff = now - WINDOW_SECONDS
        window = _hits[ip]
        while window and window[0] < cutoff:
            window.pop(0)
        if len(window) >= _limit:
            retry = int(WINDOW_SECONDS - (now - window[0])) + 1
            return JSONResponse(
                {"detail": "rate limit exceeded", "retry_after": retry},
                status_code=429, headers={"Retry-After": str(retry)})
        window.append(now)
    return await call_next(request)
