"""The manuscript bridge: what S5 hands to S6.

An analysis run should not hand the writer a 50 MB model object and hope. It
emits a small set of machine-readable files in which every reportable number
exists exactly once, carries its own uncertainty, and names the model and
script that produced it.

Each record has a stable token. The manuscript cites `[[R:h1_did_coef]]`
rather than retyping 2.41, and the provenance validator (see provenance.py)
later checks that every number in the prose traces back to one of these rows.

Everything written here carries the freeze hash it was produced under, so a
figure can always answer which design and which dataset made it.
"""
from __future__ import annotations

import csv
import hashlib
import json
import os
import subprocess
from dataclasses import dataclass, field, asdict
from typing import Any, Iterable

from .figures import FigureRecord
from .figures import write_manifest as write_figure_manifest
from .freeze import FreezeManifest
from .models import utcnow


class BridgeError(RuntimeError):
    pass


@dataclass
class ResultRecord:
    """One reportable number, with its uncertainty and its provenance."""

    token: str                     # stable id cited from the manuscript
    label: str                     # human description
    value: float
    units: str = ""
    se: float | None = None
    ci_low: float | None = None
    ci_high: float | None = None
    p_value: float | None = None
    n_obs: int | None = None
    n_clusters: int | None = None
    model: str = ""                # e.g. "LPM, owner x quarter x stage FE"
    script: str = ""               # e.g. "R/03_primary_models.R"
    family: str = "primary"        # primary | secondary | robustness | diagnostic
    adjusted: str = ""             # multiplicity adjustment applied, if any

    FIELDS = ("estimate", "value", "se", "ci", "ci_low", "ci_high", "p", "n", "clusters")

    def field_value(self, field_name: str):
        """Resolve one named field for `[[R:token|field]]`.

        Field tokens exist because proximity does not encode association. A
        p-value written beside one estimate was bound by nearness, and in
        "2.41 pp and -0.312 pp (p < 0.001)" nearness picks the wrong record.
        Three attempts at tuning that heuristic were three too many: the
        association has to be written down, not inferred.
        """
        name = field_name.lower()
        if name in ("estimate", "value"):
            return self.value
        if name == "se":
            return self.se
        if name == "ci":
            return (self.ci_low, self.ci_high)
        if name == "ci_low":
            return self.ci_low
        if name == "ci_high":
            return self.ci_high
        if name == "p":
            return self.p_value
        if name == "n":
            return self.n_obs
        if name == "clusters":
            return self.n_clusters
        raise BridgeError(
            f"unknown result field {field_name!r}; expected one of {', '.join(self.FIELDS)}"
        )

    def render(self, field_name: str, decimals: int = 2) -> str:
        v = self.field_value(field_name)
        if v is None:
            raise BridgeError(
                f"{self.token} has no {field_name}; it cannot be cited"
            )
        name = field_name.lower()
        if name == "ci":
            return f"{v[0]:.{decimals}f} to {v[1]:.{decimals}f}"
        if name == "p":
            return "< 0.001" if v < 0.001 else f"{v:.3f}"
        if name in ("n", "clusters"):
            return f"{int(v):,}"
        suffix = f" {self.units}" if self.units and name in ("estimate", "value") else ""
        return f"{v:.{decimals}f}{suffix}"

    def numbers(self) -> list[float]:
        """Every value a manuscript could legitimately quote from this record."""
        out = [self.value]
        for v in (self.se, self.ci_low, self.ci_high, self.p_value):
            if v is not None:
                out.append(float(v))
        for v in (self.n_obs, self.n_clusters):
            if v is not None:
                out.append(float(v))
        return out

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ResultsBundle:
    """Everything S6 is allowed to write from."""

    freeze_id: str
    freeze_hash: str
    records: list[ResultRecord] = field(default_factory=list)
    figures: list[dict[str, Any]] = field(default_factory=list)
    tables: list[dict[str, Any]] = field(default_factory=list)
    generated_at: str = field(default_factory=utcnow)
    # What actually produced these numbers. Without these the bundle was a
    # trusted intermediate: a ResultRecord.value could be edited while the
    # freeze_hash stayed valid, and provenance would cheerfully validate prose
    # against the altered result. The chain has to run design -> data -> code
    # -> results, not stop at the data.
    git_commit: str = ""
    script_hashes: dict[str, str] = field(default_factory=dict)
    env_hash: str = ""
    # Which pre-registered analysis plan produced these numbers. Script hashes
    # alone say "this code made this result"; the lock id says "and that code
    # was registered before anyone saw an outcome".
    analysis_lock_id: str = ""
    analysis_lock_hash: str = ""

    @property
    def results_hash(self) -> str:
        """Content hash over every record, order-independent."""
        payload = sorted(
            json.dumps(r.to_dict(), sort_keys=True, separators=(",", ":"))
            for r in self.records
        )
        return hashlib.sha256("\n".join(payload).encode("utf-8")).hexdigest()

    def payload(self) -> dict[str, Any]:
        """Exactly what gets written, so the hash covers exactly what is read."""
        return {
            "freeze_id": self.freeze_id,
            "freeze_hash": self.freeze_hash,
            "results_hash": self.results_hash,
            "git_commit": self.git_commit or current_commit(),
            "script_hashes": self.script_hashes,
            "env_hash": self.env_hash,
            "analysis_lock_id": self.analysis_lock_id,
            "analysis_lock_hash": self.analysis_lock_hash,
            "generated_at": self.generated_at,
            "n_records": len(self.records),
            "records": [r.to_dict() for r in self.records],
            "figures": self.figures,
            "tables": self.tables,
        }

    def add(self, record: ResultRecord) -> ResultRecord:
        if any(r.token == record.token for r in self.records):
            raise BridgeError(
                f"duplicate token {record.token!r}: a number must exist exactly once "
                "in the bundle, or two places in the manuscript can drift apart"
            )
        self.records.append(record)
        return record

    def get(self, token: str) -> ResultRecord:
        for r in self.records:
            if r.token == token:
                return r
        raise BridgeError(f"no result with token {token!r}")

    def all_numbers(self) -> list[tuple[float, str, str]]:
        """(value, token, units) for every quotable number in the bundle.

        Units travel with the value because the validator needs them: without
        them it once matched 241 USD against a claim of 2.41 pp by dividing by
        a hundred.
        """
        pairs: list[tuple[float, str, str]] = []
        for r in self.records:
            for v in r.numbers():
                pairs.append((v, r.token, r.units))
        return pairs

    # ---------- writers ----------

    def write(self, outdir: str) -> list[str]:
        """Emit the bridge. Returns the paths written."""
        os.makedirs(outdir, exist_ok=True)
        written = [
            self._write_results_csv(os.path.join(outdir, "PRIMARY_RESULTS.csv")),
            self._write_effects_csv(os.path.join(outdir, "EFFECT_SIZES.csv")),
            self._write_summary(os.path.join(outdir, "RESULTS_SUMMARY.md")),
            self._write_manifest(os.path.join(outdir, "ANALYSIS_MANIFEST.json")),
        ]
        if self.figures:
            written.append(self._write_figure_manifest(os.path.join(outdir, "FIGURE_MANIFEST.md")))
        return written

    def _rows(self, families: Iterable[str]) -> list[ResultRecord]:
        want = set(families)
        return [r for r in self.records if r.family in want]

    def _write_csv(self, path: str, rows: list[ResultRecord]) -> str:
        cols = ["token", "label", "value", "units", "se", "ci_low", "ci_high",
                "p_value", "n_obs", "n_clusters", "model", "script", "family", "adjusted"]
        with open(path, "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=cols)
            w.writeheader()
            for r in rows:
                w.writerow({k: getattr(r, k) for k in cols})
        return path

    def _write_results_csv(self, path: str) -> str:
        return self._write_csv(path, self._rows(("primary", "secondary")))

    def _write_effects_csv(self, path: str) -> str:
        return self._write_csv(path, self._rows(("primary", "secondary", "robustness")))

    def _write_summary(self, path: str) -> str:
        lines = [
            "# Results summary",
            "",
            f"Freeze: `{self.freeze_id}`  ",
            f"Hash: `{self.freeze_hash}`  ",
            f"Generated: {self.generated_at}",
            "",
            "Every number below carries a token. Cite the token in the manuscript, "
            "not the digits; the provenance validator checks the rest.",
            "",
        ]
        for family in ("primary", "secondary", "robustness", "diagnostic"):
            rows = self._rows((family,))
            if not rows:
                continue
            lines += [f"## {family.title()}", ""]
            for r in rows:
                lines.append(f"### `{r.token}` — {r.label}")
                val = f"{r.value:g}" + (f" {r.units}" if r.units else "")
                lines.append(f"- Estimate: **{val}**")
                if r.ci_low is not None and r.ci_high is not None:
                    lines.append(f"- 95% CI: {r.ci_low:g} to {r.ci_high:g}")
                if r.se is not None:
                    lines.append(f"- SE: {r.se:g}")
                if r.p_value is not None:
                    lines.append(f"- p: {r.p_value:g}" + (f" ({r.adjusted})" if r.adjusted else ""))
                if r.n_obs is not None:
                    clusters = f", {r.n_clusters} clusters" if r.n_clusters else ""
                    lines.append(f"- N: {r.n_obs}{clusters}")
                if r.model:
                    lines.append(f"- Model: {r.model}")
                if r.script:
                    lines.append(f"- Produced by: `{r.script}`")
                lines.append("")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("\n".join(lines))
        return path

    def _write_manifest(self, path: str) -> str:
        payload = self.payload()
        # `results_hash` covers the records. It does not cover the figures, the
        # script hashes or the commit -- so a figure entry could be repointed at
        # a different picture, or the recorded commit swapped, and the manifest
        # would still verify. The digest has to cover everything the manifest
        # asserts, not just the part that happens to be numeric.
        payload["bundle_hash"] = payload_hash(payload)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2, sort_keys=True)
        return path

    def _write_figure_manifest(self, path: str) -> str:
        """Delegate to the figure writer, which knows what a figure has to carry."""
        records = []
        for f in self.figures:
            records.append(FigureRecord(
                id=str(f.get("id", "figure")), path=str(f.get("path", "")),
                caption=str(f.get("caption") or f.get("question") or ""),
                script=str(f.get("script", "")),
                result_tokens=list(f.get("result_tokens")
                                   or ([f["result_token"]] if f.get("result_token") else [])),
                dpi=int(f.get("dpi") or 0),
                width_in=f.get("width_in"), height_in=f.get("height_in"),
                panels=int(f.get("panels") or 1),
                sha256=str(f.get("sha256", "")), bytes=int(f.get("bytes") or 0),
            ))
        return write_figure_manifest(path, records, self.freeze_id, self.results_hash)

    @classmethod
    def from_manifest(cls, path: str) -> "ResultsBundle":
        with open(path, "r", encoding="utf-8") as fh:
            raw = json.load(fh)
        bundle = cls(freeze_id=raw["freeze_id"], freeze_hash=raw["freeze_hash"],
                     generated_at=raw.get("generated_at", utcnow()),
                     figures=raw.get("figures", []), tables=raw.get("tables", []),
                     git_commit=raw.get("git_commit", ""),
                     script_hashes=raw.get("script_hashes", {}),
                     env_hash=raw.get("env_hash", ""),
                     analysis_lock_id=raw.get("analysis_lock_id", ""),
                     analysis_lock_hash=raw.get("analysis_lock_hash", ""))
        for r in raw.get("records", []):
            # add(), not append(). Loading bypassed the uniqueness check, so a
            # manifest carrying the same token twice loaded happily and get()
            # silently returned whichever came first -- the "exists exactly
            # once" invariant held only for records added in memory.
            bundle.add(ResultRecord(**r))

        # Fail closed on BOTH digests. `if stored and ...` made the check
        # conditional on the very field an attacker -- or a well-meaning script
        # normalising some JSON -- would remove: delete `results_hash` and
        # `bundle_hash` and the manifest loaded happily with a rewritten
        # estimate. This is the identical mistake already fixed once in
        # FreezeManifest.load; fixing the instance did not stop the class, which
        # is the whole reason GUARANTEES.yaml exists.
        stored = raw.get("results_hash")
        if not stored:
            raise BridgeError(
                f"{path} carries no results hash. Deleting the checksum does not "
                "disable verification; it invalidates the manifest."
            )
        if stored != bundle.results_hash:
            raise BridgeError(
                f"results manifest has been altered since it was written: stored "
                f"{stored[:12]}, recomputed {bundle.results_hash[:12]}. A number was "
                "changed after the analysis produced it."
            )

        stored_bundle = raw.get("bundle_hash")
        if not stored_bundle:
            raise BridgeError(
                f"{path} carries no bundle hash. Every manifest this engine writes "
                "has one; a manifest without it was either not written by the "
                "engine or has had its integrity fields stripped."
            )
        recomputed = payload_hash({k: v for k, v in raw.items() if k != "bundle_hash"})
        if recomputed != stored_bundle:
            raise BridgeError(
                f"analysis manifest has been altered since it was written: stored "
                f"{stored_bundle[:12]}, recomputed {recomputed[:12]}. Something "
                "outside the result values changed -- a figure binding, a script "
                "hash, or the commit the analysis claims to have run at."
            )
        return bundle


def payload_hash(payload: dict[str, Any]) -> str:
    """Content address of a whole manifest payload, order-independent."""
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def current_commit() -> str:
    """The commit the analysis ran at, if we are inside a checkout."""
    try:
        out = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True,
                             text=True, timeout=5)
        return out.stdout.strip() if out.returncode == 0 else ""
    except Exception:
        return ""


def hash_scripts(paths: list[str]) -> dict[str, str]:
    """SHA-256 of every analysis script, so the code is in the provenance chain."""
    out: dict[str, str] = {}
    for p in sorted(paths):
        if os.path.exists(p):
            with open(p, "rb") as fh:
                out[p] = hashlib.sha256(fh.read()).hexdigest()
    return out


def new_bundle(freeze: FreezeManifest, **provenance: Any) -> ResultsBundle:
    return ResultsBundle(freeze_id=freeze.freeze_id, freeze_hash=freeze.freeze_hash,
                         **provenance)
