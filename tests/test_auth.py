"""Authentication: password hashing, signed tokens, and the auth-scoped
behaviour of the alert/channel endpoints."""
import pytest
from fastapi.testclient import TestClient

from app.services import auth
from app.main import app
from app.services.ingest import run_ingest
from app.db import SessionLocal

client = TestClient(app)


# --- password hashing -------------------------------------------------------

def test_password_roundtrip():
    h = auth.hash_password("correct horse battery")
    assert h.startswith("pbkdf2_sha256$")
    assert auth.verify_password("correct horse battery", h)
    assert not auth.verify_password("wrong", h)


def test_password_hashes_are_salted():
    assert auth.hash_password("same") != auth.hash_password("same")


def test_empty_password_rejected():
    with pytest.raises(ValueError):
        auth.hash_password("")


def test_verify_bad_stored_is_false():
    assert auth.verify_password("x", "garbage") is False


# --- tokens -----------------------------------------------------------------

def test_token_roundtrip():
    tok = auth.create_token(42, now=1000)
    payload = auth.decode_token(tok, now=1000)
    assert payload["sub"] == 42


def test_token_expiry():
    tok = auth.create_token(1, ttl=100, now=1000)
    with pytest.raises(auth.TokenError):
        auth.decode_token(tok, now=1000 + 101)


def test_token_bad_signature():
    tok = auth.create_token(1, now=1000)
    with pytest.raises(auth.TokenError):
        auth.decode_token(tok + "tamper", now=1000)


def test_token_malformed():
    with pytest.raises(auth.TokenError):
        auth.decode_token("not-a-token", now=1000)


# --- auth endpoints ---------------------------------------------------------

def _register(email, pw="password123"):
    return client.post("/api/v1/auth/register", json={"email": email, "password": pw})


def test_register_login_me():
    r = _register("alice@example.com")
    assert r.status_code == 200
    token = r.json()["token"]
    assert r.json()["user"]["email"] == "alice@example.com"

    me = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200 and me.json()["email"] == "alice@example.com"

    lg = client.post("/api/v1/auth/login",
                     json={"email": "alice@example.com", "password": "password123"})
    assert lg.status_code == 200 and lg.json()["token"]


def test_register_duplicate_rejected():
    _register("dup@example.com")
    assert _register("dup@example.com").status_code == 409


def test_register_weak_password_rejected():
    assert _register("weak@example.com", pw="short").status_code == 400


def test_login_bad_password():
    _register("bob@example.com")
    r = client.post("/api/v1/auth/login",
                    json={"email": "bob@example.com", "password": "nope"})
    assert r.status_code == 401


def test_me_requires_auth():
    assert client.get("/api/v1/auth/me").status_code == 401
    assert client.get("/api/v1/auth/me",
                      headers={"Authorization": "Bearer garbage"}).status_code == 401


# --- auth scoping of data endpoints -----------------------------------------

def _seed():
    s = SessionLocal(); run_ingest(s); s.close()


def test_authed_alert_is_scoped_to_token_not_body():
    _seed()
    token = _register("carol@example.com").json()["token"]
    pid = client.get("/api/v1/deals").json()[0]["id"]
    hdr = {"Authorization": f"Bearer {token}"}
    # try to spoof someone else's email in the body -> token wins
    client.post("/api/v1/alerts", headers=hdr, json={
        "user_email": "victim@example.com", "product_id": pid,
        "rule_type": "lowest_ever"})
    # the alert is listed under carol, not the spoofed victim email
    mine = client.get("/api/v1/alerts", headers=hdr).json()
    assert any(a["product_id"] == pid for a in mine)
    victim = client.get("/api/v1/alerts",
                        params={"user_email": "victim@example.com"}).json()
    assert not any(a["product_id"] == pid for a in victim)


def test_channel_ownership_enforced():
    ta = _register("owner@example.com").json()["token"]
    tb = _register("intruder@example.com").json()["token"]
    ha = {"Authorization": f"Bearer {ta}"}
    hb = {"Authorization": f"Bearer {tb}"}
    cid = client.post("/api/v1/notifications/channels", headers=ha,
                      json={"kind": "webhook", "target": "https://hook/o"}).json()["id"]
    # intruder cannot delete owner's channel
    assert client.delete(f"/api/v1/notifications/channels/{cid}",
                         headers=hb).status_code == 403
    # owner can
    assert client.delete(f"/api/v1/notifications/channels/{cid}",
                         headers=ha).status_code == 200


def test_require_auth_flag(monkeypatch):
    import app.main as m
    monkeypatch.setattr(m, "REQUIRE_AUTH", True)
    # no token, no way to resolve an email -> 401
    assert client.get("/api/v1/alerts").status_code == 401
    assert client.post("/api/v1/notifications/channels",
                       json={"kind": "webhook", "target": "https://x"}).status_code == 401
