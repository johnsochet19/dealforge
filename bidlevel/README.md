# BidLevel — AI Bid Leveling & Procurement Platform (core slice)

A working, honestly-scoped implementation of the AI bid-leveling platform
spec: project intake → document intelligence → automatic bid packages →
subcontractor database → intelligent invitations → submission portal →
AI proposal parsing → scope normalization → leveling workspace → risk
analysis → award workflow → executive analytics.

Every AI output is **explainable by design**: each extraction carries its
source text, source sheet/page, and a confidence score, and anything below
the confidence threshold is flagged for human review — never silently
accepted.

## Run it

```bash
pip install -r requirements.txt
uvicorn bidlevel.main:app --reload --port 8100
```

Open http://localhost:8100 — the full workspace UI is served at `/`
(zero build step). Click **Seed demo** to load a complete end-to-end
scenario: a medical office building with drawings, specs, 15 auto-built
bid packages, 5 subcontractors, 3 competing electrical proposals, a
populated scope matrix, and 20+ detected risk issues.

Interactive API docs at `/docs`. BidLevel uses its own database
(`bidlevel.db` locally; set `BIDLEVEL_DATABASE_URL` for Postgres) and does
not touch the DealForge app that shares this repository.

## The workflow, module by module

| Module | What it does | Where |
|---|---|---|
| **1. Project wizard** | Validated multi-field intake (team, dates, value, delivery method, insurance/bonding requirements) | `services/projects.py` |
| **2. Drawing intelligence** | Sheet recognition (number/title/discipline/rev/scale/floor), note & schedule capture, trade detection across 25+ trades mapped to CSI divisions, quantity extraction (SF/LF/CY/EA/TON… with source sheet + confidence), structured scope understanding ("install owner supplied hardware" → install-in / material-out / owner-furnished) | `ai/drawing.py` |
| **3. Package builder** | One suggested package per detected trade, pre-loaded with sheets, quantities, scope, requirements; every edit snapshots a version and writes the audit log | `services/packages.py` |
| **4. Sub database** | Full profiles (bonding, EMR, safety, ratings, diversity certs) + compliance documents with expiry states and idempotent reminder windows (60/30/14/7/1 days) — expired insurance can't go unnoticed | `services/subs.py` |
| **5. Invitations** | Explainable AI ranking (trade match, capacity vs project size, performance, EMR, responsiveness, compliance penalties), generated emails, per-invite portal tokens, delivered→opened→…→submitted tracking, deadline reminders (7d/3d/24h/2h) | `services/invitations.py` |
| **6. Portal** | Token-scoped submission, drafts, unlimited revisions — each new version preserves the prior one with timestamps and submitter | `services/portal.py` |
| **7. Proposal parsing** | Base bid, alternates, allowances, unit prices, exclusions/qualifications/assumptions sections, lead times, schedule, warranty, payment terms, spec & drawing references — every field with source page, original text, confidence; low-confidence fields are flagged `needs_review` | `ai/proposal.py` |
| **8. Normalization** | Master Scope Matrix: synonym groups recognize "temporary power" = "temporary utilities", negation detection marks "no temporary services" as **excluded**, non-mentions as **missing**; manual overrides persist across rebuilds and *teach* the classifier new phrases | `ai/normalize.py`, `services/leveling.py` |
| **9. Leveling workspace** | Spreadsheet-style grid (bidders × scope × money rows), color-coded included/excluded/missing cells with hoverable source evidence, inline audited edits, click-to-override | `services/leveling.py`, `frontend/` |
| **10. Risk analysis** | Outlier pricing (±25% of median), math errors (line items vs lump sum), missing/excluded scope, unbalanced unit pricing, long lead times, excessive exclusions, expired insurance/licenses, low-confidence extractions — each with severity, cost impact, confidence, explanation, suggested action, evidence; acknowledge/dismiss/assign | `services/risk.py` |
| **11. Award** | Weighted best-value scoring (price/completeness/safety/schedule/quality/history/financial/responsiveness, configurable) minus open-risk penalties, committee comments, digital approvals permanently recorded, generated award letters | `services/award.py` |
| **12. Analytics** | Participation, avg bids/package, leveling savings by trade, vendor response & award distribution, diversity participation, risk exposure, AI confidence — portfolio-wide or per project | `services/analytics.py` |

## Architecture

```
bidlevel/
  db.py            engine/session (SQLite default, Postgres via BIDLEVEL_DATABASE_URL)
  models.py        schema — JSON columns for AI payloads, AuditEntry mirrors every edit
  ai/
    drawing.py     sheet/trade/quantity/scope extraction (pure, deterministic)
    proposal.py    proposal text -> structured fields with confidence + review flags
    normalize.py   scope taxonomy + synonym matching + override learning
  services/        one module per workflow step (see table above)
  main.py          FastAPI app; serves the frontend at /
  frontend/        single-file zero-build workspace UI
  demo.py          end-to-end demo seeder
tests/test_bidlevel_*.py
```

## Scope & honesty (what's deliberately not here)

- **Real OCR / CAD / BIM parsing** — the pipeline runs on extractable text.
  `documents.upload_document(text=…)` is the seam: plug a real OCR/vision
  service in behind `ai/drawing.analyze_document()` and everything
  downstream (packages, leveling, risk) works unchanged. Binary uploads
  are recorded honestly as "no extractable text", never fake-processed.
- **The AI is deterministic rules, not ML** — pattern extraction with real
  confidence scores and full provenance. That is the right, auditable first
  version; an LLM extraction layer can replace each `ai/` function behind
  the same contracts.
- **Email/calendar delivery** — invitation emails and reminders are
  generated and recorded, not SMTP-delivered (DealForge's `notify.py`
  dispatcher pattern is the template for wiring that).
- **Auth/permissions** — single-tenant demo; DealForge's token auth in this
  repo is the pattern to graduate to.
- **File storage** — document text lives in the DB; production wants object
  storage + background workers for processing.

## Tests

```bash
pytest tests/test_bidlevel_ai.py tests/test_bidlevel_services.py tests/test_bidlevel_api.py
```

Covers: sheet/trade/quantity/scope extraction, proposal parsing (including
the inferred-base-bid low-confidence path), synonym normalization with
negation and override learning, package versioning + audit, compliance
reminders idempotency, the full seeded workflow (leveling grid, risk kinds,
award scoring where the cheapest-but-riskiest bidder correctly loses),
proposal revision chains, and the HTTP API end-to-end including portal
token submission.
