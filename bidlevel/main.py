"""BidLevel API — FastAPI app.

Run:  uvicorn bidlevel.main:app --reload --port 8100
Docs: /docs        Frontend: /
"""
from __future__ import annotations

import os
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from bidlevel.db import get_db, init_db
from bidlevel.models import Award, BidPackage, Invitation, Project, ProjectDocument, Proposal, RiskIssue, Subcontractor
from bidlevel.services import (
    analytics,
    award as award_svc,
    documents,
    invitations as inv_svc,
    leveling,
    packages as pkg_svc,
    portal,
    projects as proj_svc,
    risk as risk_svc,
    subs as subs_svc,
)

app = FastAPI(title="BidLevel — AI Bid Leveling Platform", version="0.1.0")
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"],
)

FRONTEND = Path(__file__).parent / "frontend" / "index.html"


@app.on_event("startup")
def _startup() -> None:
    if os.environ.get("BIDLEVEL_SKIP_INIT") != "1":
        init_db()


@app.get("/health")
def health():
    return {"status": "ok", "app": "bidlevel"}


@app.get("/")
def frontend():
    return FileResponse(FRONTEND)


# ------------------------------------------------------------------ projects
class ProjectIn(BaseModel):
    model_config = {"extra": "allow"}
    name: str = ""
    number: str = ""
    bid_due_date: str = ""


@app.post("/api/v1/projects")
def create_project(payload: ProjectIn, db: Session = Depends(get_db)):
    data = payload.model_dump()
    errors = proj_svc.validate_project(data)
    if errors:
        raise HTTPException(422, detail={"errors": errors})
    p = proj_svc.create_project(db, data)
    return proj_svc.project_summary(p)


@app.get("/api/v1/projects")
def list_projects(db: Session = Depends(get_db)):
    return [proj_svc.project_summary(p) for p in db.query(Project).all()]


@app.get("/api/v1/projects/{project_id}")
def get_project(project_id: int, db: Session = Depends(get_db)):
    p = db.get(Project, project_id)
    if p is None:
        raise HTTPException(404, "project not found")
    return proj_svc.project_summary(p)


# ----------------------------------------------------------------- documents
class DocumentIn(BaseModel):
    filename: str
    doc_type: str = "drawings"
    text: str = ""
    size_bytes: int = 0


@app.post("/api/v1/projects/{project_id}/documents")
def upload_document(project_id: int, payload: DocumentIn, db: Session = Depends(get_db)):
    if db.get(Project, project_id) is None:
        raise HTTPException(404, "project not found")
    doc = documents.upload_document(db, project_id, payload.filename, payload.doc_type,
                                    payload.text, payload.size_bytes)
    return documents.document_summary(doc)


@app.get("/api/v1/projects/{project_id}/documents")
def list_documents(project_id: int, db: Session = Depends(get_db)):
    docs = db.query(ProjectDocument).filter_by(project_id=project_id).all()
    return [documents.document_summary(d) for d in docs]


@app.get("/api/v1/projects/{project_id}/sheets")
def list_sheets(project_id: int, db: Session = Depends(get_db)):
    from bidlevel.models import DrawingSheet
    rows = db.query(DrawingSheet).filter_by(project_id=project_id).all()
    return [{"id": s.id, "number": s.number, "title": s.title, "discipline": s.discipline,
             "revision": s.revision, "revision_date": s.revision_date, "scale": s.scale,
             "floor": s.floor, "building": s.building, "phase": s.phase,
             "notes": s.notes, "confidence": s.confidence} for s in rows]


@app.get("/api/v1/projects/{project_id}/quantities")
def list_quantities(project_id: int, db: Session = Depends(get_db)):
    from bidlevel.models import DetectedQuantity
    rows = db.query(DetectedQuantity).filter_by(project_id=project_id).all()
    return [{"id": q.id, "description": q.description, "value": q.value, "unit": q.unit,
             "confidence": q.confidence, "source_sheet": q.source_sheet,
             "source_detail": q.source_detail, "csi_code": q.csi_code,
             "trade": q.trade, "room": q.room} for q in rows]


@app.get("/api/v1/projects/{project_id}/scope-items")
def list_scope_items(project_id: int, db: Session = Depends(get_db)):
    from bidlevel.models import ScopeItem
    rows = db.query(ScopeItem).filter_by(project_id=project_id).all()
    return [{"id": s.id, "original_text": s.original_text, "trade": s.trade,
             "csi_code": s.csi_code, "installation_included": s.installation_included,
             "material_included": s.material_included, "furnished_by": s.furnished_by,
             "installed_by": s.installed_by, "excluded": s.excluded,
             "confidence": s.confidence} for s in rows]


@app.get("/api/v1/projects/{project_id}/trades")
def list_trades(project_id: int, db: Session = Depends(get_db)):
    return documents.detected_trades(db, project_id)


# ------------------------------------------------------------------ packages
@app.post("/api/v1/projects/{project_id}/packages/build")
def build_packages(project_id: int, db: Session = Depends(get_db)):
    try:
        created = pkg_svc.build_packages(db, project_id)
    except ValueError as e:
        raise HTTPException(404, str(e))
    return [pkg_svc.package_summary(p) for p in created]


@app.get("/api/v1/projects/{project_id}/packages")
def list_packages(project_id: int, db: Session = Depends(get_db)):
    rows = db.query(BidPackage).filter_by(project_id=project_id).all()
    return [pkg_svc.package_summary(p, include_content=False) for p in rows]


@app.get("/api/v1/packages/{package_id}")
def get_package(package_id: int, db: Session = Depends(get_db)):
    p = db.get(BidPackage, package_id)
    if p is None:
        raise HTTPException(404, "package not found")
    return pkg_svc.package_summary(p)


class PackageEdit(BaseModel):
    changes: dict
    user: str = ""
    reason: str = ""


@app.patch("/api/v1/packages/{package_id}")
def edit_package(package_id: int, payload: PackageEdit, db: Session = Depends(get_db)):
    try:
        p = pkg_svc.edit_package(db, package_id, payload.changes, payload.user, payload.reason)
    except ValueError as e:
        raise HTTPException(404, str(e))
    return pkg_svc.package_summary(p)


# ---------------------------------------------------------------------- subs
class SubIn(BaseModel):
    model_config = {"extra": "allow"}
    name: str


@app.post("/api/v1/subs")
def create_sub(payload: SubIn, db: Session = Depends(get_db)):
    try:
        s = subs_svc.create_sub(db, payload.model_dump())
    except ValueError as e:
        raise HTTPException(422, str(e))
    return subs_svc.sub_summary(s)


@app.get("/api/v1/subs")
def list_subs(db: Session = Depends(get_db)):
    return [subs_svc.sub_summary(s) for s in db.query(Subcontractor).all()]


@app.get("/api/v1/subs/{sub_id}")
def get_sub(sub_id: int, db: Session = Depends(get_db)):
    s = db.get(Subcontractor, sub_id)
    if s is None:
        raise HTTPException(404, "sub not found")
    return subs_svc.sub_summary(s)


class SubDocIn(BaseModel):
    kind: str
    name: str = ""
    expiration_date: str = ""


@app.post("/api/v1/subs/{sub_id}/documents")
def add_sub_document(sub_id: int, payload: SubDocIn, db: Session = Depends(get_db)):
    if db.get(Subcontractor, sub_id) is None:
        raise HTTPException(404, "sub not found")
    d = subs_svc.add_document(db, sub_id, payload.kind, payload.name, payload.expiration_date)
    return {"id": d.id, "kind": d.kind, "name": d.name, "expiration_date": d.expiration_date}


@app.post("/api/v1/subs/reminders/run")
def run_sub_reminders(db: Session = Depends(get_db)):
    return subs_svc.run_expiry_reminders(db)


# ---------------------------------------------------------------- invitations
@app.get("/api/v1/packages/{package_id}/recommendations")
def recommendations(package_id: int, limit: int = 10, db: Session = Depends(get_db)):
    try:
        return inv_svc.recommend_subs(db, package_id, limit)
    except ValueError as e:
        raise HTTPException(404, str(e))


class InviteIn(BaseModel):
    sub_ids: list[int]


@app.post("/api/v1/packages/{package_id}/invitations")
def send_invitations(package_id: int, payload: InviteIn, db: Session = Depends(get_db)):
    try:
        recs = {r["sub_id"]: r for r in inv_svc.recommend_subs(db, package_id, limit=100)}
        rows = inv_svc.send_invitations(db, package_id, payload.sub_ids, recs)
    except ValueError as e:
        raise HTTPException(404, str(e))
    return [inv_svc.invitation_summary(i) for i in rows]


@app.get("/api/v1/packages/{package_id}/invitations")
def list_invitations(package_id: int, db: Session = Depends(get_db)):
    rows = db.query(Invitation).filter_by(package_id=package_id).all()
    return [inv_svc.invitation_summary(i) for i in rows]


class EventIn(BaseModel):
    event: str


@app.post("/api/v1/invitations/{invitation_id}/events")
def record_event(invitation_id: int, payload: EventIn, db: Session = Depends(get_db)):
    try:
        inv = inv_svc.record_event(db, invitation_id, payload.event)
    except ValueError as e:
        raise HTTPException(422, str(e))
    return inv_svc.invitation_summary(inv)


@app.post("/api/v1/invitations/reminders/run")
def run_invitation_reminders(db: Session = Depends(get_db)):
    return inv_svc.run_reminders(db)


# --------------------------------------------------------------------- portal
class ProposalIn(BaseModel):
    sub_id: int | None = None
    text: str
    submitted_by: str = ""
    draft: bool = False


@app.post("/api/v1/packages/{package_id}/proposals")
def submit_proposal(package_id: int, payload: ProposalIn, db: Session = Depends(get_db)):
    if payload.sub_id is None:
        raise HTTPException(422, "sub_id required")
    try:
        p = portal.submit_proposal(db, package_id, payload.sub_id, payload.text,
                                   payload.submitted_by, draft=payload.draft)
    except ValueError as e:
        raise HTTPException(404, str(e))
    return portal.proposal_summary(p, full=True)


@app.post("/api/v1/portal/{token}/proposals")
def submit_via_portal(token: str, payload: ProposalIn, db: Session = Depends(get_db)):
    try:
        p = portal.submit_via_token(db, token, payload.text, payload.submitted_by, payload.draft)
    except ValueError as e:
        raise HTTPException(404, str(e))
    return portal.proposal_summary(p, full=True)


@app.get("/api/v1/packages/{package_id}/proposals")
def list_proposals(package_id: int, current_only: bool = True, db: Session = Depends(get_db)):
    q = db.query(Proposal).filter_by(package_id=package_id)
    if current_only:
        q = q.filter_by(is_current=True)
    return [portal.proposal_summary(p) for p in q.all()]


@app.get("/api/v1/proposals/{proposal_id}")
def get_proposal(proposal_id: int, db: Session = Depends(get_db)):
    p = db.get(Proposal, proposal_id)
    if p is None:
        raise HTTPException(404, "proposal not found")
    return portal.proposal_summary(p, full=True)


@app.get("/api/v1/packages/{package_id}/proposals/{sub_id}/versions")
def get_versions(package_id: int, sub_id: int, db: Session = Depends(get_db)):
    return portal.proposal_versions(db, package_id, sub_id)


# ------------------------------------------------------------------- leveling
@app.post("/api/v1/packages/{package_id}/leveling/rebuild")
def rebuild_matrix(package_id: int, db: Session = Depends(get_db)):
    try:
        return leveling.rebuild_scope_matrix(db, package_id)
    except ValueError as e:
        raise HTTPException(404, str(e))


@app.get("/api/v1/packages/{package_id}/leveling")
def get_grid(package_id: int, db: Session = Depends(get_db)):
    try:
        return leveling.leveling_grid(db, package_id)
    except ValueError as e:
        raise HTTPException(404, str(e))


class OverrideIn(BaseModel):
    category_id: int
    proposal_id: int
    status: str
    user: str = ""
    reason: str = ""
    learn_phrase: str = ""


@app.post("/api/v1/packages/{package_id}/leveling/override")
def override(package_id: int, payload: OverrideIn, db: Session = Depends(get_db)):
    try:
        return leveling.override_mapping(db, package_id, payload.category_id,
                                         payload.proposal_id, payload.status,
                                         payload.user, payload.reason, payload.learn_phrase)
    except ValueError as e:
        raise HTTPException(404, str(e))


class CellEdit(BaseModel):
    field: str
    value: float | str | None = None
    user: str = ""
    reason: str = ""


@app.patch("/api/v1/proposals/{proposal_id}/values")
def edit_value(proposal_id: int, payload: CellEdit, db: Session = Depends(get_db)):
    try:
        p = leveling.edit_proposal_value(db, proposal_id, payload.field, payload.value,
                                        payload.user, payload.reason)
    except ValueError as e:
        raise HTTPException(404, str(e))
    return portal.proposal_summary(p)


@app.get("/api/v1/audit")
def get_audit(entity: str | None = None, entity_id: int | None = None,
              db: Session = Depends(get_db)):
    return leveling.audit_log(db, entity, entity_id)


# ----------------------------------------------------------------------- risk
@app.post("/api/v1/packages/{package_id}/risk/analyze")
def analyze_risk(package_id: int, db: Session = Depends(get_db)):
    try:
        issues = risk_svc.analyze_package(db, package_id)
    except ValueError as e:
        raise HTTPException(404, str(e))
    return [risk_svc.issue_summary(i) for i in issues]


@app.get("/api/v1/packages/{package_id}/risk")
def list_risk(package_id: int, db: Session = Depends(get_db)):
    rows = db.query(RiskIssue).filter_by(package_id=package_id).all()
    return [risk_svc.issue_summary(i) for i in rows]


class IssueStatusIn(BaseModel):
    status: str
    assigned_to: str = ""


@app.patch("/api/v1/risk/{issue_id}")
def set_issue_status(issue_id: int, payload: IssueStatusIn, db: Session = Depends(get_db)):
    try:
        i = risk_svc.set_issue_status(db, issue_id, payload.status, payload.assigned_to)
    except ValueError as e:
        raise HTTPException(422, str(e))
    return risk_svc.issue_summary(i)


# ---------------------------------------------------------------------- award
@app.get("/api/v1/packages/{package_id}/award/recommendation")
def award_recommendation(package_id: int, db: Session = Depends(get_db)):
    try:
        return award_svc.recommend(db, package_id)
    except ValueError as e:
        raise HTTPException(404, str(e))


class AwardIn(BaseModel):
    proposal_id: int
    weights: dict | None = None
    user: str = ""


@app.post("/api/v1/packages/{package_id}/award")
def create_award(package_id: int, payload: AwardIn, db: Session = Depends(get_db)):
    try:
        a = award_svc.create_award(db, package_id, payload.proposal_id, payload.weights, payload.user)
    except ValueError as e:
        raise HTTPException(404, str(e))
    return award_svc.award_summary(a)


@app.get("/api/v1/packages/{package_id}/awards")
def list_awards(package_id: int, db: Session = Depends(get_db)):
    rows = db.query(Award).filter_by(package_id=package_id).all()
    return [award_svc.award_summary(a) for a in rows]


class CommentIn(BaseModel):
    user: str
    text: str


@app.post("/api/v1/awards/{award_id}/comments")
def add_award_comment(award_id: int, payload: CommentIn, db: Session = Depends(get_db)):
    try:
        a = award_svc.add_comment(db, award_id, payload.user, payload.text)
    except ValueError as e:
        raise HTTPException(404, str(e))
    return award_svc.award_summary(a)


class ApprovalIn(BaseModel):
    approver: str
    decision: str
    note: str = ""


@app.post("/api/v1/awards/{award_id}/approvals")
def record_approval(award_id: int, payload: ApprovalIn, db: Session = Depends(get_db)):
    try:
        a = award_svc.record_approval(db, award_id, payload.approver, payload.decision, payload.note)
    except ValueError as e:
        raise HTTPException(422, str(e))
    return award_svc.award_summary(a)


# ------------------------------------------------------------------ analytics
@app.get("/api/v1/analytics")
def get_analytics(project_id: int | None = Query(default=None), db: Session = Depends(get_db)):
    return analytics.dashboard(db, project_id)


# ----------------------------------------------------------------------- demo
@app.post("/api/v1/demo/seed")
def seed(db: Session = Depends(get_db)):
    from bidlevel.demo import seed_demo
    return seed_demo(db)
