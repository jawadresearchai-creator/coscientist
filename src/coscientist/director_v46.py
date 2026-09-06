"""V4.6 integrity/autonomy facade for the single-paper Research Director.

V4.5 established role/skill routing.  V4.6 closes the next boundary: facts that
machines can know (dataset identity, power execution, freeze/lock identity,
provenance/publication completion) must come from write-once integrity receipts,
not from reasoning-plane PASS strings.
"""
from __future__ import annotations

import argparse
import json
import os
from typing import Any

from . import director as base
from . import director_v45 as v45
from .analysis_lock import AnalysisLock, AnalysisLockViolation, discover_scripts
from .director import ActionKind, DirectorError, DirectorState, PendingAction
from .freeze import FreezeManifest, FreezeViolation
from .integrity import IntegrityReceipt, ReceiptError
from .single_paper import PaperStage, SinglePaperError, SinglePaperState


def _safe_receipt_path(receipt_dir: str, ref: str) -> str:
    name = os.path.basename(str(ref))
    if not name or name != str(ref) or name in {".", ".."}:
        raise DirectorError("receipt references must be plain filenames inside receipt-dir")
    return os.path.join(receipt_dir, name)


def _load_receipt(receipt_dir: str, ref: str, *, kind: str, paper_id: str) -> IntegrityReceipt:
    path = _safe_receipt_path(receipt_dir, ref)
    try:
        receipt = IntegrityReceipt.load(path)
        receipt.require(kind=kind, paper_id=paper_id)
        return receipt
    except (FileNotFoundError, ReceiptError) as exc:
        raise DirectorError(f"invalid {kind} receipt {ref!r}: {exc}") from exc


def _integrity_requirements(action: PendingAction) -> dict[str, Any]:
    if action.kind is ActionKind.DATA_FEASIBILITY:
        return {
            "external_source_rule": (
                "reasoning-declared external verification cannot close feasibility; "
                "materialise/register essential external data into the GMS lake first"
            )
        }
    if action.kind is ActionKind.DESIGN_CLOSURE:
        return {
            "dataset_identity": "DATASET_SET receipt required",
            "power": "POWER receipt required and deterministic powered=true",
            "reasoning_must_not_supply_hashes": True,
        }
    if action.kind is ActionKind.PRE_FREEZE_AUDIT:
        return {
            "pass_is_conjunctive": [
                "novelty_closure", "measurement", "identification", "power",
                "access_licence_ethics",
            ]
        }
    if action.kind is ActionKind.CREATE_FREEZE:
        return {"mechanical_phase": "CREATE_AND_PERSIST_FREEZE"}
    if action.kind is ActionKind.RUN_ANALYSIS:
        return {
            "mechanical_phases": [
                "CREATE_AND_PERSIST_ANALYSIS_LOCK",
                "DISPATCH_CONFIRMATORY_ANALYSIS",
                "CONSUME_ANALYSIS_RECEIPT",
            ]
        }
    if action.kind is ActionKind.FINAL_AUDIT:
        return {
            "mechanical_evidence": "FINAL_AUDIT_EVIDENCE receipt required for final PASS",
            "auditor_may_not_invent": ["numeric_provenance", "reproducibility"],
        }
    return {}


def _attach_integrity(action: PendingAction | None, director: DirectorState) -> PendingAction | None:
    if action is None:
        return None
    wanted = _integrity_requirements(action)
    changed = False
    if wanted and action.context.get("integrity_requirements") != wanted:
        action.context["integrity_requirements"] = wanted
        changed = True
    # Make the action's requested output explicit without deleting legacy keys.
    if action.kind is ActionKind.DESIGN_CLOSURE:
        for k, v in {
            "dataset_receipt_ref": "filename of canonical DATASET_SET receipt",
            "power_receipt_ref": "filename of canonical POWER receipt",
        }.items():
            if action.required_output.get(k) != v:
                action.required_output[k] = v
                changed = True
    if action.kind is ActionKind.FINAL_AUDIT:
        v = "filename of canonical FINAL_AUDIT_EVIDENCE receipt"
        if action.required_output.get("final_audit_evidence_ref") != v:
            action.required_output["final_audit_evidence_ref"] = v
            changed = True
    if changed:
        director.save()
    return action


def ensure_action(paper: SinglePaperState, director: DirectorState, catalog=None) -> PendingAction | None:
    return _attach_integrity(v45.ensure_action(paper, director, catalog), director)


def action_payload(action: PendingAction) -> dict[str, Any]:
    payload = v45.action_payload(action)
    payload["integrity_requirements"] = _integrity_requirements(action)
    return payload


def _prepare_reasoning_answer(
    paper: SinglePaperState,
    action: PendingAction,
    answer: dict[str, Any],
    *,
    receipt_dir: str,
) -> dict[str, Any]:
    """Validate/replace mechanically knowable fields before V4.4 scientific logic."""
    if not paper.active_paper:
        return answer
    pid = paper.active_paper.paper_id
    out = dict(answer)

    if action.kind is ActionKind.DATA_FEASIBILITY:
        external = out.get("external_sources", [])
        if external:
            raise DirectorError(
                "v4.6 does not accept reasoning-declared external source verification as "
                "feasibility evidence. Materialise/register the required source into the GMS "
                "lake, refresh the catalog, then answer DATA_FEASIBILITY from canonical data."
            )

    if action.kind is ActionKind.DESIGN_CLOSURE and str(out.get("decision", "")).upper() == "PASS":
        dref = str(out.get("dataset_receipt_ref", ""))
        pref = str(out.get("power_receipt_ref", ""))
        if not dref or not pref:
            raise DirectorError("design PASS requires dataset_receipt_ref and power_receipt_ref")
        datasets = _load_receipt(receipt_dir, dref, kind="DATASET_SET", paper_id=pid)
        power = _load_receipt(receipt_dir, pref, kind="POWER", paper_id=pid)
        hashes = datasets.payload.get("dataset_hashes")
        if not isinstance(hashes, dict) or not hashes:
            raise DirectorError("DATASET_SET receipt contains no dataset_hashes")
        if power.payload.get("status") != "PASS" or power.payload.get("result", {}).get("powered") is not True:
            raise DirectorError("POWER receipt is not a deterministic PASS")
        # The reasoning answer is deliberately not authoritative for these fields.
        out["dataset_hashes"] = dict(hashes)
        out["power"] = {
            "status": "PASS",
            "receipt_id": power.receipt_id,
            "receipt_hash": power.receipt_hash,
            "result": power.payload.get("result"),
        }
        out["dataset_receipt"] = {
            "receipt_id": datasets.receipt_id,
            "receipt_hash": datasets.receipt_hash,
        }

    if action.kind is ActionKind.PRE_FREEZE_AUDIT and str(out.get("decision", "")).upper() == "PASS":
        required = [
            "novelty_closure", "measurement", "identification", "power",
            "access_licence_ethics",
        ]
        bad = [k for k in required if str(out.get(k, "")).upper() != "PASS"]
        if bad:
            raise DirectorError(
                "pre-freeze PASS is conjunctive; these dimensions are not PASS: "
                + ", ".join(bad)
            )

    if action.kind is ActionKind.FINAL_AUDIT and str(out.get("decision", "")).upper() == "PASS":
        ref = str(out.get("final_audit_evidence_ref", ""))
        if not ref:
            raise DirectorError("final PASS requires final_audit_evidence_ref")
        evidence = _load_receipt(
            receipt_dir, ref, kind="FINAL_AUDIT_EVIDENCE", paper_id=pid
        )
        if evidence.payload.get("numeric_provenance") != "PASS" or \
                evidence.payload.get("reproducibility") != "PASS":
            raise DirectorError("final-audit mechanical evidence is not PASS")
        checks = dict(out.get("checks") or {})
        # Mechanical truth comes from the receipt, regardless of auditor wording.
        checks["numeric_provenance"] = "PASS"
        checks["reproducibility"] = "PASS"
        out["checks"] = checks
        out["mechanical_evidence"] = {
            "receipt_id": evidence.receipt_id,
            "receipt_hash": evidence.receipt_hash,
        }

    return out


def apply_answer(
    paper: SinglePaperState,
    director: DirectorState,
    answer: dict[str, Any],
    catalog=None,
    *,
    receipt_dir: str = "state/receipts",
) -> str:
    director.reconcile(paper)
    action = _attach_integrity(director.pending_action, director)
    if action is None:
        raise DirectorError("there is no pending director action")
    # V4.5 owns role/skill/fresh-context enforcement.
    v45.validate_reasoning_answer(action, answer)
    prepared = _prepare_reasoning_answer(
        paper, action, answer, receipt_dir=receipt_dir
    )
    return base.apply_answer(paper, director, prepared, catalog)


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


def create_analysis_lock(
    paper: SinglePaperState,
    director: DirectorState,
    *,
    freeze_path: str,
    out: str,
    root: str = ".",
    acknowledge_io: bool = False,
) -> str:
    director.reconcile(paper)
    action = ensure_action(paper, director)
    if paper.stage is not PaperStage.FROZEN or not action or action.kind is not ActionKind.RUN_ANALYSIS:
        raise DirectorError("analysis lock may be created only for pending RUN_ANALYSIS at FROZEN")
    try:
        freeze = FreezeManifest.load(freeze_path)
    except (FileNotFoundError, FreezeViolation) as exc:
        raise DirectorError(f"cannot lock analysis without intact freeze: {exc}") from exc
    if not paper.active_paper or freeze.candidate_id != paper.active_paper.paper_id:
        raise DirectorError("freeze belongs to a different paper")
    scripts = discover_scripts(root)
    try:
        lock = AnalysisLock.create(
            freeze, scripts, root=root, acknowledge_io=acknowledge_io
        )
        lock.save(out)
    except AnalysisLockViolation as exc:
        raise DirectorError(f"cannot create AnalysisLock: {exc}") from exc
    director.record("analysis_lock", {
        "analysis_lock_id": lock.lock_id,
        "analysis_lock_hash": lock.lock_hash,
        "freeze_hash": freeze.freeze_hash,
        "path": out,
    })
    return lock.lock_id


def mark_analysis_dispatched(
    paper: SinglePaperState,
    director: DirectorState,
    *,
    freeze_path: str,
    analysis_lock_path: str,
    dispatch_id: str,
) -> str:
    if not dispatch_id.strip():
        raise DirectorError("analysis dispatch requires a non-empty dispatch_id")
    freeze = FreezeManifest.load(freeze_path)
    lock = AnalysisLock.load(analysis_lock_path)
    lock.require(root=".", freeze=freeze)
    result = base.mark_analysis_started(paper, director, freeze_path)
    director.record("analysis_dispatch", {
        "dispatch_id": dispatch_id,
        "freeze_hash": freeze.freeze_hash,
        "analysis_lock_id": lock.lock_id,
        "analysis_lock_hash": lock.lock_hash,
    })
    return result


def complete_from_analysis_receipt(
    paper: SinglePaperState,
    director: DirectorState,
    *,
    receipt_path: str,
    freeze_path: str,
    analysis_lock_path: str,
) -> str:
    if not paper.active_paper or paper.stage is not PaperStage.ANALYZING:
        raise DirectorError("analysis receipt can complete only the active ANALYZING paper")
    freeze = FreezeManifest.load(freeze_path)
    lock = AnalysisLock.load(analysis_lock_path)
    try:
        receipt = IntegrityReceipt.load(receipt_path)
        receipt.require(kind="ANALYSIS", paper_id=paper.active_paper.paper_id)
    except ReceiptError as exc:
        raise DirectorError(f"invalid analysis receipt: {exc}") from exc
    p = receipt.payload
    if p.get("freeze_hash") != freeze.freeze_hash:
        raise DirectorError("analysis receipt does not match canonical freeze")
    if p.get("analysis_lock_hash") != lock.lock_hash:
        raise DirectorError("analysis receipt does not match canonical AnalysisLock")
    if p.get("provenance_status") != "PASS" or p.get("publication_status") != "PASS":
        raise DirectorError("analysis receipt is not a verified published confirmatory run")
    director.record("results", {
        "analysis_receipt_id": receipt.receipt_id,
        "analysis_receipt_hash": receipt.receipt_hash,
        "results_manifest_sha256": p.get("results_manifest_sha256"),
        "workflow_run_id": p.get("workflow_run_id"),
        "git_sha": p.get("git_sha"),
        "freeze_hash": p.get("freeze_hash"),
        "analysis_lock_hash": p.get("analysis_lock_hash"),
    })
    if director.pending_action and director.pending_action.kind is ActionKind.RUN_ANALYSIS:
        director.clear_pending(director.pending_action.id, "RESULTS_COMPLETE")
    paper.transition(PaperStage.RESULTS_COMPLETE)
    director.reconcile(paper)
    return "RESULTS_COMPLETE"


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m coscientist.director_v46")
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
            if action:
                profile = v45.execution_profile(action.kind)
                print(f"role        {profile['role']}")
                print("integrity   " + json.dumps(_integrity_requirements(action), sort_keys=True))
                if args.action_out:
                    _write_action(args.action_out, action)
            return 0
        if args.command == "create-freeze":
            print(base.create_freeze(paper, director, args.out))
            return 0
        if args.command == "create-analysis-lock":
            print(create_analysis_lock(
                paper, director, freeze_path=args.freeze, out=args.out,
                root=args.root, acknowledge_io=args.acknowledge_io,
            ))
            return 0
        if args.command == "mark-analysis-dispatched":
            print(mark_analysis_dispatched(
                paper, director, freeze_path=args.freeze,
                analysis_lock_path=args.analysis_lock, dispatch_id=args.dispatch_id,
            ))
            return 0
        if args.command == "complete-from-receipt":
            print(complete_from_analysis_receipt(
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
