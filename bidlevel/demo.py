"""Seed a realistic end-to-end demo: project -> documents -> AI pipeline ->
packages -> subs -> invitations -> proposals -> leveling -> risk -> award-ready."""
from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy.orm import Session

from bidlevel.services import documents, invitations, leveling, packages, portal, projects, risk, subs

DRAWING_TEXT = """\
A-101 - First Floor Plan
Revision 4 dated 2026-05-01
Scale: 1/8" = 1'-0"
General note: All dimensions to face of stud unless noted otherwise.
Door schedule: see A-601 for hardware sets.
Drywall partitions: 12,400 SF interior gypsum wall area
Acoustical ceiling tile: 9,800 SF suspended ceiling
Paint: 24,000 SF wall area, two coats
A-102 - Second Floor Plan
Revision 3 dated 2026-04-15
Scale: 1/8" = 1'-0"
Flooring tile: 4,200 SF porcelain tile Room 201
Carpet: 6,300 SF Room 210
S-201 - Foundation Plan
Revision 2 dated 2026-04-20
Scale: 1/4" = 1'-0"
Keynote 3: continuous footing per detail 5/S-501
Concrete footings: 850 CY cast-in-place concrete
Rebar: 42 TONS reinforcing steel
Slab on grade: 38,000 SF 5" slab
E-401 - Electrical Power Plan
Revision 1 dated 2026-03-30
Lighting fixtures: 640 EA LED fixtures
Panelboard: 12 EA branch panelboards
Conduit: 18,500 LF EMT conduit
M-301 - Mechanical Plan
Duct: 9,200 LF galvanized duct
RTU equipment: 8 EA rooftop units
P-101 - Plumbing Plan
Fixture count: 96 EA plumbing fixtures
Pipe: 12,000 LF copper and PVC pipe
"""

SPEC_TEXT = """\
Section 09 91 23 Interior Painting
Install owner supplied hardware.
Furnish and install all gypsum drywall assemblies.
Temporary power by general contractor - excluded from trade scopes.
Roofing membrane fully adhered per Section 07 54 00.
Excavation and grading per geotechnical report.
Fire sprinkler system design-build per NFPA 13.
Landscaping and irrigation by others.
Elevator equipment: furnish only - installation by others.
"""

SUBS = [
    dict(name="Volt Electric Co.", trades=["Electrical"], csi_divisions=["26"],
         coverage_area="Statewide", employees=120, annual_revenue=42_000_000,
         bonding_capacity=20_000_000, emr=0.82, safety_score=93, osha_recordables=1,
         quality_rating=4.6, schedule_rating=4.4, communication_rating=4.2,
         financial_stability=88, preferred_vendor=True, union_status="union",
         diversity=[], contact_email="bids@voltelectric.example",
         avg_bid_response_days=4, award_rate=0.38),
    dict(name="Amp Power Systems", trades=["Electrical", "Low Voltage"], csi_divisions=["26", "27"],
         coverage_area="Regional", employees=60, annual_revenue=18_000_000,
         bonding_capacity=8_000_000, emr=1.05, safety_score=81, osha_recordables=3,
         quality_rating=4.0, schedule_rating=3.8, communication_rating=4.5,
         financial_stability=72, union_status="non-union", diversity=["MBE"],
         contact_email="estimating@amppower.example", avg_bid_response_days=6, award_rate=0.22),
    dict(name="Bright Current LLC", trades=["Electrical"], csi_divisions=["26"],
         coverage_area="Metro", employees=25, annual_revenue=6_000_000,
         bonding_capacity=2_500_000, emr=0.95, safety_score=76, osha_recordables=2,
         quality_rating=3.6, schedule_rating=3.9, communication_rating=3.5,
         financial_stability=61, union_status="non-union", diversity=["WBE"],
         contact_email="hello@brightcurrent.example", avg_bid_response_days=9, award_rate=0.15),
    dict(name="Summit Drywall & Acoustics", trades=["Drywall", "Ceilings"], csi_divisions=["09"],
         coverage_area="Statewide", employees=200, annual_revenue=55_000_000,
         bonding_capacity=25_000_000, emr=0.9, safety_score=90, quality_rating=4.5,
         schedule_rating=4.6, communication_rating=4.1, financial_stability=85,
         preferred_vendor=True, union_status="union",
         contact_email="bids@summitdrywall.example", avg_bid_response_days=5, award_rate=0.31),
    dict(name="Interior Finishes Inc.", trades=["Drywall", "Painting", "Flooring"],
         csi_divisions=["09"], coverage_area="Regional", employees=85,
         annual_revenue=22_000_000, bonding_capacity=9_000_000, emr=1.1,
         safety_score=78, quality_rating=3.9, schedule_rating=4.0,
         communication_rating=3.8, financial_stability=70, union_status="non-union",
         diversity=["VBE"], contact_email="est@interiorfinishes.example",
         avg_bid_response_days=7, award_rate=0.19),
]

PROPOSAL_VOLT = """\
Volt Electric Co. — Proposal Rev 1
Base Bid: $2,450,000
Alternate 1: Generator upgrade - $185,000
Unit Price 1: Additional branch circuit @ $850 per EA
Allowance 1: Owner security rough-in $25,000
Inclusions:
- Temporary power for our own operations
- Shop drawings and engineering
- Testing and commissioning
- Cleanup of our debris daily
Exclusions:
- Sales tax
- Payment and performance bond
Schedule: 34 weeks
12 week lead time on switchgear
Payment terms: Net 30
"""

PROPOSAL_AMP = """\
Amp Power Systems Proposal
Total Bid Amount: $2,180,000
Alt 1: Generator upgrade - $210,000
Unit Price 1: Additional branch circuit @ $920 per EA
Inclusions:
- Permits and fees
- Shop drawings
Exclusions:
- Temporary power
- Testing and commissioning
- Cleanup
- Overtime labor
- Utility company fees
Schedule: 30 weeks
20 week lead time on switchgear
"""

PROPOSAL_BRIGHT = """\
Bright Current LLC bid
Base bid: $1,590,000
Lighting fixtures: $410,000
Panelboards and gear: $520,000
Branch wiring: $480,000
Fire alarm: $120,000
Exclusions:
- Bond
- Permits
- Temporary power
- Generator
- Lighting controls programming
- Site utilities
- Trenching
- Concrete pads
"""


def seed_demo(db: Session) -> dict:
    today = date.today()
    errors = projects.validate_project({"name": "Riverside Medical Office Building",
                                        "number": "26-104", "bid_due_date": ""})
    assert errors  # sanity: validation works

    project = projects.create_project(db, {
        "name": "Riverside Medical Office Building",
        "number": "26-104",
        "client": "Riverside Health Partners",
        "owner": "Riverside Health Partners",
        "architect": "Meridian Design Group",
        "engineer": "Apex Consulting Engineers",
        "general_contractor": "Cornerstone Construction",
        "estimator": "J. Ochet",
        "project_manager": "T. Alvarez",
        "address": "1200 River Rd, Springfield",
        "time_zone": "America/Chicago",
        "estimated_value": 28_500_000,
        "bid_due_date": (today + timedelta(days=14)).isoformat(),
        "construction_start": (today + timedelta(days=90)).isoformat(),
        "completion_date": (today + timedelta(days=600)).isoformat(),
        "project_type": "Healthcare / Medical Office",
        "delivery_method": "GMP",
        "union_status": "mixed",
        "tax_jurisdiction": "IL — Sangamon County",
        "insurance_requirements": "GL $2M/$4M, Auto $1M, Umbrella $5M, WC statutory",
        "bonding_requirements": "P&P bond required for packages over $500k",
    })

    documents.upload_document(db, project.id, "A-Set_Drawings.pdf", "drawings", DRAWING_TEXT)
    documents.upload_document(db, project.id, "Project_Manual_Specs.pdf", "specifications", SPEC_TEXT)

    pkgs = packages.build_packages(db, project.id)

    sub_rows = [subs.create_sub(db, s) for s in SUBS]
    subs.add_document(db, sub_rows[0].id, "insurance", "GL Certificate",
                      (today + timedelta(days=200)).isoformat())
    subs.add_document(db, sub_rows[1].id, "insurance", "GL Certificate",
                      (today + timedelta(days=20)).isoformat())     # expiring soon
    subs.add_document(db, sub_rows[2].id, "insurance", "GL Certificate",
                      (today - timedelta(days=10)).isoformat())     # expired
    subs.add_document(db, sub_rows[2].id, "license", "Electrical License",
                      (today + timedelta(days=400)).isoformat())

    elec = next((p for p in pkgs if p.trade == "Electrical"), None)
    result = {"project_id": project.id, "packages": len(pkgs), "subs": len(sub_rows)}
    if elec is not None:
        recs = invitations.recommend_subs(db, elec.id)
        rec_map = {r["sub_id"]: r for r in recs}
        invs = invitations.send_invitations(db, elec.id, [r["sub_id"] for r in recs], rec_map)
        for inv in invs:
            invitations.record_event(db, inv.id, "opened")
            invitations.record_event(db, inv.id, "downloaded")
        by_sub = {i.sub_id: i for i in invs}
        for sub_row, text in ((sub_rows[0], PROPOSAL_VOLT), (sub_rows[1], PROPOSAL_AMP),
                              (sub_rows[2], PROPOSAL_BRIGHT)):
            inv = by_sub.get(sub_row.id)
            portal.submit_proposal(db, elec.id, sub_row.id, text,
                                   submitted_by=f"estimating@{sub_row.name.split()[0].lower()}.example",
                                   invitation_id=inv.id if inv else None)
        leveling.rebuild_scope_matrix(db, elec.id)
        risk.analyze_package(db, elec.id)
        result["electrical_package_id"] = elec.id
    return result
