"""Canonical single-paper scientific state for Management Science research.

Exactly one paper may be scientifically active. Once a topic is admitted,
broad discovery is locked until that paper is SUBMISSION_READY or RETIRED for
a genuine blocker. Ordinary execution/data friction is repair work inside the
active paper, not a reason to start another topic.

Historical candidate/slate state is intentionally not part of this object.
"""
from __future__ import annotations

import argparse
import json
import os
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any

from .models import utcnow

STATE_FILENAME = "single_paper.json"
OPERATING_MODE = "SINGLE_PAPER"
STATE_SCHEMA_VERSION = 1


class SinglePaperError(RuntimeError):
    pass


class PaperStage(str, Enum):
    SELECTED = "SELECTED"
    DEVELOPING = "DEVELOPING"
    DATA_FEASIBLE = "DATA_FEASIBLE"
    DESIGN_READY = "DESIGN_READY"
    FROZEN = "FROZEN"
    ANALYZING = "ANALYZING"
    RESULTS_COMPLETE = "RESULTS_COMPLETE"
    MANUSCRIPT = "MANUSCRIPT"
    FINAL_AUDIT = "FINAL_AUDIT"
    SUBMISSION_READY = "SUBMISSION_READY"
    RETIRED = "RETIRED"


STAGE_ORDER = [
    PaperStage.SELECTED,
    PaperStage.DEVELOPING,
    PaperStage.DATA_FEASIBLE,
    PaperStage.DESIGN_READY,
    PaperStage.FROZEN,
    PaperStage.ANALYZING,
    PaperStage.RESULTS_COMPLETE,
    PaperStage.MANUSCRIPT,
    PaperStage.FINAL_AUDIT,
    PaperStage.SUBMISSION_READY,
]
TERMINAL_STAGES = {PaperStage.SUBMISSION_READY, PaperStage.RETIRED}
PRE_FREEZE_STAGES = {
    PaperStage.SELECTED,
    PaperStage.DEVELOPING,
    PaperStage.DATA_FEASIBLE,
    PaperStage.DESIGN_READY,
}


class GenuineBlocker(str, Enum):
    DIRECT_SCOOP = "DIRECT_SCOOP"
    ESSENTIAL_DATA_IMPOSSIBLE = "ESSENTIAL_DATA_IMPOSSIBLE"
    IDENTIFICATION_IMPOSSIBLE = "IDENTIFICATION_IMPOSSIBLE"
    FUNDAMENTAL_POWER_FAILURE = "FUNDAMENTAL_POWER_FAILURE"
    LEGAL_ETHICAL_IMPOSSIBLE = "LEGAL_ETHICAL_IMPOSSIBLE"
    FUNDAMENTAL_CONSTRUCT_FAILURE = "FUNDAMENTAL_CONSTRUCT_FAILURE"


@dataclass
class TopicCharter:
    paper_id: str
    working_title: str
    research_question: str
    phenomenon: str = ""
    mechanism: str = ""
    contribution: str = ""
    unit_of_analysis: str = ""
    intended_design: str = ""
    primary_outcome: str = ""
    primary_exposure: str = ""
    required_constructs: list[str] = field(default_factory=list)
    target_journal_family: str = ""
    known_threats: list[str] = field(default_factory=list)
    admitted_at: str = field(default_factory=utcnow)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "TopicCharter":
        allowed = cls.__dataclass_fields__
        return cls(**{k: v for k, v in raw.items() if k in allowed})


@dataclass
class SinglePaperState:
    path: str
    active_paper: TopicCharter | None = None
    stage: PaperStage | None = None
    evolution_count: int = 0
    current_problem: dict[str, Any] | None = None
    history: list[dict[str, Any]] = field(default_factory=list)
    schema_version: int = STATE_SCHEMA_VERSION
    operating_mode: str = OPERATING_MODE
    updated_at: str = field(default_factory=utcnow)

    @classmethod
    def load(cls, path: str) -> "SinglePaperState":
        if not os.path.exists(path):
            return cls(path=path)
        with open(path, "r", encoding="utf-8") as fh:
            raw = json.load(fh)
        mode = raw.get("operating_mode", OPERATING_MODE)
        if mode != OPERATING_MODE:
            raise SinglePaperError(
                f"{path} declares operating_mode={mode!r}; only {OPERATING_MODE} is accepted"
            )
        active_raw = raw.get("active_paper")
        active = TopicCharter.from_dict(active_raw) if isinstance(active_raw, dict) else None
        stage_raw = raw.get("stage")
        stage = PaperStage(stage_raw) if stage_raw else None
        # Legacy candidate/slate/portfolio fields are deliberately ignored.
        return cls(
            path=path,
            active_paper=active,
            stage=stage,
            evolution_count=int(raw.get("evolution_count", 0)),
            current_problem=raw.get("current_problem"),
            history=list(raw.get("history", [])),
            schema_version=int(raw.get("schema_version", STATE_SCHEMA_VERSION)),
            operating_mode=OPERATING_MODE,
            updated_at=raw.get("updated_at", utcnow()),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "operating_mode": OPERATING_MODE,
            "active_paper": self.active_paper.to_dict() if self.active_paper else None,
            "stage": self.stage.value if self.stage else None,
            "evolution_count": self.evolution_count,
            "current_problem": self.current_problem,
            "history": self.history,
            "updated_at": self.updated_at,
        }

    def save(self) -> None:
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        self.updated_at = utcnow()
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(self.to_dict(), fh, indent=2, sort_keys=True)
        os.replace(tmp, self.path)

    @property
    def discovery_allowed(self) -> bool:
        return self.active_paper is None or self.stage in TERMINAL_STAGES

    def assert_discovery_allowed(self) -> None:
        if not self.discovery_allowed:
            raise SinglePaperError(
                f"topic discovery is locked while {self.active_paper.paper_id} is active "
                f"at {self.stage.value}; strengthen the active paper instead"
            )

    def admit(self, charter: TopicCharter) -> None:
        self.assert_discovery_allowed()
        self.active_paper = charter
        self.stage = PaperStage.SELECTED
        self.evolution_count = 0
        self.current_problem = None
        # New papers do not inherit historical candidate genealogy/court state.
        self.history = [{"at": utcnow(), "event": "ADMITTED", "paper_id": charter.paper_id}]
        self.save()

    def transition(self, new_stage: PaperStage | str) -> None:
        if not self.active_paper or not self.stage:
            raise SinglePaperError("no active paper")
        target = PaperStage(new_stage)
        if target is PaperStage.RETIRED:
            raise SinglePaperError("use retire() with a genuine blocker")
        if self.stage is PaperStage.RETIRED:
            raise SinglePaperError("a retired paper cannot be reactivated; admit a new paper")
        if self.stage is PaperStage.SUBMISSION_READY:
            if target is PaperStage.SUBMISSION_READY:
                return
            raise SinglePaperError("submission-ready is terminal for this paper")
        current_i = STAGE_ORDER.index(self.stage)
        target_i = STAGE_ORDER.index(target)
        if target_i < current_i:
            raise SinglePaperError(
                f"stage cannot move backwards from {self.stage.value} to {target.value}"
            )
        if target_i > current_i + 1:
            raise SinglePaperError(
                f"stage cannot skip from {self.stage.value} to {target.value}"
            )
        if target is self.stage:
            return
        self.stage = target
        self.current_problem = None
        self.history.append({"at": utcnow(), "event": "STAGE", "stage": target.value})
        self.save()

    def evolve(self, note: str) -> None:
        if not self.active_paper or self.stage not in PRE_FREEZE_STAGES:
            raise SinglePaperError("scientific evolution is allowed only on the active paper pre-freeze")
        self.evolution_count += 1
        self.history.append({
            "at": utcnow(), "event": "EVOLVE", "count": self.evolution_count, "note": note
        })
        self.save()

    def record_problem(self, code: str, detail: str) -> None:
        if not self.active_paper:
            raise SinglePaperError("no active paper")
        self.current_problem = {
            "code": code,
            "detail": detail,
            "at": utcnow(),
            "classification": "REPAIR",
        }
        self.history.append({"at": utcnow(), "event": "PROBLEM", **self.current_problem})
        self.save()

    def clear_problem(self, note: str = "") -> None:
        if self.current_problem:
            self.history.append({"at": utcnow(), "event": "PROBLEM_CLEARED", "note": note})
        self.current_problem = None
        self.save()

    def retire(self, blocker: GenuineBlocker | str, detail: str) -> None:
        if not self.active_paper:
            raise SinglePaperError("no active paper")
        try:
            blocker = GenuineBlocker(blocker)
        except ValueError as exc:
            allowed = ", ".join(b.value for b in GenuineBlocker)
            raise SinglePaperError(
                f"{blocker!r} is not a genuine retirement blocker. Repair operational "
                f"problems inside the active topic. Allowed: {allowed}"
            ) from exc
        self.stage = PaperStage.RETIRED
        self.current_problem = {
            "code": blocker.value,
            "detail": detail,
            "at": utcnow(),
            "classification": "GENUINE_BLOCKER",
        }
        self.history.append({"at": utcnow(), "event": "RETIRED", **self.current_problem})
        self.save()

    def next_action(self) -> str:
        if self.active_paper is None:
            return "DISCOVERY: select one strong topic, admit it, then stop broad topic search"
        if self.stage is PaperStage.RETIRED:
            return "DISCOVERY: prior paper is genuinely retired; a new topic may be selected"
        if self.stage is PaperStage.SUBMISSION_READY:
            return "COMPLETE: current paper is submission-ready"
        if self.current_problem:
            return f"REPAIR: {self.current_problem['code']} — keep working on the active paper"
        actions = {
            PaperStage.SELECTED: "DEVELOP: close literature gap and sharpen theory/mechanism",
            PaperStage.DEVELOPING: "DATA FEASIBILITY: map constructs lake-first and build outcome-blind sample",
            PaperStage.DATA_FEASIBLE: "DESIGN: close measurement, identification and power before freeze",
            PaperStage.DESIGN_READY: "PRE-FREEZE AUDIT: hostile review; freeze only after feasibility is proven",
            PaperStage.FROZEN: "ANALYSIS LOCK: lock code before outcome access, then analyse",
            PaperStage.ANALYZING: "ANALYSE: execute confirmatory models and declared robustness/falsification",
            PaperStage.RESULTS_COMPLETE: "MANUSCRIPT: interpret verified results and draft from the results bridge",
            PaperStage.MANUSCRIPT: "FINAL AUDIT: numeric, citation, claim, reproducibility and journal checks",
            PaperStage.FINAL_AUDIT: "SUBMISSION: resolve audit findings and mark submission-ready",
        }
        return actions[self.stage]


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m coscientist.single_paper")
    p.add_argument("--state", default="state/single_paper.json")
    sub = p.add_subparsers(dest="command", required=True)
    sub.add_parser("init")
    sub.add_parser("status")
    sub.add_parser("next-action")
    a = sub.add_parser("admit")
    a.add_argument("--charter", required=True)
    t = sub.add_parser("transition")
    t.add_argument("--stage", choices=[s.value for s in PaperStage if s is not PaperStage.RETIRED], required=True)
    e = sub.add_parser("evolve")
    e.add_argument("--note", required=True)
    pr = sub.add_parser("problem")
    pr.add_argument("--code", required=True)
    pr.add_argument("--detail", required=True)
    r = sub.add_parser("retire")
    r.add_argument("--blocker", choices=[b.value for b in GenuineBlocker], required=True)
    r.add_argument("--detail", required=True)
    return p


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    state = SinglePaperState.load(args.state)
    if args.command == "init":
        state.save()
    elif args.command == "status":
        print(f"operating mode  {OPERATING_MODE}")
        print(f"active paper    {state.active_paper.paper_id if state.active_paper else '-'}")
        print(f"stage           {state.stage.value if state.stage else 'NO_ACTIVE_PAPER'}")
        print(f"evolutions      {state.evolution_count}")
        print(f"discovery       {'ALLOWED' if state.discovery_allowed else 'LOCKED'}")
        if state.current_problem:
            print(f"problem         {state.current_problem['code']}: {state.current_problem['detail']}")
    elif args.command == "next-action":
        print(state.next_action())
    elif args.command == "admit":
        with open(args.charter, "r", encoding="utf-8") as fh:
            state.admit(TopicCharter.from_dict(json.load(fh)))
    elif args.command == "transition":
        state.transition(args.stage)
    elif args.command == "evolve":
        state.evolve(args.note)
    elif args.command == "problem":
        state.record_problem(args.code, args.detail)
    elif args.command == "retire":
        state.retire(args.blocker, args.detail)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
