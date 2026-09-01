"""The lake as the data plane: read-first, write back what may be written.

The catalog is the engine's view of what the lake actually holds. It is
deliberately separate from the source registry: the registry says what the
engine *may reach*, the catalog says what is *already archived*.

"Write-back always" was too strong, and contradicted the registry it sits
beside: ACLED and Comtrade are admissible to query but their licences forbid
redistribution, so archiving their raw bytes would breach the terms that make
them usable at all. Deposit is therefore tiered by what each licence permits --
see `archive_policy`.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Iterable

from .models import Construct


class ArchivePolicy(str, Enum):
    """What may actually be deposited after an external fetch."""

    RAW = "RAW"                    # archive the bytes as retrieved
    DERIVED = "DERIVED"            # archive permitted aggregates only
    PROVENANCE_ONLY = "PROVENANCE_ONLY"   # store the retrieval recipe, not the data


def archive_policy(source, dataset: "LakeDataset | None" = None) -> ArchivePolicy:
    """Decide the deposit tier at write-back time.

    A dataset's own declared policy wins when it has one. BigQuery is a
    delivery platform carrying many datasets under different terms, so a
    platform-level `redistributable` flag cannot speak for all of them --
    moving the licence to the catalog was only half the fix, and this is the
    other half: the enforcement path has to read it.
    """
    if dataset is not None and dataset.archive_policy:
        return ArchivePolicy(dataset.archive_policy)
    if getattr(source, "platform", False):
        # A delivery platform carries many datasets under different terms, so
        # it cannot answer for any of them. Refusing here is fail-safe: an
        # adapter that forgets to pass its dataset gets PROVENANCE_ONLY rather
        # than the platform's blanket RAW.
        return ArchivePolicy.PROVENANCE_ONLY
    if not source.admissible:
        return ArchivePolicy.PROVENANCE_ONLY
    if source.redistributable:
        return ArchivePolicy.RAW
    return ArchivePolicy.DERIVED


@dataclass
class LakeDataset:
    id: str
    domain: str
    concepts: list[str] = field(default_factory=list)
    granularity: str | None = None
    coverage_start: str | None = None
    coverage_end: str | None = None
    keys: list[str] = field(default_factory=list)
    rows: int | None = None
    source_id: str | None = None
    licence: str | None = None      # human-readable terms
    archive_policy: str | None = None   # machine policy: RAW | DERIVED | PROVENANCE_ONLY
    # Physical/provenance fields supplied by the GMS manifest adapter.  These
    # let G3 reason over what exists without making Drive itself the index.
    dataset_id: str | None = None
    remote_path: str | None = None
    sha256: str | None = None
    bytes: int | None = None
    status: str | None = None
    availability: str | None = None
    analysis_route: str | None = None
    direct_fetch: bool | None = None
    manifest_path: str | None = None
    manifest_sha256: str | None = None
    lake_repo_sha: str | None = None
    notes: str | None = None

    def covers(self, start: str | None, end: str | None) -> bool:
        """Does this dataset span the requested window?"""
        if start and self.coverage_start and self.coverage_start > start:
            return False
        if end and self.coverage_end and self.coverage_end < end:
            return False
        return True

    def concept_overlap(self, concepts: Iterable[str]) -> int:
        return len({c.lower() for c in concepts} & set(self.concepts))


@dataclass
class LakeCatalog:
    datasets: dict[str, LakeDataset] = field(default_factory=dict)

    @classmethod
    def load(cls, path: str) -> "LakeCatalog":
        with open(path, "r", encoding="utf-8") as fh:
            raw = json.load(fh)
        out: dict[str, LakeDataset] = {}
        for e in raw.get("datasets", []):
            ds = LakeDataset(
                id=e["id"],
                domain=e.get("domain", "unknown"),
                concepts=[c.lower() for c in e.get("concepts", [])],
                granularity=e.get("granularity"),
                coverage_start=e.get("coverage_start"),
                coverage_end=e.get("coverage_end"),
                keys=e.get("keys", []),
                rows=e.get("rows"),
                source_id=e.get("source_id"),
                licence=e.get("licence"),
                archive_policy=e.get("archive_policy"),
                dataset_id=e.get("dataset_id"),
                remote_path=e.get("remote_path"),
                sha256=e.get("sha256"),
                bytes=e.get("bytes"),
                status=e.get("status"),
                availability=e.get("availability"),
                analysis_route=e.get("analysis_route"),
                direct_fetch=e.get("direct_fetch"),
                manifest_path=e.get("manifest_path"),
                manifest_sha256=e.get("manifest_sha256"),
                lake_repo_sha=e.get("lake_repo_sha"),
                notes=e.get("notes"),
            )
            out[ds.id] = ds
        return cls(datasets=out)

    def find(self, construct: Construct, require_granularity: bool = False,
             include_query_required: bool = False) -> list[LakeDataset]:
        """Candidate datasets that could measure this construct, best first.

        A catalog entry is not the same thing as usable bytes. Partial/failed
        objects do not count as lake substitutes, and query-layer-required raw
        snapshots are excluded unless the caller is explicitly asking whether
        such a deferred route exists.
        """
        scored: list[tuple[int, LakeDataset]] = []
        for ds in self.datasets.values():
            if ds.availability and ds.availability != "AVAILABLE":
                continue
            if (ds.analysis_route == "QUERY_LAYER_REQUIRED" and
                    not include_query_required):
                continue
            overlap = ds.concept_overlap(construct.concepts)
            if not overlap:
                continue
            if not ds.covers(construct.coverage_start, construct.coverage_end):
                continue
            if require_granularity and construct.granularity and ds.granularity != construct.granularity:
                continue
            scored.append((overlap, ds))
        scored.sort(key=lambda t: (-t[0], t[1].id))
        return [d for _, d in scored]

    def has(self, construct: Construct) -> bool:
        return bool(self.find(construct))

    def requires_query_layer(self, construct: Construct) -> bool:
        return any(d.analysis_route == "QUERY_LAYER_REQUIRED"
                   for d in self.find(construct, include_query_required=True))

    def to_dict(self) -> dict[str, Any]:
        return {"datasets": [d.__dict__ for d in self.datasets.values()]}
