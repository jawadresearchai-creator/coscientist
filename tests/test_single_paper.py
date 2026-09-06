import pytest

from coscientist.single_paper import (
    PaperStage,
    SinglePaperError,
    SinglePaperState,
    TopicCharter,
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


def test_new_admission_does_not_inherit_old_topic_history(tmp_path):
    state = SinglePaperState.load(str(tmp_path / "state.json"))
    state.admit(charter("MS001"))
    advance_to_submission_ready(state)
    assert len(state.history) > 1
    state.admit(charter("MS002"))
    assert len(state.history) == 1
    assert state.history[0]["event"] == "ADMITTED"
    assert state.history[0]["paper_id"] == "MS002"


def test_stage_cannot_skip_or_move_backwards(tmp_path):
    state = SinglePaperState.load(str(tmp_path / "state.json"))
    state.admit(charter())
    with pytest.raises(SinglePaperError, match="cannot skip"):
        state.transition(PaperStage.DATA_FEASIBLE)
    state.transition(PaperStage.DEVELOPING)
    with pytest.raises(SinglePaperError, match="cannot move backwards"):
        state.transition(PaperStage.SELECTED)


def test_evolution_is_pre_freeze_and_not_capped(tmp_path):
    state = SinglePaperState.load(str(tmp_path / "state.json"))
    state.admit(charter())
    for i in range(25):
        state.evolve(f"repair {i}")
    assert state.evolution_count == 25
    state.transition(PaperStage.DEVELOPING)
    state.transition(PaperStage.DATA_FEASIBLE)
    state.transition(PaperStage.DESIGN_READY)
    state.transition(PaperStage.FROZEN)
    with pytest.raises(SinglePaperError, match="pre-freeze"):
        state.evolve("too late")


def test_legacy_fields_are_ignored_when_loading(tmp_path):
    path = tmp_path / "state.json"
    path.write_text(
        '{"operating_mode":"SINGLE_PAPER","active_paper":null,"stage":null,'
        '"retired_lineages":["C705"],"candidate_slate":["C716"],"portfolio":["x"]}'
    )
    state = SinglePaperState.load(str(path))
    assert state.active_paper is None
    assert state.discovery_allowed
    assert "retired_lineages" not in state.to_dict()
    assert "candidate_slate" not in state.to_dict()
    assert "portfolio" not in state.to_dict()
