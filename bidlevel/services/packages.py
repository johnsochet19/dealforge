"""Automatic bid package builder + versioned edits.

Every trade the AI detects becomes a suggested package pre-loaded with the
project's relevant sheets, quantities, scope items, and requirements. Every
subsequent edit snapshots the prior content into the version history and the
audit log.
"""
from __future__ import annotations

import json

from sqlalchemy.orm import Session

from bidlevel.models import (
    AuditEntry,
    BidPackage,
    DetectedQuantity,
    DrawingSheet,
    Project,
    ProjectDocument,
    ScopeItem,
)
from bidlevel.services.documents import detected_trades


def build_packages(db: Session, project_id: int) -> list[BidPackage]:
    """Create one draft package per AI-detected trade (idempotent by trade)."""
    project = db.get(Project, project_id)
    if project is None:
        raise ValueError(f"project {project_id} not found")
    existing = {p.trade for p in db.query(BidPackage).filter_by(project_id=project_id).all()}
    created: list[BidPackage] = []

    sheets = db.query(DrawingSheet).filter_by(project_id=project_id).all()
    quantities = db.query(DetectedQuantity).filter_by(project_id=project_id).all()
    scope_items = db.query(ScopeItem).filter_by(project_id=project_id).all()
    specs = [d.filename for d in db.query(ProjectDocument)
             .filter_by(project_id=project_id, doc_type="specifications").all()]

    for t in detected_trades(db, project_id):
        trade = t["trade"]
        if trade in existing:
            continue
        trade_qtys = [q for q in quantities if q.trade == trade]
        trade_scope = [s for s in scope_items if s.trade == trade]
        included = [s.original_text for s in trade_scope if not s.excluded]
        excluded = [s.original_text for s in trade_scope if s.excluded]
        content = {
            "scope": f"Furnish and install all {trade.lower()} work per drawings and specifications.",
            "drawings": [s.number for s in sheets],
            "specifications": specs,
            "included": included,
            "excluded": excluded,
            "alternates": [],
            "allowances": [],
            "unit_price_requests": [
                {"description": f"{q.description[:80]}", "unit": q.unit} for q in trade_qtys[:5]
            ],
            "quantities": [
                {"description": q.description, "value": q.value, "unit": q.unit,
                 "confidence": q.confidence, "source_sheet": q.source_sheet}
                for q in trade_qtys
            ],
            "bid_instructions": f"Submit lump-sum proposal for {trade} by {project.bid_due_date}.",
            "schedule": {"start": project.construction_start, "completion": project.completion_date},
            "insurance_requirements": project.insurance_requirements,
            "bond_requirements": project.bonding_requirements,
            "special_notes": [],
        }
        pkg = BidPackage(
            project_id=project_id,
            name=f"{trade} — {project.name}",
            trade=trade,
            csi_division=t["csi_division"],
            content=content,
            ai_generated=True,
        )
        db.add(pkg)
        created.append(pkg)
    db.commit()
    for p in created:
        db.refresh(p)
    return created


def edit_package(db: Session, package_id: int, changes: dict, user: str = "", reason: str = "") -> BidPackage:
    """Apply content changes, snapshotting the previous version and auditing each field."""
    pkg = db.get(BidPackage, package_id)
    if pkg is None:
        raise ValueError(f"package {package_id} not found")
    snapshot = {"version": pkg.version, "content": pkg.content, "user": user}
    new_content = dict(pkg.content)
    for field, value in changes.items():
        old = new_content.get(field)
        if old == value:
            continue
        db.add(AuditEntry(
            entity="package", entity_id=pkg.id, field=field,
            old_value=json.dumps(old), new_value=json.dumps(value),
            user=user, reason=reason,
        ))
        new_content[field] = value
    pkg.content = new_content
    pkg.versions = list(pkg.versions) + [snapshot]
    pkg.version += 1
    db.commit()
    db.refresh(pkg)
    return pkg


def package_summary(p: BidPackage, include_content: bool = True) -> dict:
    out = {
        "id": p.id, "project_id": p.project_id, "name": p.name, "trade": p.trade,
        "csi_division": p.csi_division, "status": p.status, "version": p.version,
        "ai_generated": p.ai_generated, "created_at": p.created_at.isoformat(),
        "invitation_count": len(p.invitations),
        "proposal_count": len([x for x in p.proposals if x.is_current]),
    }
    if include_content:
        out["content"] = p.content
        out["versions"] = p.versions
    return out
