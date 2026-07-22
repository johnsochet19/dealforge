"""Executive analytics: portfolio + per-project procurement metrics."""
from __future__ import annotations

from sqlalchemy.orm import Session

from bidlevel.models import (
    Award,
    BidPackage,
    Invitation,
    Project,
    Proposal,
    RiskIssue,
    Subcontractor,
)


def dashboard(db: Session, project_id: int | None = None) -> dict:
    projects = ([db.get(Project, project_id)] if project_id
                else db.query(Project).all())
    projects = [p for p in projects if p is not None]
    pids = [p.id for p in projects]

    pkgs = db.query(BidPackage).filter(BidPackage.project_id.in_(pids)).all() if pids else []
    pkg_ids = [p.id for p in pkgs]
    invs = db.query(Invitation).filter(Invitation.package_id.in_(pkg_ids)).all() if pkg_ids else []
    props = (db.query(Proposal).filter(Proposal.package_id.in_(pkg_ids), Proposal.is_current)
             .all() if pkg_ids else [])
    awards = db.query(Award).filter(Award.package_id.in_(pkg_ids)).all() if pkg_ids else []
    risks = db.query(RiskIssue).filter(RiskIssue.package_id.in_(pkg_ids)).all() if pkg_ids else []

    # participation
    invited = len(invs)
    submitted = len({(p.package_id, p.sub_id) for p in props if p.status != "draft"})
    participation = round(submitted / invited, 3) if invited else None

    bids_per_pkg = {}
    for p in props:
        if p.status != "draft":
            bids_per_pkg[p.package_id] = bids_per_pkg.get(p.package_id, 0) + 1
    avg_bids = round(sum(bids_per_pkg.values()) / len(bids_per_pkg), 2) if bids_per_pkg else 0

    # savings: low bid vs average bid per package with >=2 bids
    savings_by_trade: dict[str, float] = {}
    for pkg in pkgs:
        bids = [p.base_bid for p in props if p.package_id == pkg.id and p.base_bid]
        if len(bids) >= 2:
            savings_by_trade[pkg.trade] = round(
                savings_by_trade.get(pkg.trade, 0) + (sum(bids) / len(bids) - min(bids)), 2)

    # awarded vs low/avg
    awarded_total = 0.0
    for a in awards:
        prop = db.get(Proposal, a.proposal_id)
        if prop and prop.base_bid:
            awarded_total += prop.base_bid

    # vendor responsiveness + award distribution
    by_vendor: dict[int, dict] = {}
    for inv in invs:
        v = by_vendor.setdefault(inv.sub_id, {"invited": 0, "submitted": 0, "awarded": 0})
        v["invited"] += 1
        if inv.status == "submitted":
            v["submitted"] += 1
    for a in awards:
        by_vendor.setdefault(a.sub_id, {"invited": 0, "submitted": 0, "awarded": 0})["awarded"] += 1
    vendors = []
    for sid, v in by_vendor.items():
        sub = db.get(Subcontractor, sid)
        vendors.append({"sub_id": sid, "name": sub.name if sub else "", **v,
                        "response_rate": round(v["submitted"] / v["invited"], 2) if v["invited"] else None})
    vendors.sort(key=lambda x: x["awarded"], reverse=True)

    # diversity participation among submitting subs
    div_counts: dict[str, int] = {}
    submitting_subs = {p.sub_id for p in props if p.status != "draft"}
    for sid in submitting_subs:
        sub = db.get(Subcontractor, sid)
        for cert in (sub.diversity if sub else []) or []:
            div_counts[cert] = div_counts.get(cert, 0) + 1

    risk_counts: dict[str, int] = {}
    open_impact = 0.0
    for r in risks:
        risk_counts[r.severity] = risk_counts.get(r.severity, 0) + 1
        if r.status == "open" and r.cost_impact:
            open_impact += r.cost_impact

    conf_values = [p.extracted.get("base_bid", {}).get("confidence") for p in props]
    conf_values = [c for c in conf_values if c is not None]

    return {
        "projects": len(projects),
        "packages": len(pkgs),
        "packages_by_status": _count_by(pkgs, "status"),
        "invitations": invited,
        "participation_rate": participation,
        "avg_bids_per_package": avg_bids,
        "savings_by_trade": savings_by_trade,
        "total_leveling_savings": round(sum(savings_by_trade.values()), 2),
        "awarded_total": round(awarded_total, 2),
        "estimated_value_total": round(sum(p.estimated_value or 0 for p in projects), 2),
        "vendors": vendors[:25],
        "diversity_participation": div_counts,
        "risk_by_severity": risk_counts,
        "open_risk_cost_exposure": round(open_impact, 2),
        "avg_extraction_confidence": round(sum(conf_values) / len(conf_values), 3) if conf_values else None,
    }


def _count_by(rows, attr) -> dict:
    out: dict[str, int] = {}
    for r in rows:
        k = getattr(r, attr)
        out[k] = out.get(k, 0) + 1
    return out
