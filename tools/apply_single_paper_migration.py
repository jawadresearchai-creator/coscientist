#!/usr/bin/env python3
"""Apply the v4.3 single-paper operating-model migration.

This file is intentionally self-removing. It is executed once by the temporary
single-paper-migration workflow, runs the repository tests, and the workflow
commits the resulting tree without this migration helper.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8-sig")


def write(path: str, text: str) -> None:
    p = ROOT / path
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text.rstrip() + "\n", encoding="utf-8")


def replace_once(path: str, old: str, new: str) -> None:
    text = read(path)
    if old not in text:
        raise RuntimeError(f"expected text not found in {path}: {old[:100]!r}")
    if text.count(old) != 1:
        raise RuntimeError(f"expected one occurrence in {path}, found {text.count(old)}")
    write(path, text.replace(old, new, 1))


def replace_region(path: str, start: str, end: str, replacement: str) -> None:
    text = read(path)
    a = text.find(start)
    if a < 0:
        raise RuntimeError(f"start marker not found in {path}: {start!r}")
    b = text.find(end, a)
    if b < 0:
        raise RuntimeError(f"end marker not found in {path}: {end!r}")
    write(path, text[:a] + replacement.rstrip() + "\n\n" + text[b:])


SINGLE_PAPER = '''
"""Single-paper scientific state for the Management Science CoScientist.

The operating rule is deliberately stronger than a preference: exactly one
paper may be scientifically active. Once admitted, broad topic discovery is
locked until that paper is SUBMISSION_READY or RETIRED for a genuine blocker.
Ordinary friction is repaired inside the active topic instead of spawning a
candidate tournament.

Historical candidate/slate state is not loaded into this object. A terminal
paper may be replaced by a new admission, and that resets paper-level history
rather than carrying old candidate genealogy into the new study.
"""
from __future__ import annotations

import argparse
import json
import os
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any

from .models import utcnow

STATE_SCHEMA_VERSION = 1
STATE_FILENAME = "single_paper.json"
OPERATING_MODE = "SINGLE_PAPER"


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
        fields = cls.__dataclass_fields__
        return cls(**{k: v for k, v in raw.items() if k in fields})


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
        # Deliberately ignore legacy candidate/slate/portfolio fields. They are
        # archival evidence, not active scientific input under the v4.3 model.
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
                f"topic discovery is locked while {self.active_paper.paper_id} is "
                f"active at {self.stage.value}; strengthen the active paper instead"
            )

    def admit(self, charter: TopicCharter) -> None:
        self.assert_discovery_allowed()
        # A new paper never inherits candidate genealogy, novelty scores, old
        # court passes, or old recovery queues. Terminal history remains in the
        # old Drive artifact, not in the new active state object.
        self.active_paper = charter
        self.stage = PaperStage.SELECTED
        self.evolution_count = 0
        self.current_problem = None
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
            "code": code, "detail": detail, "at": utcnow(), "classification": "REPAIR"
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
            return "DISCOVERY: choose one strong topic, admit it, then stop broad candidate search"
        if self.stage is PaperStage.RETIRED:
            return "DISCOVERY: the prior paper is genuinely retired; a new topic may now be selected"
        if self.stage is PaperStage.SUBMISSION_READY:
            return "COMPLETE: current paper is submission-ready; a new topic may be admitted only when desired"
        if self.current_problem:
            return f"REPAIR: {self.current_problem['code']} — keep working on the active paper"
        actions = {
            PaperStage.SELECTED: "DEVELOP: close the literature gap and sharpen theory/mechanism",
            PaperStage.DEVELOPING: "DATA FEASIBILITY: map constructs to lake-first sources and build outcome-blind sample",
            PaperStage.DATA_FEASIBLE: "DESIGN: close measurement, identification and power before freeze",
            PaperStage.DESIGN_READY: "PRE-FREEZE AUDIT: hostile review; freeze only after feasibility is proven",
            PaperStage.FROZEN: "ANALYSIS LOCK: lock code before outcome access, then analyse",
            PaperStage.ANALYZING: "ANALYSE: run confirmatory models, robustness and falsification as declared",
            PaperStage.RESULTS_COMPLETE: "MANUSCRIPT: interpret verified results and draft from the results bridge",
            PaperStage.MANUSCRIPT: "FINAL AUDIT: numeric, citation, claim, reproducibility and journal checks",
            PaperStage.FINAL_AUDIT: "SUBMISSION: resolve all substantive audit findings and mark submission-ready",
        }
        return actions[self.stage]


def pull_from_drive(state: SinglePaperState, client: Any, folder_id: str,
                    name: str = STATE_FILENAME) -> bool:
    hits = [f for f in client.list_folder(folder_id) if f.get("name") == name]
    if len(hits) > 1:
        raise SinglePaperError(
            f"Drive state is ambiguous: {len(hits)} files named {name!r}. Exactly one is allowed."
        )
    if not hits:
        state.save()
        return False
    client.download(hits[0]["id"], state.path)
    return True


def push_to_drive(state: SinglePaperState, client: Any, folder_id: str,
                  name: str = STATE_FILENAME) -> str:
    state.save()
    return client.upsert_small_file(state.path, folder_id, name=name)


def _drive_client():
    from .drive import DriveClient, DriveCredentials
    return DriveClient(DriveCredentials.from_env())


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m coscientist.single_paper")
    p.add_argument("--state", default="state/single_paper.json")
    sub = p.add_subparsers(dest="command", required=True)
    sub.add_parser("init")
    sub.add_parser("status")
    sub.add_parser("next-action")

    a = sub.add_parser("admit")
    a.add_argument("--charter", required=True, help="JSON file containing TopicCharter fields")

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

    pull = sub.add_parser("drive-pull")
    pull.add_argument("--folder", required=True)
    push = sub.add_parser("drive-push")
    push.add_argument("--folder", required=True)
    return p


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    state = SinglePaperState.load(args.state)
    if args.command == "init":
        state.save()
        return 0
    if args.command == "status":
        print(f"operating mode  {OPERATING_MODE}")
        print(f"active paper    {state.active_paper.paper_id if state.active_paper else '-'}")
        print(f"stage           {state.stage.value if state.stage else 'NO_ACTIVE_PAPER'}")
        print(f"evolutions      {state.evolution_count}")
        print(f"discovery       {'ALLOWED' if state.discovery_allowed else 'LOCKED'}")
        if state.current_problem:
            print(f"problem         {state.current_problem['code']}: {state.current_problem['detail']}")
        return 0
    if args.command == "next-action":
        print(state.next_action())
        return 0
    if args.command == "admit":
        with open(args.charter, "r", encoding="utf-8") as fh:
            state.admit(TopicCharter.from_dict(json.load(fh)))
        return 0
    if args.command == "transition":
        state.transition(args.stage)
        return 0
    if args.command == "evolve":
        state.evolve(args.note)
        return 0
    if args.command == "problem":
        state.record_problem(args.code, args.detail)
        return 0
    if args.command == "retire":
        state.retire(args.blocker, args.detail)
        return 0
    if args.command == "drive-pull":
        found = pull_from_drive(state, _drive_client(), args.folder)
        print("loaded canonical single-paper state from Drive" if found else
              "no canonical state existed; initialized NO_ACTIVE_PAPER locally")
        return 0
    if args.command == "drive-push":
        file_id = push_to_drive(state, _drive_client(), args.folder)
        print(f"canonical single-paper state saved to Drive ({file_id})")
        return 0
    raise AssertionError(args.command)


if __name__ == "__main__":
    raise SystemExit(main())
'''


TEST_SINGLE_PAPER = '''
import json

import pytest

from coscientist.single_paper import (
    PaperStage, SinglePaperError, SinglePaperState, TopicCharter,
    pull_from_drive, push_to_drive,
)


def charter(pid="MS001"):
    return TopicCharter(
        paper_id=pid,
        working_title="Focused management-science study",
        research_question="Does X affect Y through M?",
        phenomenon="X",
        mechanism="M",
        contribution="C",
        unit_of_analysis="firm-quarter",
        intended_design="panel",
        primary_outcome="Y",
        primary_exposure="X",
    )


def advance_to_submission_ready(state):
    for stage in (
        PaperStage.DEVELOPING,
        PaperStage.DATA_FEASIBLE,
        PaperStage.DESIGN_READY,
        PaperStage.FROZEN,
        PaperStage.ANALYZING,
        PaperStage.RESULTS_COMPLETE,
        PaperStage.MANUSCRIPT,
        PaperStage.FINAL_AUDIT,
        PaperStage.SUBMISSION_READY,
    ):
        state.transition(stage)


def test_cannot_admit_second_active_paper(tmp_path):
    state = SinglePaperState.load(str(tmp_path / "state.json"))
    state.admit(charter("MS001"))
    with pytest.raises(SinglePaperError, match="discovery is locked"):
        state.admit(charter("MS002"))


def test_discovery_is_locked_while_paper_is_active(tmp_path):
    state = SinglePaperState.load(str(tmp_path / "state.json"))
    assert state.discovery_allowed
    state.admit(charter())
    assert not state.discovery_allowed
    with pytest.raises(SinglePaperError):
        state.assert_discovery_allowed()


def test_operational_problem_cannot_retire_active_paper(tmp_path):
    state = SinglePaperState.load(str(tmp_path / "state.json"))
    state.admit(charter())
    state.record_problem("API_DOWN", "temporary source outage")
    assert state.stage is PaperStage.SELECTED
    assert "REPAIR" in state.next_action()
    with pytest.raises(SinglePaperError, match="not a genuine retirement blocker"):
        state.retire("API_DOWN", "still down")


def test_submission_ready_reopens_discovery(tmp_path):
    state = SinglePaperState.load(str(tmp_path / "state.json"))
    state.admit(charter())
    advance_to_submission_ready(state)
    assert state.discovery_allowed
    state.admit(charter("MS002"))
    assert state.active_paper.paper_id == "MS002"
    assert state.stage is PaperStage.SELECTED


def test_new_admission_does_not_inherit_terminal_topic_history(tmp_path):
    state = SinglePaperState.load(str(tmp_path / "state.json"))
    state.admit(charter("MS001"))
    advance_to_submission_ready(state)
    assert len(state.history) > 1
    state.admit(charter("MS002"))
    assert state.history == [pytest.approx(state.history[0], abs=0)] if False else state.history
    assert len(state.history) == 1
    assert state.history[0]["event"] == "ADMITTED"
    assert state.history[0]["paper_id"] == "MS002"


def test_drive_pull_initialises_no_active_paper_when_missing(tmp_path):
    class Fake:
        def list_folder(self, folder):
            return []
    state = SinglePaperState.load(str(tmp_path / "state.json"))
    assert not pull_from_drive(state, Fake(), "folder")
    reloaded = SinglePaperState.load(state.path)
    assert reloaded.active_paper is None
    assert reloaded.discovery_allowed


def test_drive_push_uses_one_canonical_state_file(tmp_path):
    calls = []
    class Fake:
        def upsert_small_file(self, path, folder, name=None):
            calls.append((path, folder, name))
            return "drive-id"
    state = SinglePaperState.load(str(tmp_path / "state.json"))
    state.admit(charter())
    assert push_to_drive(state, Fake(), "folder") == "drive-id"
    assert calls == [(state.path, "folder", "single_paper.json")]
'''


README = '''
# CoScientist V4.3 — single-paper Management Science research engine

CoScientist is a deterministic research-governance and analysis kernel for one
Management Science paper at a time. The system may use reasoning models for
judgment and prose through explicit handoffs/tickets, but `src/coscientist/`
never calls an LLM directly.

## Operating rule

**Exactly one paper may be scientifically active.** Broad topic discovery is
allowed only when there is no active paper, or when the current paper is
`SUBMISSION_READY` or has been `RETIRED` for a genuine blocker. After admission,
all literature search, data work, theory, design, analysis and writing serve the
same paper. Ordinary execution problems trigger repair, not replacement.

Historical candidate/slate state is archival only. It does not seed, rank,
kill, or revive the active paper.

The canonical lifecycle is:

```text
NO_ACTIVE_PAPER
  -> SELECTED
  -> DEVELOPING
  -> DATA_FEASIBLE
  -> DESIGN_READY
  -> FROZEN
  -> ANALYZING
  -> RESULTS_COMPLETE
  -> MANUSCRIPT
  -> FINAL_AUDIT
  -> SUBMISSION_READY
```

A paper can become `RETIRED` only for a genuine scientific blocker: direct
scoop with no residual contribution, essential-data impossibility,
identification impossibility, fundamental power failure, legal/ethical
impossibility, or fundamental construct failure.

See `docs/SINGLE_PAPER_RESEARCH_HANDBOOK.md` for the complete operating model.

## Data rule: lake first

The Global Management Science data lake owns ingestion; CoScientist owns
scientific selection. For each required construct the order is:

1. research-ready lake mart;
2. curated lake object;
3. raw/query-layer lake route;
4. admissible official external source;
5. lake-bounded pre-freeze evolution;
6. genuine essential-data blocker.

Do not reacquire data already held adequately. Large/query-native sources are
materialized as the smallest useful extract and that extract is frozen.

## Two locks

The design freeze fixes the scientific question, estimand, sample, treatment,
outcome, controls, exclusions, windows, models, contrasts, multiplicity policy
and dataset hashes. The analysis lock then fixes the code, environment and
execution plan before confirmatory outcome access.

```text
Drive state -> analysis-lock verify -> frozen data -> freeze-verify
           -> R/Python/Stan driver -> streams -> results bridge
           -> strict numeric provenance -> Drive results
```

Results and scientific state live in Google Drive, not Git. Code and tests live
in GitHub.

## Single-paper state commands

```bash
python -m coscientist.single_paper init
python -m coscientist.single_paper status
python -m coscientist.single_paper next-action
python -m coscientist.single_paper drive-pull --folder "$GDRIVE_STATE_FOLDER_ID"
python -m coscientist.single_paper drive-push --folder "$GDRIVE_STATE_FOLDER_ID"
```

A topic is admitted from a JSON Topic Charter:

```bash
python -m coscientist.single_paper admit --charter topic_charter.json
```

After admission, discovery is mechanically locked until the paper reaches a
terminal state.

## Analysis commands

```bash
coscientist lake-sync       --out state/gms_lake_catalog.json
coscientist gms-query-plan  --catalog state/gms_lake_catalog.json ...
coscientist freeze-verify   --manifest state/freeze.json --data-dir data
coscientist analysis-lock   --freeze state/freeze.json
coscientist analysis-verify --lock state/analysis_lock.json --freeze state/freeze.json
coscientist collect         --streams results/streams --out results/manuscript
coscientist provenance      reports/manuscript.md --require-tokens
```

## Install and test

```bash
pip install -e ".[dev]"
pytest -q            # TEST_COUNT_PLACEHOLDER tests, no credentials needed for the core suite
```

The guarantee registry is executable. `GUARANTEES.yaml` maps documented
promises to real tests; `docs/GUARANTEE_INDEX.md` keeps every guarantee claimed
in documentation so the registry cannot silently become a parallel, stale
specification.

## Scientific boundaries retained

- Freeze only after outcome-blind feasibility is demonstrated.
- An engine/transport failure is `BLOCKED`/repair work, not scientific failure.
- Confirmatory and exploratory outputs remain separate.
- Frozen scientific state is immutable.
- Every reportable result is tokened and traceable to code/data/freeze.
- Figures are bound to result tokens.
- Strict numeric provenance has no undocumented escape hatch.
- Source accessibility and redistribution rights remain separate.
- BigQuery is dry-run/cost guarded.

The system is intentionally optimized for reproducible secondary/open-data
quantitative Management Science research. Study-type-specific extensions can be
added without weakening the one-active-paper rule.
'''


AGENTS = '''
# CoScientist V4.3 — agent contract

Read this before changing the system. This is the active constitution for
Codex, Claude, ChatGPT and any other reasoning/repair plane.

## Mission

CoScientist develops **one Management Science paper at a time** from focused
topic admission through data, design, confirmatory analysis, manuscript, audit
and submission readiness.

It does **not** run a permanent candidate tournament. Once a topic is admitted,
broad discovery stops. The active question is evolved and repaired until it is
submission-ready or a genuine blocker makes completion scientifically
impossible.

Historical candidate IDs, rankings, novelty scores, court outcomes and
continuation handoffs are archival evidence only. They are not inputs to active
selection or rejection.

## One-paper rule

The scientific state is `state/single_paper.json` in the canonical Drive state
folder. Exactly one paper may be active.

Discovery is allowed only when:

1. no paper is active;
2. the current paper is `SUBMISSION_READY`; or
3. the current paper is `RETIRED` for an enumerated genuine blocker.

Do not create reserve candidates, parallel papers, leaderboards or replacement
topics while the active paper is viable.

## Genuine blockers

Retirement is permitted only for:

- direct scoop with no defensible residual contribution;
- essential-data impossibility after lake/external repair paths are exhausted;
- identification impossibility for the intended core claim;
- fundamental power failure that honest redesign cannot repair;
- legal/ethical impossibility;
- fundamental construct/measurement failure.

API outages, transfer errors, a missing optional control, one failed model,
non-significance, a nearby paper, or an arbitrary novelty score are repair
problems, not retirement grounds.

## Evolve first

Pre-freeze evolution has no arbitrary count budget. Improve measurement, data,
mechanism, identification or design as needed while preserving focus on the
same paper. A substantive evolution re-runs the affected scientific checks; it
does not reopen broad candidate discovery.

Below the outcome lock, the design is immutable. A post-freeze change is never
silently folded into the confirmatory claim.

## Data order

The lake owns ingestion. CoScientist owns scientific selection.

For every construct use:

```text
research mart -> curated lake -> raw/query route -> admissible official external
              -> defensible pre-freeze evolution -> genuine data blocker
```

Never query BigQuery blind. Never treat a credentials list as a census of
sources. Access rights and redistribution rights are separate.

## Outcome boundary

Freeze only after outcome-blind feasibility has established schema, joins,
coverage, essential-variable availability, sample construction and plausible
power. The design freeze and analysis lock remain one-way doors.

## Quota boundary

Nothing in `src/coscientist/` calls an LLM. Judgment can leave the deterministic
core as a ticket. Codex remains the engineering repair plane; scientific
judgment/reviewer work may be performed by a reasoning model, but changes must
return through reproducible state/code rather than patching results directly.

## Manuscript integrity

The results bridge is authoritative for empirical numbers. Strict provenance
must pass for confirmatory manuscript output. Figures must be bound to result
tokens. Citation/claim auditing is a manuscript-stage judgment workflow; do not
invent references or evidence.

## GitHub / Drive boundary

GitHub contains code, tests, workflows and registries. Google Drive contains
scientific state, frozen data identities, research outputs and manuscripts.
Workflows must never commit unsubmitted research state/results to Git.

## Engineering rule

Every claimed deterministic guarantee belongs in `GUARANTEES.yaml` and names a
real test. `docs/GUARANTEE_INDEX.md` is the registry-to-documentation index.
Run the entire suite after any governance change. Green tests mean the encoded
contracts hold; they do not replace scientific review.

## Canonical handbook

`docs/SINGLE_PAPER_RESEARCH_HANDBOOK.md` is the human-readable research process.
If an older document conflicts with this contract, the v4.3 single-paper rules
win for active work.
'''


HANDBOOK = '''
# Management Science CoScientist — Single-Paper Research Handbook

Version 4.3.0

## 1. Core operating rule

The CoScientist works on one paper only. It selects one topic, stops broad
candidate discovery, and focuses all subsequent research activity on making
that paper scientifically stronger and submission-ready. A new topic can be
selected only after the current paper is submission-ready or genuinely
unrecoverable.

There is no multi-paper portfolio, reserve queue, candidate leaderboard or
continuous replacement search.

## 2. What happens when a paper has a problem

The default response is **repair and evolve**, not replace.

- Missing variable -> inspect research marts, curated/raw lake objects, then
  official public sources.
- Weak measurement -> improve operationalization or validated proxy.
- Identification concern -> redesign the identification strategy pre-freeze.
- Power concern -> improve sample/treatment geometry or reassess the estimand.
- Nearby literature -> sharpen the residual contribution.
- Transport/API failure -> repair infrastructure; do not make a scientific
  rejection from an execution failure.
- Null result after a valid freeze -> report honestly; do not reopen the topic
  search or specification-shop.

## 3. Genuine blockers

Retire only when reasonable repair cannot overcome one of these:

1. direct scientific displacement leaving no meaningful residual contribution;
2. essential data/measurement impossibility;
3. identification impossibility for the intended core claim;
4. fundamental power impossibility;
5. legal or ethical impossibility;
6. fundamental construct failure.

No arbitrary novelty score is a kill threshold.

## 4. Topic selection

Topic selection runs only when no viable active paper exists.

1. Inspect the current data lake and current literature.
2. Generate a **small bounded shortlist** (normally no more than three serious
   questions).
3. Compare importance, mechanism, residual novelty, identification, data,
   measurement, power and journal relevance.
4. Select one.
5. Write a Topic Charter.
6. Stop broad discovery.

After selection, literature searches serve the active paper rather than hunt
for replacements.

## 5. Literature process

Use OpenAlex/lake marts for the broad literature universe, topic trajectories,
citation relationships and bibliometric context. Use current scholarly/public
web sources for the closest-paper audit, latest online-first work, exact DOI and
substantive reading.

Formal novelty closure occurs at two important points:

- pre-freeze: enough residual contribution exists to justify confirmatory work;
- pre-submission: check whether new literature materially displaced the claim.

Do not rerun a novelty tournament every cycle.

## 6. Theory and mechanism

Specify the chain:

```text
exposure/treatment -> mechanism -> intermediate consequence -> outcome
```

Identify the strongest rival explanation and a falsification/negative-control
logic where feasible. Prefer one coherent theoretical account to decorative
multi-theory stacking.

## 7. Data: lake first

For each required construct:

1. `03_RESEARCH` research-ready mart;
2. `02_CURATED` normalized/curated object;
3. `01_RAW_IMMUTABLE` or query-layer materialization;
4. admissible official external source;
5. defensible pre-freeze evolution;
6. genuine blocker.

External acquisition fills a defined gap. It does not duplicate data already
held adequately. Acquire/materialize the smallest sufficient slice rather than
using arbitrary file-size rules.

OpenAlex, SEC/company-research bridges, macro, finance, labor, trade,
governance/procurement and event/shock data should be drawn from the lake when
fit-for-purpose; live official sources are used for missing latest periods,
source verification, event timing and fields not yet mirrored.

## 8. Outcome-blind feasibility before freeze

Before freezing, prove that the planned study can actually be executed without
using confirmatory outcome patterns to choose the design:

- source access/licence;
- schema and joins;
- granularity and coverage;
- sample construction;
- treatment/support variation;
- essential-variable completeness;
- missingness/attrition expectations;
- pre-period noise/dependence;
- realistic power/MDE for the intended estimator.

Do not freeze a design whose required source route or treatment construction is
still hypothetical.

## 9. Hostile pre-freeze review

Use one consolidated review, not a stack of court documents. Ask:

- Is the residual contribution defensible against the closest literature?
- Is the mechanism coherent?
- Are constructs measured credibly?
- What is the strongest alternative explanation?
- Can the exact sample/data be built?
- Is plausible power adequate?
- Are access/licensing/ethics valid?

Verdicts: `PASS`, `REPAIR`, or `GENUINE_BLOCKER`.

## 10. Freeze and analysis lock

After feasibility passes, freeze the question, estimand, sample, treatment,
outcome, controls, exclusions, window, primary models/contrasts, multiplicity
policy and exact dataset hashes.

Then lock analysis code/environment **before** confirmatory outcome access.
Post-freeze changes cannot silently rewrite the confirmatory study.

## 11. Data collection and quality audit

Fetch only frozen objects/extracts. Re-hash them. Build the analysis table with
recorded joins/exclusions and produce at minimum:

- variable dictionary;
- sample flow;
- missingness/merge audit;
- descriptive/support diagnostics;
- reproducible source/version manifest.

## 12. Analysis

Run primary confirmatory models first. Report effects and uncertainty, not a
binary significant/not-significant narrative. Predeclared robustness and
falsification answer specific threats. Exploratory analyses remain explicitly
exploratory.

A valid null finding is a result; it does not trigger topic replacement.

## 13. Manuscript

Write the manuscript from verified evidence and the results bridge. A typical
archival Management Science structure is:

1. Introduction
2. Context where required
3. Theory/hypotheses where appropriate
4. Data/measurement
5. Empirical design
6. Results
7. Mechanisms/heterogeneity
8. Robustness/falsification
9. Discussion/implications
10. Limitations
11. Conclusion

Adapt to the target journal rather than forcing one rigid template.

## 14. Final audit

Before submission perform:

- scientific logic audit from phenomenon -> contribution;
- freeze/deviation audit;
- numeric provenance audit;
- citation existence and claim-support audit;
- figure/table consistency audit;
- causal-language/claim-strength audit;
- replication/reproducibility audit;
- current journal compliance audit.

Every important number comes from the results bundle; every important citation
must be real and support the proposition for which it is cited.

## 15. Minimal human-readable artifact set

Keep the workflow simple. One paper should normally expose only:

1. Topic Charter
2. Literature/Theory record
3. Data and Design Plan
4. Freeze + Analysis Lock
5. Results Bundle
6. Canonical Manuscript
7. Final Audit + Replication Package

Machine manifests may be more numerous internally; humans should not need to
navigate a forest of candidate/court documents.

## 16. Historical-state rule

Old candidate IDs, candidate rankings, novelty scores, winner selections and
continuation handoffs are **not active inputs**. Keep old records only as
archives/evidence. The active system starts from current lake state, current
literature and the single current Topic Charter.

## 17. One-sentence mission

The Management Science CoScientist selects one strong research question, stays
focused on it, uses the data lake and authoritative public sources to obtain the
minimum sufficient reproducible evidence, evolves the study rather than
continually replacing it, freezes only after outcome-blind feasibility is
proven, executes reproducible analysis, produces a provenance-backed manuscript
and begins no new paper until the current one is submission-ready or genuinely
impossible to complete.
'''


RELEASE = '''
# CoScientist v4.3.0 — Single-Paper Operating Model

This release changes the research-orchestration contract while preserving the
v4 reproducibility kernel.

## Scientific operating changes

- Exactly one paper may be active.
- Broad topic discovery locks immediately after admission.
- Historical candidate/slate state is ignored for active scientific decisions.
- Ordinary execution/data friction is repair work, not a reason to start a new
  topic.
- Retirement requires an enumerated genuine scientific blocker.
- Pre-freeze topic evolution has no arbitrary rescope-count budget.
- G3 is lake-first: existing research/curated holdings are preferred before
  external reacquisition.
- The legacy `candidate_generation` policy is explicitly disabled.

## Infrastructure changes

- Adds `coscientist.single_paper` with a canonical state machine and Drive
  pull/push commands.
- Adds small-file Drive upsert support for the canonical state JSON.
- Rewrites the scheduled cycle as a single-paper heartbeat rather than a
  candidate gauntlet.
- Adds an executable handbook and guarantee coverage for the new rules.

The design freeze, analysis lock, strict numeric provenance, figure/result
binding, confirmatory/exploratory separation and GitHub/Drive boundary are
unchanged.
'''


CYCLE = '''
# Single-paper deterministic heartbeat.
#
# This workflow never generates competing candidates. It synchronizes the GMS
# lake view, loads exactly one canonical paper-state file from Drive, reports
# the next action for that paper, and preserves the existing judgment/audit
# boundary. Broad discovery is allowed only when the state machine says there
# is no viable active paper.

name: cycle

on:
  schedule:
    - cron: "0 */6 * * *"
  workflow_dispatch:

permissions:
  contents: write

concurrency:
  group: coscientist-single-paper-cycle
  cancel-in-progress: false

jobs:
  run:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-python@v5
        with:
          python-version: "3.11"
          cache: pip

      - name: Install
        run: pip install -e ".[dev]"

      - name: Tests
        run: pytest -q

      - name: Refresh GMS lake catalog
        env:
          GDRIVE_CLIENT_ID: ${{ secrets.GDRIVE_CLIENT_ID }}
          GDRIVE_CLIENT_SECRET: ${{ secrets.GDRIVE_CLIENT_SECRET }}
          GDRIVE_TOKEN: ${{ secrets.GDRIVE_TOKEN }}
          GMSDL_MANIFEST_FOLDER_ID: ${{ secrets.GMSDL_MANIFEST_FOLDER_ID }}
          GMSDL_GITHUB_REPO: ${{ vars.GMSDL_GITHUB_REPO }}
          GMSDL_GITHUB_TOKEN: ${{ secrets.GMSDL_GITHUB_TOKEN }}
        run: |
          coscientist lake-sync --out state/gms_lake_catalog.json
          coscientist lake-doctor --catalog state/gms_lake_catalog.json

      - name: Pull canonical single-paper state
        env:
          GDRIVE_CLIENT_ID: ${{ secrets.GDRIVE_CLIENT_ID }}
          GDRIVE_CLIENT_SECRET: ${{ secrets.GDRIVE_CLIENT_SECRET }}
          GDRIVE_TOKEN: ${{ secrets.GDRIVE_TOKEN }}
          GDRIVE_STATE_FOLDER_ID: ${{ secrets.GDRIVE_STATE_FOLDER_ID }}
        run: |
          python -m coscientist.single_paper --state state/single_paper.json \
            drive-pull --folder "$GDRIVE_STATE_FOLDER_ID"

      - name: Single-paper status
        run: |
          python -m coscientist.single_paper --state state/single_paper.json status
          echo
          python -m coscientist.single_paper --state state/single_paper.json next-action

      - name: Judgment queue digest
        run: coscientist tickets

      - name: Check for blocking pre-freeze audit tickets
        id: blockcheck
        run: |
          if coscientist tickets | grep -q "^BLOCKING"; then
            echo "blocked=true" >> "$GITHUB_OUTPUT"
            echo "::warning::Current paper is waiting on a pre-freeze audit."
          else
            echo "blocked=false" >> "$GITHUB_OUTPUT"
          fi

      - name: Enforce focused next action
        if: steps.blockcheck.outputs.blocked != 'true'
        run: |
          python -m coscientist.single_paper --state state/single_paper.json next-action
          echo "No competing topic generation is run by this cycle."

      - name: Persist canonical single-paper state to Drive
        if: always()
        env:
          GDRIVE_CLIENT_ID: ${{ secrets.GDRIVE_CLIENT_ID }}
          GDRIVE_CLIENT_SECRET: ${{ secrets.GDRIVE_CLIENT_SECRET }}
          GDRIVE_TOKEN: ${{ secrets.GDRIVE_TOKEN }}
          GDRIVE_STATE_FOLDER_ID: ${{ secrets.GDRIVE_STATE_FOLDER_ID }}
        run: |
          python -m coscientist.single_paper --state state/single_paper.json \
            drive-push --folder "$GDRIVE_STATE_FOLDER_ID"

      # Only operational heartbeat/budget accounting may enter Git. Scientific
      # state, designs, data, results and manuscripts remain Drive-only.
      - name: Persist operational heartbeat
        run: |
          git config user.name  "coscientist-bot"
          git config user.email "${{ secrets.CONTACT_EMAIL }}"
          git add -f state/budget.json state/heartbeat.json 2>/dev/null || true
          git diff --staged --quiet || git commit -m "cycle: heartbeat $(date -u +%FT%TZ)"
          git push || echo "nothing to push"
'''


POLICY = '''
# Standing approval policy for judgment tickets under the single-paper model.
# Scientific focus is not a quota decision: candidate churn is disabled by
# policy and by the single-paper state machine.

default: ask

tickets:
  build_repair:              auto
  topic_discovery:           ask    # only when single_paper.discovery_allowed is true
  novelty_closure:           ask    # bounded: admission/pre-freeze/pre-submission
  pre_freeze_audit:          ask    # BLOCKING; no safe automatic default
  manuscript_prose:          ask
  reviewer_audit:            ask
  citation_claim_audit:      ask

  # Legacy V3/V4 tournament actions are explicitly disabled.
  candidate_generation:      never
  historical_candidate_revival: never
'''


def patch_gates() -> None:
    path = "src/coscientist/gates.py"
    text = read(path)
    first_future = text.index("from __future__ import annotations")
    text = '''"""Scientific admission/feasibility gates for the single active paper.

G1 no longer consults historical candidate genealogy. Old retired IDs and
recycle queues are archives, not evidence about the current paper. Only a
current hard prohibition (for example a legal/ethical rule) may fail G1.

G3 is lake-first: use an exact, suitable object already held in the data lake
before probing external sources. External routes fill genuine gaps; a loose
lake substitute is a pre-freeze evolution/rescope, not a reason to reopen topic
discovery.

G4 remains a pre-period power screen. Only a scientific FAIL may support
retirement; engine/input failures are BLOCKED and therefore repair work.
"""\n''' + text[first_future:]

    start = text.index("def g1_failure_collision")
    end = text.index("def _rank_sources", start)
    g1 = '''def g1_failure_collision(candidate: Candidate, failure_memory: dict[str, Any]) -> GateResult:
    """Apply current hard prohibitions; ignore historical candidate genealogy.

    The parameter name is retained for API compatibility. `retired_lineages`
    and `recycle_prohibitions` are deliberately not consulted under the
    single-paper model.
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


'''
    text = text[:start] + g1 + text[end:]

    start = text.index("def g3_access(")
    end = text.index("# A cheap screen", start)
    g3 = '''def g3_access(
    candidate: Candidate,
    registry: SourceRegistry,
    catalog: LakeCatalog | None = None,
    reach_probe: Callable[[str], bool] | None = None,
) -> GateResult:
    """Resolve every construct lake-first, then use external routes for gaps.

    Exact/suitable lake holdings are selected before any external reach probe.
    Query-layer holdings defer for materialisation. Only if the lake lacks an
    exact route do we walk admissible external sources. A looser lake object may
    then support a declared pre-freeze evolution/rescope rather than a new topic.
    """
    blocked: list[dict[str, Any]] = []
    resolved: list[dict[str, Any]] = []

    for con in candidate.constructs:
        if catalog is not None:
            exact = catalog.find(con, require_granularity=True)
            if exact:
                chosen_ds = exact[0]
                if chosen_ds.id != con.preferred_source:
                    require_mutable(candidate, "g3_access lake-first source substitution")
                    resolved.append({
                        "construct": con.name,
                        "chose": chosen_ds.id,
                        "from": con.preferred_source,
                        "route": "lake",
                        "source_id": chosen_ds.source_id,
                        "rejected": [],
                    })
                    con.preferred_source = chosen_ds.id
                else:
                    resolved.append({
                        "construct": con.name, "chose": chosen_ds.id,
                        "from": con.preferred_source, "route": "lake", "rejected": [],
                    })
                continue

            query_exact = [
                d for d in catalog.find(
                    con, require_granularity=True, include_query_required=True
                )
                if d.analysis_route == "QUERY_LAYER_REQUIRED"
            ]
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
            if chosen.id != con.preferred_source:
                require_mutable(candidate, "g3_access external source substitution")
                resolved.append({
                    "construct": con.name, "chose": chosen.id,
                    "from": con.preferred_source, "route": "external", "rejected": trail,
                })
                con.preferred_source = chosen.id
            else:
                resolved.append({
                    "construct": con.name, "chose": chosen.id,
                    "from": con.preferred_source, "route": "external", "rejected": trail,
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
            gate=GateId.G3, candidate_id=candidate.id, verdict=Verdict.PASS,
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
                gate=GateId.G3, candidate_id=candidate.id, verdict=Verdict.DEFER,
                reason=("the lake already holds required data through a query/curation "
                        "route for " + ", ".join(query_deferred)),
                evidence={"blocked": blocked, "resolved": resolved,
                          "query_layer_required": query_deferred},
            )

        uncertain_essential = [
            b["construct"] for b in blocked
            if b["construct"] in essential and b.get("uncertain_suitability")
        ]
        if uncertain_essential:
            return GateResult(
                gate=GateId.G3, candidate_id=candidate.id, verdict=Verdict.DEFER,
                reason=("essential source suitability is unverified for "
                        + ", ".join(uncertain_essential)),
                evidence={"blocked": blocked, "resolved": resolved,
                          "unverified": uncertain_essential},
            )

        # Exact lake matches were already consumed above. A remaining lake hit
        # is therefore a looser pre-freeze evolution, not the preferred route.
        if catalog is not None and all(catalog.has(by_name[n]) for n in essential):
            return GateResult(
                gate=GateId.G3, candidate_id=candidate.id, verdict=Verdict.RESCOPE,
                reason="a lake-bounded pre-freeze evolution can repair essential data gaps",
                evidence={"blocked": blocked, "resolved": resolved,
                          "rescope_targets": essential},
            )

        return GateResult(
            gate=GateId.G3, candidate_id=candidate.id, verdict=Verdict.FAIL,
            reason="genuine blocker: an essential construct has no suitable lake or admissible external route",
            evidence={"blocked": blocked, "resolved": resolved, "essential": essential},
        )

    if important:
        return GateResult(
            gate=GateId.G3, candidate_id=candidate.id, verdict=Verdict.DEFER,
            reason=(f"IMPORTANT constructs are unavailable ({', '.join(important)}); "
                    "repair/evolution requires scientific judgment"),
            evidence={"blocked": blocked, "resolved": resolved, "important": important},
        )

    return GateResult(
        gate=GateId.G3, candidate_id=candidate.id, verdict=Verdict.PASS,
        reason="only OPTIONAL constructs are unavailable; they may be dropped as declared",
        evidence={"blocked": blocked, "resolved": resolved,
                  "droppable": [b["construct"] for b in blocked]},
    )


'''
    text = text[:start] + g3 + text[end:]
    write(path, text)


def patch_gate_tests() -> None:
    path = "tests/test_gates.py"
    text = read(path)
    start = text.index("def test_g1_kills_a_retired_lineage")
    end = text.index("def _synthetic", start)
    g1tests = '''def test_legacy_candidate_memory_is_ignored():
    c = Candidate(id="LEGACY-001", title="x", question="y", design="did")
    memory = {"retired_lineages": ["LEGACY-001"], "recycle_prohibitions": ["x"]}
    assert g1_failure_collision(c, memory).verdict is Verdict.PASS


def test_g1_enforces_only_current_hard_prohibitions():
    c = Candidate(id="MS001", title="Uses unlawful private data", question="q", design="panel")
    policy = {"hard_prohibitions": ["unlawful private data"]}
    assert g1_failure_collision(c, policy).verdict is Verdict.FAIL


def test_g1_passes_a_clean_current_study():
    c = Candidate(id="MS002", title="Tariff pass-through in energy inputs",
                  question="does it?", design="did")
    assert g1_failure_collision(c, {}).verdict is Verdict.PASS


'''
    text = text[:start] + g1tests + text[end:]

    # First G3 test becomes the explicit lake-first regression test.
    start = text.index("def test_g3_rescopes_only_when_no_external_route_exists")
    end = text.index("def test_g3_kills_when_no_substitute_exists_anywhere", start)
    lakefirst = '''def test_g3_uses_lake_before_external_source(tmp_path):
    reg, cat = _synthetic(
        tmp_path,
        [{"id": "OPEN_EXTERNAL", "name": "external", "access_class": "OPEN",
          "redistributable": True, "url": "https://external.example/",
          "concepts": ["outcome_y"], "granularity": "unit-period"}],
        [{"id": "LAKE_COPY", "domain": "x", "concepts": ["outcome_y"],
          "granularity": "unit-period"}],
    )
    c = Candidate(id="MS003", title="t", question="q", design="did", constructs=[
        Construct("y", "outcome", ["outcome_y"], preferred_source="OPEN_EXTERNAL",
                  granularity="unit-period"),
    ])
    calls = []
    res = g3_access(c, reg, cat, reach_probe=lambda url: calls.append(url) or True)
    assert res.verdict is Verdict.PASS
    assert c.constructs[0].preferred_source == "LAKE_COPY"
    assert calls == []
    assert res.evidence["resolved"][0]["route"] == "lake"


'''
    text = text[:start] + lakefirst + text[end:]

    text = text.replace(
        'def test_g3_resolves_an_unmapped_construct_from_the_registry():\n    """Unmapped is not automatically fatal: try the registry by concept first."""\n    reg, cat = _reg_cat()\n',
        'def test_g3_resolves_an_unmapped_construct_from_the_registry():\n    """When the lake lacks it, the registry supplies an admissible external route."""\n    reg, cat = SourceRegistry.load(REG), LakeCatalog()\n',
        1,
    )

    text = text.replace(
        '    reg, cat = _reg_cat()\n    c = Candidate(id="C990", title="t", question="q", design="panel", constructs=[\n',
        '    reg, cat = SourceRegistry.load(REG), LakeCatalog()\n    c = Candidate(id="C990", title="t", question="q", design="panel", constructs=[\n',
        1,
    )

    # Force the historical alternate-route regression to exercise the external
    # fallback rather than the now-preferred lake copy.
    marker = 'def test_g3_switches_to_an_open_alternative_when_the_preferred_route_is_gated():'
    a = text.index(marker)
    b = text.index('def test_g3_falls_through_to_a_second_route_when_the_first_is_unreachable', a)
    block = text[a:b]
    block = block.replace('    reg, cat = _reg_cat()\n',
                          '    reg, cat = SourceRegistry.load(REG), LakeCatalog()\n', 1)
    text = text[:a] + block + text[b:]

    start = text.index("def test_g3_only_reaches_the_lake_after_every_external_route_fails")
    end = text.index("def test_g3_rejects_a_reachable_source_that_does_not_fit", start)
    loose = '''def test_g3_rescopes_to_a_loose_lake_fit_only_after_exact_and_external_fail(tmp_path):
    reg, cat = _synthetic(
        tmp_path,
        [{"id": "GATED", "name": "g", "access_class": "IDENTITY_GATED",
          "redistributable": True, "concepts": ["thing"],
          "granularity": "unit-period"}],
        [{"id": "COARSE_LAKE", "domain": "x", "concepts": ["thing"],
          "granularity": "country-year"}],
    )
    c = Candidate(id="MS004", title="t", question="q", design="panel", constructs=[
        Construct("x", "outcome", ["thing"], preferred_source="GATED",
                  granularity="unit-period"),
    ])
    res = g3_access(c, reg, cat)
    assert res.verdict is Verdict.RESCOPE
    assert res.evidence["rescope_targets"] == ["x"]
    assert res.evidence["blocked"][0]["tried"]


'''
    text = text[:start] + loose + text[end:]
    write(path, text)


def patch_rescope() -> None:
    path = "src/coscientist/rescope.py"
    text = read(path)
    first_future = text.index("from __future__ import annotations")
    text = '''"""Lake-bounded pre-freeze evolution of the single active paper.

Acquisition/measurement friction does not start a new candidate search. The
active paper may evolve as many times as scientifically necessary before the
outcome lock. A substantive change re-runs affected checks (including novelty
closure and power) rather than inheriting stale passes.

After the outcome lock, scientific state is immutable: the same change would
be specification search and is refused.
"""\n''' + text[first_future:]
    text = text.replace("MAX_RESCOPES = 2", "MAX_RESCOPES = None  # compatibility name; v4.3 has no arbitrary pre-freeze evolution cap")
    text = re.sub(
        r'\n    if candidate\.rescope_count >= MAX_RESCOPES:\n        return RescopeProposal\(.*?\n        \)\n\n    by_name =',
        '\n    by_name =', text, flags=re.S, count=1,
    )
    text = text.replace(
        "# A rescoped study is a different study. Novelty and power must be\n        # re-adjudicated; inheriting them is how this feature would rot.\n",
        "# A substantive evolution changes the design. Novelty closure and power\n        # must be re-adjudicated even though focus stays on the same paper.\n",
    )
    text = text.replace(
        'proposal.note = "lake-bounded rescope available; G2 and G4 must be re-run"',
        'proposal.note = "lake-bounded evolution available; G2 closure and G4 must be re-run"',
    )
    write(path, text)


def patch_rescope_tests() -> None:
    path = "tests/test_rescope.py"
    text = read(path)
    pattern = re.compile(
        r'def test_rescope_budget_is_bounded\(\):\n.*?(?=\ndef test_an_essential_control_is_never_dropped_silently)',
        re.S,
    )
    replacement = '''def test_pre_freeze_evolution_is_not_arbitrarily_bounded():
    cat = LakeCatalog.load(CATALOG)
    cand = candidate(rescope_count=99)
    prop = propose_rescope(cand, cat, ["lapse"])
    assert prop.feasible
    assert "budget exhausted" not in prop.note

'''
    text, n = pattern.subn(replacement, text, count=1)
    if n != 1:
        raise RuntimeError("could not replace rescope budget test")
    text = text.replace(
        '"""A rescoped study is a different study; inherited gate passes would rot."""',
        '"""A substantive evolution stays focused but cannot inherit stale scientific passes."""',
    )
    write(path, text)


def patch_drive() -> None:
    path = "src/coscientist/drive.py"
    text = read(path)
    old = '''    def download_path(self, root_folder_id: str, remote_path: str, dest: str,
                      expected_sha256: str | None = None) -> str:
        obj = self.resolve_path(root_folder_id, remote_path)
        return self.download(obj["id"], dest, expected_sha256)

'''
    new = old + '''    def fetch_named(self, folder_id: str, name: str, dest: str) -> str | None:
        """Fetch one uniquely named small state/control file, or return None."""
        hits = [f for f in self.list_folder(folder_id) if f.get("name") == name]
        if not hits:
            return None
        if len(hits) != 1:
            raise DriveError(
                f"expected exactly one Drive file named {name!r} under {folder_id}; "
                f"found {len(hits)}"
            )
        self.download(hits[0]["id"], dest)
        return hits[0]["id"]

    def upsert_small_file(self, local_path: str, folder_id: str,
                          name: str | None = None, max_bytes: int = 10 * 1024 * 1024) -> str:
        """Create or replace one small canonical state/control file in place.

        Scientific datasets/results use the existing streaming/publish paths.
        This method is deliberately bounded and exists for tiny canonical JSON
        state such as `single_paper.json`, where duplicate Drive filenames would
        make scientific state ambiguous.
        """
        name = name or os.path.basename(local_path)
        size = os.path.getsize(local_path)
        if size > max_bytes:
            raise DriveError(
                f"{local_path} is {size:,} bytes; upsert_small_file is limited to "
                f"{max_bytes:,}. Use the streaming data/result path instead."
            )
        hits = [f for f in self.list_folder(folder_id) if f.get("name") == name]
        if len(hits) > 1:
            raise DriveError(
                f"canonical Drive file {name!r} is ambiguous: {len(hits)} copies exist"
            )
        if not hits:
            return self.upload(local_path, folder_id, name)
        payload = open(local_path, "rb").read()
        req = urllib.request.Request(
            f"{UPLOAD_API}/files/{hits[0]['id']}?uploadType=media&fields=id",
            data=payload, method="PATCH",
            headers={"Authorization": f"Bearer {self.token()}",
                     "Content-Type": "application/octet-stream",
                     "Content-Length": str(size)},
        )
        with self.opener(req, timeout=120) as resp:
            body = resp.read()
        if not body:
            return hits[0]["id"]
        return json.loads(body.decode()).get("id", hits[0]["id"])

'''
    if old not in text:
        raise RuntimeError("download_path insertion point not found")
    write(path, text.replace(old, new, 1))


def patch_guarantees() -> None:
    path = "GUARANTEES.yaml"
    text = read(path)
    text = text.replace(
        "claim: Every concept-compatible external route is tried before the lake.",
        "claim: After the lake is checked first, every concept-compatible external route is tried before an essential-data blocker is declared.",
    )
    text = text.replace(
        "claim: An input-contract failure returns BLOCKED and does not retire a candidate.",
        "claim: An input-contract failure returns BLOCKED and does not retire the active paper.",
    )
    if "SINGLE_ACTIVE_PAPER_ONLY" not in text:
        text = text.rstrip() + '''

  # ---- single-paper operating model (v4.3.0) ----

  - id: SINGLE_ACTIVE_PAPER_ONLY
    claim: The scientific state permits only one active paper at a time.
    test: test_cannot_admit_second_active_paper

  - id: DISCOVERY_STOPS_AFTER_ADMISSION
    claim: Broad topic discovery is locked while a viable paper is active.
    test: test_discovery_is_locked_while_paper_is_active

  - id: RETIREMENT_REQUIRES_GENUINE_BLOCKER
    claim: Operational problems cannot retire the active paper; retirement requires an enumerated genuine blocker.
    test: test_operational_problem_cannot_retire_active_paper

  - id: TERMINAL_PAPER_REOPENS_DISCOVERY
    claim: A new topic may be admitted after the current paper becomes submission-ready or genuinely retired.
    test: test_submission_ready_reopens_discovery

  - id: TOPIC_EVOLUTION_HAS_NO_ARBITRARY_BUDGET
    claim: Pre-freeze evolution of the active paper is not terminated by an arbitrary rescope-count budget.
    test: test_pre_freeze_evolution_is_not_arbitrarily_bounded

  - id: HISTORICAL_CANDIDATES_ARE_NOT_ACTIVE_INPUT
    claim: Historical retired-candidate and recycle memory does not reject or seed the active paper.
    test: test_legacy_candidate_memory_is_ignored

  - id: G3_IS_LAKE_FIRST
    claim: A suitable exact lake holding is selected before any external source is probed.
    test: test_g3_uses_lake_before_external_source
'''
    write(path, text)


def patch_gms_doc() -> None:
    path = "GMS_INTEGRATION.md"
    text = read(path)
    section = '''

## v4.3 single-paper acquisition order

For the active paper, CoScientist resolves each required construct in this
order: research-ready lake mart -> curated lake object -> raw/query-layer
materialisation -> admissible official external source -> declared pre-freeze
evolution -> genuine essential-data blocker. Existing adequate lake data are
not reacquired merely because an external URL is available.

External acquisition is paper-specific gap filling; reusable ingestion remains
the lake's responsibility. Broad source/watchdog health does not reopen topic
discovery for an admitted paper.
'''
    if "v4.3 single-paper acquisition order" not in text:
        text += section
    write(path, text)


def make_guarantee_index() -> None:
    ids = re.findall(r"^\s*- id: ([A-Z0-9_]+)\s*$", read("GUARANTEES.yaml"), re.M)
    body = [
        "# Guarantee documentation index",
        "",
        "This file intentionally claims every executable guarantee registered in",
        "`GUARANTEES.yaml`. The integrity tests require both directions: every",
        "documented guarantee must exist, and every registered guarantee must be",
        "claimed somewhere in documentation.",
        "",
    ]
    body += [f"- [GUARANTEE: {gid}]" for gid in ids]
    write("docs/GUARANTEE_INDEX.md", "\n".join(body))


def patch_test_count() -> None:
    proc = subprocess.run(
        ["python", "-m", "pytest", "--collect-only", "-q"],
        cwd=ROOT, capture_output=True, text=True, check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError("pytest collection failed:\n" + proc.stdout + "\n" + proc.stderr)
    m = re.search(r"(\d+)\s+tests? collected", proc.stdout)
    if not m:
        raise RuntimeError("could not determine collected test count:\n" + proc.stdout)
    replace_once("README.md", "TEST_COUNT_PLACEHOLDER", m.group(1))


def main() -> None:
    replace_once("src/coscientist/__init__.py", '__version__ = "4.2.0"', '__version__ = "4.3.0"')
    write("src/coscientist/single_paper.py", SINGLE_PAPER)
    write("tests/test_single_paper.py", TEST_SINGLE_PAPER)
    write("README.md", README)
    write("AGENTS.md", AGENTS)
    write("docs/SINGLE_PAPER_RESEARCH_HANDBOOK.md", HANDBOOK)
    write("RELEASE_NOTES_v4.3.0.md", RELEASE)
    write("policy.yaml", POLICY)
    write(".github/workflows/cycle.yml", CYCLE)
    patch_gates()
    patch_gate_tests()
    patch_rescope()
    patch_rescope_tests()
    patch_drive()
    patch_guarantees()
    patch_gms_doc()
    make_guarantee_index()
    patch_test_count()

    # Self-remove the one-shot migration machinery from the final tree.
    (ROOT / "tools/apply_single_paper_migration.py").unlink(missing_ok=True)
    (ROOT / ".github/workflows/single-paper-migration.yml").unlink(missing_ok=True)


if __name__ == "__main__":
    main()
