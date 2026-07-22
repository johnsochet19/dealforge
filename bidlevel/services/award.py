"""Award workflow: weighted best-value recommendation, committee comments,
approval routing, and generated award documents."""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.orm import Session

from bidlevel.models import Award, BidPackage, Project, Proposal, RiskIssue, ScopeMapping, Subcontractor

DEFAULT_WEIGHTS = {
    "price": 0.35,
    "completeness": 0.20,
    "safety": 0.10,
    "schedule": 0.10,
    "quality": 0.10,
    "history": 0.05,
    "financial": 0.05,
    "responsiveness": 0.05,
}


def _completeness(db: Session, package_id: int, proposal_id: int) -> float:
    maps = db.query(ScopeMapping).filter_by(package_id=package_id, proposal_id=proposal_id).all()
    if not maps:
        return 0.5  # no matrix yet -> neutral
    included = sum(1 for m in maps if m.status == "included")
    return included / len(maps)


def score_proposals(db: Session, package_id: int, weights: dict | None = None) -> list[dict]:
    """Score every current proposal 0..100 with a per-criterion breakdown."""
    pkg = db.get(BidPackage, package_id)
    if pkg is None:
        raise ValueError(f"package {package_id} not found")
    w = {**DEFAULT_WEIGHTS, **(weights or {})}
    total_w = sum(w.values()) or 1.0
    w = {k: v / total_w for k, v in w.items()}

    proposals = [p for p in pkg.proposals if p.is_current and p.status != "draft" and p.base_bid]
    if not proposals:
        return []
    low = min(p.base_bid for p in proposals)

    risk_penalty: dict[int, float] = {}
    for issue in db.query(RiskIssue).filter_by(package_id=package_id).filter(
            RiskIssue.status.in_(["open", "acknowledged", "assigned"])).all():
        if issue.proposal_id:
            pen = {"low": 1, "medium": 3, "high": 6, "critical": 12}.get(issue.severity, 0)
            risk_penalty[issue.proposal_id] = risk_penalty.get(issue.proposal_id, 0) + pen

    out = []
    for p in proposals:
        sub = db.get(Subcontractor, p.sub_id)
        crit = {}
        crit["price"] = low / p.base_bid  # 1.0 for low bidder, proportional otherwise
        crit["completeness"] = _completeness(db, package_id, p.id)
        crit["safety"] = (sub.safety_score / 100) if sub and sub.safety_score is not None else 0.5
        crit["schedule"] = (sub.schedule_rating / 5) if sub and sub.schedule_rating is not None else 0.5
        crit["quality"] = (sub.quality_rating / 5) if sub and sub.quality_rating is not None else 0.5
        crit["history"] = sub.award_rate if sub and sub.award_rate is not None else 0.5
        crit["financial"] = (sub.financial_stability / 100) if sub and sub.financial_stability is not None else 0.5
        if sub and sub.avg_bid_response_days is not None:
            crit["responsiveness"] = max(0.0, 1 - sub.avg_bid_response_days / 14)
        else:
            crit["responsiveness"] = 0.5
        weighted = sum(crit[k] * w[k] for k in w) * 100
        penalty = risk_penalty.get(p.id, 0)
        out.append({
            "proposal_id": p.id, "sub_id": p.sub_id, "sub_name": sub.name if sub else "",
            "base_bid": p.base_bid, "score": round(max(0, weighted - penalty), 1),
            "breakdown": {k: round(v, 3) for k, v in crit.items()},
            "weights": w, "risk_penalty": penalty,
        })
    out.sort(key=lambda r: r["score"], reverse=True)
    return out


def recommend(db: Session, package_id: int, weights: dict | None = None) -> dict:
    scores = score_proposals(db, package_id, weights)
    if not scores:
        return {"recommendation": None, "finalists": []}
    return {"recommendation": scores[0], "finalists": scores}


def create_award(db: Session, package_id: int, proposal_id: int,
                 weights: dict | None = None, user: str = "") -> Award:
    pkg = db.get(BidPackage, package_id)
    prop = db.get(Proposal, proposal_id)
    if pkg is None or prop is None:
        raise ValueError("package or proposal not found")
    scores = score_proposals(db, package_id, weights)
    project = db.get(Project, pkg.project_id)
    sub = db.get(Subcontractor, prop.sub_id)
    letter = (
        f"AWARD LETTER\n\n{project.general_contractor or 'General Contractor'}\n"
        f"Project: {project.name} (#{project.number})\n"
        f"Package: {pkg.name}\n\n"
        f"Dear {sub.name},\n\n"
        f"We are pleased to award you the {pkg.trade} package in the amount of "
        f"${prop.base_bid:,.2f}, per your proposal rev {prop.version} dated "
        f"{prop.submitted_at.date().isoformat()}, subject to execution of a subcontract "
        f"agreement and current insurance and bonding.\n\n"
        f"Sincerely,\n{user or project.estimator or 'Estimating'}\n"
    )
    award = Award(
        package_id=package_id, sub_id=prop.sub_id, proposal_id=proposal_id,
        weights=weights or DEFAULT_WEIGHTS, scores={"finalists": scores},
        award_letter=letter,
    )
    db.add(award)
    db.commit()
    db.refresh(award)
    return award


def add_comment(db: Session, award_id: int, user: str, text: str) -> Award:
    a = db.get(Award, award_id)
    if a is None:
        raise ValueError(f"award {award_id} not found")
    a.comments = list(a.comments) + [
        {"user": user, "text": text, "at": datetime.now(timezone.utc).isoformat()}
    ]
    db.commit()
    db.refresh(a)
    return a


def record_approval(db: Session, award_id: int, approver: str, decision: str, note: str = "") -> Award:
    if decision not in ("approved", "rejected"):
        raise ValueError("decision must be 'approved' or 'rejected'")
    a = db.get(Award, award_id)
    if a is None:
        raise ValueError(f"award {award_id} not found")
    a.approvals = list(a.approvals) + [
        {"approver": approver, "decision": decision, "note": note,
         "at": datetime.now(timezone.utc).isoformat()}
    ]
    if decision == "approved":
        a.status = "approved"
        pkg = db.get(BidPackage, a.package_id)
        pkg.status = "awarded"
    else:
        a.status = "recommended"
    db.commit()
    db.refresh(a)
    return a


def award_summary(a: Award) -> dict:
    return {"id": a.id, "package_id": a.package_id, "sub_id": a.sub_id,
            "sub_name": a.sub.name if a.sub else "", "proposal_id": a.proposal_id,
            "status": a.status, "weights": a.weights, "scores": a.scores,
            "comments": a.comments, "approvals": a.approvals,
            "award_letter": a.award_letter, "created_at": a.created_at.isoformat()}
