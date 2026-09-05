import hashlib

import pytest

from coscientist.eventstudy_sec import (
    EXPECTED_CORPUS_COUNT,
    EXPECTED_FILENAME_SET_SHA256,
    ExtractionError,
    ai_candidates,
    extract_primary_10k,
    extract_sections,
    filename_set_sha256,
    find_headings,
    html_to_visible_text,
    select_qa,
    validate_corpus_listing,
)


def test_item1_does_not_match_item1a_and_sections_are_distinct():
    text = (
        "ITEM 1. BUSINESS\n"
        + "Business text with artificial intelligence. " * 20
        + "\nITEM 1A. RISK FACTORS\n"
        + "Risk text with machine learning. " * 20
        + "\nITEM 2. PROPERTIES\n"
        + "Property text.\n"
        + "ITEM 7. MANAGEMENT'S DISCUSSION AND ANALYSIS\n"
        + "MDA text with generative AI. " * 20
        + "\nITEM 7A. QUANTITATIVE AND QUALITATIVE DISCLOSURES\n"
    )
    headings = find_headings(text)
    assert [h.key for h in headings] == ["Item1", "Item1A", "Item2", "Item7", "Item7A"]
    sections = extract_sections(text)
    assert sections["Item1"].status == "OK"
    assert sections["Item1A"].status == "OK"
    assert sections["Item7"].status == "OK"
    assert sections["Item1"].sha256 != sections["Item1A"].sha256
    assert sections["Item1"].end_heading.startswith("ITEM 1A")
    assert "RISK FACTORS" not in sections["Item1"].text


def test_body_occurrence_beats_short_table_of_contents_occurrence():
    text = (
        "ITEM 1. BUSINESS\nTOC\nITEM 1A. RISK FACTORS\n"
        "ITEM 1. BUSINESS\n" + "body business details. " * 200
        + "\nITEM 1A. RISK FACTORS\n" + "risk details. " * 200
        + "\nITEM 2. PROPERTIES\n"
        + "ITEM 7. MANAGEMENT'S DISCUSSION AND ANALYSIS\n" + "mda details. " * 200
        + "\nITEM 8. FINANCIAL STATEMENTS\n"
    )
    sections = extract_sections(text)
    assert sections["Item1"].candidate_count >= 1
    assert "body business details" in sections["Item1"].text
    assert sections["Item1"].chars > 1000


def test_primary_document_selection_is_exact_10k_not_amendment():
    raw = (
        b"<DOCUMENT>\n<TYPE>10-K/A\n<TEXT>bad amendment</TEXT>\n</DOCUMENT>\n"
        b"<DOCUMENT>\n<TYPE>10-K\n<TEXT><html><body>good filing</body></html></TEXT>\n</DOCUMENT>"
    )
    assert "good filing" in extract_primary_10k(raw)
    assert "bad amendment" not in extract_primary_10k(raw)


def test_hidden_ixbrl_content_is_removed():
    source = (
        "<html><body><ix:hidden>ITEM 1A. FAKE HIDDEN</ix:hidden>"
        "<div>ITEM 1. BUSINESS</div><p>Visible business text.</p></body></html>"
    )
    visible = html_to_visible_text(source)
    assert "FAKE HIDDEN" not in visible
    assert "ITEM 1. BUSINESS" in visible


def test_section_hash_collision_is_hard_failure(monkeypatch):
    from coscientist import eventstudy_sec as mod

    original = mod.extract_section

    def fake(text, headings, target):
        result = original(text, headings, target)
        if target == "Item1A" and result.status == "OK":
            result.sha256 = "same"
        if target == "Item1" and result.status == "OK":
            result.sha256 = "same"
        return result

    monkeypatch.setattr(mod, "extract_section", fake)
    text = (
        "ITEM 1. BUSINESS\n" + "business " * 100
        + "\nITEM 1A. RISK FACTORS\n" + "risk " * 100
        + "\nITEM 2. PROPERTIES\n"
        + "ITEM 7. MANAGEMENT'S DISCUSSION AND ANALYSIS\n" + "mda " * 100
        + "\nITEM 8. FINANCIAL STATEMENTS\n"
    )
    with pytest.raises(ExtractionError, match="section collision"):
        mod.extract_sections(text)


def test_filename_set_identity_requires_exact_frozen_population():
    # A tiny fake corpus must fail both count and digest gates.
    files = [
        {"name": "edgar_data_1_0000000001-23-000001.txt", "id": "1", "size": "1000"},
        {"name": "edgar_data_2_0000000002-24-000002.txt", "id": "2", "size": "2000"},
    ]
    audit = validate_corpus_listing(files)
    assert audit["status"] == "FAIL"
    assert audit["expected_count"] == EXPECTED_CORPUS_COUNT
    assert audit["expected_filename_set_sha256"] == EXPECTED_FILENAME_SET_SHA256


def test_filename_hash_is_order_independent():
    a = ["b.txt", "a.txt"]
    b = ["a.txt", "b.txt"]
    assert filename_set_sha256(a) == filename_set_sha256(b)


def test_qa_sample_is_deterministic_and_size_stratified():
    files = []
    for i in range(200):
        yy = "23" if i % 2 == 0 else "24"
        files.append({
            "name": f"edgar_data_{i+1}_0000000001-{yy}-{i:06d}.txt",
            "id": str(i),
            "size": str(1000 + i * 100),
        })
    one = select_qa(files, 100)
    two = select_qa(list(reversed(files)), 100)
    assert [x["name"] for x in one] == [x["name"] for x in two]
    assert len(one) == 100
    sizes = [int(x["size"]) for x in one]
    assert min(sizes) < 5000
    assert max(sizes) > 15000
