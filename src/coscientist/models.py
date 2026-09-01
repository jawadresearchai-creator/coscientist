"""Core value objects.

Plain dataclasses on purpose: no pydantic, no runtime dependency that CI has
to resolve. Validation is explicit and local.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import Enum
from typing import Any


class FrozenDesignViolation(RuntimeError):
    """Raised by any operation that would alter a frozen design."""


def require_mutable(candidate: "Candidate", operation: str) -> None:
    """The single guard every design-mutating operation must call.

    Three separate paths walked around the outcome lock -- `apply_rescope`
    never checked it, G3 rewrote `preferred_source` on a frozen candidate, and
    a stale pre-freeze proposal could be applied after the freeze. Fixing them
    one at a time guarantees a fourth. One guard, called everywhere, is the
    only version of this that stays true as the engine grows.
    """
    if candidate.is_frozen():
        raise FrozenDesignViolation(
            f"{operation} would alter {candidate.id}, frozen under "
            f"{candidate.freeze_id}. Below the outcome lock the design is fixed; "
            "a legitimate change is a POST_FREEZE_CHANGE on a new lineage."
        )


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class AccessClass(str, Enum):
    """How a source may be reached, and whether the engine may depend on it."""

    OPEN = "OPEN"                        # no account, no key
    HELD = "HELD"                        # keyed, key already in Actions secrets
    KEYED_NEW = "KEYED_NEW"              # free key, needs a fresh application
    IDENTITY_GATED = "IDENTITY_GATED"    # ID proofing / residency / video
    PAID = "PAID"                        # any charge or credit balance

    @property
    def admissible(self) -> bool:
        return self in (AccessClass.OPEN, AccessClass.HELD)


class Verdict(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"        # scientific: the candidate does not survive
    RESCOPE = "RESCOPE"  # redefine within the lake's limits and re-run gates
    DEFER = "DEFER"      # needs judgment; ticket emitted
    BLOCKED = "BLOCKED"  # engine or input-contract failure; NOT a verdict on the science

    @property
    def retires_candidate(self) -> bool:
        return self is Verdict.FAIL


class GateId(str, Enum):
    G1 = "G1"   # failure collision
    G2 = "G2"   # novelty displacement (retrieval mechanical, verdict judgment)
    G3 = "G3"   # access class + reachability
    G4 = "G4"   # power preflight


class Necessity(str, Enum):
    """How much identification depends on a construct being measured.

    Role alone was not enough. `role == "control"` once made a construct
    mechanically droppable during rescope -- but a covariate absorbing the
    principal confound is not optional merely because it is a control, and
    dropping it silently changes what the design identifies.

    Default is ESSENTIAL, so the failure mode is a refused rescope rather than
    a quietly degraded one. Dropping requires that someone said it was safe.
    """

    ESSENTIAL = "ESSENTIAL"    # identification collapses without it
    IMPORTANT = "IMPORTANT"    # weakens the claim; needs an explicit decision
    OPTIONAL = "OPTIONAL"      # safe to drop mechanically


@dataclass
class Construct:
    """One measured thing a candidate needs.

    `concepts` are free tags used to find lake substitutes when the preferred
    source cannot be reached. `role` says what the construct does in the design;
    `necessity` says what happens to the design without it.
    """

    name: str
    role: str                      # "treatment" | "outcome" | "control" | "instrument"
    concepts: list[str] | tuple[str, ...] = field(default_factory=list)
    preferred_source: str | None = None
    granularity: str | None = None       # e.g. "firm-quarter", "patent-event"
    coverage_start: str | None = None     # ISO date
    coverage_end: str | None = None
    necessity: Necessity = Necessity.ESSENTIAL
    sealed: bool = False           # set when the parent candidate freezes

    def __setattr__(self, name: str, value: Any) -> None:
        """Sealing has to reach the constructs too.

        Guarding only `Candidate.__setattr__` left the object graph open one
        level down: `candidate.constructs[0].preferred_source = "OTHER"` never
        touched the candidate at all, and swapping which data measures the
        treatment below the outcome lock is as much a design change as
        rewriting the question.
        """
        if name != "sealed" and getattr(self, "sealed", False):
            raise FrozenDesignViolation(
                f"construct {getattr(self, 'name', '?')!r} is sealed under a frozen "
                f"design; {name} cannot be changed. Below the outcome lock the "
                "design is fixed."
            )
        object.__setattr__(self, name, value)

    def seal(self) -> None:
        # __setattr__ cannot protect mutation *inside* a list.  A frozen
        # construct whose `concepts.append(...)` still works is not frozen.
        # Convert mutable scientific containers before raising the seal.
        object.__setattr__(self, "concepts", tuple(self.concepts))
        object.__setattr__(self, "sealed", True)

    def is_primary(self) -> bool:
        return self.role in ("treatment", "outcome", "instrument")

    def droppable(self) -> bool:
        """Only a non-primary construct explicitly marked OPTIONAL may be dropped."""
        return (not self.is_primary()) and self.necessity is Necessity.OPTIONAL


@dataclass
class Candidate:
    """A research candidate moving through the gauntlet."""

    id: str
    title: str
    question: str
    design: str                          # "event_study" | "did" | "panel" | ...
    constructs: list[Construct] | tuple[Construct, ...] = field(default_factory=list)
    plausible_effect: float | None = None
    effect_units: str | None = None
    lineage: list[dict[str, Any]] = field(default_factory=list)
    rescope_count: int = 0
    status: str = "TRIAGE"          # display only; never the lock
    freeze_id: str | None = None
    freeze_hash: str | None = None  # the actual outcome lock
    created_at: str = field(default_factory=utcnow)

    # What the freeze hash actually covers. Changing any of these below the
    # lock makes the in-memory candidate describe a different study from the
    # manifest that supposedly fixes it. `status`, `lineage` and `rescope_count`
    # stay mutable: they are bookkeeping, not science.
    SEALED_FIELDS = ("question", "design", "title", "constructs",
                     "plausible_effect", "effect_units")

    def __setattr__(self, name: str, value: Any) -> None:
        """Frozen means immutable, not merely unclearable.

        The write-once guard on `freeze_hash` stopped the lock being deleted but
        left everything it locks writable: `candidate.question = "..."` after
        freezing succeeded, so the hash could no longer be cleared while the
        design it described drifted freely around it. A lock on the marker is
        not a lock on the thing.
        """
        if name in ("freeze_hash", "freeze_id"):
            current = getattr(self, name, None)
            if current is not None and value != current:
                raise FrozenDesignViolation(
                    f"{self.id} is frozen; {name} cannot be changed or cleared "
                    f"(is {current!r}, tried {value!r})"
                )
        elif name in self.SEALED_FIELDS and self.is_frozen():
            raise FrozenDesignViolation(
                f"{self.id} is frozen under {self.freeze_id}; {name} cannot be "
                "changed. A legitimate change is a POST_FREEZE_CHANGE on a new "
                "lineage, which reclassifies the affected analysis as exploratory."
            )
        object.__setattr__(self, name, value)

    def seal(self) -> None:
        """Recursively seal the scientific object graph.

        Guarding assignment is insufficient when the assigned value is a
        mutable container: `candidate.constructs.append(...)` never calls
        Candidate.__setattr__.  Freeze converts the collection and each
        construct's concept collection to tuples so no in-place mutation path
        remains.
        """
        for con in self.constructs:
            con.seal()
        object.__setattr__(self, "constructs", tuple(self.constructs))

    def is_frozen(self) -> bool:
        """The lock. Not `status`, which is a mutable label."""
        return self.freeze_hash is not None

    def primary_constructs(self) -> list[Construct]:
        return [c for c in self.constructs if c.is_primary()]

    def log(self, event: str, **detail: Any) -> None:
        self.lineage.append({"at": utcnow(), "event": event, **detail})

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class GateResult:
    gate: GateId
    candidate_id: str
    verdict: Verdict
    reason: str
    evidence: dict[str, Any] = field(default_factory=dict)
    at: str = field(default_factory=utcnow)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["gate"] = self.gate.value
        d["verdict"] = self.verdict.value
        return d
