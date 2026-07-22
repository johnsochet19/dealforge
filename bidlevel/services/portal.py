"""Bid submission portal: drafts, versioned submissions, automatic parsing.

Each new submission supersedes the prior current version but never deletes it —
the full revision chain stays queryable with timestamps and submitter identity.
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from bidlevel.ai.proposal import parse_proposal
from bidlevel.models import BidPackage, Invitation, Proposal
from bidlevel.services.invitations import record_event


def submit_proposal(
    db: Session,
    package_id: int,
    sub_id: int,
    text: str,
    submitted_by: str = "",
    invitation_id: int | None = None,
    draft: bool = False,
) -> Proposal:
    pkg = db.get(BidPackage, package_id)
    if pkg is None:
        raise ValueError(f"package {package_id} not found")

    prior = (
        db.query(Proposal)
        .filter_by(package_id=package_id, sub_id=sub_id, is_current=True)
        .one_or_none()
    )
    version = 1
    if prior is not None:
        version = prior.version + 1
        prior.is_current = False

    parsed = parse_proposal(text)
    prop = Proposal(
        package_id=package_id,
        sub_id=sub_id,
        invitation_id=invitation_id,
        version=version,
        submitted_by=submitted_by,
        raw_text=text,
        base_bid=parsed["base_bid"],
        extracted=parsed["extracted"],
        lines=parsed["lines"],
        needs_review=parsed["needs_review"],
        status="draft" if draft else "submitted",
    )
    db.add(prop)
    db.commit()
    db.refresh(prop)

    if not draft:
        inv = None
        if invitation_id:
            inv = db.get(Invitation, invitation_id)
        else:
            inv = db.query(Invitation).filter_by(package_id=package_id, sub_id=sub_id).one_or_none()
        if inv is not None:
            record_event(db, inv.id, "submitted")
        if pkg.status == "invited":
            pkg.status = "leveling"
            db.commit()
    return prop


def submit_via_token(db: Session, token: str, text: str, submitted_by: str = "", draft: bool = False) -> Proposal:
    inv = db.query(Invitation).filter_by(portal_token=token).one_or_none()
    if inv is None:
        raise ValueError("invalid portal token")
    return submit_proposal(db, inv.package_id, inv.sub_id, text,
                           submitted_by=submitted_by, invitation_id=inv.id, draft=draft)


def proposal_summary(p: Proposal, full: bool = False) -> dict:
    out = {
        "id": p.id, "package_id": p.package_id, "sub_id": p.sub_id,
        "sub_name": p.sub.name if p.sub else "", "version": p.version,
        "is_current": p.is_current, "status": p.status,
        "submitted_by": p.submitted_by, "submitted_at": p.submitted_at.isoformat(),
        "base_bid": p.base_bid, "needs_review": p.needs_review,
        "alternate_count": len(p.lines.get("alternates", [])),
        "exclusion_count": len(p.lines.get("exclusions", [])),
    }
    if full:
        out["extracted"] = p.extracted
        out["lines"] = p.lines
        out["raw_text"] = p.raw_text
    return out


def proposal_versions(db: Session, package_id: int, sub_id: int) -> list[dict]:
    rows = (
        db.query(Proposal)
        .filter_by(package_id=package_id, sub_id=sub_id)
        .order_by(Proposal.version)
        .all()
    )
    return [proposal_summary(r) for r in rows]
