"""V4.5 role/skill facade for the single-paper Research Director.

The V4.4 Director remains the scientific lifecycle authority. This facade adds a
small execution-profile contract around each pending action so a reasoning plane
knows which role and reusable skills it must use, while mechanical actions remain
strictly deterministic.

Backward compatibility matters: a pending action created under V4.4 can be
loaded unchanged. The first V4.5 ensure/status pass derives its profile from the
action kind, stores that profile in the action context, and preserves the same
action id.
"""
from __future__ import annotations

import json
import os
from enum import Enum
from typing import Any

from . import director as base
from .director import ActionKind, DirectorError, DirectorState, PendingAction
from .single_paper import SinglePaperError, SinglePaperState


class ExecutionRole(str, Enum):
    SCIENTIFIC_REASONING = "SCIENTIFIC_REASONING"
    INDEPENDENT_AUDIT = "INDEPENDENT_AUDIT"
    DETERMINISTIC = "DETERMINISTIC"


SKILL_VERSIONS: dict[str, str] = {
    "Humanizer": "2.0",
    "LiteratureTheory": "1.0",
    "StudyDesignReasoner": "1.0",
    "HostileReviewer": "1.0",
    "ManuscriptWriter": "1.0",
    "CitationIntegrity": "1.0",
    "FinalAuditReasoner": "1.0",
}


_PROFILE_MAP: dict[ActionKind, tuple[ExecutionRole, tuple[str, ...], bool]] = {
    ActionKind.DISCOVER_TOPIC: (
        ExecutionRole.SCIENTIFIC_REASONING, (), False,
    ),
    ActionKind.DEVELOP_LITERATURE_THEORY: (
        ExecutionRole.SCIENTIFIC_REASONING,
        ("LiteratureTheory", "CitationIntegrity"),
        False,
    ),
    ActionKind.DATA_FEASIBILITY: (
        ExecutionRole.SCIENTIFIC_REASONING,
        ("StudyDesignReasoner",),
        False,
    ),
    ActionKind.DESIGN_CLOSURE: (
        ExecutionRole.SCIENTIFIC_REASONING,
        ("StudyDesignReasoner",),
        False,
    ),
    ActionKind.PRE_FREEZE_AUDIT: (
        ExecutionRole.INDEPENDENT_AUDIT,
        ("HostileReviewer", "CitationIntegrity"),
        True,
    ),
    ActionKind.CREATE_FREEZE: (
        ExecutionRole.DETERMINISTIC, (), False,
    ),
    ActionKind.RUN_ANALYSIS: (
        ExecutionRole.DETERMINISTIC, (), False,
    ),
    ActionKind.MANUSCRIPT_DRAFT: (
        ExecutionRole.SCIENTIFIC_REASONING,
        ("ManuscriptWriter", "CitationIntegrity", "Humanizer"),
        False,
    ),
    ActionKind.FINAL_AUDIT: (
        ExecutionRole.INDEPENDENT_AUDIT,
        ("FinalAuditReasoner", "CitationIntegrity", "HostileReviewer"),
        True,
    ),
    ActionKind.REPAIR: (
        ExecutionRole.SCIENTIFIC_REASONING, (), False,
    ),
}


def execution_profile(kind: ActionKind) -> dict[str, Any]:
    role, skills, fresh = _PROFILE_MAP[kind]
    return {
        "role": role.value,
        "required_skills": {name: SKILL_VERSIONS[name] for name in skills},
        "fresh_context_required": fresh,
        "authority": (
            "deterministic core executes this action"
            if role is ExecutionRole.DETERMINISTIC
            else "reasoning output must return through director_answer.json"
        ),
    }


def _attach_profile(action: PendingAction | None, director: DirectorState) -> PendingAction | None:
    if action is None:
        return None
    profile = execution_profile(action.kind)
    if action.context.get("execution_profile") != profile:
        action.context["execution_profile"] = profile
        director.save()
    return action


def action_payload(action: PendingAction) -> dict[str, Any]:
    payload = action.to_dict()
    payload["execution_profile"] = execution_profile(action.kind)
    return payload


def ensure_action(
    paper: SinglePaperState,
    director: DirectorState,
    catalog=None,
) -> PendingAction | None:
    return _attach_profile(base.ensure_action(paper, director, catalog), director)


def validate_reasoning_answer(action: PendingAction, answer: dict[str, Any]) -> None:
    """Validate the role/skill boundary before V4.4 scientific validation.

    This is an auditable execution attestation, not a claim that JSON can prove
    cognitive independence. Independent-audit actions still need to be executed
    in a genuinely fresh reasoning context by the operating environment.
    """
    profile = execution_profile(action.kind)
    role = profile["role"]
    if role == ExecutionRole.DETERMINISTIC.value:
        raise DirectorError(
            f"{action.kind.value} is deterministic; a reasoning answer is not admissible"
        )

    supplied_role = str(answer.get("execution_role", ""))
    if supplied_role != role:
        raise DirectorError(
            f"{action.kind.value} requires execution_role={role}; got {supplied_role or '<missing>'}"
        )

    used = answer.get("skills_used")
    if not isinstance(used, dict):
        raise DirectorError("reasoning answer requires skills_used as a name->version object")
    missing = []
    wrong = []
    for name, version in profile["required_skills"].items():
        if name not in used:
            missing.append(name)
        elif str(used[name]) != str(version):
            wrong.append(f"{name}={used[name]!r} (expected {version!r})")
    if missing:
        raise DirectorError("reasoning answer missing required skills: " + ", ".join(missing))
    if wrong:
        raise DirectorError("reasoning answer has wrong skill versions: " + ", ".join(wrong))

    if profile["fresh_context_required"] and answer.get("fresh_context_attested") is not True:
        raise DirectorError(
            f"{action.kind.value} requires a fresh independent audit context attestation"
        )


def apply_answer(
    paper: SinglePaperState,
    director: DirectorState,
    answer: dict[str, Any],
    catalog=None,
) -> str:
    director.reconcile(paper)
    action = _attach_profile(director.pending_action, director)
    if action is None:
        raise DirectorError("there is no pending director action")
    validate_reasoning_answer(action, answer)
    return base.apply_answer(paper, director, answer, catalog)


def apply_if_present(
    paper: SinglePaperState,
    director: DirectorState,
    answer_path: str,
    catalog=None,
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
    return apply_answer(paper, director, answer, catalog)


def _write_action(path: str, action: PendingAction) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(action_payload(action), fh, indent=2, sort_keys=True)


def main(argv: list[str] | None = None) -> int:
    args = base._parser().parse_args(argv)
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
            profile = execution_profile(action.kind)
            print(f"{action.kind.value} {action.id}")
            print(f"role        {profile['role']}")
            print("skills      " + (", ".join(profile["required_skills"]) or "-"))
            print(action.question)
            return 0

        if args.command == "apply":
            print(apply_answer(paper, director, base._load_answer(args.answer), catalog))
            return 0

        if args.command == "apply-if-present":
            print(apply_if_present(paper, director, args.answer, catalog))
            return 0

        if args.command == "mechanical":
            action = ensure_action(paper, director, catalog)
            if action is None:
                print("NO_ACTION")
                return 0
            if action.kind is ActionKind.CREATE_FREEZE:
                print(base.create_freeze(paper, director, args.freeze_out))
            else:
                print(f"NO_MECHANICAL_STEP -- pending {action.kind.value}")
            return 0

        if args.command == "status":
            director.reconcile(paper)
            action = ensure_action(paper, director, catalog)
            print(f"paper       {paper.active_paper.paper_id if paper.active_paper else '-'}")
            print(f"stage       {paper.stage.value if paper.stage else 'NO_ACTIVE_PAPER'}")
            print(f"action      {action.kind.value if action else '-'}")
            print(f"action id   {action.id if action else '-'}")
            print(f"blocking    {'yes' if action and action.blocking else 'no'}")
            if action:
                profile = execution_profile(action.kind)
                print(f"role        {profile['role']}")
                print("skills      " + (", ".join(profile["required_skills"]) or "-"))
            if args.action_out and action:
                _write_action(args.action_out, action)
            return 0

        if args.command == "mark-analysis-started":
            print(base.mark_analysis_started(paper, director, args.freeze))
            return 0

        if args.command == "mark-results-complete":
            print(base.mark_results_complete(paper, director, args.results))
            return 0
    except (DirectorError, SinglePaperError) as exc:
        print(f"DIRECTOR ERROR\n{exc}")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
