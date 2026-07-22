"""Unit tests for the BidLevel AI layer (pure functions, no DB)."""
from bidlevel.ai import drawing, normalize
from bidlevel.ai.proposal import parse_proposal


def test_sheet_recognition():
    text = (
        "A-101 - First Floor Plan\n"
        "Revision 4 dated 2026-05-01\n"
        'Scale: 1/8" = 1\'-0"\n'
        "General note: dimensions to face of stud\n"
        "S-201 Foundation Plan\n"
    )
    sheets = drawing.parse_sheets(text)
    assert len(sheets) == 2
    a101 = sheets[0]
    assert a101.number == "A-101"
    assert a101.title == "First Floor Plan"
    assert a101.discipline == "Architecture"
    assert a101.revision == "4"
    assert a101.revision_date == "2026-05-01"
    assert a101.scale.startswith('1/8"')
    assert a101.floor == "First Floor"
    assert any(n["kind"] == "general_note" for n in a101.notes)
    assert sheets[1].discipline == "Structural"


def test_trade_detection():
    trades = {t for t, _, _ in drawing.detect_trades(
        "concrete footings, EMT conduit, fire alarm devices, ductwork")}
    assert {"Concrete", "Electrical", "Fire Alarm", "HVAC"} <= trades


def test_quantity_extraction_links_sheet_and_trade():
    text = "A-101 - Floor Plan\nDrywall partitions: 12,400 SF gypsum wall\nRebar: 42 TONS"
    qtys = drawing.extract_quantities(text)
    assert len(qtys) == 2
    dw = qtys[0]
    assert dw.value == 12400 and dw.unit == "SF"
    assert dw.source_sheet == "A-101"
    assert dw.trade == "Drywall" and dw.csi_code == "09"
    assert 0 < dw.confidence <= 0.95
    assert qtys[1].unit == "TON" and qtys[1].value == 42


def test_scope_interpretation_owner_furnished():
    info = drawing.interpret_scope_line("Install owner supplied hardware.")
    assert info is not None
    assert info.installation_included is True
    assert info.material_included is False
    assert info.furnished_by == "owner"
    assert info.installed_by == "contractor"
    assert not info.excluded


def test_scope_interpretation_exclusion_and_furnish_only():
    excl = drawing.interpret_scope_line("Landscaping and irrigation by others.")
    assert excl.excluded is True
    fo = drawing.interpret_scope_line("Elevator equipment: furnish only - installation by others.")
    assert fo.material_included is True or fo.installation_included is False


def test_proposal_parsing_full():
    text = (
        "Base Bid: $2,450,000\n"
        "Alternate 1: Generator upgrade - $185,000\n"
        "Unit Price 1: Additional circuit @ $850 per EA\n"
        "Allowance 1: Security rough-in $25,000\n"
        "Exclusions:\n- Sales tax\n- Bond\n"
        "Schedule: 34 weeks\n"
        "12 week lead time on switchgear\n"
        "Payment terms: Net 30\n"
        "Per Section 26 05 00 and drawing E-401 Rev 2\n"
    )
    p = parse_proposal(text)
    assert p["base_bid"] == 2_450_000
    bb = p["extracted"]["base_bid"]
    assert bb["confidence"] >= 0.9 and bb["source_page"] == 1
    assert p["lines"]["alternates"][0]["amount"] == 185_000
    assert p["lines"]["unit_prices"][0]["unit"] == "EA"
    assert p["lines"]["allowances"][0]["amount"] == 25_000
    assert [e["text"] for e in p["lines"]["exclusions"]] == ["Sales tax", "Bond"]
    assert "lead_time" in p["extracted"]
    assert "payment_terms" in p["extracted"]
    assert p["lines"]["spec_references"][0]["section"] == "26 05 00"
    assert p["needs_review"] == []


def test_proposal_inferred_base_bid_flagged_for_review():
    text = "Lighting: $410,000\nGear: $520,000\n"
    p = parse_proposal(text)
    assert p["base_bid"] == 930_000
    assert p["extracted"]["base_bid"]["confidence"] == 0.5
    assert "base_bid" in p["needs_review"]


def test_normalization_synonyms_and_missing():
    proposals = [
        {"proposal_id": 1, "statements": ["Provide temporary power"]},
        {"proposal_id": 2, "statements": ["Temporary utilities included"]},
        {"proposal_id": 3, "statements": ["No temporary services"]},
        {"proposal_id": 4, "statements": ["HVAC startup"]},
    ]
    m = normalize.build_scope_matrix(proposals)
    keys = {c["canonical_key"] for c in m["categories"]}
    assert "temporary_power" in keys
    assert m["mappings"][1]["temporary_power"]["status"] == "included"
    assert m["mappings"][2]["temporary_power"]["status"] == "included"
    assert m["mappings"][3]["temporary_power"]["status"] == "excluded"
    assert m["mappings"][4]["temporary_power"]["status"] == "missing"


def test_normalization_override_learning():
    hits = normalize.classify_statement("site trailer hookups included")
    assert not any(h["canonical_key"] == "temporary_power" for h in hits)
    hits = normalize.classify_statement(
        "site trailer hookups included",
        overrides=[("site trailer hookups", "temporary_power")],
    )
    assert any(h["canonical_key"] == "temporary_power" and h["status"] == "included" for h in hits)
