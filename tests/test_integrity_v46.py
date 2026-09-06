import json
from pathlib import Path

import pytest

from coscientist.analysis_lock import AnalysisLock
from coscientist.director import ActionKind, DirectorError, DirectorState
from coscientist.director_v46 import (
    apply_answer,
    complete_from_analysis_receipt,
    ensure_action,
)
from coscientist.freeze import FreezeManifest
from coscientist.gms_lake import Availability, GMSCatalog, GMSObject
from coscientist.integrity import IntegrityReceipt, ReceiptError, build_dataset_receipt
from coscientist.single_paper import PaperStage, SinglePaperState, TopicCharter


ROOT = Path(__file__).resolve().parents[1]


def charter(pid="MS-V46"):
    return TopicCharter(
        paper_id=pid,
        working_title="Integrity test",
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


def paper(tmp_path, stage=PaperStage.SELECTED):
    p = SinglePaperState.load(str(tmp_path / "single_paper.json"))
    p.admit(charter())
    order = [
        PaperStage.DEVELOPING,
        PaperStage.DATA_FEASIBLE,
        PaperStage.DESIGN_READY,
        PaperStage.FROZEN,
        PaperStage.ANALYZING,
        PaperStage.RESULTS_COMPLETE,
        PaperStage.MANUSCRIPT,
        PaperStage.FINAL_AUDIT,
    ]
    for item in order:
        if p.stage is stage:
            break
        p.transition(item)
        if item is stage:
            break
    return p


def director(tmp_path, p):
    d = DirectorState.load(str(tmp_path / "director.json"))
    d.reconcile(p)
    return d


def test_receipt_is_write_once_and_tamper_evident(tmp_path):
    path = tmp_path / "receipt.json"
    r = IntegrityReceipt("TEST", "P1", {"x": 1})
    rid = r.save(str(path))
    assert IntegrityReceipt.load(str(path)).receipt_id == rid
    with pytest.raises(ReceiptError, match="cannot be replaced"):
        IntegrityReceipt("TEST", "P1", {"x": 2}).save(str(path))

    raw = json.loads(path.read_text())
    raw["payload"]["x"] = 99
    path.write_text(json.dumps(raw))
    with pytest.raises(ReceiptError, match="altered"):
        IntegrityReceipt.load(str(path))


def test_dataset_receipt_uses_catalog_hash_not_reasoning_hash(tmp_path):
    obj = GMSObject(
        id="x", domain="firms", source_id="SRC", dataset_id="DS",
        remote_path="03_RESEARCH/panel.csv", sha256="a" * 64,
        availability=Availability.AVAILABLE.value,
    )
    cat = GMSCatalog(lake_repo="lake", lake_repo_sha="b" * 40,
                     generated_at="2026-09-06T00:00:00Z", datasets=[obj])
    r = build_dataset_receipt(cat, paper_id="P1", remote_paths=[obj.remote_path])
    assert r.payload["dataset_hashes"] == {obj.remote_path: "a" * 64}


def test_dataset_receipt_refuses_unhashed_object():
    obj = GMSObject(
        id="x", domain="firms", source_id="SRC", dataset_id="DS",
        remote_path="03_RESEARCH/panel.csv", sha256=None,
        availability=Availability.AVAILABLE.value,
    )
    cat = GMSCatalog(datasets=[obj])
    with pytest.raises(ReceiptError, match="no valid catalog SHA-256"):
        build_dataset_receipt(cat, paper_id="P1", remote_paths=[obj.remote_path])


def test_design_pass_uses_machine_receipts_not_answer_hashes(tmp_path):
    p = paper(tmp_path, PaperStage.DATA_FEASIBLE)
    d = director(tmp_path, p)
    a = ensure_action(p, d)
    assert a.kind is ActionKind.DESIGN_CLOSURE

    receipts = tmp_path / "receipts"
    receipts.mkdir()
    dr = IntegrityReceipt("DATASET_SET", p.active_paper.paper_id, {
        "dataset_hashes": {"03_RESEARCH/panel.csv": "a" * 64}
    })
    pr = IntegrityReceipt("POWER", p.active_paper.paper_id, {
        "status": "PASS", "result": {"powered": True, "mde": 0.1}
    })
    dr.save(str(receipts / "dataset_receipt.json"))
    pr.save(str(receipts / "power_receipt.json"))

    answer = {
        "action_id": a.id,
        "execution_role": "SCIENTIFIC_REASONING",
        "skills_used": {"StudyDesignReasoner": "1.0"},
        "decision": "PASS",
        "estimand": "ATE",
        "design": "panel DID",
        "sample_definition": "all eligible firms",
        "treatment": "X",
        "outcome": "Y",
        "models": ["primary"],
        "primary_contrasts": ["post x exposure"],
        "multiplicity_policy": "primary-only",
        "dataset_hashes": {"fabricated.csv": "f" * 64},
        "power": {"status": "PASS", "invented": True},
        "dataset_receipt_ref": "dataset_receipt.json",
        "power_receipt_ref": "power_receipt.json",
    }
    assert apply_answer(p, d, answer, receipt_dir=str(receipts)) == "DESIGN_READY"
    record = d.records["design_closure"]
    assert record["dataset_hashes"] == {"03_RESEARCH/panel.csv": "a" * 64}
    assert "fabricated.csv" not in record["dataset_hashes"]
    assert record["power"]["receipt_id"] == pr.receipt_id


def test_design_pass_rejects_missing_power_receipt(tmp_path):
    p = paper(tmp_path, PaperStage.DATA_FEASIBLE)
    d = director(tmp_path, p)
    a = ensure_action(p, d)
    receipts = tmp_path / "receipts"
    receipts.mkdir()
    IntegrityReceipt("DATASET_SET", p.active_paper.paper_id,
                     {"dataset_hashes": {"x": "a" * 64}}).save(
        str(receipts / "dataset_receipt.json")
    )
    answer = {
        "action_id": a.id,
        "execution_role": "SCIENTIFIC_REASONING",
        "skills_used": {"StudyDesignReasoner": "1.0"},
        "decision": "PASS",
        "dataset_receipt_ref": "dataset_receipt.json",
        "power_receipt_ref": "missing.json",
    }
    with pytest.raises(DirectorError, match="invalid POWER receipt"):
        apply_answer(p, d, answer, receipt_dir=str(receipts))


def test_prefreeze_pass_is_conjunctive(tmp_path):
    p = paper(tmp_path, PaperStage.DESIGN_READY)
    d = director(tmp_path, p)
    a = ensure_action(p, d)
    assert a.kind is ActionKind.PRE_FREEZE_AUDIT
    answer = {
        "action_id": a.id,
        "execution_role": "INDEPENDENT_AUDIT",
        "skills_used": {"HostileReviewer": "1.0", "CitationIntegrity": "1.0"},
        "fresh_context_attested": True,
        "decision": "PASS",
        "findings": [],
        "novelty_closure": "PASS",
        "measurement": "PASS",
        "identification": "FAIL",
        "power": "PASS",
        "access_licence_ethics": "PASS",
    }
    with pytest.raises(DirectorError, match="conjunctive"):
        apply_answer(p, d, answer)


def test_reasoning_declared_external_source_cannot_close_feasibility(tmp_path):
    p = paper(tmp_path, PaperStage.DEVELOPING)
    d = director(tmp_path, p)
    a = ensure_action(p, d)
    assert a.kind is ActionKind.DATA_FEASIBILITY
    answer = {
        "action_id": a.id,
        "execution_role": "SCIENTIFIC_REASONING",
        "skills_used": {"StudyDesignReasoner": "1.0"},
        "requirements": [{"name": "X", "concepts": ["x"], "necessity": "ESSENTIAL"}],
        "external_sources": [{"verified": True, "free": True}],
    }
    with pytest.raises(DirectorError, match="does not accept reasoning-declared external"):
        apply_answer(p, d, answer)


def _freeze_and_lock(tmp_path, pid="MS-V46"):
    freeze = FreezeManifest(
        candidate_id=pid,
        question="Q", estimand="E", design="D", sample_definition="S",
        treatment="T", outcome="Y", dataset_hashes={"x": "a" * 64},
    )
    freeze_path = tmp_path / "freeze.json"
    freeze.save(str(freeze_path))
    (tmp_path / "python").mkdir()
    (tmp_path / "python" / "01_primary.py").write_text("print('ok')\n")
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\nversion='0'\n")
    lock = AnalysisLock.create(freeze, ["python/01_primary.py"], root=str(tmp_path))
    lock_path = tmp_path / "analysis_lock.json"
    lock.save(str(lock_path))
    return freeze, freeze_path, lock, lock_path


def test_analysis_receipt_is_required_for_results_completion(tmp_path):
    p = paper(tmp_path, PaperStage.ANALYZING)
    d = director(tmp_path, p)
    ensure_action(p, d)
    freeze, freeze_path, lock, lock_path = _freeze_and_lock(tmp_path)
    receipt = IntegrityReceipt("ANALYSIS", p.active_paper.paper_id, {
        "freeze_hash": freeze.freeze_hash,
        "analysis_lock_hash": lock.lock_hash,
        "provenance_status": "PASS",
        "publication_status": "PASS",
        "results_manifest_sha256": "b" * 64,
        "workflow_run_id": "123-1",
        "git_sha": "c" * 40,
    })
    receipt_path = tmp_path / "analysis_receipt.json"
    receipt.save(str(receipt_path))
    assert complete_from_analysis_receipt(
        p, d, receipt_path=str(receipt_path), freeze_path=str(freeze_path),
        analysis_lock_path=str(lock_path),
    ) == "RESULTS_COMPLETE"
    assert p.stage is PaperStage.RESULTS_COMPLETE
    assert d.records["results"]["analysis_receipt_id"] == receipt.receipt_id


def test_v46_workflows_use_integrity_director_and_no_historical_default():
    cycle = (ROOT / ".github/workflows/cycle.yml").read_text()
    analysis = (ROOT / ".github/workflows/analysis.yml").read_text()
    assert "coscientist.director_v46" in cycle
    assert "contents: write" not in cycle
    assert "actions: write" in cycle
    assert "default: C705" not in analysis
    assert "git_sha:" in analysis
    assert "analysis_receipt.json" in analysis
