"""Machine-verifiable lifecycle receipts for CoScientist v4.6.

Reasoning may select, interpret and audit evidence, but it must not manufacture
facts that deterministic code can know directly.  This module records those
facts as small, write-once, tamper-evident receipts.  A receipt hash is not a
claim that an LLM cannot reproduce the bytes; provenance comes from the
controlled workflow/Drive write path.  The hash makes later mutation visible
and lets downstream gates bind to one exact record.
"""
from __future__ import annotations

import hashlib
import json
import os
from dataclasses import asdict, dataclass, field
from typing import Any, Iterable

from . import __version__
from .analysis_lock import AnalysisLock
from .freeze import FreezeManifest
from .gms_lake import Availability, GMSCatalog
from .models import utcnow

RECEIPT_SCHEMA_VERSION = 1


class ReceiptError(RuntimeError):
    pass


def sha256_file(path: str, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        while block := fh.read(chunk):
            h.update(block)
    return h.hexdigest()


def _canonical_hash(payload: dict[str, Any]) -> str:
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


@dataclass
class IntegrityReceipt:
    kind: str
    paper_id: str
    payload: dict[str, Any]
    created_at: str = field(default_factory=utcnow)
    engine_version: str = field(default_factory=lambda: __version__)
    schema_version: int = RECEIPT_SCHEMA_VERSION

    @property
    def scientific_content(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "kind": self.kind,
            "paper_id": self.paper_id,
            "payload": self.payload,
            "created_at": self.created_at,
            "engine_version": self.engine_version,
        }

    @property
    def receipt_hash(self) -> str:
        return _canonical_hash(self.scientific_content)

    @property
    def receipt_id(self) -> str:
        return f"RC-{self.kind}-{self.paper_id}-{self.receipt_hash[:12]}"

    def to_dict(self) -> dict[str, Any]:
        raw = asdict(self)
        raw["receipt_hash"] = self.receipt_hash
        raw["receipt_id"] = self.receipt_id
        return raw

    def save(self, path: str) -> str:
        """Write once. Same receipt is idempotent; different bytes are refused."""
        if os.path.exists(path):
            existing = IntegrityReceipt.load(path)
            if existing.receipt_hash == self.receipt_hash:
                return existing.receipt_id
            raise ReceiptError(
                f"receipt path {path} already contains {existing.receipt_id}; "
                "integrity receipts cannot be replaced in place"
            )
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(self.to_dict(), fh, indent=2, sort_keys=True)
        os.replace(tmp, path)
        return self.receipt_id

    @classmethod
    def load(cls, path: str) -> "IntegrityReceipt":
        with open(path, "r", encoding="utf-8") as fh:
            raw = json.load(fh)
        stored_hash = raw.pop("receipt_hash", None)
        stored_id = raw.pop("receipt_id", None)
        if not stored_hash:
            raise ReceiptError(f"{path} has no receipt_hash")
        receipt = cls(**raw)
        if receipt.receipt_hash != stored_hash:
            raise ReceiptError(
                f"{path} was altered: stored {stored_hash[:12]}, "
                f"recomputed {receipt.receipt_hash[:12]}"
            )
        if stored_id and receipt.receipt_id != stored_id:
            raise ReceiptError(
                f"{path} stores receipt id {stored_id} but derives {receipt.receipt_id}"
            )
        return receipt

    def require(self, *, kind: str | None = None, paper_id: str | None = None) -> None:
        if kind is not None and self.kind != kind:
            raise ReceiptError(f"expected {kind} receipt, got {self.kind}")
        if paper_id is not None and self.paper_id != paper_id:
            raise ReceiptError(
                f"receipt belongs to {self.paper_id}, expected active paper {paper_id}"
            )


def build_dataset_receipt(
    catalog: GMSCatalog,
    *,
    paper_id: str,
    remote_paths: Iterable[str],
) -> IntegrityReceipt:
    """Bind design inputs to exact AVAILABLE lake objects and their catalog hashes.

    Only objects with a real SHA-256 may enter a confirmatory dataset receipt.
    Query-layer/external data must first be materialised into a hashable lake
    object; an LLM-provided hash is never accepted as dataset identity.
    """
    requested = list(dict.fromkeys(str(x) for x in remote_paths))
    if not requested:
        raise ReceiptError("dataset receipt requires at least one remote_path")
    by_path = {d.remote_path: d for d in catalog.datasets}
    rows: list[dict[str, Any]] = []
    hashes: dict[str, str] = {}
    for path in requested:
        d = by_path.get(path)
        if d is None:
            raise ReceiptError(f"dataset {path!r} is absent from the current GMS catalog")
        if d.availability != Availability.AVAILABLE.value:
            raise ReceiptError(f"dataset {path!r} is not AVAILABLE")
        sha = str(d.sha256 or "").lower()
        if len(sha) != 64 or any(c not in "0123456789abcdef" for c in sha):
            raise ReceiptError(
                f"dataset {path!r} has no valid catalog SHA-256; materialise/hash it first"
            )
        hashes[path] = sha
        rows.append({
            "source_id": d.source_id,
            "dataset_id": d.dataset_id,
            "remote_path": d.remote_path,
            "sha256": sha,
            "analysis_route": d.analysis_route,
            "granularity": d.granularity,
            "coverage_start": d.coverage_start,
            "coverage_end": d.coverage_end,
            "bytes": d.bytes,
        })
    payload = {
        "lake_repo": catalog.lake_repo,
        "lake_repo_sha": catalog.lake_repo_sha,
        "catalog_generated_at": catalog.generated_at,
        "dataset_hashes": dict(sorted(hashes.items())),
        "datasets": sorted(rows, key=lambda x: x["remote_path"]),
    }
    return IntegrityReceipt("DATASET_SET", paper_id, payload)


def build_power_receipt(
    *,
    paper_id: str,
    input_path: str,
    pre_period_end: Any,
    parameters: dict[str, Any],
    result: dict[str, Any],
) -> IntegrityReceipt:
    """Bind a deterministic pre-period power result to the bytes and parameters used."""
    powered = result.get("powered")
    if powered is not True:
        raise ReceiptError("a PASS power receipt requires deterministic powered=true")
    payload = {
        "input_sha256": sha256_file(input_path),
        "input_name": os.path.basename(input_path),
        "pre_period_end": pre_period_end,
        "parameters": parameters,
        "result": result,
        "status": "PASS",
    }
    return IntegrityReceipt("POWER", paper_id, payload)


def build_analysis_receipt(
    *,
    paper_id: str,
    freeze_path: str,
    analysis_lock_path: str,
    results_manifest_path: str,
    git_sha: str,
    workflow_run_id: str,
    provenance_status: str,
    publication_status: str,
) -> IntegrityReceipt:
    """Record the successful confirmatory chain after publication.

    Receipt creation itself verifies the freeze and AnalysisLock structures and
    refuses a non-PASS provenance/publication state.  The result manifest is
    content-hashed so later completion cannot point at different bytes.
    """
    if provenance_status.upper() != "PASS":
        raise ReceiptError("analysis receipt requires provenance_status=PASS")
    if publication_status.upper() != "PASS":
        raise ReceiptError("analysis receipt requires publication_status=PASS")
    freeze = FreezeManifest.load(freeze_path)
    lock = AnalysisLock.load(analysis_lock_path)
    if freeze.candidate_id != paper_id:
        raise ReceiptError("freeze belongs to a different paper")
    if lock.freeze_hash != freeze.freeze_hash:
        raise ReceiptError("analysis lock is not bound to the supplied freeze")
    if not os.path.isfile(results_manifest_path):
        raise ReceiptError(f"results manifest missing: {results_manifest_path}")
    payload = {
        "freeze_id": freeze.freeze_id,
        "freeze_hash": freeze.freeze_hash,
        "analysis_lock_id": lock.lock_id,
        "analysis_lock_hash": lock.lock_hash,
        "results_manifest_name": os.path.basename(results_manifest_path),
        "results_manifest_sha256": sha256_file(results_manifest_path),
        "git_sha": str(git_sha),
        "workflow_run_id": str(workflow_run_id),
        "provenance_status": "PASS",
        "publication_status": "PASS",
    }
    return IntegrityReceipt("ANALYSIS", paper_id, payload)


def build_final_audit_evidence(
    *,
    paper_id: str,
    freeze_path: str,
    analysis_receipt_path: str,
    manuscript_path: str,
    numeric_provenance_status: str,
    reproducibility_status: str,
) -> IntegrityReceipt:
    """Mechanical evidence consumed by, but not invented by, the final auditor."""
    if numeric_provenance_status.upper() != "PASS":
        raise ReceiptError("final-audit evidence requires numeric provenance PASS")
    if reproducibility_status.upper() != "PASS":
        raise ReceiptError("final-audit evidence requires reproducibility PASS")
    freeze = FreezeManifest.load(freeze_path)
    analysis = IntegrityReceipt.load(analysis_receipt_path)
    analysis.require(kind="ANALYSIS", paper_id=paper_id)
    if analysis.payload.get("freeze_hash") != freeze.freeze_hash:
        raise ReceiptError("analysis receipt and freeze do not match")
    if not os.path.isfile(manuscript_path):
        raise ReceiptError(f"manuscript missing: {manuscript_path}")
    return IntegrityReceipt("FINAL_AUDIT_EVIDENCE", paper_id, {
        "freeze_hash": freeze.freeze_hash,
        "analysis_receipt_hash": analysis.receipt_hash,
        "manuscript_sha256": sha256_file(manuscript_path),
        "numeric_provenance": "PASS",
        "reproducibility": "PASS",
    })
