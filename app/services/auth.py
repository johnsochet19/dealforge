"""Authentication: password hashing + signed bearer tokens.

Deliberately dependency-free (stdlib only) to match the project's "runs with
zero external services" ethos:

- Passwords are stored as ``pbkdf2_sha256$iterations$salt$hash`` -- salted,
  many-iteration PBKDF2, verified in constant time.
- Tokens are a compact HS256-signed JWT-shape (``payload.signature``), signed
  with ``SECRET_KEY`` from the environment. Stateless: no session table, so any
  instance can validate a token. Set a strong ``SECRET_KEY`` in production;
  rotating it invalidates outstanding tokens (users just log in again).

Kept as pure functions so the whole auth path is unit-testable without FastAPI.
"""
import base64
import hashlib
import hmac
import json
import os
import secrets
import time

_ALGO = "pbkdf2_sha256"
_ITERATIONS = 260_000
_DEFAULT_SECRET = "dev-insecure-change-me"  # only used if SECRET_KEY is unset
DEFAULT_TTL = 7 * 24 * 3600  # 7 days


class TokenError(Exception):
    """Raised when a token is malformed, mis-signed, or expired."""


# --- passwords --------------------------------------------------------------

def hash_password(password: str) -> str:
    if not password:
        raise ValueError("password must not be empty")
    salt = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, _ITERATIONS)
    return "{}${}${}${}".format(
        _ALGO, _ITERATIONS,
        base64.b64encode(salt).decode(), base64.b64encode(dk).decode(),
    )


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, iters, salt_b64, hash_b64 = stored.split("$")
        if algo != _ALGO:
            return False
        salt = base64.b64decode(salt_b64)
        expected = base64.b64decode(hash_b64)
        dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, int(iters))
        return hmac.compare_digest(dk, expected)
    except Exception:
        return False


# --- tokens -----------------------------------------------------------------

def _secret() -> bytes:
    return os.getenv("SECRET_KEY", _DEFAULT_SECRET).encode()


def using_default_secret() -> bool:
    return os.getenv("SECRET_KEY", _DEFAULT_SECRET) == _DEFAULT_SECRET


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _b64url_decode(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _sign(body: str) -> str:
    return _b64url(hmac.new(_secret(), body.encode(), hashlib.sha256).digest())


def create_token(user_id: int, *, ttl: int = DEFAULT_TTL, now: int | None = None) -> str:
    now = int(now if now is not None else time.time())
    payload = {"sub": user_id, "iat": now, "exp": now + ttl}
    body = _b64url(json.dumps(payload, separators=(",", ":")).encode())
    return f"{body}.{_sign(body)}"


def decode_token(token: str, *, now: int | None = None) -> dict:
    now = int(now if now is not None else time.time())
    try:
        body, sig = token.split(".")
    except (ValueError, AttributeError):
        raise TokenError("malformed token")
    if not hmac.compare_digest(sig, _sign(body)):
        raise TokenError("bad signature")
    try:
        payload = json.loads(_b64url_decode(body))
    except Exception:
        raise TokenError("undecodable payload")
    if int(payload.get("exp", 0)) < now:
        raise TokenError("token expired")
    return payload
