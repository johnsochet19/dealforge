"""Bid normalization: map every proposal onto a shared Master Scope Matrix so
bidders are compared apples-to-apples.

Synonym groups capture regional/vendor terminology. User overrides (stored as
SynonymOverride rows) are merged in at runtime, so manual corrections improve
future mapping — the "learning" loop without a training pipeline.
"""
from __future__ import annotations

import re

# canonical_key -> display name, synonym phrases
SCOPE_SYNONYMS: dict[str, dict] = {
    "temporary_power": {
        "name": "Temporary Power / Utilities",
        "phrases": ["temporary power", "temp power", "temporary utilities", "temporary services",
                    "temp utilities", "construction power"],
    },
    "hoisting": {
        "name": "Hoisting & Crane",
        "phrases": ["hoisting", "crane", "craneage", "rigging", "lifting"],
    },
    "cleanup": {
        "name": "Cleanup & Debris Removal",
        "phrases": ["cleanup", "clean up", "debris removal", "trash removal", "dumpster", "broom clean"],
    },
    "permits": {
        "name": "Permits & Fees",
        "phrases": ["permit", "permits", "fees", "inspection fees"],
    },
    "engineering": {
        "name": "Engineering & Shop Drawings",
        "phrases": ["shop drawings", "engineering", "submittals", "stamped drawings", "calcs", "design"],
    },
    "taxes": {
        "name": "Sales & Use Tax",
        "phrases": ["sales tax", "use tax", "taxes", "tax"],
    },
    "bonds": {
        "name": "Payment & Performance Bond",
        "phrases": ["bond", "bonds", "p&p bond", "payment and performance"],
    },
    "scaffolding": {
        "name": "Scaffolding & Access",
        "phrases": ["scaffold", "scaffolding", "lifts", "access equipment", "man lift"],
    },
    "layout": {
        "name": "Layout & Field Engineering",
        "phrases": ["layout", "field engineering", "survey", "control lines"],
    },
    "warranty": {
        "name": "Warranty",
        "phrases": ["warranty", "guarantee"],
    },
    "freight": {
        "name": "Freight & Delivery",
        "phrases": ["freight", "delivery", "shipping"],
    },
    "testing": {
        "name": "Testing & Commissioning",
        "phrases": ["testing", "commissioning", "startup", "start-up", "balancing"],
    },
}

NEGATION_RE = re.compile(
    r"\b(?:no|not|excludes?|excluded|excluding|without|by others|nic)\b", re.IGNORECASE
)


def _phrase_map(overrides: list[tuple[str, str]] | None = None) -> list[tuple[str, str]]:
    """[(phrase, canonical_key)] longest-phrase-first; overrides take priority."""
    pairs: list[tuple[str, str]] = []
    for key, spec in SCOPE_SYNONYMS.items():
        for p in spec["phrases"]:
            pairs.append((p.lower(), key))
    for phrase, key in overrides or []:
        pairs.append((phrase.lower(), key))
    pairs.sort(key=lambda t: len(t[0]), reverse=True)
    return pairs


def classify_statement(text: str, overrides: list[tuple[str, str]] | None = None) -> list[dict]:
    """Classify one proposal statement against the scope taxonomy.

    Returns [{canonical_key, status(included|excluded), evidence, confidence}].
    """
    low = text.lower()
    hits = []
    seen = set()
    for phrase, key in _phrase_map(overrides):
        if phrase in low and key not in seen:
            seen.add(key)
            negated = bool(NEGATION_RE.search(low))
            hits.append({
                "canonical_key": key,
                "status": "excluded" if negated else "included",
                "evidence": text.strip(),
                "confidence": 0.9 if phrase in low else 0.7,
            })
    return hits


def build_scope_matrix(proposals: list[dict], overrides: list[tuple[str, str]] | None = None) -> dict:
    """Build the Master Scope Matrix across proposals.

    proposals: [{proposal_id, statements: [str, ...]}]
    Returns {categories: [{canonical_key, name}], mappings: {proposal_id: {key: {...}}}}
    A category enters the matrix if ANY bidder mentions it; bidders that never
    mention it are marked `missing` — the leveling gap this engine exists to catch.
    """
    per_proposal: dict = {}
    active_keys: set[str] = set()

    for p in proposals:
        found: dict[str, dict] = {}
        for stmt in p["statements"]:
            for hit in classify_statement(stmt, overrides):
                key = hit["canonical_key"]
                prev = found.get(key)
                # an explicit exclusion beats an earlier inclusion mention
                if prev is None or (hit["status"] == "excluded" and prev["status"] == "included"):
                    found[key] = hit
        per_proposal[p["proposal_id"]] = found
        active_keys.update(found.keys())

    categories = [
        {"canonical_key": k, "name": SCOPE_SYNONYMS.get(k, {"name": k})["name"]}
        for k in sorted(active_keys)
    ]
    mappings: dict = {}
    for p in proposals:
        pid = p["proposal_id"]
        row = {}
        for k in active_keys:
            hit = per_proposal[pid].get(k)
            if hit:
                row[k] = hit
            else:
                row[k] = {"canonical_key": k, "status": "missing", "evidence": "", "confidence": 0.75}
        mappings[pid] = row
    return {"categories": categories, "mappings": mappings}
