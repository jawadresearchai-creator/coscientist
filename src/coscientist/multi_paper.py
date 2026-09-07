"""Multi-paper registry for Management Science CoScientist.

This module removes the old *global* one-paper lock without discarding the mature
per-paper lifecycle engine. Each paper owns an independent paper-state file,
Director file, answer inbox, receipts and artifact root. A root registry indexes
those paper-local state machines and may expose a focus paper for UI convenience;
focus never implies exclusivity.

Legacy ``SinglePaperState`` remains the paper-local lifecycle representation for
backward compatibility. Its historical name no longer means that the whole
CoScientist can operate only one paper.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
from dataclasses import asdict, dataclass, field
from typing import Any

from .models import utcnow
from .single_paper import PaperStage, SinglePaperState, TopicCharter

REGISTRY_FILENAME = "paper_registry.json"
OPERATING_MODE = "MULTI_PAPER"
SCHEMA_VERSION = 1
ACTIVE_STATUSES = {"ACTIVE", "REPAIR", "PAUSED"}
KNOWN_STATUSES = ACTIVE_STATUSES | {"TERMINAL", "ARCHIVED"}


class MultiPaperError(RuntimeError):
    pass


@dataclass
class PaperRecord:
    paper_id: str
    paper_state_path: str
    director_path: str
    answer_path: str
    artifact_root: str
    registry_status: str = "ACTIVE"
    created_at: str = field(default_factory=utcnow)
    updated_at: str = field(default_factory=utcnow)
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "PaperRecord":
        allowed = cls.__dataclass_fields__
        obj = cls(**{k: v for k, v in raw.items() if k in allowed})
        if obj.registry_status not in KNOWN_STATUSES:
            raise MultiPaperError(
                f"paper {obj.paper_id}: unknown registry_status={obj.registry_status!r}"
            )
        return obj

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class PaperRegistry:
    path: str
    papers: dict[str, PaperRecord] = field(default_factory=dict)
    focus_paper_id: str | None = None
    schema_version: int = SCHEMA_VERSION
    operating_mode: str = OPERATING_MODE
    updated_at: str = field(default_factory=utcnow)
    history: list[dict[str, Any]] = field(default_factory=list)

    @classmethod
    def load(cls, path: str) -> "PaperRegistry":
        if not os.path.exists(path):
            return cls(path=path)
        with open(path, "r", encoding="utf-8") as fh:
            raw = json.load(fh)
        mode = raw.get("operating_mode", OPERATING_MODE)
        if mode != OPERATING_MODE:
            raise MultiPaperError(
                f"{path} declares operating_mode={mode!r}; expected {OPERATING_MODE}"
            )
        papers_raw = raw.get("papers", {})
        if not isinstance(papers_raw, dict):
            raise MultiPaperError("paper_registry.json 'papers' must be an object keyed by paper_id")
        papers: dict[str, PaperRecord] = {}
        for paper_id, record_raw in papers_raw.items():
            if not isinstance(record_raw, dict):
                raise MultiPaperError(f"paper {paper_id}: registry entry must be an object")
            record = PaperRecord.from_dict(record_raw)
            if record.paper_id != paper_id:
                raise MultiPaperError(
                    f"registry key {paper_id!r} does not match record paper_id {record.paper_id!r}"
                )
            papers[paper_id] = record
        focus = raw.get("focus_paper_id") or None
        if focus and focus not in papers:
            raise MultiPaperError(f"focus_paper_id={focus!r} is not registered")
        return cls(
            path=path,
            papers=papers,
            focus_paper_id=focus,
            schema_version=int(raw.get("schema_version", SCHEMA_VERSION)),
            operating_mode=OPERATING_MODE,
            updated_at=raw.get("updated_at", utcnow()),
            history=list(raw.get("history", [])),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "operating_mode": OPERATING_MODE,
            "focus_paper_id": self.focus_paper_id,
            "papers": {pid: rec.to_dict() for pid, rec in sorted(self.papers.items())},
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

    def register(self, record: PaperRecord, *, set_focus: bool = False) -> None:
        existing = self.papers.get(record.paper_id)
        if existing:
            same_identity = (
                existing.paper_state_path == record.paper_state_path
                and existing.director_path == record.director_path
                and existing.answer_path == record.answer_path
                and existing.artifact_root == record.artifact_root
            )
            if not same_identity:
                raise MultiPaperError(
                    f"paper {record.paper_id} is already registered with different state paths"
                )
            existing.registry_status = record.registry_status
            existing.metadata.update(record.metadata)
            existing.updated_at = utcnow()
        else:
            self.papers[record.paper_id] = record
            self.history.append({
                "at": utcnow(),
                "event": "PAPER_REGISTERED",
                "paper_id": record.paper_id,
                "registry_status": record.registry_status,
            })
        if set_focus or self.focus_paper_id is None:
            self.focus_paper_id = record.paper_id
        self.save()

    def create_paper(
        self,
        charter: TopicCharter,
        *,
        state_root: str = "state",
        set_focus: bool = False,
    ) -> PaperRecord:
        if charter.paper_id in self.papers:
            raise MultiPaperError(f"paper {charter.paper_id} is already registered")
        paper_dir = os.path.join(state_root, charter.paper_id)
        os.makedirs(paper_dir, exist_ok=True)
        state_path = os.path.join(paper_dir, "paper_state.json")
        director_path = os.path.join(paper_dir, "director.json")
        answer_path = os.path.join(paper_dir, "director_answer.json")
        if os.path.exists(state_path):
            raise MultiPaperError(f"refusing to overwrite existing {state_path}")
        state = SinglePaperState.load(state_path)
        state.admit(charter)
        record = PaperRecord(
            paper_id=charter.paper_id,
            paper_state_path=state_path,
            director_path=director_path,
            answer_path=answer_path,
            artifact_root=paper_dir,
        )
        self.register(record, set_focus=set_focus)
        return record

    def resolve(self, paper_id: str | None = None) -> PaperRecord:
        pid = paper_id or self.focus_paper_id
        if not pid:
            raise MultiPaperError("no paper_id supplied and no focus paper is set")
        try:
            return self.papers[pid]
        except KeyError as exc:
            raise MultiPaperError(f"paper {pid!r} is not registered") from exc

    def set_focus(self, paper_id: str) -> None:
        if paper_id not in self.papers:
            raise MultiPaperError(f"paper {paper_id!r} is not registered")
        self.focus_paper_id = paper_id
        self.history.append({"at": utcnow(), "event": "FOCUS_CHANGED", "paper_id": paper_id})
        self.save()

    def set_registry_status(self, paper_id: str, status: str) -> None:
        if status not in KNOWN_STATUSES:
            raise MultiPaperError(f"unknown registry status {status!r}")
        record = self.resolve(paper_id)
        record.registry_status = status
        record.updated_at = utcnow()
        self.history.append({
            "at": utcnow(), "event": "REGISTRY_STATUS", "paper_id": paper_id, "status": status
        })
        self.save()

    def active_paper_ids(self) -> list[str]:
        return sorted(
            pid for pid, rec in self.papers.items() if rec.registry_status in ACTIVE_STATUSES
        )

    def validate(self) -> None:
        seen_paths: dict[str, str] = {}
        for pid, rec in self.papers.items():
            if rec.paper_id != pid:
                raise MultiPaperError(f"paper key mismatch for {pid}")
            for label, path in (
                ("paper_state_path", rec.paper_state_path),
                ("director_path", rec.director_path),
                ("answer_path", rec.answer_path),
            ):
                key = os.path.normcase(os.path.normpath(path))
                if key in seen_paths:
                    raise MultiPaperError(
                        f"{label} for {pid} collides with paper {seen_paths[key]}: {path}"
                    )
                seen_paths[key] = pid
        if self.focus_paper_id and self.focus_paper_id not in self.papers:
            raise MultiPaperError("focus paper is not registered")


def migrate_legacy_root_state(
    registry: PaperRegistry,
    *,
    legacy_state_path: str,
    legacy_director_path: str,
    legacy_answer_path: str | None = None,
    state_root: str = "state",
    set_focus: bool = True,
) -> PaperRecord | None:
    """Copy the legacy root single-paper state into one paper-local directory.

    The legacy files are intentionally left untouched as historical/backward-
    compatibility artifacts. This function is idempotent when the same paper
    has already been registered with the expected paths.
    """
    if not os.path.exists(legacy_state_path):
        return None
    legacy = SinglePaperState.load(legacy_state_path)
    if not legacy.active_paper:
        return None
    pid = legacy.active_paper.paper_id
    paper_dir = os.path.join(state_root, pid)
    os.makedirs(paper_dir, exist_ok=True)
    state_path = os.path.join(paper_dir, "paper_state.json")
    director_path = os.path.join(paper_dir, "director.json")
    answer_path = os.path.join(paper_dir, "director_answer.json")

    if not os.path.exists(state_path):
        shutil.copy2(legacy_state_path, state_path)
    if os.path.exists(legacy_director_path) and not os.path.exists(director_path):
        shutil.copy2(legacy_director_path, director_path)
    if legacy_answer_path and os.path.exists(legacy_answer_path) and not os.path.exists(answer_path):
        shutil.copy2(legacy_answer_path, answer_path)

    record = PaperRecord(
        paper_id=pid,
        paper_state_path=state_path,
        director_path=director_path,
        answer_path=answer_path,
        artifact_root=paper_dir,
        registry_status="ACTIVE",
        metadata={"migrated_from_legacy_root": True},
    )
    registry.register(record, set_focus=set_focus)
    return record


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m coscientist.multi_paper")
    p.add_argument("--registry", default="state/paper_registry.json")
    sub = p.add_subparsers(dest="command", required=True)

    sub.add_parser("list")
    sub.add_parser("validate")
    f = sub.add_parser("focus")
    f.add_argument("--paper-id", required=True)
    s = sub.add_parser("set-status")
    s.add_argument("--paper-id", required=True)
    s.add_argument("--status", choices=sorted(KNOWN_STATUSES), required=True)
    c = sub.add_parser("create")
    c.add_argument("--charter", required=True)
    c.add_argument("--state-root", default="state")
    c.add_argument("--focus", action="store_true")
    m = sub.add_parser("migrate-legacy")
    m.add_argument("--legacy-state", default="state/single_paper.json")
    m.add_argument("--legacy-director", default="state/director.json")
    m.add_argument("--legacy-answer", default="state/director_answer.json")
    m.add_argument("--state-root", default="state")
    return p


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    registry = PaperRegistry.load(args.registry)
    if args.command == "list":
        print(json.dumps(registry.to_dict(), indent=2, sort_keys=True))
        return 0
    if args.command == "validate":
        registry.validate()
        print(f"MULTI_PAPER_REGISTRY_OK papers={len(registry.papers)} active={len(registry.active_paper_ids())}")
        return 0
    if args.command == "focus":
        registry.set_focus(args.paper_id)
        print(f"FOCUS {args.paper_id}")
        return 0
    if args.command == "set-status":
        registry.set_registry_status(args.paper_id, args.status)
        print(f"STATUS {args.paper_id} {args.status}")
        return 0
    if args.command == "create":
        with open(args.charter, "r", encoding="utf-8") as fh:
            charter = TopicCharter.from_dict(json.load(fh))
        rec = registry.create_paper(charter, state_root=args.state_root, set_focus=args.focus)
        print(json.dumps(rec.to_dict(), indent=2, sort_keys=True))
        return 0
    if args.command == "migrate-legacy":
        rec = migrate_legacy_root_state(
            registry,
            legacy_state_path=args.legacy_state,
            legacy_director_path=args.legacy_director,
            legacy_answer_path=args.legacy_answer,
            state_root=args.state_root,
        )
        print(json.dumps(rec.to_dict() if rec else {"status": "NO_LEGACY_ACTIVE_PAPER"}, indent=2))
        return 0
    raise AssertionError(args.command)


if __name__ == "__main__":
    raise SystemExit(main())
