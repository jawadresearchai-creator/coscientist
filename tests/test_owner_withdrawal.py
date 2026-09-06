import pytest

from coscientist.director import ActionKind, DirectorError, DirectorState
from coscientist.director_v461 import apply_answer, ensure_action
from coscientist.single_paper import PaperStage, SinglePaperError, SinglePaperState, TopicCharter


def charter(pid="MS-OLD"):
    return TopicCharter(
        paper_id=pid,
        working_title="Old paper",
        research_question="Does X affect Y?",
        phenomenon="X",
        mechanism="M",
        contribution="C",
        unit_of_analysis="firm-day",
        intended_design="event study",
        primary_outcome="Y",
        primary_exposure="X",
    )


def test_owner_can_withdraw_without_scientific_blocker(tmp_path):
    state = SinglePaperState.load(str(tmp_path / "single_paper.json"))
    state.admit(charter())
    state.withdraw(
        "Owner no longer wants to pursue this question.",
        "event study of the ChatGPT Astra release",
    )
    assert state.stage is PaperStage.USER_WITHDRAWN
    assert state.discovery_allowed
    assert state.current_problem is None
    assert state.owner_next_topic_direction == "event study of the ChatGPT Astra release"
    assert state.history[-1]["classification"] == "OWNER_DECISION"


def test_withdrawal_cannot_be_faked_as_normal_transition(tmp_path):
    state = SinglePaperState.load(str(tmp_path / "single_paper.json"))
    state.admit(charter())
    with pytest.raises(SinglePaperError, match="use withdraw"):
        state.transition(PaperStage.USER_WITHDRAWN)
    assert state.stage is PaperStage.SELECTED


def test_one_paper_rule_still_blocks_parallel_admission_until_withdrawn(tmp_path):
    state = SinglePaperState.load(str(tmp_path / "single_paper.json"))
    state.admit(charter("MS-1"))
    with pytest.raises(SinglePaperError, match="discovery is locked"):
        state.admit(charter("MS-2"))
    state.withdraw("Owner changes research priority.", "Astra release event study")
    state.admit(charter("MS-2"))
    assert state.active_paper.paper_id == "MS-2"
    assert state.stage is PaperStage.SELECTED
    assert state.owner_next_topic_direction is None


def test_withdrawal_replaces_stale_pending_action_with_directed_discovery(tmp_path):
    paper = SinglePaperState.load(str(tmp_path / "single_paper.json"))
    director = DirectorState.load(str(tmp_path / "director.json"))
    paper.admit(charter())
    first = ensure_action(paper, director)
    assert first.kind is ActionKind.DEVELOP_LITERATURE_THEORY

    paper.withdraw("Owner prefers a new topic.", "ChatGPT Astra release event study")
    next_action = ensure_action(paper, director)
    assert next_action.kind is ActionKind.DISCOVER_TOPIC
    assert next_action.id != first.id
    assert next_action.context["owner_withdrawal"] is True
    assert next_action.context["owner_topic_direction"] == "ChatGPT Astra release event study"
    assert next_action.context["max_shortlist"] == 1
    assert "ChatGPT Astra release event study" in next_action.question


def test_owner_directed_discovery_requires_one_acknowledged_candidate(tmp_path):
    paper = SinglePaperState.load(str(tmp_path / "single_paper.json"))
    director = DirectorState.load(str(tmp_path / "director.json"))
    paper.admit(charter())
    ensure_action(paper, director)
    paper.withdraw("Owner chooses another question.", "ChatGPT Astra release event study")
    action = ensure_action(paper, director)

    bad = {
        "action_id": action.id,
        "execution_role": "SCIENTIFIC_REASONING",
        "skills_used": {},
        "owner_direction_acknowledged": False,
        "shortlist": [{"id": "MS-ASTRA"}],
        "selected_id": "MS-ASTRA",
        "charter": {
            "paper_id": "MS-ASTRA",
            "working_title": "Astra",
            "research_question": "Does Astra affect firms?",
            "phenomenon": "release",
            "mechanism": "information shock",
            "contribution": "event evidence",
            "unit_of_analysis": "firm-day",
            "intended_design": "event study",
            "primary_outcome": "return",
            "primary_exposure": "Astra exposure",
        },
    }
    with pytest.raises(DirectorError, match="acknowledge"):
        apply_answer(paper, director, bad)
