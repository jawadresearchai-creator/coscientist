import json

import pytest

from coscientist.director import (
    ActionKind,
    DirectorError,
    DirectorState,
    apply_answer,
    apply_if_present,
    create_freeze,
    ensure_action,
    resolve_data_requirements,
)
from coscientist.director_drive import pull_answer
from coscientist.drive import DriveError
from coscientist.gms_lake import GMSCatalog, GMSObject
from coscientist.single_paper import PaperStage, SinglePaperState, TopicCharter


def paper_state(tmp_path):
    return SinglePaperState.load(str(tmp_path / "single_paper.json"))


def director_state(tmp_path):
    return DirectorState.load(str(tmp_path / "director.json"))


def charter(pid="MS001"):
    return TopicCharter(
        paper_id=pid,
        working_title="Focused paper",
        research_question="Does X affect Y through M?",
        phenomenon="X",
        mechanism="M",
        contribution="C",
        unit_of_analysis="firm-year",
        intended_design="panel",
        primary_outcome="Y",
        primary_exposure="X",
        required_constructs=["X", "Y"],
    )


def discovery_answer(action_id, pid="MS001", extra=0):
    shortlist = [{
        "id": pid,
        "title": "Focused paper",
        "research_question": "Does X affect Y through M?",
        "importance": "important",
        "mechanism": "M",
        "residual_contribution": "C",
        "data_fit": "credible",
        "identification": "panel",
    }]
    for i in range(extra):
        shortlist.append({
            "id": f"ALT{i}", "title": f"Alt {i}", "research_question": "q",
            "importance": "i", "mechanism": "m", "residual_contribution": "c",
            "data_fit": "d", "identification": "x",
        })
    return {
        "action_id": action_id,
        "shortlist": shortlist,
        "selected_id": pid,
        "selection_rationale": "best balance",
        "charter": charter(pid).to_dict(),
    }


def literature_answer(action_id):
    return {
        "action_id": action_id,
        "closest_papers": [
            {"title": "A", "year": 2025, "doi_or_url": "doi:a", "why_close": "x"},
            {"title": "B", "year": 2026, "doi_or_url": "doi:b", "why_close": "y"},
        ],
        "mechanism": "X changes decision quality, which changes Y",
        "rival_explanation": "selection",
        "observable_implications": ["timing pattern", "heterogeneity"],
        "residual_contribution": "identifies the mechanism in a setting not resolved by A/B",
        "required_constructs": ["X", "Y", "decision quality"],
        "known_threats": ["selection"],
    }


def available_obj(path, *, concepts, granularity="firm-year", route="DIRECT_FETCH",
                  source="SRC", dataset="d", sha="a" * 64):
    return GMSObject(
        id=f"{source}/{dataset}", domain="corporate", source_id=source,
        dataset_id=dataset, remote_path=path, sha256=sha, bytes=100,
        status="OK", availability="AVAILABLE", analysis_route=route,
        direct_fetch=route != "QUERY_LAYER_REQUIRED", concepts=concepts,
        granularity=granularity, coverage_start="2010", coverage_end="2026",
    )


def advance_to_developing(paper, director):
    action = ensure_action(paper, director)
    apply_answer(paper, director, discovery_answer(action.id))
    action = ensure_action(paper, director)
    apply_answer(paper, director, literature_answer(action.id))
    assert paper.stage is PaperStage.DEVELOPING


def test_director_emits_one_idempotent_bounded_discovery_action(tmp_path):
    p, d = paper_state(tmp_path), director_state(tmp_path)
    first = ensure_action(p, d)
    second = ensure_action(p, d)
    assert first.id == second.id
    assert first.kind is ActionKind.DISCOVER_TOPIC
    assert first.context["max_shortlist"] == 3
    assert "historical candidate" in " ".join(first.context["rules"])


def test_discovery_rejects_more_than_three_topics(tmp_path):
    p, d = paper_state(tmp_path), director_state(tmp_path)
    action = ensure_action(p, d)
    with pytest.raises(DirectorError, match="1..3"):
        apply_answer(p, d, discovery_answer(action.id, extra=3))
    assert p.active_paper is None


def test_one_topic_is_admitted_and_broad_discovery_stops(tmp_path):
    p, d = paper_state(tmp_path), director_state(tmp_path)
    action = ensure_action(p, d)
    assert apply_answer(p, d, discovery_answer(action.id)) == "ADMITTED"
    assert p.active_paper.paper_id == "MS001"
    assert not p.discovery_allowed
    next_action = ensure_action(p, d)
    assert next_action.kind is ActionKind.DEVELOP_LITERATURE_THEORY
    assert next_action.paper_id == "MS001"


def test_literature_closure_deepens_same_paper(tmp_path):
    p, d = paper_state(tmp_path), director_state(tmp_path)
    action = ensure_action(p, d)
    apply_answer(p, d, discovery_answer(action.id))
    action = ensure_action(p, d)
    result = apply_answer(p, d, literature_answer(action.id))
    assert result == "DEVELOPING"
    assert p.active_paper.paper_id == "MS001"
    assert p.active_paper.mechanism.startswith("X changes")
    assert d.records["literature_theory"]["rival_explanation"] == "selection"


def test_lake_resolution_prefers_research_tier_over_raw(tmp_path):
    catalog = GMSCatalog(datasets=[
        available_obj("01_RAW_IMMUTABLE/raw.csv", concepts=["x"], dataset="raw", sha="b" * 64),
        available_obj("03_RESEARCH/mart.parquet", concepts=["x"], dataset="mart", sha="c" * 64),
    ])
    result = resolve_data_requirements([
        {"name": "X", "concepts": ["x"], "necessity": "ESSENTIAL",
         "granularity": "firm-year", "coverage_start": "2012", "coverage_end": "2025"}
    ], catalog, [])
    assert result["status"] == "PASS"
    assert result["resolutions"][0]["remote_path"].startswith("03_RESEARCH")
    assert "03_RESEARCH/mart.parquet" in result["dataset_hashes"]


def test_unresolved_essential_data_repairs_same_paper(tmp_path):
    p, d = paper_state(tmp_path), director_state(tmp_path)
    advance_to_developing(p, d)
    action = ensure_action(p, d, GMSCatalog())
    answer = {
        "action_id": action.id,
        "requirements": [{
            "name": "X", "concepts": ["x"], "necessity": "ESSENTIAL",
            "granularity": "firm-year", "coverage_start": "2010", "coverage_end": "2025",
        }],
        "external_sources": [],
    }
    assert apply_answer(p, d, answer, GMSCatalog()) == "REPAIR"
    assert p.stage is PaperStage.DEVELOPING
    assert p.active_paper.paper_id == "MS001"
    assert p.evolution_count == 1


def test_verified_free_authoritative_external_source_can_close_real_gap(tmp_path):
    p, d = paper_state(tmp_path), director_state(tmp_path)
    advance_to_developing(p, d)
    action = ensure_action(p, d, GMSCatalog())
    answer = {
        "action_id": action.id,
        "requirements": [{
            "name": "X", "concepts": ["x"], "necessity": "ESSENTIAL",
            "granularity": "firm-year", "coverage_start": "2010", "coverage_end": "2025",
        }],
        "external_sources": [{
            "requirement": "X", "source_id": "OFFICIAL_X", "url": "https://example.org/x",
            "verified": True, "free": True, "official_or_authoritative": True,
            "access_class": "OPEN", "granularity": "firm-year",
            "coverage_start": "2000", "coverage_end": "2026", "licence": "public",
        }],
    }
    assert apply_answer(p, d, answer, GMSCatalog()) == "DATA_FEASIBLE"
    assert p.stage is PaperStage.DATA_FEASIBLE


def test_design_pass_requires_real_dataset_hash_and_power_pass(tmp_path):
    p, d = paper_state(tmp_path), director_state(tmp_path)
    p.admit(charter())
    p.transition(PaperStage.DEVELOPING)
    p.transition(PaperStage.DATA_FEASIBLE)
    d.reconcile(p)
    action = ensure_action(p, d)
    bad = {
        "action_id": action.id, "decision": "PASS", "estimand": "ATE", "design": "panel",
        "sample_definition": "firms", "treatment": "X", "outcome": "Y", "models": ["FE"],
        "primary_contrasts": ["X"], "multiplicity_policy": "none",
        "dataset_hashes": {"panel": "not-a-hash"}, "power": {"status": "PASS"},
    }
    with pytest.raises(DirectorError, match="invalid SHA-256"):
        apply_answer(p, d, bad)
    assert p.stage is PaperStage.DATA_FEASIBLE


def test_design_repair_does_not_reopen_topic_discovery(tmp_path):
    p, d = paper_state(tmp_path), director_state(tmp_path)
    p.admit(charter())
    p.transition(PaperStage.DEVELOPING)
    p.transition(PaperStage.DATA_FEASIBLE)
    d.reconcile(p)
    action = ensure_action(p, d)
    result = apply_answer(p, d, {
        "action_id": action.id, "decision": "REPAIR",
        "repair_summary": "tighten treatment timing",
    })
    assert result == "REPAIR"
    assert p.active_paper.paper_id == "MS001"
    assert p.stage is PaperStage.DATA_FEASIBLE
    assert not p.discovery_allowed


def _design_pass(action_id):
    return {
        "action_id": action_id, "decision": "PASS", "estimand": "ATE",
        "design": "panel event study", "sample_definition": "eligible firms",
        "treatment": "X", "outcome": "Y", "controls": ["size"], "exclusions": [],
        "window_start": "2015", "window_end": "2025", "pre_period_end": "2019",
        "models": ["firm and year FE"], "primary_contrasts": ["post X"],
        "multiplicity_policy": "BH-FDR", "dataset_hashes": {"panel.parquet": "d" * 64},
        "power": {"status": "PASS", "method": "pre-period MDE", "plausible_effect": 0.1,
                  "mde": 0.05, "evidence": "adequate"},
    }


def _audit_pass(action_id):
    return {
        "action_id": action_id, "decision": "PASS", "findings": [],
        "novelty_closure": "PASS", "measurement": "PASS", "identification": "PASS",
        "power": "PASS", "access_licence_ethics": "PASS",
    }


def test_prefreeze_pass_creates_real_freeze_before_frozen_stage(tmp_path):
    p, d = paper_state(tmp_path), director_state(tmp_path)
    p.admit(charter())
    p.transition(PaperStage.DEVELOPING)
    p.transition(PaperStage.DATA_FEASIBLE)
    d.reconcile(p)
    design_action = ensure_action(p, d)
    apply_answer(p, d, _design_pass(design_action.id))
    audit_action = ensure_action(p, d)
    assert audit_action.kind is ActionKind.PRE_FREEZE_AUDIT
    assert apply_answer(p, d, _audit_pass(audit_action.id)) == "FREEZE_READY"
    freeze_action = ensure_action(p, d)
    assert freeze_action.kind is ActionKind.CREATE_FREEZE
    assert p.stage is PaperStage.DESIGN_READY
    freeze_path = tmp_path / "freeze.json"
    fid = create_freeze(p, d, str(freeze_path))
    assert fid.startswith("DF-MS001-")
    assert freeze_path.exists()
    assert p.stage is PaperStage.FROZEN


def test_prefreeze_repair_keeps_same_design_lineage_pre_freeze(tmp_path):
    p, d = paper_state(tmp_path), director_state(tmp_path)
    p.admit(charter())
    p.transition(PaperStage.DEVELOPING)
    p.transition(PaperStage.DATA_FEASIBLE)
    p.transition(PaperStage.DESIGN_READY)
    d.reconcile(p)
    action = ensure_action(p, d)
    assert apply_answer(p, d, {
        "action_id": action.id, "decision": "REPAIR",
        "repair_summary": "improve negative control",
    }) == "REPAIR"
    assert p.stage is PaperStage.DESIGN_READY
    assert p.active_paper.paper_id == "MS001"
    assert p.evolution_count == 1


def test_stale_answer_is_ignored_instead_of_corrupting_new_action(tmp_path):
    p, d = paper_state(tmp_path), director_state(tmp_path)
    action = ensure_action(p, d)
    answer_path = tmp_path / "director_answer.json"
    answer_path.write_text(json.dumps({"action_id": "DA-old", "shortlist": []}))
    result = apply_if_present(p, d, str(answer_path), None)
    assert result == "STALE_ANSWER_FOR_OTHER_ACTION"
    assert d.pending_action.id == action.id
    assert p.active_paper is None


def test_final_audit_requires_every_mandated_check_to_pass(tmp_path):
    p, d = paper_state(tmp_path), director_state(tmp_path)
    p.admit(charter())
    for stage in (PaperStage.DEVELOPING, PaperStage.DATA_FEASIBLE, PaperStage.DESIGN_READY,
                  PaperStage.FROZEN, PaperStage.ANALYZING, PaperStage.RESULTS_COMPLETE,
                  PaperStage.MANUSCRIPT):
        p.transition(stage)
    d.reconcile(p)
    action = ensure_action(p, d)
    checks = {
        "scientific_logic": "PASS", "numeric_provenance": "PASS", "citations": "PASS",
        "figures_tables": "PASS", "claim_strength": "PASS", "reproducibility": "PASS",
        "journal_compliance": "PASS", "novelty_refresh": "FAIL",
    }
    with pytest.raises(DirectorError, match="every mandated audit check"):
        apply_answer(p, d, {"action_id": action.id, "decision": "PASS", "checks": checks})
    assert p.stage is PaperStage.MANUSCRIPT


def test_director_resets_records_when_a_new_paper_is_admitted(tmp_path):
    p, d = paper_state(tmp_path), director_state(tmp_path)
    p.admit(charter("MS001"))
    d.reconcile(p)
    d.record("old", {"value": 1})
    for stage in (PaperStage.DEVELOPING, PaperStage.DATA_FEASIBLE, PaperStage.DESIGN_READY,
                  PaperStage.FROZEN, PaperStage.ANALYZING, PaperStage.RESULTS_COMPLETE,
                  PaperStage.MANUSCRIPT, PaperStage.FINAL_AUDIT, PaperStage.SUBMISSION_READY):
        p.transition(stage)
    p.admit(charter("MS002"))
    d.reconcile(p)
    assert d.bound_paper_id == "MS002"
    assert "old" not in d.records


def test_duplicate_drive_answer_files_are_refused(tmp_path):
    class FakeClient:
        def list_folder(self, folder_id):
            return [
                {"id": "1", "name": "director_answer.json"},
                {"id": "2", "name": "director_answer.json"},
            ]

    with pytest.raises(DriveError, match="answer inbox is ambiguous"):
        pull_answer(str(tmp_path / "answer.json"), FakeClient(), "folder")
