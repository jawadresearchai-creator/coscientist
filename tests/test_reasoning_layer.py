from pathlib import Path

import pytest

from coscientist import director as base
from coscientist.director import ActionKind, DirectorError, DirectorState
from coscientist.director_v45 import (
    ExecutionRole,
    action_payload,
    ensure_action,
    execution_profile,
    validate_reasoning_answer,
)
from coscientist.single_paper import PaperStage, SinglePaperState, TopicCharter


ROOT = Path(__file__).resolve().parents[1]


def _paper(tmp_path):
    return SinglePaperState.load(str(tmp_path / "single_paper.json"))


def _director(tmp_path):
    return DirectorState.load(str(tmp_path / "director.json"))


def _charter():
    return TopicCharter(
        paper_id="MS-V45",
        working_title="V4.5 routing test",
        research_question="Does X affect Y?",
        phenomenon="X",
        mechanism="M",
        contribution="C",
        unit_of_analysis="firm-year",
        intended_design="panel",
        primary_outcome="Y",
        primary_exposure="X",
        required_constructs=["X", "Y"],
    )


def _at_stage(paper, stage):
    paper.admit(_charter())
    path = [
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
    for item in path:
        if paper.stage is stage:
            break
        paper.transition(item)
        if item is stage:
            break


def test_action_profile_is_persisted_and_serialized(tmp_path):
    p, d = _paper(tmp_path), _director(tmp_path)
    p.admit(_charter())
    d.reconcile(p)
    action = ensure_action(p, d)
    assert action.kind is ActionKind.DEVELOP_LITERATURE_THEORY
    profile = action.context["execution_profile"]
    assert profile["role"] == ExecutionRole.SCIENTIFIC_REASONING.value
    assert profile["required_skills"] == {
        "LiteratureTheory": "1.0",
        "CitationIntegrity": "1.0",
    }
    assert action_payload(action)["execution_profile"] == profile


def test_prefreeze_audit_rejects_normal_reasoning_role(tmp_path):
    p, d = _paper(tmp_path), _director(tmp_path)
    _at_stage(p, PaperStage.DESIGN_READY)
    d.reconcile(p)
    action = ensure_action(p, d)
    assert action.kind is ActionKind.PRE_FREEZE_AUDIT
    with pytest.raises(DirectorError, match="requires execution_role=INDEPENDENT_AUDIT"):
        validate_reasoning_answer(action, {
            "execution_role": "SCIENTIFIC_REASONING",
            "skills_used": {"HostileReviewer": "1.0", "CitationIntegrity": "1.0"},
            "fresh_context_attested": True,
        })


def test_audit_requires_fresh_context_attestation(tmp_path):
    p, d = _paper(tmp_path), _director(tmp_path)
    _at_stage(p, PaperStage.DESIGN_READY)
    d.reconcile(p)
    action = ensure_action(p, d)
    with pytest.raises(DirectorError, match="fresh independent audit context"):
        validate_reasoning_answer(action, {
            "execution_role": "INDEPENDENT_AUDIT",
            "skills_used": {"HostileReviewer": "1.0", "CitationIntegrity": "1.0"},
        })


def test_literature_answer_requires_declared_skills(tmp_path):
    p, d = _paper(tmp_path), _director(tmp_path)
    p.admit(_charter())
    d.reconcile(p)
    action = ensure_action(p, d)
    with pytest.raises(DirectorError, match="missing required skills: CitationIntegrity"):
        validate_reasoning_answer(action, {
            "execution_role": "SCIENTIFIC_REASONING",
            "skills_used": {"LiteratureTheory": "1.0"},
        })


def test_mechanical_action_profile_is_deterministic_and_skill_free(tmp_path):
    p, d = _paper(tmp_path), _director(tmp_path)
    _at_stage(p, PaperStage.DESIGN_READY)
    d.reconcile(p)
    d.record("pre_freeze_audit", {"decision": "PASS"})
    action = ensure_action(p, d)
    assert action.kind is ActionKind.CREATE_FREEZE
    profile = execution_profile(action.kind)
    assert profile["role"] == ExecutionRole.DETERMINISTIC.value
    assert profile["required_skills"] == {}
    with pytest.raises(DirectorError, match="is deterministic"):
        validate_reasoning_answer(action, {
            "execution_role": "SCIENTIFIC_REASONING",
            "skills_used": {},
        })


def test_legacy_pending_action_is_enriched_without_recreation(tmp_path):
    p, d = _paper(tmp_path), _director(tmp_path)
    legacy = base.ensure_action(p, d)
    assert "execution_profile" not in legacy.context
    old_id = legacy.id
    enriched = ensure_action(p, d)
    assert enriched.id == old_id
    assert enriched.context["execution_profile"]["role"] == "SCIENTIFIC_REASONING"


def test_reasoning_skill_and_role_contract_files_exist():
    required = [
        "agents/scientific_reasoning/AGENT.md",
        "agents/independent_audit/AGENT.md",
        "skills/humanizer/SKILL.md",
        "skills/literature_theory/SKILL.md",
        "skills/study_design_reasoner/SKILL.md",
        "skills/hostile_reviewer/SKILL.md",
        "skills/manuscript_writer/SKILL.md",
        "skills/citation_integrity/SKILL.md",
        "skills/final_audit_reasoner/SKILL.md",
    ]
    missing = [path for path in required if not (ROOT / path).is_file()]
    assert not missing, f"missing reasoning-layer contracts: {missing}"
