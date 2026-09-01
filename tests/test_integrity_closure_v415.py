"""Regression tests for the v4.1.5 execution-integrity closure.

These are deliberately adversarial: each test reproduces a path that walked
around a guarantee in v4.1.4.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

import pytest

from coscientist.analysis_lock import (AnalysisLock, AnalysisLockViolation,
                                        discover_scripts, execution_plan)
from coscientist.drive import DriveError
from coscientist.emit import EmitError, emit_done, emit_row, read_stream, stream_stem
from coscientist.freeze import FreezeManifest, freeze_candidate, manifest_from_candidate
from coscientist.gates import g3_access
from coscientist.models import AccessClass, Candidate, Construct, FrozenDesignViolation, Verdict
from coscientist.registry import Source, SourceRegistry
import coscientist.cli as cli


def _candidate() -> Candidate:
    return Candidate(
        id="C1", title="t", question="q", design="did",
        constructs=[Construct("T", "treatment", ["t"]),
                    Construct("O", "outcome", ["o"])],
    )


def _freeze(c: Candidate) -> FreezeManifest:
    return manifest_from_candidate(
        c, estimand="e", sample_definition="s",
        dataset_hashes={"panel.csv": "a" * 64},
    )


def test_freeze_converts_scientific_containers_to_immutable_tuples():
    c = _candidate()
    m = _freeze(c)
    freeze_candidate(c, m)
    assert isinstance(c.constructs, tuple)
    assert isinstance(c.constructs[0].concepts, tuple)
    with pytest.raises(AttributeError):
        c.constructs.append(Construct("X", "control"))
    with pytest.raises(AttributeError):
        c.constructs[0].concepts.append("new")


def test_python_emitter_identity_comes_from_runner(tmp_path, monkeypatch):
    monkeypatch.setenv("COSCIENTIST_CURRENT_SCRIPT", "python/03_actual.py")
    wrong = tmp_path / f"{stream_stem('python/04_other.py')}.results.jsonl"
    with pytest.raises(EmitError, match="different script stream|tried to attribute"):
        emit_row(str(wrong), token="x", label="x", value=1.0,
                 script="python/04_other.py")

    right = tmp_path / f"{stream_stem('python/03_actual.py')}.results.jsonl"
    emit_row(str(right), token="x", label="x", value=1.0,
             script="python/03_actual.py")
    emit_done(str(right), "python/03_actual.py")
    stream = read_stream(str(right))
    assert stream.script == "python/03_actual.py"


def test_completion_sentinel_must_agree_with_record_and_filename(tmp_path):
    p = tmp_path / f"{stream_stem('R/03_actual.R')}.results.jsonl"
    p.write_text(
        json.dumps({"token": "x", "label": "x", "value": 1.0,
                    "script": "R/03_actual.R"}) + "\n" +
        json.dumps({"__complete__": True, "script": "R/04_other.R"}) + "\n"
    )
    with pytest.raises(EmitError, match="sentinel|producer witness"):
        read_stream(str(p))


def test_execution_plan_is_global_numeric_order_and_stan_is_not_a_driver(tmp_path):
    for d in ("R", "python", "stan"):
        (tmp_path / d).mkdir()
    (tmp_path / "R/09_post.R").write_text("x <- 1")
    (tmp_path / "python/03_pre.py").write_text("x = 1")
    (tmp_path / "stan/01_model.stan").write_text("parameters { real y; }")
    assert execution_plan(str(tmp_path)) == ["python/03_pre.py", "R/09_post.R"]
    assert "stan/01_model.stan" in discover_scripts(str(tmp_path))


def test_analysis_lock_stores_and_verifies_the_actual_execution_plan(tmp_path):
    (tmp_path / "R").mkdir()
    (tmp_path / "R/03_primary.R").write_text("x <- 1")
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\nversion='1'\n")
    c = _candidate(); f = _freeze(c)
    lock = AnalysisLock.create(f, discover_scripts(str(tmp_path)), root=str(tmp_path))
    assert lock.execution_order == ["R/03_primary.R"]
    (tmp_path / "python").mkdir()
    (tmp_path / "python/02_new.py").write_text("x = 1")
    problems = lock.verify(str(tmp_path), freeze=f)
    assert any("execution plan changed" in p for p in problems)


def test_analysis_lock_receipt_protects_timestamp_and_version(tmp_path):
    (tmp_path / "R").mkdir()
    (tmp_path / "R/03_primary.R").write_text("x <- 1")
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\nversion='1'\n")
    f = _freeze(_candidate())
    lock = AnalysisLock.create(f, discover_scripts(str(tmp_path)), root=str(tmp_path))
    path = tmp_path / "lock.json"
    lock.save(str(path))
    raw = json.loads(path.read_text())
    raw["locked_at"] = "1999-01-01T00:00:00+00:00"
    path.write_text(json.dumps(raw))
    with pytest.raises(AnalysisLockViolation, match="receipt"):
        AnalysisLock.load(str(path))


def test_g3_defers_when_essential_source_suitability_is_unknown():
    src = Source(id="S", name="unknown", access_class=AccessClass.OPEN,
                 redistributable=True, concepts=["patent"])
    reg = SourceRegistry({"S": src})
    c = Candidate(id="C", title="t", question="q", design="did",
                  constructs=[Construct("O", "outcome", ["patent"],
                                        granularity="patent-event",
                                        coverage_start="2010-01-01",
                                        coverage_end="2024-12-31")])
    result = g3_access(c, reg)
    assert result.verdict is Verdict.DEFER
    assert "unverified" in result.reason


def test_analysis_verify_fails_closed_when_design_freeze_is_missing(tmp_path):
    (tmp_path / "R").mkdir()
    (tmp_path / "R/03_primary.R").write_text("x <- 1")
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\nversion='1'\n")
    f = _freeze(_candidate())
    lock = AnalysisLock.create(f, discover_scripts(str(tmp_path)), root=str(tmp_path))
    lp = tmp_path / "lock.json"; lock.save(str(lp))
    rc = cli.cmd_analysis_verify(argparse.Namespace(
        lock=str(lp), freeze=str(tmp_path / "missing.json"), root=str(tmp_path)))
    assert rc == 2


def test_publish_writes_completion_marker_last(monkeypatch, tmp_path):
    (tmp_path / "a.txt").write_text("a")
    (tmp_path / "b.txt").write_text("b")
    calls = []
    class Fake:
        def create_folder(self, name, parent):
            calls.append(("folder", name, parent)); return "run-folder"
        def upload(self, path, folder, name=None):
            calls.append(("upload", name, folder)); return f"id-{name}"
    monkeypatch.setattr(cli, "_drive", lambda: Fake())
    rc = cli.cmd_publish(argparse.Namespace(
        folder="root", dir=str(tmp_path), study="C1", run_id="42", mode="confirmatory"))
    assert rc == 0
    uploaded = [x[1] for x in calls if x[0] == "upload"]
    assert uploaded[-1] == "_COMPLETE.json"


def test_publish_failure_never_writes_completion_marker(monkeypatch, tmp_path):
    (tmp_path / "a.txt").write_text("a")
    (tmp_path / "b.txt").write_text("b")
    calls = []
    class Fake:
        def create_folder(self, name, parent): return "run-folder"
        def upload(self, path, folder, name=None):
            calls.append(name)
            if name == "b.txt": raise DriveError("network died")
            return "id"
    monkeypatch.setattr(cli, "_drive", lambda: Fake())
    rc = cli.cmd_publish(argparse.Namespace(
        folder="root", dir=str(tmp_path), study="C1", run_id="42", mode="confirmatory"))
    assert rc == 2
    assert "_COMPLETE.json" not in calls


def test_state_fetch_bootstraps_self_validating_freeze_and_lock(monkeypatch, tmp_path):
    repo = tmp_path / "repo"; repo.mkdir()
    (repo / "R").mkdir(); (repo / "R/03_primary.R").write_text("x <- 1")
    (repo / "pyproject.toml").write_text("[project]\nname='x'\nversion='1'\n")
    c = _candidate(); f = _freeze(c)
    freeze_src = tmp_path / "freeze-src.json"; f.save(str(freeze_src))
    lock = AnalysisLock.create(f, discover_scripts(str(repo)), root=str(repo))
    lock_src = tmp_path / "lock-src.json"; lock.save(str(lock_src))

    class Fake:
        def fetch_study_state(self, root, study, dest):
            import shutil, hashlib
            Path(dest).mkdir(parents=True, exist_ok=True)
            shutil.copy2(freeze_src, Path(dest) / "freeze.json")
            shutil.copy2(lock_src, Path(dest) / "analysis_lock.json")
            return {
                "freeze.json": hashlib.sha256((Path(dest) / "freeze.json").read_bytes()).hexdigest(),
                "analysis_lock.json": hashlib.sha256((Path(dest) / "analysis_lock.json").read_bytes()).hexdigest(),
            }
    monkeypatch.setattr(cli, "_drive", lambda: Fake())
    dest = tmp_path / "state"
    rc = cli.cmd_state_fetch(argparse.Namespace(folder="root", study="C1", dest=str(dest)))
    assert rc == 0
    assert FreezeManifest.load(str(dest / "freeze.json")).candidate_id == "C1"
    assert AnalysisLock.load(str(dest / "analysis_lock.json")).freeze_hash == f.freeze_hash
