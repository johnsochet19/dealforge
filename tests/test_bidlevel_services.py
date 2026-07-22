"""Service-layer tests over an in-memory SQLite database."""
from datetime import date, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import bidlevel.db as bldb
from bidlevel.db import Base
from bidlevel.models import BidPackage, Proposal, ScopeMapping
from bidlevel.services import (
    award,
    documents,
    invitations,
    leveling,
    packages,
    portal,
    projects,
    risk,
    subs,
)


@pytest.fixture()
def db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    s = Session()
    yield s
    s.close()


@pytest.fixture()
def seeded(db):
    from bidlevel.demo import seed_demo
    result = seed_demo(db)
    return db, result


def test_project_validation():
    assert projects.validate_project({}) != []
    errs = projects.validate_project({"name": "X", "number": "1", "bid_due_date": "2026-08-01",
                                      "estimated_value": "abc"})
    assert any("number" in e.lower() for e in errs)
    assert projects.validate_project({"name": "X", "number": "1", "bid_due_date": "2026-08-01"}) == []


def test_document_pipeline_and_package_builder(db):
    p = projects.create_project(db, {"name": "T", "number": "1", "bid_due_date": "2026-08-01"})
    doc = documents.upload_document(
        db, p.id, "set.pdf", "drawings",
        "A-101 - Floor Plan\nDrywall: 1,000 SF partitions\nConduit: 500 LF EMT electrical\n",
    )
    assert doc.processing_status == "complete"
    assert doc.pipeline["sheet_recognition"]["status"] == "complete"
    assert doc.confidence > 0
    trades = {t["trade"] for t in documents.detected_trades(db, p.id)}
    assert {"Drywall", "Electrical"} <= trades
    pkgs = packages.build_packages(db, p.id)
    assert {x.trade for x in pkgs} >= {"Drywall", "Electrical"}
    # idempotent
    assert packages.build_packages(db, p.id) == []
    dw = next(x for x in pkgs if x.trade == "Drywall")
    assert dw.content["quantities"][0]["unit"] == "SF"


def test_package_edit_versions_and_audit(db):
    p = projects.create_project(db, {"name": "T", "number": "1", "bid_due_date": "2026-08-01"})
    documents.upload_document(db, p.id, "d.pdf", "drawings", "roofing membrane 100 SF")
    pkg = packages.build_packages(db, p.id)[0]
    packages.edit_package(db, pkg.id, {"scope": "New scope"}, user="alice", reason="tighten")
    db.refresh(pkg)
    assert pkg.version == 2
    assert pkg.versions[0]["version"] == 1
    log = leveling.audit_log(db, "package", pkg.id)
    assert log and log[0]["user"] == "alice"


def test_sub_compliance_and_reminders(db):
    s = subs.create_sub(db, {"name": "Acme", "trades": ["Roofing"]})
    today = date.today()
    subs.add_document(db, s.id, "insurance", "GL", (today - timedelta(days=1)).isoformat())
    subs.add_document(db, s.id, "license", "Lic", (today + timedelta(days=10)).isoformat())
    comp = subs.compliance_status(s, today)
    assert comp["overall"] == "expired"
    fired = subs.run_expiry_reminders(db, today)
    assert any(f["kind"] == "license" for f in fired)
    # idempotent within the same window
    assert subs.run_expiry_reminders(db, today) == []


def test_full_workflow_leveling_risk_award(seeded):
    db, result = seeded
    pkg_id = result["electrical_package_id"]

    grid = leveling.leveling_grid(db, pkg_id)
    assert len(grid["bidders"]) == 3
    assert grid["stats"]["low"] == 1_590_000
    # temporary power: Volt included, Amp excluded, Bright excluded
    row = next(r for r in grid["scope_rows"] if r["canonical_key"] == "temporary_power")
    statuses = {b["sub_name"]: row["cells"][b["proposal_id"]]["status"] for b in grid["bidders"]}
    assert statuses["Volt Electric Co."] == "included"
    assert statuses["Amp Power Systems"] == "excluded"

    issues = db.query(Proposal).all()
    risks = risk.analyze_package(db, pkg_id)
    kinds = {r.kind for r in risks}
    assert "abnormally_low_price" in kinds          # Bright at 1.59M vs 2.18/2.45M median
    assert "expired_insurance" in kinds             # Bright's expired GL
    assert "excessive_exclusions" in kinds          # Bright has 8 exclusions
    low = next(r for r in risks if r.kind == "abnormally_low_price")
    assert low.cost_impact and low.explanation and low.suggested_action

    rec = award.recommend(db, pkg_id)
    assert rec["recommendation"] is not None
    assert len(rec["finalists"]) == 3
    # every finalist has an explainable breakdown
    assert set(rec["finalists"][0]["breakdown"]) >= {"price", "completeness", "safety"}
    # Bright is cheapest but penalized: should not win
    assert rec["recommendation"]["sub_name"] != "Bright Current LLC"

    a = award.create_award(db, pkg_id, rec["recommendation"]["proposal_id"], user="J. Ochet")
    assert "AWARD LETTER" in a.award_letter
    award.add_comment(db, a.id, "committee", "Approved in review meeting")
    a = award.record_approval(db, a.id, "VP Preconstruction", "approved")
    assert a.status == "approved"
    assert db.get(BidPackage, pkg_id).status == "awarded"


def test_proposal_versioning_and_revision_chain(seeded):
    db, result = seeded
    pkg_id = result["electrical_package_id"]
    grid = leveling.leveling_grid(db, pkg_id)
    b = grid["bidders"][0]
    p2 = portal.submit_proposal(db, pkg_id, b["sub_id"], "Base Bid: $1,650,000",
                                submitted_by="rev2@example.com")
    assert p2.version == 2 and p2.is_current
    versions = portal.proposal_versions(db, pkg_id, b["sub_id"])
    assert [v["version"] for v in versions] == [1, 2]
    assert versions[0]["is_current"] is False


def test_manual_override_survives_rebuild_and_learns(seeded):
    db, result = seeded
    pkg_id = result["electrical_package_id"]
    grid = leveling.leveling_grid(db, pkg_id)
    row = next(r for r in grid["scope_rows"] if r["canonical_key"] == "temporary_power")
    # find a bidder marked missing/excluded and override to included
    target = None
    for b in grid["bidders"]:
        if row["cells"][b["proposal_id"]]["status"] != "included":
            target = b
            break
    assert target
    leveling.override_mapping(db, pkg_id, row["category_id"], target["proposal_id"],
                              "included", user="alice", learn_phrase="site power hookups")
    grid2 = leveling.rebuild_scope_matrix(db, pkg_id)
    row2 = next(r for r in grid2["scope_rows"] if r["canonical_key"] == "temporary_power")
    cell = row2["cells"][target["proposal_id"]]
    assert cell["status"] == "included" and cell["manual_override"] is True
    # taught phrase now classifies
    from bidlevel.ai.normalize import classify_statement
    from bidlevel.models import SynonymOverride
    ov = [(o.phrase, o.canonical_key) for o in db.query(SynonymOverride).all()]
    hits = classify_statement("site power hookups by GC", ov)
    assert any(h["canonical_key"] == "temporary_power" for h in hits)


def test_invitation_tracking_and_reminders(seeded):
    db, result = seeded
    pkg_id = result["electrical_package_id"]
    invs = db.query(__import__("bidlevel.models", fromlist=["Invitation"]).Invitation)\
        .filter_by(package_id=pkg_id).all()
    assert invs
    summ = invitations.invitation_summary(invs[0])
    assert summ["flags"]["delivered"] and summ["flags"]["opened"]
    fired = invitations.run_reminders(db)
    # bid due in 14 days -> only the 168h window is within range? 14d=336h > 168h, so none yet
    assert all(f["window_hours"] <= 168 for f in fired)


def test_edit_value_audited(seeded):
    db, result = seeded
    pkg_id = result["electrical_package_id"]
    grid = leveling.leveling_grid(db, pkg_id)
    pid = grid["bidders"][0]["proposal_id"]
    leveling.edit_proposal_value(db, pid, "base_bid", 1_600_000, user="bob", reason="plug")
    assert db.get(Proposal, pid).base_bid == 1_600_000
    log = leveling.audit_log(db, "proposal", pid)
    assert log[0]["field"] == "base_bid" and log[0]["user"] == "bob"
