"""Document upload + automatic AI processing.

Processing starts the moment a document lands — the user never presses a
"process" button. Results (sheets, quantities, scope items) are persisted so
every downstream module links back to its source.
"""
from __future__ import annotations

from dataclasses import asdict

from sqlalchemy.orm import Session

from bidlevel.ai import drawing
from bidlevel.models import DetectedQuantity, DrawingSheet, ProjectDocument, ScopeItem

SUPPORTED_FORMATS = {"pdf", "dwg", "ifc", "xlsx", "xls", "docx", "doc", "zip", "txt", "csv"}


def infer_format(filename: str) -> str:
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    return ext if ext in SUPPORTED_FORMATS else "unknown"


def upload_document(
    db: Session,
    project_id: int,
    filename: str,
    doc_type: str,
    text: str,
    size_bytes: int = 0,
) -> ProjectDocument:
    """Store the document and immediately run the AI pipeline over its text.

    `text` is the extractable text content. Binary formats (DWG/IFC) with no
    text still get a record; their pipeline reports what it could not read
    rather than pretending.
    """
    doc = ProjectDocument(
        project_id=project_id,
        filename=filename,
        doc_type=doc_type,
        file_format=infer_format(filename),
        size_bytes=size_bytes or len(text.encode()),
        raw_text=text,
        processing_status="processing",
    )
    db.add(doc)
    db.commit()
    db.refresh(doc)
    process_document(db, doc)
    return doc


def process_document(db: Session, doc: ProjectDocument) -> ProjectDocument:
    if not doc.raw_text.strip():
        doc.pipeline = {
            "ocr": {"status": "skipped", "detail": f"no extractable text in .{doc.file_format} file"},
        }
        doc.processing_status = "complete"
        doc.confidence = 0.0
        db.commit()
        return doc

    result = drawing.analyze_document(doc.raw_text)

    for s in result["sheets"]:
        db.add(DrawingSheet(document_id=doc.id, project_id=doc.project_id, **asdict(s)))
    for q in result["quantities"]:
        db.add(DetectedQuantity(project_id=doc.project_id, document_id=doc.id, **asdict(q)))
    for sc in result["scope"]:
        db.add(ScopeItem(project_id=doc.project_id, document_id=doc.id, **asdict(sc)))

    doc.pipeline = result["pipeline"]
    doc.confidence = result["confidence"]
    doc.processing_status = "complete"
    db.commit()
    db.refresh(doc)
    return doc


def detected_trades(db: Session, project_id: int) -> list[dict]:
    """Union of trades detected across all of a project's documents."""
    docs = db.query(ProjectDocument).filter_by(project_id=project_id).all()
    found: dict[str, dict] = {}
    for doc in docs:
        if not doc.raw_text:
            continue
        for trade, div, kw in drawing.detect_trades(doc.raw_text):
            entry = found.setdefault(trade, {"trade": trade, "csi_division": div, "evidence": [], "documents": []})
            entry["evidence"].append(kw)
            if doc.filename not in entry["documents"]:
                entry["documents"].append(doc.filename)
    return sorted(found.values(), key=lambda t: t["csi_division"])


def document_summary(d: ProjectDocument) -> dict:
    return {
        "id": d.id, "project_id": d.project_id, "filename": d.filename,
        "doc_type": d.doc_type, "file_format": d.file_format, "size_bytes": d.size_bytes,
        "uploaded_at": d.uploaded_at.isoformat(), "pipeline": d.pipeline,
        "processing_status": d.processing_status, "confidence": d.confidence,
        "sheet_count": len(d.sheets),
    }
