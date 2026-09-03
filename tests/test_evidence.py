from coscientist.evidence import (
    EvidenceDirection,
    EvidenceItem,
    EvidenceSourceKind,
    EvidenceTier,
    assess_claim,
    lint_public_omics_language,
    public_omics_attribution,
)


def test_convergent_public_omics_supports_but_does_not_become_author_generated_data():
    assessment = assess_claim(
        "mechanical-stress-module",
        [
            EvidenceItem(
                id="exp1",
                source_kind=EvidenceSourceKind.NEW_EXPERIMENT,
                direction=EvidenceDirection.SUPPORTS,
                tier=EvidenceTier.DIRECT,
                description="new physiology experiment",
            ),
            EvidenceItem(
                id="geo1",
                source_kind=EvidenceSourceKind.PUBLIC_OMICS,
                direction=EvidenceDirection.SUPPORTS,
                tier=EvidenceTier.CONVERGENT,
                description="independent public wheat-root RNA-seq",
                accession_or_citation="GSE-TEST",
            ),
        ],
    )
    assert assessment.verdict == "SUPPORTED"
    assert assessment.support_count == 2


def test_strong_contradiction_forces_mixed_evidence_wording():
    assessment = assess_claim(
        "claim",
        [
            EvidenceItem("a", EvidenceSourceKind.PUBLIC_OMICS, EvidenceDirection.SUPPORTS, EvidenceTier.CONVERGENT, "support"),
            EvidenceItem("b", EvidenceSourceKind.PUBLIC_OMICS, EvidenceDirection.CONTRADICTS, EvidenceTier.SUPPORTING, "contradiction"),
        ],
    )
    assert assessment.verdict == "MIXED"
    assert assessment.recommended_language == "shows mixed evidence regarding"


def test_public_omics_language_linter_catches_false_authorship_claim():
    violations = lint_public_omics_language("Our RNA-seq showed a strong calcium-signalling response.")
    assert violations


def test_language_linter_allows_explicit_secondary_reanalysis():
    text = "Secondary reanalysis of the publicly available dataset GSE123 showed pathway enrichment."
    assert lint_public_omics_language(text) == []


def test_attribution_requires_accession_and_makes_secondary_status_explicit():
    text = public_omics_attribution("GSE123", "increased enrichment of ROS-related pathways.")
    assert "Secondary reanalysis" in text
    assert "GSE123" in text
