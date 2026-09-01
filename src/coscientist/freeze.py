"""The freeze manifest: the outcome lock as a mechanism, not a convention.

Until now the lock was `candidate.status == "FROZEN"` -- a flag anyone could
set, unset, or work around, protecting the single most important boundary in
the engine. This module replaces the flag with a hash.

Everything the design commits to is serialised canonically and hashed. Any
later change to the question, sample, treatment, outcome, exclusions, window,
models or multiplicity policy changes the hash, and every downstream artifact
carries the hash it was produced under. A post-freeze design change therefore
stops being something you have to notice and becomes something that fails.

The hash covers scientific content only. Timestamps, authorship and file paths
sit outside it, so freezing the same design twice yields the same id -- which
is the correct behaviour: it is the same design.
"""
from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field, asdict
from typing import Any

from . import __version__
from .models import Candidate, utcnow


class FreezeError(RuntimeError):
    pass


class FreezeViolation(FreezeError):
    """Raised when an artifact's freeze hash does not match the design."""


def hash_file(path: str, chunk: int = 1 << 20) -> str:
    """SHA-256 of a data file, streamed so a 2 GB panel does not enter memory."""
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        while True:
            block = fh.read(chunk)
            if not block:
                break
            h.update(block)
    return h.hexdigest()


@dataclass
class FreezeManifest:
    """The pre-registered design, and the hash that makes it binding."""

    candidate_id: str
    question: str
    estimand: str
    design: str
    sample_definition: str
    treatment: str
    outcome: str
    controls: list[str] = field(default_factory=list)
    exclusions: list[str] = field(default_factory=list)
    window_start: str | None = None
    window_end: str | None = None
    pre_period_end: str | None = None
    models: list[str] = field(default_factory=list)
    primary_contrasts: list[str] = field(default_factory=list)
    multiplicity_policy: str = "none"
    dataset_hashes: dict[str, str] = field(default_factory=dict)

    # Outside the hash: provenance, not design.
    frozen_at: str = field(default_factory=utcnow)
    engine_version: str = field(default_factory=lambda: __version__)
    note: str = ""

    def scientific_content(self) -> dict[str, Any]:
        """Exactly what the hash covers. Order-independent by construction."""
        return {
            "candidate_id": self.candidate_id,
            "question": self.question.strip(),
            "estimand": self.estimand.strip(),
            "design": self.design.strip(),
            "sample_definition": self.sample_definition.strip(),
            "treatment": self.treatment.strip(),
            "outcome": self.outcome.strip(),
            "controls": sorted(self.controls),
            "exclusions": sorted(self.exclusions),
            "window_start": self.window_start,
            "window_end": self.window_end,
            "pre_period_end": self.pre_period_end,
            "models": sorted(self.models),
            "primary_contrasts": sorted(self.primary_contrasts),
            "multiplicity_policy": self.multiplicity_policy,
            "dataset_hashes": dict(sorted(self.dataset_hashes.items())),
        }

    @property
    def freeze_hash(self) -> str:
        blob = json.dumps(self.scientific_content(), sort_keys=True,
                          separators=(",", ":"), ensure_ascii=True)
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()

    @property
    def freeze_id(self) -> str:
        return f"DF-{self.candidate_id}-{self.freeze_hash[:12]}"

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["freeze_hash"] = self.freeze_hash
        d["freeze_id"] = self.freeze_id
        return d

    def save(self, path: str) -> str:
        """Write-once. There is no replace path, and no flag that provides one.

        Two cases and no third. Same scientific hash: the file already records
        this design, so return its id and write nothing -- otherwise re-saving
        rewrites `frozen_at`, `engine_version` and `note`, keeping the id
        stable while the freeze's own history is quietly replaced. Different
        hash: refuse. A legitimate change writes a POST_FREEZE_CHANGE and a new
        lineage file.

        An earlier version took `allow_replace=True`. An escape hatch on a
        one-way door is a door.
        """
        if os.path.exists(path):
            existing = FreezeManifest.load(path)
            if existing.freeze_hash == self.freeze_hash:
                return existing.freeze_id      # already recorded; leave history alone
            raise FreezeViolation(
                f"{path} already holds freeze {existing.freeze_id}, which hashes "
                f"differently from this one ({self.freeze_id}). A freeze cannot be "
                "replaced in place: record a POST_FREEZE_CHANGE and write a new "
                "lineage version instead."
            )
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(self.to_dict(), fh, indent=2, sort_keys=True)
        os.replace(tmp, path)
        return self.freeze_id

    @classmethod
    def load(cls, path: str) -> "FreezeManifest":
        with open(path, "r", encoding="utf-8") as fh:
            raw = json.load(fh)
        stored_hash = raw.pop("freeze_hash", None)
        stored_id = raw.pop("freeze_id", None)

        # Fail CLOSED. `if stored_hash:` meant deleting the checksum field
        # disabled tamper detection entirely: strip the hash, edit the
        # question, and the altered design loaded clean under a freshly
        # computed id. A tamper check with a trivial bypass is not a check.
        if not stored_hash:
            raise FreezeViolation(
                f"freeze manifest at {path} carries no stored hash. A manifest "
                "without its checksum cannot be verified and is not a freeze."
            )

        manifest = cls(**raw)
        if stored_hash != manifest.freeze_hash:
            raise FreezeViolation(
                f"freeze manifest at {path} has been altered since it was written: "
                f"stored hash {stored_hash[:12]}, recomputed {manifest.freeze_hash[:12]}. "
                "The design changed after the lock."
            )
        if stored_id and stored_id != manifest.freeze_id:
            raise FreezeViolation(
                f"freeze manifest at {path} stores id {stored_id} but the design "
                f"derives {manifest.freeze_id}."
            )
        return manifest


REQUIRED = ("question", "estimand", "design", "sample_definition", "treatment", "outcome")

# Fields the Candidate and its manifest both carry. They must agree, or the
# in-memory object and the authoritative freeze describe different studies from
# the moment the freeze is created.
SHARED = ("question", "design")

# The structural fields too. `SHARED` covered only the two plain strings, so a
# candidate whose treatment was T and outcome O could be frozen against a
# manifest naming WRONG-T and WRONG-O and `freeze_candidate` accepted it: the
# two objects described different experiments from the instant of freezing.
STRUCTURAL = ("treatment", "outcome", "controls")


def candidate_disagreements(candidate: Candidate, manifest: "FreezeManifest") -> list[str]:
    """Every field on which the candidate and its manifest describe different studies."""
    out = [f for f in SHARED
           if (getattr(manifest, f) or "").strip() != (getattr(candidate, f) or "").strip()]
    derived = {
        "treatment": next((c.name for c in candidate.constructs
                           if c.role == "treatment"), ""),
        "outcome": next((c.name for c in candidate.constructs
                         if c.role == "outcome"), ""),
        "controls": sorted(c.name for c in candidate.constructs if c.role == "control"),
    }
    for name in STRUCTURAL:
        want, have = derived[name], getattr(manifest, name)
        if name == "controls":
            if sorted(have or []) != want:
                out.append(f"controls (candidate {want}, manifest {sorted(have or [])})")
        elif (have or "").strip() != want:
            out.append(f"{name} (candidate {want!r}, manifest {(have or '').strip()!r})")
    return out


def manifest_from_candidate(candidate: Candidate, **overrides: Any) -> "FreezeManifest":
    """Derive the manifest from the candidate so the two cannot disagree.

    Building them independently let a candidate asking QUESTION A be frozen
    against a manifest describing QUESTION B, and `freeze_candidate` accepted
    it because it only compared ids. One source of scientific state is safer
    than two that must be kept in step by hand.
    """
    fields = {
        "candidate_id": candidate.id,
        "question": candidate.question,
        "design": candidate.design,
        "treatment": next((c.name for c in candidate.constructs if c.role == "treatment"), ""),
        "outcome": next((c.name for c in candidate.constructs if c.role == "outcome"), ""),
        "controls": [c.name for c in candidate.constructs if c.role == "control"],
    }
    fields.update(overrides)
    return FreezeManifest(**fields)


def freeze_candidate(candidate: Candidate, manifest: FreezeManifest) -> str:
    """Freeze a candidate against a manifest. One-way door."""
    if candidate.is_frozen():
        raise FreezeError(f"{candidate.id} is already frozen; a freeze is a one-way door")
    if manifest.candidate_id != candidate.id:
        raise FreezeError(
            f"manifest is for {manifest.candidate_id}, candidate is {candidate.id}"
        )
    disagree = candidate_disagreements(candidate, manifest)
    if disagree:
        raise FreezeError(
            f"candidate and manifest disagree on {', '.join(disagree)}. Freezing would "
            "record a design the candidate does not describe; build the manifest with "
            "manifest_from_candidate()."
        )
    missing = [f for f in REQUIRED if not (getattr(manifest, f) or "").strip()]
    if missing:
        raise FreezeError(f"cannot freeze with empty {', '.join(missing)}")
    if not manifest.dataset_hashes:
        raise FreezeError(
            "cannot freeze without at least one dataset hash: a design frozen against "
            "unidentified data cannot be reproduced or audited"
        )

    fid = manifest.freeze_id
    candidate.status = "FROZEN"
    # The lock is the hash, not the status string. `status` stays for display;
    # `freeze_hash` is what design-mutating operations actually check, because
    # a mutable string on a mutable dataclass could be flipped back to ACTIVE
    # and rescoping resumed on a frozen design.
    candidate.freeze_id = fid
    candidate.freeze_hash = manifest.freeze_hash
    # Seal the constructs as well. The candidate's own scientific fields are
    # now refused by __setattr__; without this the objects hanging off it are
    # not, and swapping a construct's source is a design change either way.
    candidate.seal()
    candidate.log("freeze", freeze_id=fid, freeze_hash=manifest.freeze_hash)
    return fid


def require_freeze(manifest: FreezeManifest, claimed: str) -> None:
    """Gate every downstream artifact on the hash it claims to be produced under."""
    if claimed not in (manifest.freeze_hash, manifest.freeze_id):
        raise FreezeViolation(
            f"artifact claims freeze {claimed!r} but the design hashes to "
            f"{manifest.freeze_id!r}. Either the artifact predates a design change "
            "or the design moved after the lock; both require a POST_FREEZE_CHANGE record."
        )


@dataclass
class DatasetCheck:
    name: str
    expected: str
    status: str            # MATCH | DRIFTED | MISSING | UNREADABLE
    actual: str | None = None

    @property
    def ok(self) -> bool:
        return self.status == "MATCH"


def verify_datasets(manifest: FreezeManifest, data_dir: str) -> list[DatasetCheck]:
    """Re-hash every frozen dataset and report, per dataset.

    Separated from the CLI so the result can be asserted on directly. The CLI
    once computed exactly this, printed DRIFTED, then printed "freeze intact"
    and exited 0 -- the check ran and its answer was discarded, which is worse
    than not checking at all because it manufactures confidence.
    """
    checks: list[DatasetCheck] = []
    for name, expected in sorted(manifest.dataset_hashes.items()):
        path = os.path.join(data_dir, name)
        if not os.path.exists(path):
            checks.append(DatasetCheck(name, expected, "MISSING"))
            continue
        try:
            actual = hash_file(path)
        except OSError:
            checks.append(DatasetCheck(name, expected, "UNREADABLE"))
            continue
        checks.append(DatasetCheck(
            name, expected, "MATCH" if actual == expected else "DRIFTED", actual))
    return checks


def require_datasets(manifest: FreezeManifest, data_dir: str) -> None:
    """Hard gate. Any dataset not exactly as frozen forbids analysis."""
    bad = [c for c in verify_datasets(manifest, data_dir) if not c.ok]
    if bad:
        detail = "; ".join(f"{c.name}: {c.status}" for c in bad)
        raise FreezeViolation(
            f"frozen datasets are not as frozen ({detail}). The design was "
            "registered against specific bytes; analysing different ones produces "
            "a result no manifest describes."
        )
