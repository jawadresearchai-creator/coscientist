import json

from coscientist.multi_paper import PaperRegistry
from coscientist.single_paper import PaperStage, TopicCharter


def charter(pid: str) -> TopicCharter:
    return TopicCharter(
        paper_id=pid,
        working_title=f"Paper {pid}",
        research_question=f"Question for {pid}?",
        intended_design="panel",
        primary_outcome="Y",
        primary_exposure="X",
    )


def test_multiple_active_papers_are_allowed(tmp_path):
    registry = PaperRegistry.load(str(tmp_path / "paper_registry.json"))
    a = registry.create_paper(charter("MS-A"), state_root=str(tmp_path / "state"), set_focus=True)
    b = registry.create_paper(charter("MS-B"), state_root=str(tmp_path / "state"))

    assert set(registry.active_paper_ids()) == {"MS-A", "MS-B"}
    assert registry.focus_paper_id == "MS-A"
    assert a.paper_state_path != b.paper_state_path
    assert a.director_path != b.director_path
    assert a.answer_path != b.answer_path


def test_focus_is_not_exclusive(tmp_path):
    registry = PaperRegistry.load(str(tmp_path / "paper_registry.json"))
    registry.create_paper(charter("MS-A"), state_root=str(tmp_path / "state"), set_focus=True)
    registry.create_paper(charter("MS-B"), state_root=str(tmp_path / "state"))
    registry.set_focus("MS-B")

    assert registry.focus_paper_id == "MS-B"
    assert set(registry.active_paper_ids()) == {"MS-A", "MS-B"}


def test_each_paper_keeps_independent_lifecycle(tmp_path):
    registry = PaperRegistry.load(str(tmp_path / "paper_registry.json"))
    a = registry.create_paper(charter("MS-A"), state_root=str(tmp_path / "state"))
    b = registry.create_paper(charter("MS-B"), state_root=str(tmp_path / "state"))

    from coscientist.single_paper import SinglePaperState

    sa = SinglePaperState.load(a.paper_state_path)
    sb = SinglePaperState.load(b.paper_state_path)
    sa.transition(PaperStage.DEVELOPING)

    sa2 = SinglePaperState.load(a.paper_state_path)
    sb2 = SinglePaperState.load(b.paper_state_path)
    assert sa2.stage is PaperStage.DEVELOPING
    assert sb2.stage is PaperStage.SELECTED


def test_registry_round_trip_and_validate(tmp_path):
    path = tmp_path / "paper_registry.json"
    registry = PaperRegistry.load(str(path))
    registry.create_paper(charter("MS-A"), state_root=str(tmp_path / "state"), set_focus=True)
    registry.create_paper(charter("MS-B"), state_root=str(tmp_path / "state"))

    loaded = PaperRegistry.load(str(path))
    loaded.validate()
    assert loaded.operating_mode == "MULTI_PAPER"
    assert sorted(loaded.papers) == ["MS-A", "MS-B"]
    assert loaded.focus_paper_id == "MS-A"


def test_registry_status_does_not_change_other_paper(tmp_path):
    registry = PaperRegistry.load(str(tmp_path / "paper_registry.json"))
    registry.create_paper(charter("MS-A"), state_root=str(tmp_path / "state"))
    registry.create_paper(charter("MS-B"), state_root=str(tmp_path / "state"))
    registry.set_registry_status("MS-A", "PAUSED")

    assert registry.papers["MS-A"].registry_status == "PAUSED"
    assert registry.papers["MS-B"].registry_status == "ACTIVE"
    assert set(registry.active_paper_ids()) == {"MS-A", "MS-B"}
