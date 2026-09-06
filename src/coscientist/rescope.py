"""Lake-bounded pre-freeze evolution of the single active paper.

Acquisition or measurement friction does not start a new topic search. The
active paper may evolve as many times as scientifically necessary before the
outcome lock. A substantive change re-runs affected scientific checks rather
than inheriting stale passes.

After the outcome lock, the same scientific change would be specification
search and is refused.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from .lake import LakeCatalog, LakeDataset
from .models import Candidate, Construct, GateId, require_mutable, utcnow

# Compatibility name retained for imports from older callers. v4.3 intentionally
# has no arbitrary pre-freeze evolution-count ceiling.
MAX_RESCOPES = None


class RescopeRefused(RuntimeError):
    """Raised when an evolution is attempted where it would not be legitimate."""


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
    """Find a lake-bounded evolution of the active paper, if one exists."""
    try:
        require_mutable(candidate, "propose_rescope")
    except Exception as exc:
        raise RescopeRefused(str(exc)) from exc

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
                proposal.dropped_controls.append(name)
            else:
                # Essential/important constructs do not disappear merely to keep
                # the pipeline moving. If no credible substitute exists, this
                # remains a genuine scientific/data problem for the active paper.
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
        # Focus remains on the same active paper, but a substantive design/data
        # evolution cannot inherit old novelty/power adjudications.
        proposal.gates_to_rerun = [GateId.G2.value, GateId.G4.value]
        proposal.note = "lake-bounded evolution available; novelty closure and power must be re-run"
    elif proposal.feasible:
        proposal.feasible = False
        proposal.note = "nothing to evolve"

    if not proposal.feasible and not proposal.note:
        proposal.note = "no lake substitute for an essential construct"
    return proposal


def apply_rescope(candidate: Candidate, proposal: RescopeProposal) -> Candidate:
    """Apply a valid pre-freeze evolution while preserving one-paper lineage."""
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
    candidate.status = "EVOLVED"
    candidate.log(
        "evolve",
        proposal=proposal.to_dict(),
        evolution_count=candidate.rescope_count,
        gates_dirty=proposal.gates_to_rerun,
    )
    return candidate
