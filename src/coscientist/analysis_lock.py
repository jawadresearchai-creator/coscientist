"""The analysis-code lock: pre-specification, not just reproducibility.

The design freeze locks the question, the estimand, the sample and the dataset
bytes. It does not lock the code. So this was possible, and nothing in the
system could see it:

    design freezes -> outcomes become visible -> edit 03_primary_models.R ->
    try another specification -> edit again -> keep the one that works ->
    collect() -> the FINAL script is hashed

Afterwards `ANALYSIS_MANIFEST.json` can say, truthfully, "this result was
produced by this script". What it cannot say is "this is the script that
existed before anyone looked at the outcomes". Those are different claims:
the first is reproducibility provenance, the second is pre-specification
provenance, and only the second supports a confirmatory result.

The lock closes that gap. It is created after the design freeze and before
outcome access, it hashes every script and every environment lock, and the
workflow verifies it immediately before execution. Editing a script after the
lock is then not a silent act -- it is a verification failure with a name.

It also records what the scripts are *able* to do. R cannot be sandboxed from
here: `read.csv`, `download.file` and `system()` all remain reachable, so
`cos_data()` is a sanctioned loader rather than a capability boundary. Instead
of pretending otherwise, `scan_io` finds every unsanctioned read and network
call and writes it into the lock, where it is hashed, visible, and has to be
acknowledged explicitly. Unsanctioned acquisition becomes a declared fact
rather than an invisible one.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass, field, asdict
from typing import Any, Iterable

from . import __version__
from .freeze import FreezeManifest
from .models import utcnow


class AnalysisLockViolation(RuntimeError):
    """The code that is about to run is not the code that was locked."""


# Engine files. They are part of the package, are hashed like everything else,
# and are exempt from the IO scan because being the sanctioned reader is their
# job -- cos_data() must call fread() or it cannot load anything at all.
ENGINE_FILES = ("R/emit.R", "R/00_setup.R", "R/dependencies.R")

ENV_FILES = ("renv.lock", "pyproject.toml", "requirements.txt", "conda.yaml")

# Deliberately literal rather than clever. A regex that tries to understand R
# will be wrong in both directions; a list of call names that read files or
# open sockets is checkable and its failure mode is a false positive somebody
# acknowledges once.
IO_CALLS = {
    ".R": [
        (r"\bread\.(csv|table|delim|fwf)\s*\(", "direct file read outside cos_data()"),
        (r"\bread(RDS|_csv|_tsv|_dta|_sav|_parquet|_excel|xl)\s*\(", "direct file read outside cos_data()"),
        (r"\bfread\s*\(", "direct file read outside cos_data()"),
        (r"\bload\s*\(", "loads an .RData image, which can contain anything"),
        (r"\b(download\.file|url|curl_download|GET|POST|request)\s*\(", "network access"),
        (r"\b(system|system2|shell|pipe)\s*\(", "shell execution"),
        (r"\bSys\.setenv\s*\(", "mutates the environment the runner set up"),
        (r"\bset\.seed\s*\(", "reseeds outside cos_seed(), detaching randomness from the design"),
    ],
    ".py": [
        (r"\bopen\s*\(", "direct file read outside the sanctioned loader"),
        (r"\bpd\.read_\w+\s*\(", "direct file read outside the sanctioned loader"),
        (r"\b(requests|urllib|httpx|aiohttp)\b", "network access"),
        (r"\b(subprocess|os\.system|os\.popen)\b", "shell execution"),
        (r"\b(random\.seed|np\.random\.seed)\s*\(", "reseeds outside the design-derived seed"),
    ],
}


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        while chunk := fh.read(1 << 20):
            h.update(chunk)
    return h.hexdigest()


@dataclass
class IOFinding:
    script: str
    line: int
    call: str
    why: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def scan_io(paths: Iterable[str], root: str = ".") -> list[IOFinding]:
    """Find every unsanctioned read, fetch or reseed in the scripts to be locked."""
    out: list[IOFinding] = []
    for rel in sorted(paths):
        if rel in ENGINE_FILES:
            continue
        ext = os.path.splitext(rel)[1]
        patterns = IO_CALLS.get(ext)
        if not patterns:
            continue
        full = os.path.join(root, rel)
        if not os.path.exists(full):
            continue
        with open(full, "r", encoding="utf-8", errors="replace") as fh:
            for lineno, line in enumerate(fh, start=1):
                stripped = line.strip()
                if stripped.startswith("#"):
                    continue
                for pattern, why in patterns:
                    m = re.search(pattern, line)
                    if m:
                        out.append(IOFinding(rel, lineno, m.group(0).strip(), why))
    return out


@dataclass
class AnalysisLock:
    """What will run, hashed, before any outcome is visible."""

    freeze_id: str
    freeze_hash: str
    script_hashes: dict[str, str] = field(default_factory=dict)
    env_hashes: dict[str, str] = field(default_factory=dict)
    execution_order: list[str] = field(default_factory=list)
    io_findings: list[dict[str, Any]] = field(default_factory=list)
    io_acknowledged: bool = False
    locked_at: str = field(default_factory=utcnow)
    engine_version: str = __version__

    @property
    def lock_hash(self) -> str:
        payload = {
            "freeze_hash": self.freeze_hash,
            "script_hashes": self.script_hashes,
            "env_hashes": self.env_hashes,
            "execution_order": self.execution_order,
            "io_findings": self.io_findings,
            "io_acknowledged": self.io_acknowledged,
        }
        blob = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()

    @property
    def lock_id(self) -> str:
        return f"AL-{self.freeze_id}-{self.lock_hash[:12]}"

    @property
    def receipt_hash(self) -> str:
        """Tamper-evident provenance receipt for the lock event itself.

        `lock_hash` intentionally identifies the scientific/code plan.  The
        chronology claim also depends on when and under which engine version
        that plan was locked.  Keeping a second receipt preserves both
        identities without making the stable plan hash depend on a timestamp.
        """
        payload = {
            "lock_hash": self.lock_hash,
            "freeze_hash": self.freeze_hash,
            "locked_at": self.locked_at,
            "engine_version": self.engine_version,
        }
        blob = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()

    @property
    def receipt_id(self) -> str:
        return f"AR-{self.lock_id}-{self.receipt_hash[:12]}"

    # ---------- creation ----------

    @classmethod
    def create(cls, freeze: FreezeManifest, scripts: list[str], *,
               root: str = ".", acknowledge_io: bool = False) -> "AnalysisLock":
        """Lock the code. Call this after the design freeze, before outcomes."""
        if not scripts:
            raise AnalysisLockViolation(
                "no analysis scripts to lock. A lock over nothing would verify "
                "cleanly against any code at all."
            )
        missing = [s for s in scripts if not os.path.exists(os.path.join(root, s))]
        if missing:
            raise AnalysisLockViolation(
                f"cannot lock scripts that do not exist: {', '.join(missing)}"
            )

        findings = scan_io(scripts, root)
        if findings and not acknowledge_io:
            lines = "\n".join(
                f"  {f.script}:{f.line}  {f.call}  -- {f.why}" for f in findings[:20])
            raise AnalysisLockViolation(
                f"{len(findings)} unsanctioned IO or seeding call(s) in the scripts "
                f"being locked:\n{lines}\n\n"
                "R cannot be sandboxed from here, so these are recorded rather than "
                "blocked -- but they are recorded, hashed and visible, which is the "
                "point. Route data through cos_data() and randomness through "
                "cos_seed(), or lock with acknowledge_io=True to declare them."
            )

        env = {}
        for name in ENV_FILES:
            path = os.path.join(root, name)
            if os.path.exists(path):
                env[name] = sha256_file(path)
        if not env:
            raise AnalysisLockViolation(
                "no environment lock found (renv.lock, pyproject.toml, ...). Locking "
                "the code without locking what it runs on records half a claim."
            )

        plan = execution_plan(root)
        if not plan:
            raise AnalysisLockViolation(
                "no executable study stages in the analysis plan. Engine helpers and "
                "Stan model files may be hashed, but at least one numbered R or "
                "Python driver must actually execute."
            )

        return cls(
            freeze_id=freeze.freeze_id,
            freeze_hash=freeze.freeze_hash,
            script_hashes={s: sha256_file(os.path.join(root, s)) for s in sorted(scripts)},
            env_hashes=env,
            execution_order=plan,
            io_findings=[f.to_dict() for f in findings],
            io_acknowledged=bool(findings) and acknowledge_io,
        )

    # ---------- persistence ----------

    def save(self, path: str) -> str:
        """Write-once, exactly like the freeze it binds to.

        A lock that can be rewritten after outcomes are visible is not a lock,
        it is a note. There is deliberately no `allow_replace`.
        """
        if os.path.exists(path):
            existing = AnalysisLock.load(path)
            if existing.lock_hash != self.lock_hash:
                raise AnalysisLockViolation(
                    f"an analysis lock already exists at {path} ({existing.lock_id}) "
                    f"and differs from this one ({self.lock_id}). Re-locking after "
                    "the outcomes are visible is the specification search this "
                    "mechanism exists to prevent. A legitimate change is a "
                    "POST_FREEZE_CHANGE on a new lineage, reclassified as exploratory."
                )
            return path
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        payload = asdict(self)
        payload["lock_hash"] = self.lock_hash
        payload["lock_id"] = self.lock_id
        payload["receipt_hash"] = self.receipt_hash
        payload["receipt_id"] = self.receipt_id
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2, sort_keys=True)
        os.replace(tmp, path)
        return path

    @classmethod
    def load(cls, path: str) -> "AnalysisLock":
        with open(path, "r", encoding="utf-8") as fh:
            raw = json.load(fh)
        stored = raw.pop("lock_hash", None)
        raw.pop("lock_id", None)
        stored_receipt = raw.pop("receipt_hash", None)
        raw.pop("receipt_id", None)
        # Fail closed. An absent checksum is the most alarming possible state,
        # not an instruction to skip checking -- the same lesson the freeze and
        # the results manifest each had to learn separately.
        if not stored:
            raise AnalysisLockViolation(
                f"{path} carries no lock hash. Deleting the checksum does not "
                "disable verification; it invalidates the lock."
            )
        if not stored_receipt:
            raise AnalysisLockViolation(
                f"{path} carries no receipt hash. The lock plan and the fact of "
                "when/version it was locked are both part of the pre-specification "
                "record; deleting the chronology checksum invalidates it."
            )
        lock = cls(**raw)
        if lock.lock_hash != stored:
            raise AnalysisLockViolation(
                f"{path} has been altered since it was written: stored "
                f"{stored[:12]}, recomputed {lock.lock_hash[:12]}."
            )
        if lock.receipt_hash != stored_receipt:
            raise AnalysisLockViolation(
                f"{path} lock receipt has been altered: stored "
                f"{stored_receipt[:12]}, recomputed {lock.receipt_hash[:12]}. "
                "Changing locked_at or engine_version changes the chronology claim."
            )
        return lock

    # ---------- verification ----------

    def verify(self, root: str = ".", *, freeze: FreezeManifest | None = None
               ) -> list[str]:
        """Re-hash everything and return the discrepancies, in both directions.

        Both directions matters. Checking only that locked scripts are unchanged
        would let a new `R/07_extra_models.R` appear after the lock and run
        alongside them, which is exactly the search this prevents.
        """
        problems: list[str] = []

        if freeze is not None and freeze.freeze_hash != self.freeze_hash:
            problems.append(
                f"this lock was made for freeze {self.freeze_hash[:12]}, but the "
                f"design on disk is {freeze.freeze_hash[:12]}"
            )

        for rel, expected in sorted(self.script_hashes.items()):
            full = os.path.join(root, rel)
            if not os.path.exists(full):
                problems.append(f"{rel}: locked script is missing")
                continue
            got = sha256_file(full)
            if got != expected:
                problems.append(
                    f"{rel}: edited since the lock (locked {expected[:12]}, "
                    f"now {got[:12]})")

        present = set(discover_scripts(root))
        extra = sorted(present - set(self.script_hashes))
        if extra:
            problems.append(
                "analysis scripts present that were not locked: "
                + ", ".join(extra)
                + " -- a script added after the lock runs alongside the locked "
                  "ones and is exactly the specification search this prevents")

        current_plan = execution_plan(root)
        if current_plan != self.execution_order:
            problems.append(
                "execution plan changed since the lock (locked "
                + " -> ".join(self.execution_order)
                + "; now " + " -> ".join(current_plan) + ")"
            )

        for name, expected in sorted(self.env_hashes.items()):
            full = os.path.join(root, name)
            if not os.path.exists(full):
                problems.append(f"{name}: locked environment file is missing")
            elif sha256_file(full) != expected:
                problems.append(f"{name}: environment changed since the lock")

        return problems

    def require(self, root: str = ".", *, freeze: FreezeManifest | None = None) -> None:
        problems = self.verify(root, freeze=freeze)
        if problems:
            raise AnalysisLockViolation(
                "the code about to run is not the code that was locked:\n  - "
                + "\n  - ".join(problems)
                + "\n\nThe design freeze says which question; this lock says which "
                  "analysis. Running edited code against frozen data produces a "
                  "result that is reproducible but no longer pre-specified."
            )


ANALYSIS_GLOBS = ("R/**/*.R", "python/**/*.py", "stan/**/*.stan")


def discover_scripts(root: str = ".") -> list[str]:
    """Every file that can produce a number, as repo-relative paths."""
    import glob as _glob
    out: list[str] = []
    for pattern in ANALYSIS_GLOBS:
        for path in _glob.glob(os.path.join(root, pattern), recursive=True):
            out.append(os.path.relpath(path, root))
    return sorted(out)


def execution_plan(root: str = ".") -> list[str]:
    """The numbered study drivers, in global numeric stage order.

    R and Python are executable drivers. Stan files are models consumed by a
    locked driver and are hashed by `discover_scripts`, but are never placed in
    the direct execution plan.  Sorting by full path once put R/09 before
    python/03; the numeric stage prefix is the cross-language order.
    """
    planned: list[str] = []
    for rel in discover_scripts(root):
        ext = os.path.splitext(rel)[1].lower()
        if ext not in (".r", ".py"):
            continue
        if rel in ENGINE_FILES:
            continue
        if not re.match(r"^\d\d_", os.path.basename(rel)):
            continue
        planned.append(rel)

    def key(rel: str):
        base = os.path.basename(rel)
        return (int(base[:2]), rel.lower())

    return sorted(planned, key=key)
