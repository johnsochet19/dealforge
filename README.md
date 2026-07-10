# DealForge AI — core platform

A working, honestly-scoped foundation for a pricing-intelligence platform:
connector-based ingestion, real price-history aggregation, an **explainable**
Deal Score, an alerts engine, and a zero-build dashboard.

This is the *core slice*, not the full enterprise spec — see "Scope & honesty"
below for what's deliberately out.

## Run it (zero external services)

```bash
pip install -r requirements.txt
uvicorn app.main:app --reload            # API on http://localhost:8000

# in another shell: build some price history, then evaluate
python - <<'PY'
import app.main
from app.db import SessionLocal
from app.services.ingest import run_ingest
db = SessionLocal()
for _ in range(30): run_ingest(db)       # 30 ticks -> real history to score
db.close(); print("seeded")
PY
```

Open `frontend/index.html` in a browser (it calls `http://localhost:8000`).
Use **Run ingest tick** to add a fresh price point and watch scores move.

With Docker + Postgres instead of SQLite:

```bash
docker compose up --build
```

## API (v1)

| Method | Path | Purpose |
|---|---|---|
| GET  | `/health` | liveness |
| POST | `/api/v1/auth/register` | create account, returns bearer token |
| POST | `/api/v1/auth/login` | log in, returns bearer token |
| GET  | `/api/v1/auth/me` | current account (requires token) |
| POST | `/api/v1/ingest` | run all connectors, append observations |
| GET  | `/api/v1/deals?min_score=&category=` | scored deal cards, sorted |
| GET  | `/api/v1/products/{id}` | single card |
| GET  | `/api/v1/products/{id}/history` | price series |
| POST | `/api/v1/alerts` | create alert |
| GET  | `/api/v1/alerts?user_email=` | list alerts |
| POST | `/api/v1/alerts/evaluate` | evaluate active alerts, record events, deliver |
| GET  | `/api/v1/alerts/events?user_email=` | fired events (with delivery status) |
| GET  | `/api/v1/notifications/kinds` | available delivery channel kinds |
| POST | `/api/v1/notifications/channels` | add a delivery channel (webhook/email) |
| GET  | `/api/v1/notifications/channels?user_email=` | list a user's channels |
| DELETE | `/api/v1/notifications/channels/{id}` | remove a channel |
| POST | `/api/v1/notifications/channels/{id}/test` | send a test notification |

Interactive docs at `/docs`.

## Architecture

```
connectors/     RetailerConnector interface + registry + a mock feed
services/
  history.py    price aggregates (pure functions: low/high/avg/median/vol/...)
  scoring.py    explainable Deal Score (weights documented, returns breakdown)
  ingest.py     runs connectors -> upserts products, appends observations
  alerts.py     rule engine -> AlertEvent records (delivery is separate)
  notify.py     dispatcher: delivers fired alerts to webhook/email channels
  deals.py      composes history + scoring into API "cards"
models.py       schema; price_observations is the partition target at scale
main.py         FastAPI app
```

**Adding a retailer:** subclass `RetailerConnector`, wrap an *official* API,
call `register(...)`. No core changes. That seam is where ToS-compliant data
access lives.

## The Deal Score is a heuristic, on purpose

Every point is traceable to real price history (see `scoring.py`). A deal
platform that scores something 92 must be able to say *why* — so the API
returns a `score_breakdown`. This is more trustworthy than a black-box
"prediction AI" that hides a random number, and it's the right first version
before any trained model.

## Tests

```bash
pytest --cov=app        # 20 tests, ~96% coverage
```

## Scope & honesty (what's NOT here yet)

The original brief describes months of team work. Deliberately out of this
build, with the honest reason:

- **Live retailer data** — requires official API keys / licensed feeds;
  scraping most retailers violates their ToS. The mock feed is the stand-in;
  swap in a real connector.
- **Trained price-prediction / quality / assistant models** — the buy/wait
  call here is a transparent rule, not ML. Real models need labeled history.
- **Community, admin panel, GraphQL, CI/CD** — scaffolding points exist
  (connectors/registry are pluggable) but aren't implemented.

## Accounts & auth

Register/login return a **bearer token** (`services/auth.py`: stdlib PBKDF2
password hashing + an HS256-signed stateless token — no extra dependencies).
Send it as `Authorization: Bearer <token>`. When a token is present, alert and
notification-channel operations are **scoped to that account** and a
`user_email` in the request body/query can't override it (no spoofing);
channels are ownership-checked.

By default (`REQUIRE_AUTH` unset) the data endpoints still accept an explicit
`user_email` so the zero-friction demo works without logging in. For a real
deployment, lock everything down:

```bash
REQUIRE_AUTH=true SECRET_KEY=$(openssl rand -hex 32)
```

`SECRET_KEY` signs tokens — set a strong one; the app logs a warning if it's
unset. Rotating it invalidates outstanding tokens (users just log in again).
The dashboard has a **👤 sign in** panel that stores the token in the browser
and drives the notifications panel as the signed-in account.

## Notification delivery

When an alert fires, evaluation records an `AlertEvent` **and** hands it to the
dispatcher (`services/notify.py`), which delivers to every channel the user
configured. Two channels ship: **webhook** (POSTs the event JSON) and **email**
(SMTP). Channels are pluggable — register a new `Channel` subclass and it's
available with no change to rule logic. Delivery is best-effort: a failing
channel is recorded on the event (`delivery_status`: `sent` / `partial` /
`failed` / `no_channels`), never breaking evaluation.

Add a channel via the dashboard's **🔔 notifications** panel or the API, then
**Test** it. Email needs SMTP configured via env (unset ⇒ email delivery is
recorded as failed, not silently dropped):

```bash
SMTP_HOST=smtp.example.com SMTP_PORT=587 SMTP_STARTTLS=true \
SMTP_USER=apikey SMTP_PASSWORD=… SMTP_FROM=alerts@yourdomain.com
```

## Production notes for scale

- Partition `price_observations BY RANGE (observed_at)` monthly in Postgres;
  BRIN on `observed_at`, btree on `(product_id, observed_at)`. Consider
  TimescaleDB continuous aggregates to serve the 30/90/365-day windows.
- Move `run_ingest` and `evaluate_alerts` to a scheduled worker
  (Celery/RQ/APScheduler), fanned out per connector.
- Cache deal cards; recompute on new observation, not per request.
