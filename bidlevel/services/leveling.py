"""Bid leveling workspace: the normalized comparison grid + audited edits +
manual scope-mapping overrides that teach the normalizer."""
from __future__ import annotations

import json

from sqlalchemy.orm import Session

from bidlevel.ai import normalize
from bidlevel.models import (
    AuditEntry,
    BidPackage,
    Proposal,
    ScopeCategory,
    ScopeMapping,
    SynonymOverride,
)


def _statements(p: Proposal) -> list[str]:
    stmts = []
    for kind in ("inclusions", "exclusions", "qualifications", "assumptions"):
        for entry in p.lines.get(kind, []):
            text = entry["text"] if isinstance(entry, dict) else str(entry)
            if kind == "exclusions":
                text = f"excluded: {text}"
            stmts.append(text)
    for li in p.lines.get("line_items", []):
        stmts.append(li["description"])
    return stmts


def rebuild_scope_matrix(db: Session, package_id: int) -> dict:
    """(Re)build the Master Scope Matrix for a package from current proposals.

    Manual overrides are preserved: a mapping the user corrected is never
    overwritten by a rebuild, and stored synonym overrides feed the classifier.
    """
    pkg = db.get(BidPackage, package_id)
    if pkg is None:
        raise ValueError(f"package {package_id} not found")
    proposals = [p for p in pkg.proposals if p.is_current and p.status != "draft"]
    overrides = [(o.phrase, o.canonical_key) for o in db.query(SynonymOverride).all()]

    matrix = normalize.build_scope_matrix(
        [{"proposal_id": p.id, "statements": _statements(p)} for p in proposals],
        overrides=overrides,
    )

    manual = {
        (m.category_id, m.proposal_id): m
        for m in db.query(ScopeMapping).filter_by(package_id=package_id, manual_override=True).all()
    }
    manual_by_key = {}
    cat_by_id = {c.id: c for c in db.query(ScopeCategory).filter_by(package_id=package_id).all()}
    for (cat_id, pid), m in manual.items():
        cat = cat_by_id.get(cat_id)
        if cat:
            manual_by_key[(cat.canonical_key, pid)] = m

    # wipe machine-generated rows, keep manual ones out of the delete
    db.query(ScopeMapping).filter_by(package_id=package_id, manual_override=False).delete()
    db.query(ScopeCategory).filter_by(package_id=package_id).delete()
    db.flush()

    key_to_cat: dict[str, ScopeCategory] = {}
    for order, c in enumerate(matrix["categories"]):
        cat = ScopeCategory(package_id=package_id, name=c["name"],
                            canonical_key=c["canonical_key"], order=order)
        db.add(cat)
        db.flush()
        key_to_cat[c["canonical_key"]] = cat

    for pid, row in matrix["mappings"].items():
        for key, hit in row.items():
            cat = key_to_cat[key]
            kept = manual_by_key.get((key, pid))
            if kept is not None:
                kept.category_id = cat.id  # re-point preserved manual row at the new category row
                continue
            db.add(ScopeMapping(
                package_id=package_id, category_id=cat.id, proposal_id=pid,
                status=hit["status"], evidence=hit["evidence"], confidence=hit["confidence"],
            ))
    db.commit()
    return leveling_grid(db, package_id)


def override_mapping(db: Session, package_id: int, category_id: int, proposal_id: int,
                     status: str, user: str = "", reason: str = "",
                     learn_phrase: str = "") -> dict:
    """Manually correct a scope mapping. Optionally teach the phrase for the future."""
    m = (
        db.query(ScopeMapping)
        .filter_by(package_id=package_id, category_id=category_id, proposal_id=proposal_id)
        .one_or_none()
    )
    cat = db.get(ScopeCategory, category_id)
    if cat is None:
        raise ValueError("category not found")
    old = m.status if m else "missing"
    if m is None:
        m = ScopeMapping(package_id=package_id, category_id=category_id,
                         proposal_id=proposal_id, status=status, confidence=1.0)
        db.add(m)
    m.status = status
    m.confidence = 1.0
    m.manual_override = True
    db.add(AuditEntry(entity="scope_mapping", entity_id=proposal_id,
                      field=cat.canonical_key, old_value=old, new_value=status,
                      user=user, reason=reason))
    if learn_phrase.strip():
        db.add(SynonymOverride(phrase=learn_phrase.strip(), canonical_key=cat.canonical_key,
                               created_by=user))
    db.commit()
    return {"category_id": category_id, "proposal_id": proposal_id, "status": status,
            "manual_override": True}


def edit_proposal_value(db: Session, proposal_id: int, field: str, value,
                        user: str = "", reason: str = "") -> Proposal:
    """Inline edit of a leveled value (e.g. plugged base bid), fully audited."""
    p = db.get(Proposal, proposal_id)
    if p is None:
        raise ValueError(f"proposal {proposal_id} not found")
    if field == "base_bid":
        old = p.base_bid
        p.base_bid = float(value) if value is not None else None
    else:
        extracted = dict(p.extracted)
        old = extracted.get(field, {}).get("normalized")
        extracted[field] = {**extracted.get(field, {}), "normalized": value,
                            "confidence": 1.0, "value": str(value), "manually_edited": True}
        p.extracted = extracted
    db.add(AuditEntry(entity="proposal", entity_id=p.id, field=field,
                      old_value=json.dumps(old), new_value=json.dumps(value),
                      user=user, reason=reason))
    db.commit()
    db.refresh(p)
    return p


def leveling_grid(db: Session, package_id: int) -> dict:
    """The core screen's payload: bidders × scope rows × money rows."""
    pkg = db.get(BidPackage, package_id)
    if pkg is None:
        raise ValueError(f"package {package_id} not found")
    proposals = [p for p in pkg.proposals if p.is_current and p.status != "draft"]
    cats = (db.query(ScopeCategory).filter_by(package_id=package_id)
            .order_by(ScopeCategory.order).all())
    maps = db.query(ScopeMapping).filter_by(package_id=package_id).all()
    by_cell = {(m.category_id, m.proposal_id): m for m in maps}

    bidders = []
    for p in sorted(proposals, key=lambda x: (x.base_bid is None, x.base_bid or 0)):
        bidders.append({
            "proposal_id": p.id, "sub_id": p.sub_id,
            "sub_name": p.sub.name if p.sub else "",
            "version": p.version, "base_bid": p.base_bid,
            "needs_review": p.needs_review,
            "alternates": p.lines.get("alternates", []),
            "allowances": p.lines.get("allowances", []),
            "unit_prices": p.lines.get("unit_prices", []),
            "exclusions": p.lines.get("exclusions", []),
            "line_items": p.lines.get("line_items", []),
        })
    scope_rows = []
    for c in cats:
        cells = {}
        for p in proposals:
            m = by_cell.get((c.id, p.id))
            cells[p.id] = ({"status": m.status, "evidence": m.evidence,
                            "confidence": m.confidence, "manual_override": m.manual_override}
                           if m else {"status": "missing", "evidence": "", "confidence": 0.75,
                                      "manual_override": False})
        scope_rows.append({"category_id": c.id, "name": c.name,
                           "canonical_key": c.canonical_key, "cells": cells})

    bids = [b["base_bid"] for b in bidders if b["base_bid"] is not None]
    stats = {}
    if bids:
        stats = {"low": min(bids), "high": max(bids),
                 "avg": round(sum(bids) / len(bids), 2), "spread": round(max(bids) - min(bids), 2)}
    return {"package": {"id": pkg.id, "name": pkg.name, "trade": pkg.trade, "status": pkg.status},
            "bidders": bidders, "scope_rows": scope_rows, "stats": stats}


def audit_log(db: Session, entity: str | None = None, entity_id: int | None = None) -> list[dict]:
    q = db.query(AuditEntry).order_by(AuditEntry.at.desc())
    if entity:
        q = q.filter_by(entity=entity)
    if entity_id is not None:
        q = q.filter_by(entity_id=entity_id)
    return [{"id": a.id, "entity": a.entity, "entity_id": a.entity_id, "field": a.field,
             "old_value": a.old_value, "new_value": a.new_value, "user": a.user,
             "reason": a.reason, "at": a.at.isoformat()} for a in q.limit(500).all()]
