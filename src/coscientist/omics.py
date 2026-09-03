"""Public-omics data fitness, representation choice, and immutable dataset freeze.

This is the deterministic G3-OMICS layer. It does not decide biological meaning
from free text on its own; the reasoning layer supplies structured target terms
and dataset metadata, and this module applies auditable compatibility rules.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
import hashlib
import json
import re
from typing import Any, Iterable

from . import __version__
from .models import utcnow


class OmicsCapabilityBlocked(RuntimeError):
    pass


class OmicsModality(str, Enum):
    RNA_SEQ = "RNA_SEQ"
    MICROARRAY = "MICROARRAY"
    SINGLE_CELL_RNA_SEQ = "SINGLE_CELL_RNA_SEQ"
    PROTEOMICS = "PROTEOMICS"
    METABOLOMICS = "METABOLOMICS"
    EPIGENOMICS = "EPIGENOMICS"
    GWAS = "GWAS"
    QTL = "QTL"
    PHENOMICS = "PHENOMICS"


class OmicsFitnessClass(str, Enum):
    A = "A_DIRECTLY_COMPARABLE"
    B = "B_STRONGLY_COMPATIBLE"
    C = "C_MECHANISTICALLY_COMPATIBLE"
    D = "D_CONTEXTUAL_ONLY"
    E = "E_INCOMPATIBLE"


class DataRepresentation(str, Enum):
    FASTQ = "FASTQ"
    RAW_COUNTS = "RAW_COUNTS"
    PROCESSED_MATRIX = "PROCESSED_MATRIX"


EVIDENCE_USE = {
    OmicsFitnessClass.A: "CONVERGENT",
    OmicsFitnessClass.B: "SUPPORTING",
    OmicsFitnessClass.C: "MECHANISTIC",
    OmicsFitnessClass.D: "CONTEXTUAL",
    OmicsFitnessClass.E: "REJECT",
}


def _norm(text: str | None) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).strip()


def _tokens(text: str | None) -> set[str]:
    return {t for t in _norm(text).split() if len(t) > 1}


@dataclass(frozen=True)
class PublicOmicsDataset:
    accession: str
    repository: str
    modality: OmicsModality
    species: str
    tissue: str
    condition: str
    biological_replicates: int
    public: bool = True
    provenance_url: str | None = None
    source_publication: str | None = None
    developmental_stage: str | None = None
    sampling_time_hours: float | None = None
    reference_build: str | None = None
    annotation_version: str | None = None
    batch_documented: bool = False
    metadata_complete: bool = True
    raw_available: bool = False
    counts_available: bool = False
    processed_available: bool = True
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["modality"] = self.modality.value
        return d

    def provenance_failures(self) -> list[str]:
        failures: list[str] = []
        if not self.public:
            failures.append("dataset is not declared public")
        if not self.accession.strip():
            failures.append("missing accession")
        if not self.repository.strip():
            failures.append("missing repository")
        if not self.species.strip():
            failures.append("missing species")
        if not self.tissue.strip():
            failures.append("missing tissue")
        if not self.condition.strip():
            failures.append("missing condition")
        if self.biological_replicates < 1:
            failures.append("biological replicate count must be positive")
        if not self.provenance_url and not self.source_publication:
            failures.append("no provenance URL or source publication")
        return failures


@dataclass(frozen=True)
class OmicsTargetProfile:
    species: str
    tissue: str
    condition_terms: tuple[str, ...]
    accepted_modalities: tuple[OmicsModality, ...] = (OmicsModality.RNA_SEQ,)
    mechanism_terms: tuple[str, ...] = ()
    compatible_species: tuple[str, ...] = ()
    developmental_stage: str | None = None
    sampling_time_hours: float | None = None
    time_tolerance_hours: float = 24.0
    min_replicates: int = 3


@dataclass(frozen=True)
class OmicsFitnessResult:
    accession: str
    classification: OmicsFitnessClass
    score: int
    evidence_use: str
    reasons: tuple[str, ...] = ()
    hard_failures: tuple[str, ...] = ()

    @property
    def admissible(self) -> bool:
        return self.classification is not OmicsFitnessClass.E


def _coverage(terms: Iterable[str], haystack: str) -> float:
    want = {_norm(t) for t in terms if _norm(t)}
    if not want:
        return 0.0
    tokens = _tokens(haystack)
    matched = 0
    for term in want:
        term_tokens = set(term.split())
        if term_tokens and term_tokens.issubset(tokens):
            matched += 1
    return matched / len(want)


def assess_omics_fitness(dataset: PublicOmicsDataset, target: OmicsTargetProfile) -> OmicsFitnessResult:
    """Classify a public dataset from A (direct) to E (incompatible).

    A/B are the only classes suitable for strong independent support. C is
    mechanistic context, D is contextual only, and E must not enter the claim
    evidence matrix except as a recorded rejection.
    """
    failures = dataset.provenance_failures()
    if dataset.modality not in target.accepted_modalities:
        failures.append(
            f"modality {dataset.modality.value} is outside accepted modalities"
        )
    if dataset.biological_replicates < 2:
        failures.append("fewer than two biological replicates")
    if failures:
        return OmicsFitnessResult(
            accession=dataset.accession,
            classification=OmicsFitnessClass.E,
            score=0,
            evidence_use=EVIDENCE_USE[OmicsFitnessClass.E],
            hard_failures=tuple(failures),
            reasons=("failed mandatory provenance/design checks",),
        )

    species_exact = _norm(dataset.species) == _norm(target.species)
    species_compatible = _norm(dataset.species) in {_norm(x) for x in target.compatible_species}
    tissue_exact = _norm(dataset.tissue) == _norm(target.tissue)
    condition_coverage = _coverage(target.condition_terms, dataset.condition)
    mechanism_coverage = _coverage(target.mechanism_terms, dataset.condition)
    replicate_ok = dataset.biological_replicates >= target.min_replicates

    stage_ok = True
    if target.developmental_stage:
        stage_ok = _norm(dataset.developmental_stage) == _norm(target.developmental_stage)

    time_ok = True
    if target.sampling_time_hours is not None:
        if dataset.sampling_time_hours is None:
            time_ok = False
        else:
            time_ok = abs(dataset.sampling_time_hours - target.sampling_time_hours) <= target.time_tolerance_hours

    score = 0
    score += 30 if species_exact else (10 if species_compatible else 0)
    score += 20 if tissue_exact else 0
    score += round(25 * condition_coverage)
    score += round(10 * mechanism_coverage)
    score += 5 if replicate_ok else 0
    score += 3 if dataset.metadata_complete else 0
    score += 2 if (dataset.counts_available or dataset.raw_available) else 0
    score += 3 if stage_ok else 0
    score += 2 if time_ok else 0
    score = min(score, 100)

    reasons: list[str] = []
    reasons.append("species exact" if species_exact else "species differs from target")
    reasons.append("tissue exact" if tissue_exact else "tissue differs from target")
    reasons.append(f"condition-term coverage={condition_coverage:.2f}")
    if target.mechanism_terms:
        reasons.append(f"mechanism-term coverage={mechanism_coverage:.2f}")
    reasons.append(
        f"replicates={dataset.biological_replicates} ({'adequate' if replicate_ok else 'below target'})"
    )
    if target.developmental_stage:
        reasons.append("developmental stage compatible" if stage_ok else "developmental stage differs/unknown")
    if target.sampling_time_hours is not None:
        reasons.append("sampling time compatible" if time_ok else "sampling time differs/unknown")

    if (
        species_exact
        and tissue_exact
        and condition_coverage >= 0.75
        and replicate_ok
        and dataset.metadata_complete
        and stage_ok
        and time_ok
    ):
        cls = OmicsFitnessClass.A
    elif species_exact and tissue_exact and condition_coverage >= 0.34 and replicate_ok:
        cls = OmicsFitnessClass.B
    elif species_exact and (tissue_exact or condition_coverage > 0 or mechanism_coverage > 0):
        cls = OmicsFitnessClass.C
    elif species_compatible or mechanism_coverage > 0:
        cls = OmicsFitnessClass.D
    else:
        cls = OmicsFitnessClass.E

    return OmicsFitnessResult(
        accession=dataset.accession,
        classification=cls,
        score=score,
        evidence_use=EVIDENCE_USE[cls],
        reasons=tuple(reasons),
        hard_failures=(),
    )


RAW_REQUIRED_PURPOSES = {
    "read_level_qc",
    "alternative_splicing",
    "variant_calling",
    "novel_transcript",
    "allele_specific_expression",
}


def choose_representation(dataset: PublicOmicsDataset, purposes: Iterable[str]) -> DataRepresentation:
    """Choose the least expensive representation that can answer the question."""
    purpose_set = {_norm(x).replace(" ", "_") for x in purposes}
    if purpose_set & RAW_REQUIRED_PURPOSES:
        if not dataset.raw_available:
            needed = ", ".join(sorted(purpose_set & RAW_REQUIRED_PURPOSES))
            raise OmicsCapabilityBlocked(
                f"{dataset.accession} needs read-level data for {needed}, but raw reads are unavailable"
            )
        return DataRepresentation.FASTQ
    if dataset.counts_available:
        return DataRepresentation.RAW_COUNTS
    if dataset.processed_available:
        return DataRepresentation.PROCESSED_MATRIX
    if dataset.raw_available:
        return DataRepresentation.FASTQ
    raise OmicsCapabilityBlocked(
        f"{dataset.accession} exposes no raw reads, count matrix, or processed matrix"
    )


@dataclass(frozen=True)
class OmicsDatasetFreeze:
    """Selection/provenance lock for public omics before outcome analysis."""

    datasets: tuple[PublicOmicsDataset, ...]
    selection_protocol: str
    exclusions: tuple[tuple[str, str], ...] = ()
    frozen_at: str = field(default_factory=utcnow)
    engine_version: str = field(default_factory=lambda: __version__)

    def scientific_content(self) -> dict[str, Any]:
        datasets = sorted((d.to_dict() for d in self.datasets), key=lambda d: (d["repository"], d["accession"]))
        return {
            "datasets": datasets,
            "selection_protocol": self.selection_protocol.strip(),
            "exclusions": sorted([list(x) for x in self.exclusions]),
        }

    @property
    def freeze_hash(self) -> str:
        blob = json.dumps(self.scientific_content(), sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()

    @property
    def freeze_id(self) -> str:
        return f"ODF-{self.freeze_hash[:16]}"

    def to_dict(self) -> dict[str, Any]:
        return {
            **self.scientific_content(),
            "frozen_at": self.frozen_at,
            "engine_version": self.engine_version,
            "freeze_hash": self.freeze_hash,
            "freeze_id": self.freeze_id,
        }


def freeze_omics_datasets(
    datasets: Iterable[PublicOmicsDataset],
    *,
    selection_protocol: str,
    exclusions: Iterable[tuple[str, str]] = (),
) -> OmicsDatasetFreeze:
    datasets_tuple = tuple(datasets)
    if not datasets_tuple:
        raise OmicsCapabilityBlocked("cannot freeze an empty public-omics dataset set")
    if not selection_protocol.strip():
        raise OmicsCapabilityBlocked("dataset freeze requires an explicit selection protocol")
    seen: set[tuple[str, str]] = set()
    for dataset in datasets_tuple:
        failures = dataset.provenance_failures()
        if failures:
            raise OmicsCapabilityBlocked(
                f"cannot freeze {dataset.accession or '<missing accession>'}: " + "; ".join(failures)
            )
        key = (_norm(dataset.repository), _norm(dataset.accession))
        if key in seen:
            raise OmicsCapabilityBlocked(
                f"duplicate public dataset in freeze: {dataset.repository}:{dataset.accession}"
            )
        seen.add(key)
    return OmicsDatasetFreeze(
        datasets=datasets_tuple,
        selection_protocol=selection_protocol,
        exclusions=tuple(exclusions),
    )


def verify_omics_freeze(payload: dict[str, Any]) -> bool:
    """Verify a serialized freeze payload; fail closed on missing checksum."""
    stored = payload.get("freeze_hash")
    if not stored:
        return False
    scientific = {
        "datasets": sorted(payload.get("datasets") or [], key=lambda d: (d.get("repository", ""), d.get("accession", ""))),
        "selection_protocol": (payload.get("selection_protocol") or "").strip(),
        "exclusions": sorted(payload.get("exclusions") or []),
    }
    blob = json.dumps(scientific, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest() == stored
