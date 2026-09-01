"""Lake-bounded rescope: what happens when acquisition fails.

The rule this implements: an acquisition failure must not simply kill a
candidate. The engine falls back to what the lake already holds, redefines
whatever needs redefining so the study is feasible within those limits, and
moves on.

Two hard boundaries make that safe rather than reckless.

1. PRE-FREEZE ONLY. Rescoping after the outcome lock would be choosing a
   research question to fit data whose results are already visible. Above the
   lock it is ordinary design work; below it, it is specification search.
   `rescope` refuses to run on a frozen candidate.

2. GATES ARE RE-RUN, NOT INHERITED. A rescoped study is a DIFFERENT study. Its
   novelty residual changes because the closest-paper set changes, and its
   power changes because the sample changes. Carrying forward a G2 or G4 pass
   earned by the original scope is the subtle way this feature would corrupt
   the engine, so the proposal marks those gates dirty.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any

from .lake import LakeCatalog, LakeDataset
from .models import Candidate, Construct, GateId, require_mutable, utcnow

MAX_RESCOPES = 2


class RescopeRefused(RuntimeError):
    """Raised when a rescope is attempted where it would not be legitimate."""


@dataclass
class ConstructSwap:
    construct: str
    role: str
    from_source: str | None
    to_dataset: str
    granularity_before: str | None
    granularity_after: str | None
    coverage_after: tuple[str | None, str | None]
    degraded: bool

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class RescopeProposal:
    candidate_id: str
    feasible: bool
    swaps: list[ConstructSwap] = field(default_factory=list)
    dropped_controls: list[str] = field(default_factory=list)
    unresolved: list[str] = field(default_factory=list)
    gates_to_rerun: list[str] = field(default_factory=list)
    note: str = ""
    at: str = field(default_factory=utcnow)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["swaps"] = [s.to_dict() for s in self.swaps]
        return d


def _pick(catalog: LakeCatalog, construct: Construct) -> LakeDataset | None:
    exact = catalog.find(construct, require_granularity=True)
    if exact:
        return exact[0]
    loose = catalog.find(construct, require_granularity=False)
    return loose[0] if loose else None


def propose_rescope(
    candidate: Candidate,
    catalog: LakeCatalog,
    failed_constructs: list[str],
) -> RescopeProposal:
    """Find a lake-bounded version of this candidate, if one exists."""
    try:
        require_mutable(candidate, "propose_rescope")
    except Exception as exc:
        raise RescopeRefused(str(exc)) from exc
    if candidate.rescope_count >= MAX_RESCOPES:
        return RescopeProposal(
            candidate_id=candidate.id,
            feasible=False,
            unresolved=list(failed_constructs),
            note=f"rescope budget exhausted ({MAX_RESCOPES} used)",
        )

    by_name = {c.name: c for c in candidate.constructs}
    proposal = RescopeProposal(candidate_id=candidate.id, feasible=True)

    for name in failed_constructs:
        con = by_name.get(name)
        if con is None:
            proposal.unresolved.append(name)
            proposal.feasible = False
            continue

        substitute = _pick(catalog, con)
        if substitute is None:
            if con.droppable():
                # Explicitly declared OPTIONAL by whoever specified the design.
                proposal.dropped_controls.append(name)
            else:
                # Everything else blocks: primary constructs, and any control
                # not explicitly marked OPTIONAL. A covariate absorbing the
                # principal confound is not expendable merely because its role
                # string says "control", and this function is not entitled to
                # decide that identification survives without it.
                proposal.unresolved.append(name)
                proposal.feasible = False
            continue

        degraded = bool(con.granularity and substitute.granularity != con.granularity)
        proposal.swaps.append(
            ConstructSwap(
                construct=name,
                role=con.role,
                from_source=con.preferred_source,
                to_dataset=substitute.id,
                granularity_before=con.granularity,
                granularity_after=substitute.granularity,
                coverage_after=(substitute.coverage_start, substitute.coverage_end),
                degraded=degraded,
            )
        )

    if proposal.feasible and (proposal.swaps or proposal.dropped_controls):
        # A rescoped study is a different study. Novelty and power must be
        # re-adjudicated; inheriting them is how this feature would rot.
        proposal.gates_to_rerun = [GateId.G2.value, GateId.G4.value]
        proposal.note = "lake-bounded rescope available; G2 and G4 must be re-run"
    elif proposal.feasible:
        proposal.feasible = False
        proposal.note = "nothing to rescope"

    if not proposal.feasible and not proposal.note:
        proposal.note = "no lake substitute for a primary construct"
    return proposal


def apply_rescope(candidate: Candidate, proposal: RescopeProposal) -> Candidate:
    """Mutate the candidate onto its lake-bounded scope, preserving lineage."""
    # A proposal's validity is time-dependent. One generated legitimately
    # before the freeze could be applied after it, because only propose_rescope
    # checked the lock. The guard belongs on every mutating call, not the
    # first one in the sequence.
    try:
        require_mutable(candidate, "apply_rescope")
    except Exception as exc:
        raise RescopeRefused(str(exc)) from exc
    if not proposal.feasible:
        raise RescopeRefused(f"{candidate.id}: {proposal.note}")

    by_name = {c.name: c for c in candidate.constructs}
    for swap in proposal.swaps:
        con = by_name[swap.construct]
        con.preferred_source = swap.to_dataset
        if swap.granularity_after:
            con.granularity = swap.granularity_after
        start, end = swap.coverage_after
        con.coverage_start = start or con.coverage_start
        con.coverage_end = end or con.coverage_end

    if proposal.dropped_controls:
        dropped = set(proposal.dropped_controls)
        candidate.constructs = [c for c in candidate.constructs if c.name not in dropped]

    candidate.rescope_count += 1
    candidate.status = "RESCOPED"
    candidate.log(
        "rescope",
        proposal=proposal.to_dict(),
        rescope_count=candidate.rescope_count,
        gates_dirty=proposal.gates_to_rerun,
    )
    return candidate
