"""Project creation wizard: validation + persistence."""
from __future__ import annotations

from sqlalchemy.orm import Session

from bidlevel.models import Project

REQUIRED_FIELDS = ["name", "number", "bid_due_date"]

ALLOWED_FIELDS = {
    "name", "number", "client", "owner", "architect", "engineer", "general_contractor",
    "estimator", "project_manager", "superintendent", "address", "latitude", "longitude",
    "time_zone", "estimated_value", "bid_due_date", "construction_start", "completion_date",
    "csi_structure", "project_type", "delivery_method", "union_status", "tax_jurisdiction",
    "insurance_requirements", "bonding_requirements",
}


def validate_project(payload: dict) -> list[str]:
    """Return a list of human-readable validation errors (empty = valid)."""
    errors = []
    for f in REQUIRED_FIELDS:
        if not str(payload.get(f) or "").strip():
            errors.append(f"Field '{f}' is required.")
    ev = payload.get("estimated_value")
    if ev is not None and ev != "":
        try:
            if float(ev) < 0:
                errors.append("Estimated value must be non-negative.")
        except (TypeError, ValueError):
            errors.append("Estimated value must be a number.")
    return errors


def create_project(db: Session, payload: dict) -> Project:
    data = {k: v for k, v in payload.items() if k in ALLOWED_FIELDS and v is not None}
    if "estimated_value" in data and data["estimated_value"] != "":
        data["estimated_value"] = float(data["estimated_value"])
    project = Project(**data)
    db.add(project)
    db.commit()
    db.refresh(project)
    return project


def project_summary(p: Project) -> dict:
    return {
        "id": p.id, "name": p.name, "number": p.number, "client": p.client,
        "owner": p.owner, "architect": p.architect, "engineer": p.engineer,
        "general_contractor": p.general_contractor, "estimator": p.estimator,
        "project_manager": p.project_manager, "superintendent": p.superintendent,
        "address": p.address, "latitude": p.latitude, "longitude": p.longitude,
        "time_zone": p.time_zone, "estimated_value": p.estimated_value,
        "bid_due_date": p.bid_due_date, "construction_start": p.construction_start,
        "completion_date": p.completion_date, "csi_structure": p.csi_structure,
        "project_type": p.project_type, "delivery_method": p.delivery_method,
        "union_status": p.union_status, "tax_jurisdiction": p.tax_jurisdiction,
        "insurance_requirements": p.insurance_requirements,
        "bonding_requirements": p.bonding_requirements,
        "status": p.status, "created_at": p.created_at.isoformat(),
    }
