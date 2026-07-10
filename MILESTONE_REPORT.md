# Milestone Report — AI Intelligence Layer

Date: 2026-07-10 · Branch: `claude/dealforge-orientation-8dc669`

This milestone extends the working DealForge core with the headline
intelligence and insight features from the enterprise brief, built as **real,
tested, running code** — no placeholders or mocks.

## Scope honesty (read this first)

The full enterprise brief (18 retailer connectors, Google/Apple OAuth, trained
ML models, an LLM assistant, SMS/push, community, admin, GraphQL, 90% coverage
across all of it) is genuinely a multi-team, multi-month program. Several parts
**cannot** be built as non-mock, running code without external credentials,
datasets, or network access this environment doesn't have:

- **Retailer connectors** (Amazon, Walmart, Target, …): each needs that
  retailer's official API keys, and this sandbox's egress policy blocks their
  hosts. eBay is implemented as the real reference; others follow the same
  pattern once keys exist. Stubbing 17 fake connectors is the mock code the
  brief forbids, so they were **not** generated.
- **Google/Apple OAuth**: needs OAuth client credentials.
- **Trained price-prediction / quality ML**: needs labeled datasets + training
  infra. The prediction here is a transparent statistical model instead.
- **SMS / push**: need provider credentials. Slack/Discord/Telegram are
  implemented because they are plain HTTP and need only a user-supplied
  URL/token.

What follows is what was actually built and verified.

## Delivered this milestone

| Feature | Endpoint(s) | Notes |
|---|---|---|
| Price Prediction AI | `GET /products/{id}/forecast` + `prediction` in every card | Explainable: buy/wait, expected price, probability lower, expected savings, next-dip cadence, rationale |
| Shopping Assistant | `POST /assistant` | Grounded in the catalog; cites real products; no LLM key required |
| Analytics | `GET /analytics` | Retailer/brand/category rankings, averages, price-trend movers |
| Dashboard intelligence | `GET /dashboard` | today's best, biggest discounts, price drops, hidden gems, AI picks, trending |
| Slack / Discord / Telegram | via notification channels | Real HTTP delivery; register into the existing pluggable dispatcher |
| CSV reporting | `GET /reports/deals.csv`, `/reports/rankings.csv` | Built from the same cards/analytics the API serves |
| Rate limiting | all routes | Per-IP fixed window, `RATE_LIMIT` env (off by default) |
| Dashboard UI | served at `/` | Prediction line per card, "Ask AI" panel, data-source chip |

Prior milestones (already on `main`): notification delivery, user accounts +
auth, real eBay connector, persistent Postgres, product images + store links.

## Issues found and how they were resolved

1. **Degenerate-span forecast (caught in the live run).** When many observations
   land within the same second, the time-based trend regression divided by a
   near-zero interval and produced an absurd `trend_per_day`
   (≈ -2.3e7 $/day). **Resolved:** when the fitted window spans < 1 day the
   forecast now reports a flat trend honestly and skips the dip-cadence estimate
   rather than inventing a rate. Unit tests use day-spaced series and still
   exercise the regression path.
2. **Prediction decision logic mis-fired on two cases.** "Still falling" was
   read from the whole-window slope instead of the latest step, and a strong
   downtrend at an all-time low couldn't push `probability_lower` above 0.5.
   **Resolved:** "still falling" now uses the most recent step; probability
   weights the trend slightly above history. Both are covered by tests.
3. **A stale channel-set assertion broke** when Slack/Discord/Telegram were
   added (it hard-coded `{webhook, email}`). **Resolved:** loosened to a subset
   check so new channels don't regress it.
4. **SQLite schema drift** (pre-existing risk): new tables/columns weren't added
   to an existing DB. **Resolved earlier this session** by `init_db()`, which
   converges the schema on boot.

## Performance benchmarks

Measured on the running app (SQLite, 8 products, ~480 price observations):

| Endpoint | Response time |
|---|---|
| `/api/v1/deals` | ~26 ms |
| `/api/v1/dashboard` | ~23 ms |
| `/api/v1/analytics` | ~24 ms |
| `/api/v1/search?q=…` | ~23 ms |
| `/api/v1/assistant` | ~24 ms |
| `/api/v1/reports/deals.csv` | ~23 ms |

Test suite: **109 tests, ~4 s**, all green. Code: ~2,160 lines across `app/`,
12 test files.

## Remaining technical debt

- **All cards recomputed per request.** `all_cards` composes history + scoring +
  forecast for every product on each call. Fine at this scale; at millions of
  products this must become a precomputed/materialized layer refreshed on new
  observations, with cached deal cards (already noted in the README's scale
  section).
- **Dashboard/analytics/assistant call `all_cards`** and filter in Python. At
  scale, push filters into SQL / a search index (Elasticsearch/OpenSearch) and
  serve rankings from rollup tables.
- **Rate limiter is in-process.** Correct for a single worker; a multi-worker or
  multi-instance deployment needs a shared store (Redis).
- **Forecast is univariate** (price-time only). It does not yet use seasonality,
  demand, or cross-retailer signal — good next modeling step.
- **Assistant is rule-based.** Intent matching is keyword-based; an optional LLM
  layer (behind a key) would handle free-form phrasing while keeping the grounded
  facts authoritative.
- **Coverage not measured this run.** Add `pytest --cov` gating in CI.

## Recommended next steps (in priority order)

1. **Caching layer for deal cards** — recompute on new observation, not per
   request; add Redis. Unblocks the "millions of products" goal.
2. **One or two more real connectors** (Best Buy / Walmart have official APIs)
   once keys are available — reuse the eBay pattern.
3. **Background scheduler** for ingest + alert evaluation (APScheduler/Celery)
   so data updates without manual ticks.
4. **Watchlists / saved products / recently viewed** — DB-backed, scoped to the
   authenticated user (auth already exists).
5. **Optional LLM assistant** behind `ANTHROPIC_API_KEY`, grounded by the
   existing retrieval so answers stay truthful.
6. **CI/CD**: GitHub Actions running `pytest --cov` with a coverage gate, plus
   the Docker build.
7. **Seasonality/demand features** in the forecast; **GraphQL** surface over the
   same services if a client needs it.
