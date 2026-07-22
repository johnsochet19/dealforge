"""AI risk analysis over every bid in a package.

Each check emits explainable issues: severity, estimated cost impact,
confidence, plain-language explanation, suggested action, and evidence.
"""
from __future__ import annotations

import statistics
from datetime import date

from sqlalchemy.orm import Session

from bidlevel.models import (
    BidPackage,
    Proposal,
    RiskIssue,
    ScopeCategory,
    ScopeMapping,
    Subcontractor,
)
from bidlevel.services.subs import compliance_status

EXCESSIVE_EXCLUSIONS = 8
OUTLIER_PCT = 0.25  # ±25% of median flags pricing


def _issue(package_id: int, proposal_id: int | None, kind: str, severity: str,
           explanation: str, action: str, evidence: str = "",
           cost_impact: float | None = None, confidence: float = 0.8) -> RiskIssue:
    return RiskIssue(
        package_id=package_id, proposal_id=proposal_id, kind=kind, severity=severity,
        cost_impact=cost_impact, confidence=confidence, explanation=explanation,
        suggested_action=action, evidence=evidence,
    )


def analyze_package(db: Session, package_id: int, today: date | None = None) -> list[RiskIssue]:
    """Run every risk check; replaces prior open machine-generated issues."""
    pkg = db.get(BidPackage, package_id)
    if pkg is None:
        raise ValueError(f"package {package_id} not found")
    today = today or date.today()
    proposals = [p for p in pkg.proposals if p.is_current and p.status != "draft"]

    # keep acknowledged/dismissed/assigned issues; refresh only open ones
    db.query(RiskIssue).filter_by(package_id=package_id, status="open").delete()
    issues: list[RiskIssue] = []

    bids = [(p, p.base_bid) for p in proposals if p.base_bid is not None]
    if len(bids) >= 3:
        median = statistics.median(b for _, b in bids)
        for p, b in bids:
            dev = (b - median) / median if median else 0
            if dev <= -OUTLIER_PCT:
                issues.append(_issue(
                    package_id, p.id, "abnormally_low_price", "high",
                    f"Base bid ${b:,.0f} is {abs(dev):.0%} below the median of ${median:,.0f}. "
                    "Low bids often signal missed scope or a mistake.",
                    "Hold a scope-review call before shortlisting; ask the bidder to confirm inclusions.",
                    evidence=f"median=${median:,.0f}, bid=${b:,.0f}",
                    cost_impact=round(median - b, 2), confidence=0.85,
                ))
            elif dev >= OUTLIER_PCT:
                issues.append(_issue(
                    package_id, p.id, "abnormally_high_price", "medium",
                    f"Base bid ${b:,.0f} is {dev:.0%} above the median of ${median:,.0f}.",
                    "Check for scope gold-plating, market conditions, or limited interest.",
                    evidence=f"median=${median:,.0f}, bid=${b:,.0f}",
                    cost_impact=round(b - median, 2), confidence=0.8,
                ))

    for p in proposals:
        # math check: line items vs stated base bid
        line_items = p.lines.get("line_items", [])
        stated = p.extracted.get("base_bid", {})
        if line_items and stated.get("confidence", 0) >= 0.9 and p.base_bid:
            total = sum(li["amount"] for li in line_items)
            if total and abs(total - p.base_bid) / p.base_bid > 0.02:
                issues.append(_issue(
                    package_id, p.id, "math_error", "high",
                    f"Line items sum to ${total:,.0f} but the stated base bid is ${p.base_bid:,.0f} "
                    f"(difference ${abs(total - p.base_bid):,.0f}).",
                    "Ask the bidder to reconcile the breakdown against the lump sum.",
                    evidence=f"sum(line_items)=${total:,.0f} vs base_bid=${p.base_bid:,.0f}",
                    cost_impact=round(abs(total - p.base_bid), 2), confidence=0.9,
                ))
        # excessive exclusions
        excl = p.lines.get("exclusions", [])
        if len(excl) >= EXCESSIVE_EXCLUSIONS:
            issues.append(_issue(
                package_id, p.id, "excessive_exclusions", "medium",
                f"{len(excl)} exclusions listed — unusually many for one package.",
                "Level the exclusions against other bidders and price the gaps.",
                evidence="; ".join(e["text"][:60] for e in excl[:5]), confidence=0.75,
            ))
        # unbalanced unit pricing
        ups = p.lines.get("unit_prices", [])
        amounts = [u["amount"] for u in ups if u.get("amount")]
        if len(amounts) >= 3:
            med = statistics.median(amounts)
            for u in ups:
                if med and u.get("amount") and u["amount"] > med * 10:
                    issues.append(_issue(
                        package_id, p.id, "unbalanced_unit_pricing", "medium",
                        f"Unit price '{u.get('description', '')[:60]}' at ${u['amount']:,.2f} is >10× "
                        f"the median unit price (${med:,.2f}) — possible front-loading.",
                        "Compare against other bidders' unit prices before accepting.",
                        evidence=u.get("original_text", ""), confidence=0.7,
                    ))
        # long lead times
        lead = p.extracted.get("lead_time")
        if lead:
            txt = str(lead.get("normalized", "")).lower()
            import re as _re
            m = _re.search(r"(\d+)\s*(week|month)", txt)
            if m and ((m.group(2) == "week" and int(m.group(1)) >= 16) or
                      (m.group(2) == "month" and int(m.group(1)) >= 4)):
                issues.append(_issue(
                    package_id, p.id, "long_lead_time", "medium",
                    f"Quoted lead time '{lead['normalized']}' may threaten the construction start.",
                    "Verify procurement dates against the project schedule; consider early release.",
                    evidence=lead.get("original_text", ""), confidence=0.75,
                ))
        # compliance
        sub = db.get(Subcontractor, p.sub_id)
        if sub:
            comp = compliance_status(sub, today)
            for d in comp["documents"]:
                if d["state"] == "expired":
                    sev = "critical" if d["kind"] == "insurance" else "high"
                    issues.append(_issue(
                        package_id, p.id, f"expired_{d['kind']}", sev,
                        f"{sub.name}: {d['kind']} '{d['name']}' expired {d['expiration_date']}.",
                        "Request updated documentation before any award.",
                        evidence=f"expiration={d['expiration_date']}", confidence=1.0,
                    ))
        # low-confidence extractions awaiting human review
        if p.needs_review:
            issues.append(_issue(
                package_id, p.id, "needs_human_review", "low",
                f"Fields extracted below the confidence threshold: {', '.join(p.needs_review)}.",
                "Open the proposal and confirm the flagged values against the source text.",
                confidence=0.95,
            ))

    # missing scope from the leveling matrix
    cats = {c.id: c for c in db.query(ScopeCategory).filter_by(package_id=package_id).all()}
    for m in db.query(ScopeMapping).filter_by(package_id=package_id, status="missing").all():
        cat = cats.get(m.category_id)
        if cat is None:
            continue
        prop = db.get(Proposal, m.proposal_id)
        others = [p.base_bid for p in proposals if p.id != m.proposal_id and p.base_bid]
        impact = None
        if others:
            impact = round(statistics.median(others) * 0.02, 2)  # rough 2%-of-median placeholder
        issues.append(_issue(
            package_id, m.proposal_id, "missing_scope", "high",
            f"'{cat.name}' is not addressed by {prop.sub.name if prop and prop.sub else 'this bidder'} "
            "while other bidders address it.",
            "Confirm whether the scope is included; if not, add a plug value when leveling.",
            evidence=f"category={cat.canonical_key}", cost_impact=impact, confidence=m.confidence,
        ))
    for m in db.query(ScopeMapping).filter_by(package_id=package_id, status="excluded").all():
        cat = cats.get(m.category_id)
        if cat is None:
            continue
        issues.append(_issue(
            package_id, m.proposal_id, "excluded_scope", "medium",
            f"'{cat.name}' explicitly excluded.",
            "Price the exclusion or assign it to another package.",
            evidence=m.evidence, confidence=m.confidence,
        ))

    for i in issues:
        db.add(i)
    db.commit()
    return issues


def set_issue_status(db: Session, issue_id: int, status: str, assigned_to: str = "") -> RiskIssue:
    if status not in ("open", "acknowledged", "dismissed", "assigned"):
        raise ValueError(f"invalid status '{status}'")
    issue = db.get(RiskIssue, issue_id)
    if issue is None:
        raise ValueError(f"issue {issue_id} not found")
    issue.status = status
    issue.assigned_to = assigned_to if status == "assigned" else issue.assigned_to
    db.commit()
    db.refresh(issue)
    return issue


def issue_summary(i: RiskIssue) -> dict:
    return {"id": i.id, "package_id": i.package_id, "proposal_id": i.proposal_id,
            "kind": i.kind, "severity": i.severity, "cost_impact": i.cost_impact,
            "confidence": i.confidence, "explanation": i.explanation,
            "suggested_action": i.suggested_action, "evidence": i.evidence,
            "status": i.status, "assigned_to": i.assigned_to,
            "created_at": i.created_at.isoformat()}
