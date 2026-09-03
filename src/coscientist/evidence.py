"""Evidence triangulation and claim-strength calibration.

The CoScientist must synthesize supporting, neutral, and contradictory evidence
without letting one attractive dataset define the story. This module gives the
reasoning layer a deterministic evidence ledger and conservative wording cap.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import re
from typing import Iterable


class EvidenceSourceKind(str, Enum):
    NEW_EXPERIMENT = "NEW_EXPERIMENT"
    PUBLIC_OMICS = "PUBLIC_OMICS"
    PUBLIC_NON_OMICS = "PUBLIC_NON_OMICS"
    LITERATURE = "LITERATURE"


class EvidenceDirection(str, Enum):
    SUPPORTS = "SUPPORTS"
    CONTRADICTS = "CONTRADICTS"
    NEUTRAL = "NEUTRAL"


class EvidenceTier(str, Enum):
    DIRECT = "DIRECT"
    CONVERGENT = "CONVERGENT"
    SUPPORTING = "SUPPORTING"
    MECHANISTIC = "MECHANISTIC"
    CONTEXTUAL = "CONTEXTUAL"
    SPECULATIVE = "SPECULATIVE"


TIER_RANK = {
    EvidenceTier.SPECULATIVE: 0,
    EvidenceTier.CONTEXTUAL: 1,
    EvidenceTier.MECHANISTIC: 2,
    EvidenceTier.SUPPORTING: 3,
    EvidenceTier.CONVERGENT: 4,
    EvidenceTier.DIRECT: 5,
}


@dataclass(frozen=True)
class EvidenceItem:
    id: str
    source_kind: EvidenceSourceKind
    direction: EvidenceDirection
    tier: EvidenceTier
    description: str
    independent: bool = True
    accession_or_citation: str | None = None


@dataclass(frozen=True)
class ClaimAssessment:
    claim_id: str
    verdict: str
    recommended_language: str
    support_count: int
    contradiction_count: int
    neutral_count: int
    strongest_support: EvidenceTier | None
    strongest_contradiction: EvidenceTier | None
    rationale: str


def _strongest(items: list[EvidenceItem]) -> EvidenceTier | None:
    if not items:
        return None
    return max((x.tier for x in items), key=lambda x: TIER_RANK[x])


def _wording_for_tier(tier: EvidenceTier | None) -> str:
    if tier is EvidenceTier.DIRECT:
        return "provides direct evidence for"
    if tier is EvidenceTier.CONVERGENT:
        return "provides convergent evidence for"
    if tier is EvidenceTier.SUPPORTING:
        return "supports"
    if tier is EvidenceTier.MECHANISTIC:
        return "is consistent with"
    if tier is EvidenceTier.CONTEXTUAL:
        return "suggests"
    if tier is EvidenceTier.SPECULATIVE:
        return "raises the possibility of"
    return "does not establish"


def assess_claim(claim_id: str, evidence: Iterable[EvidenceItem]) -> ClaimAssessment:
    items = list(evidence)
    supports = [x for x in items if x.direction is EvidenceDirection.SUPPORTS]
    contradicts = [x for x in items if x.direction is EvidenceDirection.CONTRADICTS]
    neutral = [x for x in items if x.direction is EvidenceDirection.NEUTRAL]
    strongest_support = _strongest(supports)
    strongest_contradiction = _strongest(contradicts)

    if not supports and not contradicts:
        return ClaimAssessment(
            claim_id, "UNSUPPORTED", "does not establish", 0, 0, len(neutral),
            None, None, "no directional evidence is registered for this claim"
        )
    if not supports and contradicts:
        return ClaimAssessment(
            claim_id, "CONTRADICTED", "is not supported by the available evidence",
            0, len(contradicts), len(neutral), None, strongest_contradiction,
            "registered evidence is directional only against the claim"
        )

    support_rank = TIER_RANK[strongest_support] if strongest_support else -1
    contradiction_rank = TIER_RANK[strongest_contradiction] if strongest_contradiction else -1

    if contradicts and contradiction_rank >= support_rank - 1:
        verdict = "MIXED"
        wording = "shows mixed evidence regarding"
        rationale = (
            "contradictory evidence is similar in strength to the strongest support; "
            "the manuscript must report heterogeneity rather than collapse it into a positive claim"
        )
    elif contradicts:
        verdict = "QUALIFIED_SUPPORT"
        wording = _wording_for_tier(strongest_support)
        rationale = (
            "support outweighs the registered contradiction, but the contradiction must be disclosed "
            "and the claim qualified"
        )
    else:
        verdict = "SUPPORTED"
        wording = _wording_for_tier(strongest_support)
        rationale = "registered directional evidence supports the claim without a contradictory item"

    return ClaimAssessment(
        claim_id=claim_id,
        verdict=verdict,
        recommended_language=wording,
        support_count=len(supports),
        contradiction_count=len(contradicts),
        neutral_count=len(neutral),
        strongest_support=strongest_support,
        strongest_contradiction=strongest_contradiction,
        rationale=rationale,
    )


PUBLIC_OMICS_MISREPRESENTATION_PATTERNS = (
    (r"\bour\s+(rna[ -]?seq|transcriptom(?:e|ic|ics)|qpcr|rt[ -]?qpcr)\b", "possessive wording implies author-generated molecular measurements"),
    (r"\bwe\s+(performed|conducted|generated|sequenced)\s+(rna[ -]?seq|transcriptom(?:e|ic|ics)|qpcr|rt[ -]?qpcr)\b", "wording explicitly claims author-generated molecular measurements"),
    (r"\bwe\s+sequenced\b", "wording explicitly claims sequencing by the authors"),
)


def lint_public_omics_language(text: str, *, has_new_molecular_measurements: bool = False) -> list[str]:
    """Flag language that misrepresents secondary omics as newly generated data."""
    if has_new_molecular_measurements:
        return []
    violations: list[str] = []
    for pattern, reason in PUBLIC_OMICS_MISREPRESENTATION_PATTERNS:
        if re.search(pattern, text, flags=re.IGNORECASE):
            violations.append(reason)
    return sorted(set(violations))


def public_omics_attribution(accession: str, observation: str) -> str:
    accession = accession.strip()
    observation = observation.strip()
    if not accession:
        raise ValueError("public omics result language requires an accession")
    if not observation:
        raise ValueError("public omics result language requires an observation")
    return f"Secondary reanalysis of the publicly available dataset {accession} showed {observation}"
