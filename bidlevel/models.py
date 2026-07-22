"""BidLevel schema.

Design notes
------------
- JSON columns hold open-ended structured payloads (extracted fields, event
  timelines, score breakdowns) so the schema stays stable while the AI layer
  evolves. Every JSON payload the AI produces carries per-field confidence and
  source references — that contract lives in bidlevel/ai/.
- Every mutation of leveling data is mirrored into AuditEntry; nothing is
  updated in place without a trail.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from bidlevel.db import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


# --------------------------------------------------------------------------- #
# 1. Projects & documents
# --------------------------------------------------------------------------- #
class Project(Base):
    __tablename__ = "bl_projects"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(300))
    number: Mapped[str] = mapped_column(String(100))
    client: Mapped[str] = mapped_column(String(300), default="")
    owner: Mapped[str] = mapped_column(String(300), default="")
    architect: Mapped[str] = mapped_column(String(300), default="")
    engineer: Mapped[str] = mapped_column(String(300), default="")
    general_contractor: Mapped[str] = mapped_column(String(300), default="")
    estimator: Mapped[str] = mapped_column(String(300), default="")
    project_manager: Mapped[str] = mapped_column(String(300), default="")
    superintendent: Mapped[str] = mapped_column(String(300), default="")
    address: Mapped[str] = mapped_column(String(500), default="")
    latitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    longitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    time_zone: Mapped[str] = mapped_column(String(64), default="")
    estimated_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    bid_due_date: Mapped[str] = mapped_column(String(32), default="")       # ISO date
    construction_start: Mapped[str] = mapped_column(String(32), default="")
    completion_date: Mapped[str] = mapped_column(String(32), default="")
    csi_structure: Mapped[str] = mapped_column(String(32), default="masterformat-2020")
    project_type: Mapped[str] = mapped_column(String(100), default="")
    delivery_method: Mapped[str] = mapped_column(String(100), default="")
    union_status: Mapped[str] = mapped_column(String(32), default="")       # union / non-union / mixed
    tax_jurisdiction: Mapped[str] = mapped_column(String(200), default="")
    insurance_requirements: Mapped[str] = mapped_column(Text, default="")
    bonding_requirements: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(32), default="bidding")      # bidding/awarded/archived
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    documents: Mapped[list["ProjectDocument"]] = relationship(back_populates="project")
    packages: Mapped[list["BidPackage"]] = relationship(back_populates="project")


class ProjectDocument(Base):
    __tablename__ = "bl_documents"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("bl_projects.id"), index=True)
    filename: Mapped[str] = mapped_column(String(400))
    doc_type: Mapped[str] = mapped_column(String(64), default="drawings")  # drawings/specifications/scope/…
    file_format: Mapped[str] = mapped_column(String(16), default="pdf")
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    raw_text: Mapped[str] = mapped_column(Text, default="")               # extracted / provided text content
    uploaded_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    # AI pipeline: each stage -> {status, detail}; overall confidence 0..1
    pipeline: Mapped[dict] = mapped_column(JSON, default=dict)
    processing_status: Mapped[str] = mapped_column(String(32), default="pending")  # pending/processing/complete/failed
    confidence: Mapped[float] = mapped_column(Float, default=0.0)

    project: Mapped[Project] = relationship(back_populates="documents")
    sheets: Mapped[list["DrawingSheet"]] = relationship(back_populates="document")


class DrawingSheet(Base):
    __tablename__ = "bl_sheets"

    id: Mapped[int] = mapped_column(primary_key=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("bl_documents.id"), index=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("bl_projects.id"), index=True)
    number: Mapped[str] = mapped_column(String(64))
    title: Mapped[str] = mapped_column(String(300), default="")
    discipline: Mapped[str] = mapped_column(String(64), default="")
    revision: Mapped[str] = mapped_column(String(32), default="")
    revision_date: Mapped[str] = mapped_column(String(32), default="")
    scale: Mapped[str] = mapped_column(String(64), default="")
    floor: Mapped[str] = mapped_column(String(64), default="")
    building: Mapped[str] = mapped_column(String(64), default="")
    phase: Mapped[str] = mapped_column(String(64), default="")
    notes: Mapped[list] = mapped_column(JSON, default=list)     # [{kind, text}] general notes/keynotes/schedules
    confidence: Mapped[float] = mapped_column(Float, default=0.0)

    document: Mapped[ProjectDocument] = relationship(back_populates="sheets")


class DetectedQuantity(Base):
    __tablename__ = "bl_quantities"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("bl_projects.id"), index=True)
    document_id: Mapped[int | None] = mapped_column(ForeignKey("bl_documents.id"), nullable=True)
    description: Mapped[str] = mapped_column(String(500))
    value: Mapped[float] = mapped_column(Float)
    unit: Mapped[str] = mapped_column(String(32))               # SF, LF, CY, EA, TON, …
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    source_sheet: Mapped[str] = mapped_column(String(64), default="")
    source_detail: Mapped[str] = mapped_column(String(500), default="")   # original text
    csi_code: Mapped[str] = mapped_column(String(16), default="")
    trade: Mapped[str] = mapped_column(String(100), default="")
    room: Mapped[str] = mapped_column(String(100), default="")


class ScopeItem(Base):
    """Structured scope understanding extracted from documents."""

    __tablename__ = "bl_scope_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("bl_projects.id"), index=True)
    document_id: Mapped[int | None] = mapped_column(ForeignKey("bl_documents.id"), nullable=True)
    original_text: Mapped[str] = mapped_column(Text)
    trade: Mapped[str] = mapped_column(String(100), default="")
    csi_code: Mapped[str] = mapped_column(String(16), default="")
    installation_included: Mapped[bool] = mapped_column(Boolean, default=True)
    material_included: Mapped[bool] = mapped_column(Boolean, default=True)
    furnished_by: Mapped[str] = mapped_column(String(32), default="contractor")  # contractor/owner/others
    installed_by: Mapped[str] = mapped_column(String(32), default="contractor")
    excluded: Mapped[bool] = mapped_column(Boolean, default=False)
    confidence: Mapped[float] = mapped_column(Float, default=0.0)


# --------------------------------------------------------------------------- #
# 3. Bid packages
# --------------------------------------------------------------------------- #
class BidPackage(Base):
    __tablename__ = "bl_packages"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("bl_projects.id"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    trade: Mapped[str] = mapped_column(String(100))
    csi_division: Mapped[str] = mapped_column(String(16), default="")
    status: Mapped[str] = mapped_column(String(32), default="draft")  # draft/invited/leveling/awarded/archived
    # scope payload: {scope, drawings[], specifications[], included[], excluded[],
    #                 alternates[], allowances[], unit_price_requests[], bid_instructions,
    #                 schedule, insurance_requirements, bond_requirements, special_notes}
    content: Mapped[dict] = mapped_column(JSON, default=dict)
    version: Mapped[int] = mapped_column(Integer, default=1)
    versions: Mapped[list] = mapped_column(JSON, default=list)  # prior contents [{version, content, user, at}]
    ai_generated: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    project: Mapped[Project] = relationship(back_populates="packages")
    invitations: Mapped[list["Invitation"]] = relationship(back_populates="package")
    proposals: Mapped[list["Proposal"]] = relationship(back_populates="package")


# --------------------------------------------------------------------------- #
# 4. Subcontractors
# --------------------------------------------------------------------------- #
class Subcontractor(Base):
    __tablename__ = "bl_subs"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(300))
    trades: Mapped[list] = mapped_column(JSON, default=list)          # ["Electrical", …]
    csi_divisions: Mapped[list] = mapped_column(JSON, default=list)   # ["26", …]
    coverage_area: Mapped[str] = mapped_column(String(300), default="")
    office_locations: Mapped[list] = mapped_column(JSON, default=list)
    employees: Mapped[int | None] = mapped_column(Integer, nullable=True)
    annual_revenue: Mapped[float | None] = mapped_column(Float, nullable=True)
    bonding_capacity: Mapped[float | None] = mapped_column(Float, nullable=True)
    emr: Mapped[float | None] = mapped_column(Float, nullable=True)   # experience modification rate
    safety_score: Mapped[float | None] = mapped_column(Float, nullable=True)     # 0..100
    osha_recordables: Mapped[int | None] = mapped_column(Integer, nullable=True)
    quality_rating: Mapped[float | None] = mapped_column(Float, nullable=True)   # 0..5
    schedule_rating: Mapped[float | None] = mapped_column(Float, nullable=True)
    communication_rating: Mapped[float | None] = mapped_column(Float, nullable=True)
    financial_stability: Mapped[float | None] = mapped_column(Float, nullable=True)  # 0..100
    preferred_vendor: Mapped[bool] = mapped_column(Boolean, default=False)
    union_status: Mapped[str] = mapped_column(String(32), default="")
    diversity: Mapped[list] = mapped_column(JSON, default=list)  # ["MBE","WBE","VBE","DBE"]
    contact_email: Mapped[str] = mapped_column(String(300), default="")
    contact_phone: Mapped[str] = mapped_column(String(64), default="")
    past_projects: Mapped[list] = mapped_column(JSON, default=list)
    avg_bid_response_days: Mapped[float | None] = mapped_column(Float, nullable=True)
    award_rate: Mapped[float | None] = mapped_column(Float, nullable=True)  # 0..1 historical
    notes: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    documents: Mapped[list["SubDocument"]] = relationship(back_populates="sub")


class SubDocument(Base):
    """Compliance documents (insurance, license, bond letter) with expiry tracking."""

    __tablename__ = "bl_sub_documents"

    id: Mapped[int] = mapped_column(primary_key=True)
    sub_id: Mapped[int] = mapped_column(ForeignKey("bl_subs.id"), index=True)
    kind: Mapped[str] = mapped_column(String(64))                 # insurance/license/bond/w9/reference
    name: Mapped[str] = mapped_column(String(300), default="")
    expiration_date: Mapped[str] = mapped_column(String(32), default="")  # ISO date; "" = never
    reminders_sent: Mapped[list] = mapped_column(JSON, default=list)
    uploaded_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    sub: Mapped[Subcontractor] = relationship(back_populates="documents")


# --------------------------------------------------------------------------- #
# 5-6. Invitations & submission portal
# --------------------------------------------------------------------------- #
class Invitation(Base):
    __tablename__ = "bl_invitations"

    id: Mapped[int] = mapped_column(primary_key=True)
    package_id: Mapped[int] = mapped_column(ForeignKey("bl_packages.id"), index=True)
    sub_id: Mapped[int] = mapped_column(ForeignKey("bl_subs.id"), index=True)
    status: Mapped[str] = mapped_column(String(32), default="sent")
    # timeline events: [{event: delivered/opened/clicked/downloaded/declined/accepted/submitted, at}]
    events: Mapped[list] = mapped_column(JSON, default=list)
    email_body: Mapped[str] = mapped_column(Text, default="")
    recommendation: Mapped[dict] = mapped_column(JSON, default=dict)  # AI score + reasons at invite time
    reminder_rules: Mapped[list] = mapped_column(JSON, default=list)  # hours before due, e.g. [168, 72, 24, 2]
    reminders_sent: Mapped[list] = mapped_column(JSON, default=list)
    portal_token: Mapped[str] = mapped_column(String(64), default="", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    package: Mapped[BidPackage] = relationship(back_populates="invitations")
    sub: Mapped[Subcontractor] = relationship()


class Proposal(Base):
    __tablename__ = "bl_proposals"

    id: Mapped[int] = mapped_column(primary_key=True)
    package_id: Mapped[int] = mapped_column(ForeignKey("bl_packages.id"), index=True)
    sub_id: Mapped[int] = mapped_column(ForeignKey("bl_subs.id"), index=True)
    invitation_id: Mapped[int | None] = mapped_column(ForeignKey("bl_invitations.id"), nullable=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    is_current: Mapped[bool] = mapped_column(Boolean, default=True)
    submitted_by: Mapped[str] = mapped_column(String(300), default="")
    submitted_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    raw_text: Mapped[str] = mapped_column(Text, default="")
    base_bid: Mapped[float | None] = mapped_column(Float, nullable=True)
    # extracted: {field: {value, normalized, confidence, source_page, bbox, original_text}}
    extracted: Mapped[dict] = mapped_column(JSON, default=dict)
    # structured lists: alternates[], allowances[], unit_prices[], exclusions[],
    # qualifications[], assumptions[], line_items[{description, amount}]
    lines: Mapped[dict] = mapped_column(JSON, default=dict)
    needs_review: Mapped[list] = mapped_column(JSON, default=list)  # fields flagged below confidence threshold
    status: Mapped[str] = mapped_column(String(32), default="submitted")  # draft/submitted/leveled

    package: Mapped[BidPackage] = relationship(back_populates="proposals")
    sub: Mapped[Subcontractor] = relationship()


# --------------------------------------------------------------------------- #
# 8-9. Normalization & leveling
# --------------------------------------------------------------------------- #
class ScopeCategory(Base):
    """A row of the Master Scope Matrix for a package."""

    __tablename__ = "bl_scope_categories"

    id: Mapped[int] = mapped_column(primary_key=True)
    package_id: Mapped[int] = mapped_column(ForeignKey("bl_packages.id"), index=True)
    name: Mapped[str] = mapped_column(String(300))
    canonical_key: Mapped[str] = mapped_column(String(200), index=True)
    order: Mapped[int] = mapped_column(Integer, default=0)


class ScopeMapping(Base):
    """How one proposal maps onto one scope category."""

    __tablename__ = "bl_scope_mappings"

    id: Mapped[int] = mapped_column(primary_key=True)
    package_id: Mapped[int] = mapped_column(ForeignKey("bl_packages.id"), index=True)
    category_id: Mapped[int] = mapped_column(ForeignKey("bl_scope_categories.id"), index=True)
    proposal_id: Mapped[int] = mapped_column(ForeignKey("bl_proposals.id"), index=True)
    status: Mapped[str] = mapped_column(String(32))               # included/excluded/missing/unclear
    evidence: Mapped[str] = mapped_column(Text, default="")       # proposal text that drove the call
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    manual_override: Mapped[bool] = mapped_column(Boolean, default=False)


class SynonymOverride(Base):
    """User corrections that teach the normalizer new phrase -> category links."""

    __tablename__ = "bl_synonym_overrides"

    id: Mapped[int] = mapped_column(primary_key=True)
    phrase: Mapped[str] = mapped_column(String(300), index=True)
    canonical_key: Mapped[str] = mapped_column(String(200))
    created_by: Mapped[str] = mapped_column(String(200), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class AuditEntry(Base):
    __tablename__ = "bl_audit"

    id: Mapped[int] = mapped_column(primary_key=True)
    entity: Mapped[str] = mapped_column(String(64), index=True)   # package/proposal/mapping/award/…
    entity_id: Mapped[int] = mapped_column(Integer, index=True)
    field: Mapped[str] = mapped_column(String(200), default="")
    old_value: Mapped[str] = mapped_column(Text, default="")
    new_value: Mapped[str] = mapped_column(Text, default="")
    user: Mapped[str] = mapped_column(String(200), default="")
    reason: Mapped[str] = mapped_column(Text, default="")
    at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


# --------------------------------------------------------------------------- #
# 10. Risk
# --------------------------------------------------------------------------- #
class RiskIssue(Base):
    __tablename__ = "bl_risks"

    id: Mapped[int] = mapped_column(primary_key=True)
    package_id: Mapped[int] = mapped_column(ForeignKey("bl_packages.id"), index=True)
    proposal_id: Mapped[int | None] = mapped_column(ForeignKey("bl_proposals.id"), nullable=True)
    kind: Mapped[str] = mapped_column(String(64))       # missing_scope/low_price/math_error/expired_insurance/…
    severity: Mapped[str] = mapped_column(String(16))   # low/medium/high/critical
    cost_impact: Mapped[float | None] = mapped_column(Float, nullable=True)
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    explanation: Mapped[str] = mapped_column(Text, default="")
    suggested_action: Mapped[str] = mapped_column(Text, default="")
    evidence: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(32), default="open")  # open/acknowledged/dismissed/assigned
    assigned_to: Mapped[str] = mapped_column(String(200), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


# --------------------------------------------------------------------------- #
# 11. Award
# --------------------------------------------------------------------------- #
class Award(Base):
    __tablename__ = "bl_awards"

    id: Mapped[int] = mapped_column(primary_key=True)
    package_id: Mapped[int] = mapped_column(ForeignKey("bl_packages.id"), index=True)
    sub_id: Mapped[int] = mapped_column(ForeignKey("bl_subs.id"))
    proposal_id: Mapped[int] = mapped_column(ForeignKey("bl_proposals.id"))
    weights: Mapped[dict] = mapped_column(JSON, default=dict)
    scores: Mapped[dict] = mapped_column(JSON, default=dict)      # per-finalist breakdown at decision time
    comments: Mapped[list] = mapped_column(JSON, default=list)    # committee comments [{user, text, at}]
    approvals: Mapped[list] = mapped_column(JSON, default=list)   # [{approver, decision, at, note}]
    status: Mapped[str] = mapped_column(String(32), default="recommended")  # recommended/approved/executed
    award_letter: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    sub: Mapped[Subcontractor] = relationship()
