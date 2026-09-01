"""The triage gauntlet: cheapest killer first.

Gate order is fixed by cost, so a candidate never reaches an expensive gate
until it has survived every cheaper one.

  G1  failure collision      seconds     mechanical
  G2  novelty displacement   ~20 searches  retrieval mechanical, verdict judgment
  G3  access class + reach   minutes     mechanical
  G4  power preflight        ~1 hour     mechanical

G3 is the gate that would have saved C705: it asks whether the engine is
*permitted* to have the data, not merely whether a URL responds. An
identity-gated source is permanently unavailable to an autonomous engine and
is knowable as such in seconds.
"""
from __future__ import annotations

from typing import Any, Callable

import pandas as pd

from .lake import LakeCatalog
from .models import (Candidate, GateId, GateResult, Necessity,
                     require_mutable, Verdict)
from .power import PowerError, power_preflight
from .registry import SourceRegistry


def g1_failure_collision(candidate: Candidate, failure_memory: dict[str, Any]) -> GateResult:
    """Has this lineage already been retired, or does it sit on a prohibition?"""
    retired = {r.lower() for r in failure_memory.get("retired_lineages", [])}
    prohibitions = failure_memory.get("recycle_prohibitions", [])

    if candidate.id.lower() in retired:
        return GateResult(
            gate=GateId.G1,
            candidate_id=candidate.id,
            verdict=Verdict.FAIL,
            reason=f"{candidate.id} is a retired lineage",
            evidence={"matched": candidate.id},
        )

    haystack = f"{candidate.title} {candidate.question}".lower()
    for rule in prohibitions:
        text = rule if isinstance(rule, str) else rule.get("pattern", "")
        if text and text.lower() in haystack:
            return GateResult(
                gate=GateId.G1,
                candidate_id=candidate.id,
                verdict=Verdict.FAIL,
                reason="construct sits on a standing recycle prohibition",
                evidence={"prohibition": text},
            )

    return GateResult(
        gate=GateId.G1,
        candidate_id=candidate.id,
        verdict=Verdict.PASS,
        reason="no collision with failure memory",
    )


def _rank_sources(candidates: list) -> list:
    """Order candidate sources by how usable they actually are right now.

    `by_concept` returns best concept-overlap first, then alphabetical -- which
    put BIGQUERY_PUBLIC ahead of PATENTSVIEW_S3 and made the engine pick a
    credentialed source over an equally good keyless one, then rescope because
    the credentials were absent. Rank on usability: admissible first, then
    credentials actually present, then OPEN ahead of HELD.
    """
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
    """Walk every external route for one construct, best first.

    Alternate-source discovery used to run only when no preferred source was
    named at all. So a construct pinned to USPTO_ODP -- identity-gated, and
    therefore permanently unusable -- went straight to the lake, ignoring
    EPO_INPADOC sitting in the same registry, OPEN, measuring the same
    concepts. The same hole meant an unreachable first-ranked route was never
    followed by the second.

    Returns (chosen_source_or_None, trail) where trail records every route
    considered and why each was rejected.
    """
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

        # Reachable is not the same as suitable. "Usable" once meant only
        # "responds and vaguely measures the concept", so a country-year source
        # covering 2025-2026 was assigned to a construct needing patent-event
        # data over 2010-2024. Now that G3 substitutes sources on its own, fit
        # has to be checked before the route is accepted.
        if con.granularity:
            if not src.granularity:
                trail.append({"source": src.id,
                              "why": f"unknown granularity; needs {con.granularity}",
                              "uncertain": True})
                continue
            if con.granularity != src.granularity:
                trail.append({"source": src.id,
                              "why": f"granularity {src.granularity} != {con.granularity}"})
                continue
        if con.coverage_start:
            if not src.coverage_start:
                trail.append({"source": src.id,
                              "why": f"unknown coverage start; needs {con.coverage_start}",
                              "uncertain": True})
                continue
            if src.coverage_start > con.coverage_start:
                trail.append({"source": src.id,
                              "why": f"starts {src.coverage_start}, needs {con.coverage_start}"})
                continue
        if con.coverage_end:
            if not src.coverage_end:
                trail.append({"source": src.id,
                              "why": f"unknown coverage end; needs {con.coverage_end}",
                              "uncertain": True})
                continue
            if src.coverage_end < con.coverage_end:
                trail.append({"source": src.id,
                              "why": f"ends {src.coverage_end}, needs {con.coverage_end}"})
                continue
        return src, trail
    return None, trail


def g3_access(
    candidate: Candidate,
    registry: SourceRegistry,
    catalog: LakeCatalog | None = None,
    reach_probe: Callable[[str], bool] | None = None,
) -> GateResult:
    """Access class first, then reachability, across every external route.

    Order is: preferred source, then every concept-compatible alternative,
    ranked by usability. Only when all of them fail does the lake come into
    play, and only then can the verdict be RESCOPE or FAIL.
    """
    blocked: list[dict[str, Any]] = []
    resolved: list[dict[str, Any]] = []

    for con in candidate.constructs:
        chosen, trail = _resolve_construct(con, registry, reach_probe)
        if chosen is not None:
            if chosen.id != con.preferred_source:
                # Rewriting preferred_source alters scientific state, so it
                # goes through the same guard as every other mutation -- G3 was
                # quietly re-routing frozen candidates.
                require_mutable(candidate, "g3_access source substitution")
                resolved.append({"construct": con.name, "chose": chosen.id,
                                 "from": con.preferred_source, "rejected": trail})
                con.preferred_source = chosen.id
            continue
        blocked.append({"construct": con.name, "source": con.preferred_source,
                        "why": "no usable external route", "primary": con.is_primary(),
                        "necessity": con.necessity.value, "tried": trail,
                        "uncertain_suitability": any(t.get("uncertain") for t in trail)})

    if not blocked:
        return GateResult(
            gate=GateId.G3, candidate_id=candidate.id, verdict=Verdict.PASS,
            reason="every construct has a usable, suitable external route",
            evidence={"resolved": resolved},
        )

    # Necessity, not role. A control can be non-primary and still indispensable
    # for identification; "only non-primary constructs blocked, they can be
    # dropped" was passing candidates whose confound-absorbing covariate had no
    # source at all.
    by_name = {c.name: c for c in candidate.constructs}
    essential = [b["construct"] for b in blocked
                 if b["primary"] or by_name[b["construct"]].necessity is Necessity.ESSENTIAL]
    important = [b["construct"] for b in blocked
                 if b["construct"] not in essential
                 and by_name[b["construct"]].necessity is Necessity.IMPORTANT]

    if essential:
        uncertain_essential = [b["construct"] for b in blocked
                               if b["construct"] in essential and b.get("uncertain_suitability")]
        # Unknown suitability is not evidence that the science fails.  It is an
        # unresolved source-metadata problem and must be verified explicitly.
        if uncertain_essential:
            return GateResult(
                gate=GateId.G3, candidate_id=candidate.id, verdict=Verdict.DEFER,
                reason=("essential source suitability is unverified for "
                        + ", ".join(uncertain_essential)),
                evidence={"blocked": blocked, "resolved": resolved,
                          "unverified": uncertain_essential},
            )
        if catalog is not None:
            query_deferred = [n for n in essential if catalog.requires_query_layer(by_name[n])]
            if query_deferred:
                return GateResult(
                    gate=GateId.G3, candidate_id=candidate.id, verdict=Verdict.DEFER,
                    reason=("the lake holds required data but only through a query/curation "
                            "route for " + ", ".join(query_deferred)),
                    evidence={"blocked": blocked, "resolved": resolved,
                              "query_layer_required": query_deferred},
                )
            if all(catalog.has(by_name[n]) for n in essential):
                return GateResult(
                    gate=GateId.G3, candidate_id=candidate.id, verdict=Verdict.RESCOPE,
                    reason="no external route for an essential construct, but the lake holds it",
                    evidence={"blocked": blocked, "resolved": resolved,
                              "rescope_targets": essential},
                )
        return GateResult(
            gate=GateId.G3, candidate_id=candidate.id, verdict=Verdict.FAIL,
            reason="an essential construct has no admissible route and no lake substitute",
            evidence={"blocked": blocked, "resolved": resolved, "essential": essential},
        )

    if important:
        return GateResult(
            gate=GateId.G3, candidate_id=candidate.id, verdict=Verdict.DEFER,
            reason=(f"constructs marked IMPORTANT are unavailable ({', '.join(important)}); "
                    "dropping them weakens the claim, which is a judgment call"),
            evidence={"blocked": blocked, "resolved": resolved, "important": important},
        )

    return GateResult(
        gate=GateId.G3, candidate_id=candidate.id, verdict=Verdict.PASS,
        reason="only OPTIONAL constructs blocked; they may be dropped as declared",
        evidence={"blocked": blocked, "resolved": resolved,
                  "droppable": [b["construct"] for b in blocked]},
    )


# A cheap screen kills only what is hopeless. Below this ratio the design is
# adequate; above it, hopeless; in between, the approximation is not sharp
# enough to end a candidate and the case is escalated to a design-specific
# Monte Carlo (G4B), which is not built yet.
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
    """Can this design detect an effect the mechanism could plausibly produce?

    This is G4A, a cheap screen, and it is scoped to what a cheap screen can
    honestly decide. It ends a candidate only when the shortfall is an order of
    magnitude, not when it is marginal -- a generic approximation is entitled to
    kill the hopeless, not to adjudicate the close.
    """
    if candidate.plausible_effect is None:
        return GateResult(
            gate=GateId.G4, candidate_id=candidate.id, verdict=Verdict.DEFER,
            reason="no plausible effect size declared; derive it from first principles first",
        )
    try:
        res = power_preflight(
            pre_period_panel, unit=unit, time=time, outcome=outcome,
            pre_period_end=pre_period_end, cluster=cluster,
            treated_share=treated_share, plausible_effect=candidate.plausible_effect,
        )
    except PowerError as exc:
        # BLOCKED, not FAIL. A candidate must never be retired because the
        # orchestrator handed the gate the wrong dataframe, or because a panel
        # arrived with post-treatment rows in it. That is an input-contract
        # failure, and conflating it with a scientific verdict would quietly
        # kill good science on an engine bug -- the exact confusion V3 built
        # its four failure classes to prevent.
        return GateResult(
            gate=GateId.G4, candidate_id=candidate.id, verdict=Verdict.BLOCKED,
            reason=f"power preflight could not run: {exc}",
        )

    base = (
        f"MDE {res.mde:.4g} vs plausible {res.plausible_effect:.4g} "
        f"(ratio {res.ratio:.3g})"
    )
    if res.powered:
        return GateResult(gate=GateId.G4, candidate_id=candidate.id,
                          verdict=Verdict.PASS, reason=base, evidence=res.to_dict())
    if res.ratio >= kill_margin:
        return GateResult(
            gate=GateId.G4, candidate_id=candidate.id, verdict=Verdict.FAIL,
            reason=base + " -- underpowered by construction; execution cannot rescue this",
            evidence=res.to_dict(),
        )
    return GateResult(
        gate=GateId.G4, candidate_id=candidate.id, verdict=Verdict.DEFER,
        reason=base + (
            f" -- short of power but inside the {kill_margin:g}x margin; too close "
            "for a generic approximation to end a candidate. Escalate to G4B "
            "design-specific Monte Carlo."
        ),
        evidence=res.to_dict(),
    )
