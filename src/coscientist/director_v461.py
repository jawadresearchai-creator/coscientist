"""CoScientist v4.6.1 owner-withdrawal facade.

V4.6.1 keeps every v4.6 integrity guarantee and corrects one governance flaw:
the one-paper rule prevents parallel active papers, but it must not trap the
owner in a topic. An explicit owner instruction may place the current paper in
USER_WITHDRAWN, preserve that decision in state, and immediately reopen one
fresh discovery action. If the owner names the next topic, discovery is directed
toward that topic rather than reopening an unrelated tournament.
"""
from __future__ import annotations

import argparse
import json
import os
from typing import Any

from . import director as base
from . import director_v45 as v45
from . import director_v46 as v46
from .analysis_lock import AnalysisLockViolation
from .director import ActionKind, DirectorError, DirectorState, PendingAction
from .freeze import FreezeViolation
from .integrity import ReceiptError
from .single_paper import PaperStage, SinglePaperError, SinglePaperState


def _owner_discovery_action(
    paper: SinglePaperState,
    director: DirectorState,
    catalog=None,
) -> PendingAction:
    direction = (paper.owner_next_topic_direction or "").strip()
    if direction:
        question = (
            "The owner explicitly withdrew the prior paper and directed the next study toward: "
            f"{direction}. Use current literature and the current GMS lake to evaluate this "
            "direction. If scientifically viable, return exactly one focused Management Science "
            "Topic Charter centered on that direction. You may refine the research question, event "
            "definition, outcomes, mechanism, data route and identification design, but do not "
            "substitute an unrelated topic."
        )
        max_shortlist = 1
    else:
        question = (
            "The owner explicitly withdrew the prior paper. Using current literature and the "
            "current GMS lake, produce no more than three serious Management Science questions, "
            "select exactly one, and return a Topic Charter. Do not revive historical candidates "
            "or create a reserve queue."
        )
        max_shortlist = base.MAX_DISCOVERY_SHORTLIST

    return director.set_pending(base._action(
        ActionKind.DISCOVER_TOPIC,
        paper,
        question=question,
        required_output={
            "action_id": "copy from request",
            "owner_direction_acknowledged": bool(direction),
            "shortlist": [
                {
                    "id": "fresh paper id",
                    "title": "working title",
                    "research_question": "single focused question",
                    "importance": "why it matters",
                    "mechanism": "principal mechanism",
                    "residual_contribution": "what remains unanswered",
                    "data_fit": "lake/public-data feasibility",
                    "identification": "credible design path",
                }
            ],
            "selected_id": "exactly one shortlist id",
            "charter": {
                "paper_id": "must equal selected_id",
                "working_title": "...",
                "research_question": "...",
                "phenomenon": "...",
                "mechanism": "...",
                "contribution": "...",
                "unit_of_analysis": "...",
                "intended_design": "...",
                "primary_outcome": "...",
                "primary_exposure": "...",
                "required_constructs": [],
                "target_journal_family": "...",
                "known_threats": [],
            },
        },
        context={
            "owner_withdrawal": True,
            "owner_topic_direction": direction or None,
            "max_shortlist": max_shortlist,
            "rules": [
                "owner withdrawal is a preference/priority decision, not a scientific failure",
                "exactly one paper may become active; no reserve queue or parallel paper",
                "when owner_topic_direction is present, keep discovery centered on it",
                "use current literature and the lake first; verify real data/identification feasibility",
                "once selected, broad discovery stops until completion, genuine retirement, or another explicit owner withdrawal",
            ],
            "lake": base._lake_snapshot(catalog),
        },
    ))


def ensure_action(
    paper: SinglePaperState,
    director: DirectorState,
    catalog=None,
) -> PendingAction | None:
    director.reconcile(paper)
    if director.pending_action:
        return director.pending_action
    if paper.active_paper is not None and paper.stage is PaperStage.USER_WITHDRAWN:
        return _owner_discovery_action(paper, director, catalog)
    return v46.ensure_action(paper, director, catalog)


def action_payload(action: PendingAction) -> dict[str, Any]:
    return v46.action_payload(action)


def apply_answer(
    paper: SinglePaperState,
    director: DirectorState,
    answer: dict[str, Any],
    catalog=None,
    *,
    receipt_dir: str = "state/receipts",
) -> str:
    director.reconcile(paper)
    action = director.pending_action
    if action and action.kind is ActionKind.DISCOVER_TOPIC and paper.stage is PaperStage.USER_WITHDRAWN:
        direction = (paper.owner_next_topic_direction or "").strip()
        if direction:
            shortlist = answer.get("shortlist")
            if not isinstance(shortlist, list) or len(shortlist) != 1:
                raise DirectorError(
                    "owner-directed discovery requires exactly one candidate centered on the named direction"
                )
            if answer.get("owner_direction_acknowledged") is not True:
                raise DirectorError("owner-directed discovery must acknowledge the owner's next-topic direction")
    return v46.apply_answer(
        paper, director, answer, catalog, receipt_dir=receipt_dir
    )


def apply_if_present(
    paper: SinglePaperState,
    director: DirectorState,
    answer_path: str,
    catalog=None,
    *,
    receipt_dir: str = "state/receipts",
) -> str:
    if not os.path.exists(answer_path):
        return "NO_ANSWER"
    answer = base._load_answer(answer_path)
    action_id = str(answer.get("action_id", ""))
    if action_id in director.applied_action_ids:
        return "ALREADY_APPLIED"
    if not director.pending_action:
        return "STALE_ANSWER_NO_PENDING_ACTION"
    if action_id != director.pending_action.id:
        return "STALE_ANSWER_FOR_OTHER_ACTION"
    return apply_answer(
        paper, director, answer, catalog, receipt_dir=receipt_dir
    )


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m coscientist.director_v461")
    p.add_argument("--state", default="state/single_paper.json")
    p.add_argument("--director", default="state/director.json")
    p.add_argument("--catalog", default="state/gms_lake_catalog.json")
    p.add_argument("--receipt-dir", default="state/receipts")
    sub = p.add_subparsers(dest="command", required=True)

    e = sub.add_parser("ensure")
    e.add_argument("--action-out", default="state/director_action.json")
    a = sub.add_parser("apply")
    a.add_argument("--answer", required=True)
    ap = sub.add_parser("apply-if-present")
    ap.add_argument("--answer", default="state/director_answer.json")
    s = sub.add_parser("status")
    s.add_argument("--action-out", default=None)

    w = sub.add_parser("withdraw")
    w.add_argument("--reason", required=True)
    w.add_argument("--next-topic", default=None)
    w.add_argument("--action-out", default="state/director_action.json")

    f = sub.add_parser("create-freeze")
    f.add_argument("--out", default="state/freeze.json")
    al = sub.add_parser("create-analysis-lock")
    al.add_argument("--freeze", default="state/freeze.json")
    al.add_argument("--out", default="state/analysis_lock.json")
    al.add_argument("--root", default=".")
    al.add_argument("--acknowledge-io", action="store_true")
    md = sub.add_parser("mark-analysis-dispatched")
    md.add_argument("--freeze", default="state/freeze.json")
    md.add_argument("--analysis-lock", default="state/analysis_lock.json")
    md.add_argument("--dispatch-id", required=True)
    cr = sub.add_parser("complete-from-receipt")
    cr.add_argument("--receipt", required=True)
    cr.add_argument("--freeze", default="state/freeze.json")
    cr.add_argument("--analysis-lock", default="state/analysis_lock.json")
    return p


def _write_action(path: str, action: PendingAction) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(action_payload(action), fh, indent=2, sort_keys=True)


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    paper = SinglePaperState.load(args.state)
    director = DirectorState.load(args.director)
    catalog = base._load_catalog(args.catalog)
    try:
        if args.command == "withdraw":
            paper.withdraw(args.reason, args.next_topic)
            director.reconcile(paper)
            action = ensure_action(paper, director, catalog)
            if action:
                _write_action(args.action_out, action)
                print(f"USER_WITHDRAWN -> {action.kind.value} {action.id}")
            else:
                print("USER_WITHDRAWN")
            return 0
        if args.command == "ensure":
            action = ensure_action(paper, director, catalog)
            if action is None:
                print("SUBMISSION_READY -- no research action pending")
                return 0
            _write_action(args.action_out, action)
            print(f"{action.kind.value} {action.id}")
            print(f"role        {v45.execution_profile(action.kind)['role']}")
            print(action.question)
            return 0
        if args.command == "apply":
            print(apply_answer(
                paper, director, base._load_answer(args.answer), catalog,
                receipt_dir=args.receipt_dir,
            ))
            return 0
        if args.command == "apply-if-present":
            print(apply_if_present(
                paper, director, args.answer, catalog,
                receipt_dir=args.receipt_dir,
            ))
            return 0
        if args.command == "status":
            action = ensure_action(paper, director, catalog)
            print(f"paper       {paper.active_paper.paper_id if paper.active_paper else '-'}")
            print(f"stage       {paper.stage.value if paper.stage else 'NO_ACTIVE_PAPER'}")
            print(f"action      {action.kind.value if action else '-'}")
            print(f"action id   {action.id if action else '-'}")
            if paper.owner_next_topic_direction:
                print(f"owner next  {paper.owner_next_topic_direction}")
            if action:
                profile = v45.execution_profile(action.kind)
                print(f"role        {profile['role']}")
                if args.action_out:
                    _write_action(args.action_out, action)
            return 0
        if args.command == "create-freeze":
            print(base.create_freeze(paper, director, args.out))
            return 0
        if args.command == "create-analysis-lock":
            print(v46.create_analysis_lock(
                paper, director, freeze_path=args.freeze, out=args.out,
                root=args.root, acknowledge_io=args.acknowledge_io,
            ))
            return 0
        if args.command == "mark-analysis-dispatched":
            print(v46.mark_analysis_dispatched(
                paper, director, freeze_path=args.freeze,
                analysis_lock_path=args.analysis_lock, dispatch_id=args.dispatch_id,
            ))
            return 0
        if args.command == "complete-from-receipt":
            print(v46.complete_from_analysis_receipt(
                paper, director, receipt_path=args.receipt,
                freeze_path=args.freeze, analysis_lock_path=args.analysis_lock,
            ))
            return 0
    except (DirectorError, SinglePaperError, ReceiptError, FreezeViolation,
            AnalysisLockViolation, FileNotFoundError) as exc:
        print(f"DIRECTOR ERROR\n{exc}")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
