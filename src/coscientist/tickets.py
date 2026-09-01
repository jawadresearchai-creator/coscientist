"""The judgment queue.

The mechanism that makes Claude and Codex optional. The deterministic core
never calls an LLM inline; it writes a ticket and carries on with the ticket's
declared default. A ticket is drained only when quota exists and policy allows.

Consequence: there is no such thing as "quota died mid-execution". An LLM is
never in the middle of anything, only ever at a boundary answering a question
that was already written down.

Exactly one ticket type blocks: the pre-freeze audit. Freezing an unaudited
design is worse than waiting, because everything after the outcome lock
inherits that design and cannot be repaired without losing confirmatory
status.
"""
from __future__ import annotations

import json
import os
import uuid
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any

import yaml

from .models import utcnow


class Agent(str, Enum):
    CLAUDE = "claude"      # audits science  -> ranked findings
    CODEX = "codex"        # audits engineering -> failing tests


class TicketType(str, Enum):
    NOVELTY_VERDICT = "novelty_verdict"
    PRE_FREEZE_AUDIT = "pre_freeze_audit"
    MANUSCRIPT_PROSE = "manuscript_prose"
    REVIEWER_COURT = "reviewer_court"
    BUILD_REPAIR = "build_repair"
    CANDIDATE_GENERATION = "candidate_generation"


class TicketState(str, Enum):
    OPEN = "OPEN"
    PARTIAL = "PARTIAL"     # agent started, quota died; safe to resume
    ANSWERED = "ANSWERED"
    DEFAULTED = "DEFAULTED"  # never answered; declared default applied


BLOCKING: set[TicketType] = {TicketType.PRE_FREEZE_AUDIT}


@dataclass
class Ticket:
    id: str
    type: TicketType
    agent: Agent
    subject: str
    question: str
    default_action: str
    blocking: bool = False
    context: dict[str, Any] = field(default_factory=dict)
    state: TicketState = TicketState.OPEN
    answer: dict[str, Any] | None = None
    est_cost: str | None = None
    created_at: str = field(default_factory=utcnow)
    updated_at: str = field(default_factory=utcnow)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        for k in ("type", "agent", "state"):
            d[k] = getattr(self, k).value
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Ticket":
        return cls(
            id=d["id"],
            type=TicketType(d["type"]),
            agent=Agent(d["agent"]),
            subject=d["subject"],
            question=d["question"],
            default_action=d["default_action"],
            blocking=d.get("blocking", False),
            context=d.get("context", {}),
            state=TicketState(d.get("state", "OPEN")),
            answer=d.get("answer"),
            est_cost=d.get("est_cost"),
            created_at=d.get("created_at", utcnow()),
            updated_at=d.get("updated_at", utcnow()),
        )


class Policy:
    """Standing approval policy: auto / ask / never, per ticket type.

    Set once, so quota spending is governed by a policy rather than by forty
    interruptions. The slate approval remains a separate, scientific gate.
    """

    VALID = {"auto", "ask", "never"}

    def __init__(self, rules: dict[str, str], default: str = "ask") -> None:
        bad = {k: v for k, v in rules.items() if v not in self.VALID}
        if bad:
            raise ValueError(f"invalid policy values: {bad}")
        if default not in self.VALID:
            raise ValueError(f"invalid default policy {default!r}")
        self.rules = rules
        self.default = default

    @classmethod
    def load(cls, path: str) -> "Policy":
        with open(path, "r", encoding="utf-8") as fh:
            raw = yaml.safe_load(fh) or {}
        return cls(rules=raw.get("tickets", {}) or {}, default=raw.get("default", "ask"))

    def decide(self, ticket: Ticket) -> str:
        return self.rules.get(ticket.type.value, self.default)


class TicketQueue:
    """A directory of JSON tickets. Deliberately boring and inspectable."""

    def __init__(self, root: str) -> None:
        self.root = root
        os.makedirs(root, exist_ok=True)

    def _path(self, ticket_id: str) -> str:
        return os.path.join(self.root, f"{ticket_id}.json")

    def emit(
        self,
        *,
        type: TicketType,
        agent: Agent,
        subject: str,
        question: str,
        default_action: str,
        context: dict[str, Any] | None = None,
        est_cost: str | None = None,
    ) -> Ticket:
        ticket = Ticket(
            id=f"{type.value}-{uuid.uuid4().hex[:10]}",
            type=type,
            agent=agent,
            subject=subject,
            question=question,
            default_action=default_action,
            blocking=type in BLOCKING,
            context=context or {},
            est_cost=est_cost,
        )
        self._write(ticket)
        return ticket

    def _write(self, ticket: Ticket) -> None:
        ticket.updated_at = utcnow()
        tmp = self._path(ticket.id) + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(ticket.to_dict(), fh, indent=2, sort_keys=True)
        os.replace(tmp, self._path(ticket.id))   # atomic: a ticket is never half-written

    def all(self) -> list[Ticket]:
        out = []
        for fn in sorted(os.listdir(self.root)):
            if not fn.endswith(".json"):
                continue
            with open(os.path.join(self.root, fn), "r", encoding="utf-8") as fh:
                out.append(Ticket.from_dict(json.load(fh)))
        return out

    def open_tickets(self) -> list[Ticket]:
        return [t for t in self.all() if t.state in (TicketState.OPEN, TicketState.PARTIAL)]

    def blocking_open(self) -> list[Ticket]:
        return [t for t in self.open_tickets() if t.blocking]

    def answer(self, ticket_id: str, answer: dict[str, Any]) -> Ticket:
        t = self.get(ticket_id)
        t.answer = answer
        t.state = TicketState.ANSWERED
        self._write(t)
        return t

    def mark_partial(self, ticket_id: str, progress: dict[str, Any]) -> Ticket:
        """Quota died while the agent was working. Resume, do not restart."""
        t = self.get(ticket_id)
        t.state = TicketState.PARTIAL
        t.context = {**t.context, "partial_progress": progress}
        self._write(t)
        return t

    def apply_default(self, ticket_id: str) -> Ticket:
        t = self.get(ticket_id)
        if t.blocking:
            raise RuntimeError(
                f"{t.id} is a blocking ticket ({t.type.value}); it has no safe default"
            )
        t.state = TicketState.DEFAULTED
        self._write(t)
        return t

    def get(self, ticket_id: str) -> Ticket:
        with open(self._path(ticket_id), "r", encoding="utf-8") as fh:
            return Ticket.from_dict(json.load(fh))

    def digest(self, policy: Policy) -> dict[str, list[dict[str, Any]]]:
        """What to show the scientist at a checkpoint, grouped by policy."""
        out: dict[str, list[dict[str, Any]]] = {"auto": [], "ask": [], "never": []}
        for t in self.open_tickets():
            out[policy.decide(t)].append(
                {
                    "id": t.id,
                    "type": t.type.value,
                    "agent": t.agent.value,
                    "subject": t.subject,
                    "blocking": t.blocking,
                    "est_cost": t.est_cost,
                    "if_skipped": t.default_action,
                }
            )
        return out
