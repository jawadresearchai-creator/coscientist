"""Admission and feasibility gates for the single active paper.

G1 no longer consults historical candidate genealogy. Old candidate IDs,
retirement lists and recycle queues are archive records, not evidence about the
current paper. Only a current hard prohibition may fail G1.

G3 is lake-first. A suitable object already held in the Global Management
Science lake is preferred to external reacquisition. External routes fill
actual gaps. A looser lake substitute is a pre-freeze evolution of the same
paper, not a reason to restart topic discovery.

G4 remains a pre-period power screen. Input/engine failures are BLOCKED rather
than scientific FAIL, so operational friction cannot retire the paper.
"""
from __future__ import annotations

from typing import Any, Callable

import pandas as pd

from .lake import LakeCatalog
from .models import (
    Candidate,
    GateId,
    GateResult,
    Necessity,
    Verdict,
    require_mutable,
)
from .power import PowerError, power_preflight
from .registry import SourceRegistry


def g1_failure_collision(candidate: Candidate, failure_memory: dict[str, Any]) -> GateResult:
    """Apply current hard prohibitions; deliberately ignore historical candidates.

    The argument name is retained for API compatibility with older callers.
    `retired_lineages` and `recycle_prohibitions` are not active scientific
    inputs under the single-paper operating model.
    """
    haystack = f"{candidate.title} {candidate.question}".lower()
    for rule in failure_memory.get("hard_prohibitions", []):
        text = rule if isinstance(rule, str) else rule.get("pattern", "")
        if text and text.lower() in haystack:
            return GateResult(
                gate=GateId.G1,
                candidate_id=candidate.id,
                verdict=Verdict.FAIL,
                reason="current hard prohibition blocks the study",
                evidence={"prohibition": text},
            )
    return GateResult(
        gate=GateId.G1,
        candidate_id=candidate.id,
        verdict=Verdict.PASS,
        reason="historical candidate memory ignored; no current hard prohibition",
    )


def _rank_sources(candidates: list) -> list:
    """Order external sources by actual usability after the lake has been checked."""
    def key(src):
        ok, _ = src.secrets_present()
        return (
            0 if src.admissible else 1,
            0 if ok else 1,
            0 if src.access_class.value == "OPEN" else 1,
            src.id,
        )
    return sorted(candidates, key=key)


def _resolve_construct(con, registry, reach_probe):
    """Walk every admissible external route for a construct, best first."""
    routes: list[str] = []
    if con.preferred_source:
        routes.append(con.preferred_source)
    for src in _rank_sources(registry.by_concept(con.concepts)) if con.concepts else []:
        if src.id not in routes:
            routes.append(src.id)

    trail: list[dict[str, Any]] = []
    for source_id in routes:
        try:
            src = registry.get(source_id)
        except Exception:
            trail.append({"source": source_id, "why": "not in registry"})
            continue
        if not src.admissible:
            trail.append({"source": src.id, "why": f"access_class={src.access_class.value}"})
            continue
        ok, missing = src.secrets_present()
        if not ok:
            trail.append({"source": src.id, "why": f"missing secrets {missing}"})
            continue
        if reach_probe and src.url and not reach_probe(src.url):
            trail.append({"source": src.id, "why": "unreachable"})
            continue

        # Reachability is not suitability. Fail closed when metadata needed to
        # establish fit is absent; uncertainty is a verification task, not a
        # license to assume compatibility.
        if con.granularity:
            if not src.granularity:
                trail.append({
                    "source": src.id,
                    "why": f"unknown granularity; needs {con.granularity}",
                    "uncertain": True,
                })
                continue
            if con.granularity != src.granularity:
                trail.append({
                    "source": src.id,
                    "why": f"granularity {src.granularity} != {con.granularity}",
                })
                continue
        if con.coverage_start:
            if not src.coverage_start:
                trail.append({
                    "source": src.id,
                    "why": f"unknown coverage start; needs {con.coverage_start}",
                    "uncertain": True,
                })
                continue
            if src.coverage_start > con.coverage_start:
                trail.append({
                    "source": src.id,
                    "why": f"starts {src.coverage_start}, needs {con.coverage_start}",
                })
                continue
        if con.coverage_end:
            if not src.coverage_end:
                trail.append({
                    "source": src.id,
                    "why": f"unknown coverage end; needs {con.coverage_end}",
                    "uncertain": True,
                })
                continue
            if src.coverage_end < con.coverage_end:
                trail.append({
                    "source": src.id,
                    "why": f"ends {src.coverage_end}, needs {con.coverage_end}",
                })
                continue
        return src, trail
    return None, trail


def _lake_exact(catalog: LakeCatalog, con):
    """Suitable directly usable lake objects, exact on requested granularity."""
    return catalog.find(con, require_granularity=True)


def _lake_query_exact(catalog: LakeCatalog, con):
    """Exact objects whose bytes must first be materialised through a query layer."""
    return [
        d for d in catalog.find(
            con, require_granularity=True, include_query_required=True
        )
        if d.analysis_route == "QUERY_LAYER_REQUIRED"
    ]


def g3_access(
    candidate: Candidate,
    registry: SourceRegistry,
    catalog: LakeCatalog | None = None,
    reach_probe: Callable[[str], bool] | None = None,
) -> GateResult:
    """Resolve constructs lake-first, then use external sources for true gaps."""
    blocked: list[dict[str, Any]] = []
    resolved: list[dict[str, Any]] = []

    for con in candidate.constructs:
        if catalog is not None:
            exact = _lake_exact(catalog, con)
            if exact:
                ds = exact[0]
                if ds.id != con.preferred_source:
                    require_mutable(candidate, "g3_access lake-first source substitution")
                    con.preferred_source = ds.id
                resolved.append({
                    "construct": con.name,
                    "chose": ds.id,
                    "from": None,
                    "route": "lake",
                    "source_id": ds.source_id,
                    "rejected": [],
                })
                continue

            query_exact = _lake_query_exact(catalog, con)
            if query_exact:
                blocked.append({
                    "construct": con.name,
                    "source": con.preferred_source,
                    "why": "exact lake data require query/curation materialisation",
                    "primary": con.is_primary(),
                    "necessity": con.necessity.value,
                    "tried": [],
                    "query_layer": [d.id for d in query_exact],
                    "uncertain_suitability": False,
                })
                continue

        chosen, trail = _resolve_construct(con, registry, reach_probe)
        if chosen is not None:
            old = con.preferred_source
            if chosen.id != old:
                require_mutable(candidate, "g3_access external source substitution")
                con.preferred_source = chosen.id
            resolved.append({
                "construct": con.name,
                "chose": chosen.id,
                "from": old,
                "route": "external",
                "rejected": trail,
            })
            continue

        blocked.append({
            "construct": con.name,
            "source": con.preferred_source,
            "why": "no exact lake route and no usable external route",
            "primary": con.is_primary(),
            "necessity": con.necessity.value,
            "tried": trail,
            "uncertain_suitability": any(t.get("uncertain") for t in trail),
        })

    if not blocked:
        return GateResult(
            gate=GateId.G3,
            candidate_id=candidate.id,
            verdict=Verdict.PASS,
            reason="every construct resolved, preferring suitable lake holdings",
            evidence={"resolved": resolved},
        )

    by_name = {c.name: c for c in candidate.constructs}
    essential = [
        b["construct"] for b in blocked
        if b["primary"] or by_name[b["construct"]].necessity is Necessity.ESSENTIAL
    ]
    important = [
        b["construct"] for b in blocked
        if b["construct"] not in essential
        and by_name[b["construct"]].necessity is Necessity.IMPORTANT
    ]

    if essential:
        query_deferred = [
            b["construct"] for b in blocked
            if b["construct"] in essential and b.get("query_layer")
        ]
        if query_deferred:
            return GateResult(
                gate=GateId.G3,
                candidate_id=candidate.id,
                verdict=Verdict.DEFER,
                reason=("the lake already holds required data through a query/curation "
                        "route for " + ", ".join(query_deferred)),
                evidence={
                    "blocked": blocked,
                    "resolved": resolved,
                    "query_layer_required": query_deferred,
                },
            )

        uncertain = [
            b["construct"] for b in blocked
            if b["construct"] in essential and b.get("uncertain_suitability")
        ]
        if uncertain:
            return GateResult(
                gate=GateId.G3,
                candidate_id=candidate.id,
                verdict=Verdict.DEFER,
                reason="essential source suitability is unverified for " + ", ".join(uncertain),
                evidence={"blocked": blocked, "resolved": resolved, "unverified": uncertain},
            )

        # Exact lake fits were already consumed. A remaining catalog hit is a
        # looser, explicit pre-freeze evolution of the same active paper.
        if catalog is not None and all(catalog.has(by_name[n]) for n in essential):
            return GateResult(
                gate=GateId.G3,
                candidate_id=candidate.id,
                verdict=Verdict.RESCOPE,
                reason="lake-bounded pre-freeze evolution can repair essential data gaps",
                evidence={
                    "blocked": blocked,
                    "resolved": resolved,
                    "rescope_targets": essential,
                },
            )

        return GateResult(
            gate=GateId.G3,
            candidate_id=candidate.id,
            verdict=Verdict.FAIL,
            reason="genuine blocker: an essential construct has no suitable lake or admissible external route",
            evidence={"blocked": blocked, "resolved": resolved, "essential": essential},
        )

    if important:
        return GateResult(
            gate=GateId.G3,
            candidate_id=candidate.id,
            verdict=Verdict.DEFER,
            reason=(f"IMPORTANT constructs are unavailable ({', '.join(important)}); "
                    "repair/evolution requires scientific judgment"),
            evidence={"blocked": blocked, "resolved": resolved, "important": important},
        )

    return GateResult(
        gate=GateId.G3,
        candidate_id=candidate.id,
        verdict=Verdict.PASS,
        reason="only OPTIONAL constructs are unavailable; they may be dropped as declared",
        evidence={
            "blocked": blocked,
            "resolved": resolved,
            "droppable": [b["construct"] for b in blocked],
        },
    )


# A cheap power screen can kill only the clearly hopeless. Borderline designs
# must be escalated to design-specific simulation rather than judged by a
# generic approximation.
KILL_MARGIN = 3.0


def g4_power(
    candidate: Candidate,
    pre_period_panel: pd.DataFrame,
    *,
    unit: str,
    time: str,
    outcome: str,
    pre_period_end: Any,
    cluster: str | None = None,
    treated_share: float = 0.5,
    kill_margin: float = KILL_MARGIN,
) -> GateResult:
    """Can the intended design detect an effect the mechanism could plausibly produce?"""
    if candidate.plausible_effect is None:
        return GateResult(
            gate=GateId.G4,
            candidate_id=candidate.id,
            verdict=Verdict.DEFER,
            reason="no plausible effect size declared; derive it before power adjudication",
        )
    try:
        res = power_preflight(
            pre_period_panel,
            unit=unit,
            time=time,
            outcome=outcome,
            pre_period_end=pre_period_end,
            cluster=cluster,
            treated_share=treated_share,
            plausible_effect=candidate.plausible_effect,
        )
    except PowerError as exc:
        return GateResult(
            gate=GateId.G4,
            candidate_id=candidate.id,
            verdict=Verdict.BLOCKED,
            reason=f"power preflight could not run: {exc}",
        )

    base = (
        f"MDE {res.mde:.4g} vs plausible {res.plausible_effect:.4g} "
        f"(ratio {res.ratio:.3g})"
    )
    if res.powered:
        return GateResult(
            gate=GateId.G4,
            candidate_id=candidate.id,
            verdict=Verdict.PASS,
            reason=base,
            evidence=res.to_dict(),
        )
    if res.ratio >= kill_margin:
        return GateResult(
            gate=GateId.G4,
            candidate_id=candidate.id,
            verdict=Verdict.FAIL,
            reason=base + " -- fundamentally underpowered at a plausible effect scale",
            evidence=res.to_dict(),
        )
    return GateResult(
        gate=GateId.G4,
        candidate_id=candidate.id,
        verdict=Verdict.DEFER,
        reason=base + (
            f" -- inside the {kill_margin:g}x uncertainty margin; escalate to G4B "
            "design-specific Monte Carlo rather than retiring the paper"
        ),
        evidence=res.to_dict(),
    )
