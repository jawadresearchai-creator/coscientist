"""The R/Python joint: how an analysis script becomes a ResultRecord.

The results bridge is Python; the econometrics are R. Something has to carry a
coefficient from `fixest::feols` into a `ResultRecord` without a human retyping
it, because retyping is precisely the error class the bridge exists to remove.

The contract is newline-delimited JSON. Each analysis script appends one object
per reportable number to its own stream file, then writes a terminal sentinel.
Python reads the streams, validates them, and assembles the bundle.

Three properties make this a contract rather than a convention:

1. TRUNCATION IS DETECTED. A script that dies at line 40 of 90 leaves a
   perfectly well-formed JSONL file containing half an analysis. Without a
   sentinel that file assembles into a bundle, the manuscript is written from
   it, and nothing anywhere says the models never finished. Every stream must
   end with `{"__complete__": true}` or the whole collection is refused.

2. INCOHERENT STATISTICS ARE REFUSED. A confidence interval that does not
   bracket its own estimate, a negative standard error, a p-value above one:
   each means the emitting script passed its columns in the wrong order. That
   bug produces a table that looks entirely publishable, so it has to be caught
   mechanically rather than by reading.

3. NON-FINITE VALUES ARE REFUSED. A model that fails to converge returns NaN,
   `jsonlite` renders it, `float()` accepts it, and the validator then compares
   NaN against prose -- where every comparison is false and every check passes.
   A number that is not a number is not a result.
"""
from __future__ import annotations

import json
import math
import os
import re
from dataclasses import dataclass
from typing import Any, Iterable

from .figures import FigureRecord, bind as bind_figures
from .manuscript import BridgeError, ResultRecord, ResultsBundle

SENTINEL = "__complete__"
STREAM_SUFFIX = ".results.jsonl"
FIGURE_SUFFIX = ".figures.jsonl"

FAMILIES = ("primary", "secondary", "robustness", "diagnostic")

# broom::tidy() names, because that is what `tidy(feols(...))` actually
# produces. Requiring a rename step in every R script would put a manual
# transcription back into the one path built to remove it.
ALIASES = {
    "estimate": "value",
    "std.error": "se",
    "std_error": "se",
    "stderr": "se",
    "conf.low": "ci_low",
    "conf_low": "ci_low",
    "conf.high": "ci_high",
    "conf_high": "ci_high",
    "p.value": "p_value",
    "p": "p_value",
    "nobs": "n_obs",
    "n": "n_obs",
    "clusters": "n_clusters",
}

REQUIRED = ("token", "label", "value", "script")


def stream_stem(script: str) -> str:
    """The stream filename `emitter()` derives from a script path.

    Kept in one place because three things have to agree: the file the runner
    executed, the sentinel it sealed with, and the script each record names.
    """
    return re.sub(r"[^A-Za-z0-9._-]+", "_", os.path.basename(script))


class EmitError(BridgeError):
    """A stream violates the analysis-script contract."""


def _normalise(raw: dict[str, Any], origin: str) -> dict[str, Any]:
    """Apply aliases, refusing any collision rather than picking a winner."""
    out: dict[str, Any] = {}
    provenance: dict[str, str] = {}
    for key, value in raw.items():
        canonical = ALIASES.get(key, key)
        if canonical in out and out[canonical] != value:
            raise EmitError(
                f"{origin}: {provenance[canonical]!r} and {key!r} both set "
                f"{canonical!r} to different values ({out[canonical]!r} vs {value!r}). "
                "Choose one; guessing which the author meant is how a table ends up "
                "reporting a number the model never produced."
            )
        out[canonical] = value
        provenance[canonical] = key
    return out


def _finite(name: str, value: Any, origin: str) -> float:
    try:
        f = float(value)
    except (TypeError, ValueError) as exc:
        raise EmitError(f"{origin}: {name}={value!r} is not numeric") from exc
    if not math.isfinite(f):
        raise EmitError(
            f"{origin}: {name} is {value!r}. A non-finite value means the model did "
            "not converge or the estimate is undefined -- it is not a result, and "
            "silently carrying it forward makes every downstream check pass "
            "vacuously."
        )
    return f


def _check_coherent(rec: ResultRecord, origin: str) -> None:
    """Statistics that cannot be true of any real estimate.

    Each of these is the signature of arguments passed in the wrong order --
    a mistake whose output is a table that reads perfectly well.
    """
    if rec.se is not None and rec.se < 0:
        raise EmitError(f"{origin}: {rec.token} has a negative standard error ({rec.se})")
    if rec.p_value is not None and not (0.0 <= rec.p_value <= 1.0):
        raise EmitError(f"{origin}: {rec.token} has p={rec.p_value}, outside [0, 1]")
    lo, hi = rec.ci_low, rec.ci_high
    if (lo is None) != (hi is None):
        raise EmitError(
            f"{origin}: {rec.token} has one confidence bound and not the other; "
            "half an interval cannot be reported"
        )
    if lo is not None and hi is not None:
        if lo > hi:
            raise EmitError(
                f"{origin}: {rec.token} has ci_low {lo} above ci_high {hi} -- the "
                "bounds are swapped"
            )
        if not (lo <= rec.value <= hi):
            raise EmitError(
                f"{origin}: {rec.token} estimate {rec.value} lies outside its own "
                f"interval [{lo}, {hi}]. Either the interval belongs to a different "
                "coefficient or the columns were misaligned."
            )
    if rec.n_obs is not None and rec.n_obs <= 0:
        raise EmitError(f"{origin}: {rec.token} reports n_obs={rec.n_obs}")
    if rec.n_clusters is not None and rec.n_obs is not None and rec.n_clusters > rec.n_obs:
        raise EmitError(
            f"{origin}: {rec.token} has more clusters ({rec.n_clusters}) than "
            f"observations ({rec.n_obs})"
        )
    if rec.family not in FAMILIES:
        raise EmitError(
            f"{origin}: {rec.token} declares family {rec.family!r}; expected one of "
            f"{', '.join(FAMILIES)}. Family decides which table a number reaches, so "
            "an unrecognised one would silently drop it from the manuscript."
        )


def record_from_row(raw: dict[str, Any], origin: str = "<stream>") -> ResultRecord:
    """One JSON object from an analysis script becomes one validated record."""
    row = _normalise(raw, origin)
    missing = [k for k in REQUIRED if row.get(k) in (None, "")]
    if missing:
        raise EmitError(
            f"{origin}: record missing {', '.join(missing)}. Every number carries its "
            "own token and names the script that produced it; an anonymous number "
            "cannot be cited or reproduced."
        )

    rec = ResultRecord(
        token=str(row["token"]),
        label=str(row["label"]),
        value=_finite("value", row["value"], origin),
        units=str(row.get("units", "") or ""),
        se=None if row.get("se") is None else _finite("se", row["se"], origin),
        ci_low=None if row.get("ci_low") is None else _finite("ci_low", row["ci_low"], origin),
        ci_high=None if row.get("ci_high") is None else _finite("ci_high", row["ci_high"], origin),
        p_value=None if row.get("p_value") is None else _finite("p_value", row["p_value"], origin),
        n_obs=None if row.get("n_obs") is None else int(_finite("n_obs", row["n_obs"], origin)),
        n_clusters=(None if row.get("n_clusters") is None
                    else int(_finite("n_clusters", row["n_clusters"], origin))),
        model=str(row.get("model", "") or ""),
        script=str(row["script"]),
        family=str(row.get("family", "primary") or "primary"),
        adjusted=str(row.get("adjusted", "") or ""),
    )
    _check_coherent(rec, origin)
    return rec


@dataclass
class Stream:
    """One analysis script's output, and whether it finished."""

    path: str
    script: str
    records: list[ResultRecord]
    complete: bool


def read_stream(path: str) -> Stream:
    """Parse one `.results.jsonl` file and require all producer witnesses to agree."""
    records: list[ResultRecord] = []
    complete = False
    sentinel_script = ""
    record_scripts: set[str] = set()
    with open(path, "r", encoding="utf-8") as fh:
        for lineno, line in enumerate(fh, start=1):
            line = line.strip()
            if not line:
                continue
            origin = f"{os.path.basename(path)}:{lineno}"
            try:
                raw = json.loads(line)
            except json.JSONDecodeError as exc:
                raise EmitError(
                    f"{origin}: not valid JSON ({exc}). A stream is written one "
                    "complete object per line; a partial line means the script was "
                    "killed mid-write."
                ) from exc
            if not isinstance(raw, dict):
                raise EmitError(f"{origin}: expected a JSON object, got {type(raw).__name__}")
            if raw.get(SENTINEL):
                if complete:
                    raise EmitError(f"{origin}: stream declares completion twice")
                complete = True
                sentinel_script = str(raw.get("script", "") or "")
                continue
            if complete:
                raise EmitError(
                    f"{origin}: a record appears after the completion sentinel. The "
                    "stream was appended to by a second run; results from two runs "
                    "cannot be told apart."
                )
            rec = record_from_row(raw, origin)
            record_scripts.add(rec.script)
            records.append(rec)

    stem = os.path.basename(path)[: -len(STREAM_SUFFIX)]
    if len(record_scripts) > 1:
        raise EmitError(
            f"{os.path.basename(path)}: records disagree about their producer: "
            + ", ".join(sorted(record_scripts))
        )
    record_script = next(iter(record_scripts), "")
    witnesses = [x for x in (record_script, sentinel_script) if x]
    for claimed in witnesses:
        if stream_stem(claimed) != stem:
            raise EmitError(
                f"{os.path.basename(path)}: filename identifies stream {stem!r}, "
                f"but a producer witness names {claimed!r}. Filename, every record, "
                "and the completion sentinel must identify the same locked script."
            )
    if record_script and sentinel_script and record_script != sentinel_script:
        raise EmitError(
            f"{os.path.basename(path)}: records name {record_script!r} but the "
            f"completion sentinel names {sentinel_script!r}. A sealed stream needs "
            "three agreeing witnesses, not a majority vote."
        )
    script = record_script or sentinel_script
    return Stream(path=path, script=script, records=records, complete=complete)


def read_figure_stream(path: str, sealed_stems: set[str]) -> list[FigureRecord]:
    """Parse one `.figures.jsonl`, refusing figures from a script that never sealed.

    A figures stream has no sentinel of its own; its guarantee comes from the
    results stream written by the same `emitter()`. A figure file with no sealed
    sibling is a picture from a script that may have died before drawing the
    rest of them.
    """
    stem = os.path.basename(path)[: -len(FIGURE_SUFFIX)]
    if stem not in sealed_stems:
        raise EmitError(
            f"{os.path.basename(path)}: no sealed results stream from the same "
            "script. Figures are guaranteed by the script that emitted them "
            "having finished; this one has no such guarantee."
        )
    out: list[FigureRecord] = []
    with open(path, "r", encoding="utf-8") as fh:
        for lineno, line in enumerate(fh, start=1):
            line = line.strip()
            if not line:
                continue
            origin = f"{os.path.basename(path)}:{lineno}"
            try:
                raw = json.loads(line)
            except json.JSONDecodeError as exc:
                raise EmitError(f"{origin}: not valid JSON ({exc})") from exc
            missing = [k for k in ("id", "path", "caption") if not raw.get(k)]
            if missing:
                raise EmitError(f"{origin}: figure missing {', '.join(missing)}")
            tokens = raw.get("result_tokens") or []
            if isinstance(tokens, str):
                tokens = [tokens]
            out.append(FigureRecord(
                id=str(raw["id"]), path=str(raw["path"]), caption=str(raw["caption"]),
                script=str(raw.get("script", "")), result_tokens=[str(t) for t in tokens],
                dpi=int(raw.get("dpi") or 0),
                width_in=raw.get("width_in"), height_in=raw.get("height_in"),
                panels=int(raw.get("panels") or 1),
            ))
    return out


def collect(
    results_dir: str,
    bundle: ResultsBundle,
    *,
    require_complete: bool = True,
    known_scripts: Iterable[str] | None = None,
    figure_base_dir: str = ".",
) -> ResultsBundle:
    """Assemble every analysis stream into the bundle, or refuse to.

    `require_complete` has no off switch in the workflow. It is a parameter only
    so a test can exercise the truncation path without writing a sentinel.
    """
    if not os.path.isdir(results_dir):
        raise EmitError(f"no results directory at {results_dir}")

    paths = sorted(
        os.path.join(results_dir, n) for n in os.listdir(results_dir)
        if n.endswith(STREAM_SUFFIX)
    )
    if not paths:
        raise EmitError(
            f"no {STREAM_SUFFIX} streams in {results_dir}. An analysis that emitted "
            "nothing has not run; an empty bundle would otherwise pass every check "
            "downstream because there is nothing in it to contradict."
        )

    streams = [read_stream(p) for p in paths]
    truncated = [s for s in streams if not s.complete]
    if truncated and require_complete:
        raise EmitError(
            "these analysis streams never wrote a completion sentinel: "
            + ", ".join(os.path.basename(s.path) for s in truncated)
            + ". Each is a script that died partway through. The records it did "
            "write are real, which is exactly why they are dangerous: they assemble "
            "into a bundle that looks like a finished analysis."
        )

    owners: dict[str, str] = {}
    for stream in streams:
        for rec in stream.records:
            if rec.token in owners:
                raise EmitError(
                    f"token {rec.token!r} is emitted by both {owners[rec.token]} and "
                    f"{rec.script}. A number must exist exactly once, or two places "
                    "in the manuscript can drift apart."
                )
            owners[rec.token] = rec.script
            bundle.add(rec)

    # The bundle claims script hashes; a record naming a script outside that set
    # is a number whose code is not in the provenance chain.
    if known_scripts is not None:
        known = set(known_scripts)
        orphans = sorted({s for s in owners.values() if s not in known})
        if orphans:
            raise EmitError(
                f"records name scripts that were never hashed: {', '.join(orphans)}. "
                "The provenance chain runs design -> data -> code -> results; a "
                "result whose code is unhashed breaks it."
            )

    # Figures last: they bind to result tokens, so every result has to be in
    # the bundle before a figure can be checked against it.
    sealed = {os.path.basename(s.path)[: -len(STREAM_SUFFIX)]
              for s in streams if s.complete}
    fig_paths = sorted(
        os.path.join(results_dir, n) for n in os.listdir(results_dir)
        if n.endswith(FIGURE_SUFFIX)
    )
    declared: list[FigureRecord] = []
    for fp in fig_paths:
        declared.extend(read_figure_stream(fp, sealed))
    if declared:
        bound = bind_figures(declared, {r.token for r in bundle.records},
                             base_dir=figure_base_dir)
        bundle.figures = [f.to_dict() for f in bound]
    return bundle


def _runner_script() -> str:
    """The script identity comes from the runner, never from study code."""
    return os.environ.get("COSCIENTIST_CURRENT_SCRIPT", "").strip()


def _require_python_stream_identity(path: str, claimed: str | None = None) -> str:
    runner = _runner_script()
    if not runner:
        # Library/test use outside the managed runner remains possible, but no
        # confirmatory GitHub execution omits this variable.
        return claimed or ""
    if claimed and claimed != runner:
        raise EmitError(
            f"Python emitter is running {runner!r}; study code tried to attribute "
            f"the result to {claimed!r}. Producer identity is runner-derived."
        )
    stem = os.path.basename(path)
    suffix = STREAM_SUFFIX if stem.endswith(STREAM_SUFFIX) else FIGURE_SUFFIX if stem.endswith(FIGURE_SUFFIX) else ""
    if suffix:
        actual_stem = stem[:-len(suffix)]
        if actual_stem != stream_stem(runner):
            raise EmitError(
                f"Python emitter is running {runner!r}, but output path {path!r} "
                "belongs to a different script stream."
            )
    return runner


def emit_row(path: str, **fields: Any) -> None:
    """Python-side equivalent of `emit.R`, with runner-derived provenance."""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    script = _require_python_stream_identity(path, str(fields.get("script", "") or "") or None)
    if script:
        fields["script"] = script
    record_from_row(fields, origin=os.path.basename(path))   # validate before writing
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(fields, sort_keys=True, separators=(",", ":")) + "\n")


def emit_done(path: str, script: str = "") -> None:
    actual = _require_python_stream_identity(path, script or None)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps({SENTINEL: True, "script": actual or script}) + "\n")

