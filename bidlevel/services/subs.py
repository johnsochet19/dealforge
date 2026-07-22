"""Subcontractor database: profiles + compliance document expiry tracking.

`compliance_status` never lets an expired insurance cert or license go
unnoticed: every listing and invitation surface carries it.
"""
from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy.orm import Session

from bidlevel.models import SubDocument, Subcontractor

EXPIRY_REMINDER_DAYS = [60, 30, 14, 7, 1]

SUB_FIELDS = {
    "name", "trades", "csi_divisions", "coverage_area", "office_locations", "employees",
    "annual_revenue", "bonding_capacity", "emr", "safety_score", "osha_recordables",
    "quality_rating", "schedule_rating", "communication_rating", "financial_stability",
    "preferred_vendor", "union_status", "diversity", "contact_email", "contact_phone",
    "past_projects", "avg_bid_response_days", "award_rate", "notes",
}


def create_sub(db: Session, payload: dict) -> Subcontractor:
    if not str(payload.get("name") or "").strip():
        raise ValueError("Subcontractor name is required.")
    sub = Subcontractor(**{k: v for k, v in payload.items() if k in SUB_FIELDS})
    db.add(sub)
    db.commit()
    db.refresh(sub)
    return sub


def add_document(db: Session, sub_id: int, kind: str, name: str, expiration_date: str = "") -> SubDocument:
    doc = SubDocument(sub_id=sub_id, kind=kind, name=name, expiration_date=expiration_date)
    db.add(doc)
    db.commit()
    db.refresh(doc)
    return doc


def _doc_state(doc: SubDocument, today: date) -> str:
    if not doc.expiration_date:
        return "current"
    exp = date.fromisoformat(doc.expiration_date)
    if exp < today:
        return "expired"
    if exp <= today + timedelta(days=EXPIRY_REMINDER_DAYS[0]):
        return "expiring_soon"
    return "current"


def compliance_status(sub: Subcontractor, today: date | None = None) -> dict:
    today = today or date.today()
    docs = []
    worst = "current"
    for d in sub.documents:
        state = _doc_state(d, today)
        docs.append({"id": d.id, "kind": d.kind, "name": d.name,
                     "expiration_date": d.expiration_date, "state": state})
        if state == "expired":
            worst = "expired"
        elif state == "expiring_soon" and worst != "expired":
            worst = "expiring_soon"
    return {"overall": worst, "documents": docs}


def run_expiry_reminders(db: Session, today: date | None = None) -> list[dict]:
    """Record reminders for docs hitting a reminder window. Idempotent per (doc, window)."""
    today = today or date.today()
    fired = []
    for doc in db.query(SubDocument).all():
        if not doc.expiration_date:
            continue
        exp = date.fromisoformat(doc.expiration_date)
        days_left = (exp - today).days
        if days_left < 0:
            continue
        applicable = [w for w in EXPIRY_REMINDER_DAYS if days_left <= w]
        if not applicable:
            continue
        tightest = min(applicable)
        if tightest in doc.reminders_sent:
            continue  # already reminded at this urgency level
        # mark every applicable window as covered so the doc fires once per level
        doc.reminders_sent = sorted(set(list(doc.reminders_sent) + applicable), reverse=True)
        fired.append({
            "sub_id": doc.sub_id, "document_id": doc.id, "kind": doc.kind,
            "name": doc.name, "days_left": days_left, "window": tightest,
            "message": f"{doc.kind} '{doc.name}' expires in {days_left} day(s).",
        })
    db.commit()
    return fired


def sub_summary(s: Subcontractor, today: date | None = None) -> dict:
    return {
        "id": s.id, "name": s.name, "trades": s.trades, "csi_divisions": s.csi_divisions,
        "coverage_area": s.coverage_area, "office_locations": s.office_locations,
        "employees": s.employees, "annual_revenue": s.annual_revenue,
        "bonding_capacity": s.bonding_capacity, "emr": s.emr, "safety_score": s.safety_score,
        "osha_recordables": s.osha_recordables, "quality_rating": s.quality_rating,
        "schedule_rating": s.schedule_rating, "communication_rating": s.communication_rating,
        "financial_stability": s.financial_stability, "preferred_vendor": s.preferred_vendor,
        "union_status": s.union_status, "diversity": s.diversity,
        "contact_email": s.contact_email, "contact_phone": s.contact_phone,
        "past_projects": s.past_projects, "avg_bid_response_days": s.avg_bid_response_days,
        "award_rate": s.award_rate, "notes": s.notes,
        "compliance": compliance_status(s, today),
    }
