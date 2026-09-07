"""Paper-scoped Director router for the multi-paper CoScientist.

Each registered paper has an independent lifecycle state, Director state and
answer inbox. This module routes the mature v4.6.1 Director facade to exactly
one selected paper without imposing any global one-paper lock.
"""
from __future__ import annotations

import argparse
import json
import os

from . import director as base
from . import director_v45 as v45
from . import director_v461 as v461
from .director import DirectorState
from .multi_paper import MultiPaperError, PaperRegistry
from .single_paper import SinglePaperState


def _load_selected(registry_path: str, paper_id: str | None):
    registry = PaperRegistry.load(registry_path)
    registry.validate()
    record = registry.resolve(paper_id)
    paper = SinglePaperState.load(record.paper_state_path)
    director = DirectorState.load(record.director_path)
    if not paper.active_paper:
        raise MultiPaperError(f"paper {record.paper_id} has no paper-local active charter")
    if paper.active_paper.paper_id != record.paper_id:
        raise MultiPaperError(
            f"registry paper_id {record.paper_id} != paper state {paper.active_paper.paper_id}"
        )
    return registry, record, paper, director


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m coscientist.director_multi")
    p.add_argument("--registry", default="state/paper_registry.json")
    p.add_argument("--paper-id", default=None)
    p.add_argument("--catalog", default="state/gms_lake_catalog.json")
    p.add_argument("--receipt-dir", default=None)
    sub = p.add_subparsers(dest="command", required=True)

    sub.add_parser("status")
    e = sub.add_parser("ensure")
    e.add_argument("--action-out", default=None)
    a = sub.add_parser("apply")
    a.add_argument("--answer", default=None)
    ap = sub.add_parser("apply-if-present")
    ap.add_argument("--answer", default=None)
    w = sub.add_parser("withdraw")
    w.add_argument("--reason", required=True)
    w.add_argument("--next-topic", default=None)
    f = sub.add_parser("create-freeze")
    f.add_argument("--out", default=None)
    al = sub.add_parser("create-analysis-lock")
    al.add_argument("--freeze", default=None)
    al.add_argument("--out", default=None)
    al.add_argument("--root", default=".")
    al.add_argument("--acknowledge-io", action="store_true")
    md = sub.add_parser("mark-analysis-dispatched")
    md.add_argument("--freeze", default=None)
    md.add_argument("--analysis-lock", default=None)
    md.add_argument("--dispatch-id", required=True)
    cr = sub.add_parser("complete-from-receipt")
    cr.add_argument("--receipt", required=True)
    cr.add_argument("--freeze", default=None)
    cr.add_argument("--analysis-lock", default=None)
    return p


def _write_json(path: str, payload: dict) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, sort_keys=True)


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    registry, record, paper, director = _load_selected(args.registry, args.paper_id)
    catalog = base._load_catalog(args.catalog)
    receipt_dir = args.receipt_dir or os.path.join(record.artifact_root, "receipts")

    if args.command == "status":
        action = v461.ensure_action(paper, director, catalog)
        payload = {
            "paper_id": record.paper_id,
            "registry_status": record.registry_status,
            "stage": paper.stage.value if paper.stage else None,
            "pending_action": action.kind.value if action else None,
            "pending_action_id": action.id if action else None,
            "role": v45.execution_profile(action.kind)["role"] if action else None,
            "paper_state_path": record.paper_state_path,
            "director_path": record.director_path,
            "answer_path": record.answer_path,
        }
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0

    if args.command == "ensure":
        action = v461.ensure_action(paper, director, catalog)
        if action is None:
            print("NO_PENDING_ACTION")
            return 0
        out = args.action_out or os.path.join(record.artifact_root, "director_action.json")
        _write_json(out, v461.action_payload(action))
        print(f"{record.paper_id} {action.kind.value} {action.id}")
        return 0

    if args.command == "apply":
        answer = args.answer or record.answer_path
        print(v461.apply_answer(
            paper, director, base._load_answer(answer), catalog, receipt_dir=receipt_dir
        ))
        return 0

    if args.command == "apply-if-present":
        answer = args.answer or record.answer_path
        print(v461.apply_if_present(
            paper, director, answer, catalog, receipt_dir=receipt_dir
        ))
        return 0

    if args.command == "withdraw":
        paper.withdraw(args.reason, args.next_topic)
        director.reconcile(paper)
        # Withdrawal is paper-local. Other registered papers remain untouched.
        registry.set_registry_status(record.paper_id, "TERMINAL")
        print(f"USER_WITHDRAWN {record.paper_id}")
        return 0

    if args.command == "create-freeze":
        out = args.out or os.path.join(record.artifact_root, "freeze.json")
        print(base.create_freeze(paper, director, out))
        return 0

    if args.command == "create-analysis-lock":
        freeze = args.freeze or os.path.join(record.artifact_root, "freeze.json")
        out = args.out or os.path.join(record.artifact_root, "analysis_lock.json")
        print(v461.v46.create_analysis_lock(
            paper, director, freeze_path=freeze, out=out,
            root=args.root, acknowledge_io=args.acknowledge_io,
        ))
        return 0

    if args.command == "mark-analysis-dispatched":
        freeze = args.freeze or os.path.join(record.artifact_root, "freeze.json")
        lock = args.analysis_lock or os.path.join(record.artifact_root, "analysis_lock.json")
        print(v461.v46.mark_analysis_dispatched(
            paper, director, freeze_path=freeze,
            analysis_lock_path=lock, dispatch_id=args.dispatch_id,
        ))
        return 0

    if args.command == "complete-from-receipt":
        freeze = args.freeze or os.path.join(record.artifact_root, "freeze.json")
        lock = args.analysis_lock or os.path.join(record.artifact_root, "analysis_lock.json")
        print(v461.v46.complete_from_analysis_receipt(
            paper, director, receipt_path=args.receipt,
            freeze_path=freeze, analysis_lock_path=lock,
        ))
        return 0

    raise AssertionError(args.command)


if __name__ == "__main__":
    raise SystemExit(main())
