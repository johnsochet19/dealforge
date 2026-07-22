"""Proposal parsing: turn a subcontractor's proposal text into structured,
confidence-scored fields.

Contract: every extracted field is
    {value, normalized, confidence, source_page, original_text}
Fields whose confidence falls below REVIEW_THRESHOLD are flagged for human
review instead of being silently accepted.
"""
from __future__ import annotations

import re

REVIEW_THRESHOLD = 0.7

MONEY_RE = r"\$?\s*(\d{1,3}(?:,\d{3})*(?:\.\d{1,2})?)"

FIELD_PATTERNS: list[tuple[str, re.Pattern, float]] = [
    ("base_bid", re.compile(rf"(?:base\s+bid|total\s+(?:base\s+)?(?:bid|price|proposal)|bid\s+amount|lump\s+sum)\s*[:=]?\s*{MONEY_RE}", re.IGNORECASE), 0.92),
    ("bond_amount", re.compile(rf"(?:bond|bonding)\s*(?:cost|premium|amount)?\s*[:=]?\s*{MONEY_RE}", re.IGNORECASE), 0.8),
]

LIST_PATTERNS: dict[str, re.Pattern] = {
    "alternates": re.compile(rf"^\s*(?:alt(?:ernate)?\.?\s*#?\s*(\d+|[A-Z]))\s*[:\-–]\s*(.+?)(?:\s*[:=]?\s*{MONEY_RE})?\s*$", re.IGNORECASE),
    "unit_prices": re.compile(rf"^\s*(?:unit\s+price|up)\s*#?\s*(\d+)?\s*[:\-–]\s*(.+?)\s*[:=@]\s*{MONEY_RE}\s*(?:per|/)\s*(\w+)\s*$", re.IGNORECASE),
    "allowances": re.compile(rf"^\s*allowance\s*#?\s*(\d+)?\s*[:\-–]?\s*(.+?)\s*[:=]?\s*{MONEY_RE}\s*$", re.IGNORECASE),
}

SECTION_HEADERS = {
    "exclusions": re.compile(r"^\s*(exclusions?|excluded|not\s+included|clarifications?\s*/\s*exclusions?)\s*:?\s*$", re.IGNORECASE),
    "qualifications": re.compile(r"^\s*(qualifications?|clarifications?)\s*:?\s*$", re.IGNORECASE),
    "assumptions": re.compile(r"^\s*(assumptions?)\s*:?\s*$", re.IGNORECASE),
    "inclusions": re.compile(r"^\s*(inclusions?|included|scope\s+of\s+work)\s*:?\s*$", re.IGNORECASE),
}

INLINE_EXCLUDE_RE = re.compile(r"^\s*(?:exclud\w*|not included)\s*[:\-–]\s*(.+)$", re.IGNORECASE)
LINE_ITEM_RE = re.compile(rf"^\s*(?!alt|allowance|unit\s+price|base\s+bid|total|bond)([A-Za-z][^:$]{{2,80}}?)\s*[:\-–]\s*{MONEY_RE}\s*$", re.IGNORECASE)
LEAD_TIME_RE = re.compile(r"(\d+)\s*(?:-\s*\d+\s*)?(week|day|month)s?\s+lead\s*time|lead\s*time\s*[:=]?\s*(\d+)\s*(week|day|month)s?", re.IGNORECASE)
SCHEDULE_RE = re.compile(r"(?:duration|schedule)\s*[:=]?\s*(\d+)\s*(week|day|month|working day)s?", re.IGNORECASE)
WARRANTY_RE = re.compile(r"(\d+)\s*[-\s]?year\s+warranty|warranty\s*[:=]?\s*(\d+)\s*year", re.IGNORECASE)
PAYMENT_RE = re.compile(r"(net\s*\d+|\d+%\s*retainage|retainage\s*[:=]?\s*\d+%)", re.IGNORECASE)
REV_RE = re.compile(r"\brev(?:ision)?\.?\s*#?\s*(\d+)\b", re.IGNORECASE)
SPEC_REF_RE = re.compile(r"\b(?:spec(?:ification)?\s+)?section\s+(\d{2}\s?\d{2}\s?\d{2})\b", re.IGNORECASE)
DRAWING_REF_RE = re.compile(r"\b((?:FP|[ASMEPCLTGIQDF])-?\d{1,4}(?:\.\d{1,2})?)\b(?=.*\b(?:rev|drawing|sheet|dated)\b)", re.IGNORECASE)


def _money(s: str) -> float:
    return float(s.replace(",", ""))


def parse_proposal(text: str) -> dict:
    """Parse proposal text. Returns {base_bid, extracted, lines, needs_review}."""
    extracted: dict[str, dict] = {}
    lines_out: dict[str, list] = {
        "alternates": [],
        "allowances": [],
        "unit_prices": [],
        "exclusions": [],
        "qualifications": [],
        "assumptions": [],
        "inclusions": [],
        "line_items": [],
        "spec_references": [],
        "drawing_references": [],
    }

    text_lines = text.splitlines()
    section: str | None = None

    for page_guess, raw in enumerate(text_lines):
        line = raw.strip()
        if not line:
            continue
        source_page = page_guess // 40 + 1  # ~40 lines per page heuristic

        header_hit = False
        for name, pat in SECTION_HEADERS.items():
            if pat.match(line):
                section = name
                header_hit = True
                break
        if header_hit:
            continue

        m = INLINE_EXCLUDE_RE.match(line)
        if m:
            lines_out["exclusions"].append({"text": m.group(1).strip(), "confidence": 0.9, "source_page": source_page})
            continue

        matched_list = False
        for name, pat in LIST_PATTERNS.items():
            m = pat.match(line)
            if m:
                groups = m.groups()
                entry: dict = {"original_text": line, "source_page": source_page, "confidence": 0.85}
                if name == "alternates":
                    entry.update({"number": groups[0], "description": groups[1].strip(),
                                  "amount": _money(groups[2]) if groups[2] else None})
                elif name == "unit_prices":
                    entry.update({"number": groups[0], "description": groups[1].strip(),
                                  "amount": _money(groups[2]), "unit": groups[3].upper()})
                elif name == "allowances":
                    entry.update({"number": groups[0], "description": groups[1].strip(),
                                  "amount": _money(groups[2])})
                lines_out[name].append(entry)
                matched_list = True
                break
        if matched_list:
            continue

        field_hit = False
        for fname, pat, conf in FIELD_PATTERNS:
            m = pat.search(line)
            if m and fname not in extracted:
                extracted[fname] = {
                    "value": m.group(0),
                    "normalized": _money(m.group(1)),
                    "confidence": conf,
                    "source_page": source_page,
                    "original_text": line,
                }
                field_hit = True

        for pat, key in ((LEAD_TIME_RE, "lead_time"), (SCHEDULE_RE, "schedule"),
                         (WARRANTY_RE, "warranty"), (PAYMENT_RE, "payment_terms"),
                         (REV_RE, "revision")):
            m = pat.search(line)
            if m and key not in extracted:
                extracted[key] = {
                    "value": m.group(0),
                    "normalized": m.group(0).strip(),
                    "confidence": 0.8,
                    "source_page": source_page,
                    "original_text": line,
                }
                field_hit = True

        for m in SPEC_REF_RE.finditer(line):
            lines_out["spec_references"].append({"section": m.group(1), "source_page": source_page})
        for m in DRAWING_REF_RE.finditer(line):
            lines_out["drawing_references"].append({"sheet": m.group(1).upper(), "source_page": source_page})

        if field_hit:
            section = None  # a top-level field ends any list section
            continue

        li = LINE_ITEM_RE.match(line)
        if li and section not in ("exclusions", "assumptions", "qualifications"):
            lines_out["line_items"].append({
                "description": li.group(1).strip(),
                "amount": _money(li.group(2)),
                "confidence": 0.8,
                "source_page": source_page,
            })
            continue

        if section in ("exclusions", "qualifications", "assumptions", "inclusions"):
            body = line.lstrip("-•*0123456789. ").strip()
            if body:
                lines_out[section].append({"text": body, "confidence": 0.85, "source_page": source_page})

    # If no explicit base bid but line items exist, infer with LOW confidence.
    if "base_bid" not in extracted and lines_out["line_items"]:
        total = sum(li["amount"] for li in lines_out["line_items"])
        extracted["base_bid"] = {
            "value": f"inferred from {len(lines_out['line_items'])} line items",
            "normalized": total,
            "confidence": 0.5,
            "source_page": None,
            "original_text": "(no explicit base bid found; summed line items)",
        }

    needs_review = [k for k, v in extracted.items() if v["confidence"] < REVIEW_THRESHOLD]
    base = extracted.get("base_bid", {}).get("normalized")
    return {"base_bid": base, "extracted": extracted, "lines": lines_out, "needs_review": needs_review}
