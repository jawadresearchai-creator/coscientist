"""Pre-specification, not just reproducibility.

The design freeze locks the question and the data. It does not lock the code,
so this was possible and nothing in the system could see it:

    design freezes -> outcomes visible -> edit 03_primary_models.R ->
    try another specification -> keep the one that works -> collect()

Afterwards the manifest says, truthfully, "this result was produced by this
script". What it could not say is "this is the script that existed before
anyone looked at the outcomes". Only the second supports a confirmatory claim.
"""
import os

import pytest

from coscientist.analysis_lock import (AnalysisLock, AnalysisLockViolation,
                                       discover_scripts, execution_plan, scan_io)
from coscientist.freeze import FreezeManifest


def fm(**kw):
    base = dict(candidate_id="C705", question="q", estimand="e", design="did",
                sample_definition="s", treatment="t", outcome="o",
                dataset_hashes={"panel.csv": "a" * 64})
    base.update(kw)
    return FreezeManifest(**base)


@pytest.fixture
def repo(tmp_path):
    """A minimal repo: one R script, one Python script, one environment lock."""
    (tmp_path / "R").mkdir()
    (tmp_path / "python").mkdir()
    (tmp_path / "R" / "03_primary_models.R").write_text(
        'source("R/00_setup.R"); cos_setup()\n'
        'd <- cos_data("panel.csv")\n'
        'm <- lm(y ~ treat_post, data = d)\n')
    (tmp_path / "python" / "05_tables.py").write_text("x = 1\n")
    (tmp_path / "renv.lock").write_text('{"R": {"Version": "4.3.3"}}\n')
    return tmp_path


def lock(repo, **kw):
    return AnalysisLock.create(fm(), discover_scripts(str(repo)), root=str(repo), **kw)


def test_a_lock_hashes_every_script_and_the_environment(repo):
    al = lock(repo)
    assert set(al.script_hashes) == {"R/03_primary_models.R", "python/05_tables.py"}
    assert "renv.lock" in al.env_hashes
    assert al.lock_id.startswith("AL-DF-C705-")


def test_an_edited_script_fails_verification(repo):
    """The finding this module exists for."""
    al = lock(repo)
    (repo / "R" / "03_primary_models.R").write_text(
        'source("R/00_setup.R"); cos_setup()\n'
        'd <- cos_data("panel.csv")\n'
        'm <- lm(y ~ treat_post + i(quarter), data = d)   # tried this after seeing y\n')
    problems = al.verify(str(repo))
    assert any("edited since the lock" in p for p in problems)
    with pytest.raises(AnalysisLockViolation, match="not the code that was locked"):
        al.require(str(repo))


def test_a_script_added_after_the_lock_fails_verification(repo):
    """Both directions.

    Checking only that locked scripts are unchanged would let a new
    `07_extra_models.R` appear after the lock and run alongside them, which is
    the specification search this prevents wearing a different hat.
    """
    al = lock(repo)
    (repo / "R" / "07_extra_models.R").write_text("# one more specification\n")
    assert any("not locked" in p for p in al.verify(str(repo)))


def test_a_deleted_script_fails_verification(repo):
    al = lock(repo)
    os.remove(repo / "python" / "05_tables.py")
    assert any("missing" in p for p in al.verify(str(repo)))


def test_a_changed_environment_fails_verification(repo):
    """Locking the code without locking what it runs on records half a claim."""
    al = lock(repo)
    (repo / "renv.lock").write_text('{"R": {"Version": "4.4.0"}}\n')
    assert any("environment changed" in p for p in al.verify(str(repo)))


def test_a_lock_from_a_different_design_is_refused(repo):
    al = lock(repo)
    other = fm(question="an entirely different question")
    assert any("this lock was made for freeze" in p
               for p in al.verify(str(repo), freeze=other))


def test_the_lock_is_write_once(repo, tmp_path):
    """A lock that can be rewritten after outcomes are visible is a note."""
    path = str(tmp_path / "analysis_lock.json")
    lock(repo).save(path)
    (repo / "R" / "03_primary_models.R").write_text("# a different analysis\n")
    with pytest.raises(AnalysisLockViolation, match="already exists"):
        lock(repo).save(path)


def test_resaving_an_identical_lock_is_not_an_error(repo, tmp_path):
    path = str(tmp_path / "analysis_lock.json")
    lock(repo).save(path)
    lock(repo).save(path)          # same code, same hash, no drama


def test_a_lock_without_its_hash_is_invalid(repo, tmp_path):
    """Fail closed, like the freeze and the results manifest before it.

    Deleting a checksum is not an instruction to skip checking.
    """
    import json
    path = str(tmp_path / "analysis_lock.json")
    lock(repo).save(path)
    raw = json.load(open(path))
    del raw["lock_hash"]
    json.dump(raw, open(path, "w"))
    with pytest.raises(AnalysisLockViolation, match="carries no lock hash"):
        AnalysisLock.load(path)


def test_a_tampered_lock_is_detected(repo, tmp_path):
    import json
    path = str(tmp_path / "analysis_lock.json")
    lock(repo).save(path)
    raw = json.load(open(path))
    raw["script_hashes"]["R/03_primary_models.R"] = "b" * 64
    json.dump(raw, open(path, "w"))
    with pytest.raises(AnalysisLockViolation, match="altered since it was written"):
        AnalysisLock.load(path)


def test_locking_nothing_is_refused(repo):
    with pytest.raises(AnalysisLockViolation, match="no analysis scripts"):
        AnalysisLock.create(fm(), [], root=str(repo))


def test_locking_without_an_environment_file_is_refused(repo):
    os.remove(repo / "renv.lock")
    with pytest.raises(AnalysisLockViolation, match="no environment lock"):
        lock(repo)


# ---------------------------------------------------------------- IO scan ---

def test_unsanctioned_reads_block_the_lock_until_declared(repo):
    """R cannot be sandboxed from here, so this records rather than prevents.

    `cos_data()` refuses an unfrozen dataset, but R can still call `read.csv`,
    `download.file` or `system()` directly -- it is a sanctioned loader, not a
    capability boundary. Pretending otherwise would be the fail-open pattern
    again. Instead every such call is found, hashed into the lock, and has to
    be acknowledged, so unsanctioned acquisition is a declared fact.
    """
    (repo / "R" / "04_extra.R").write_text(
        'extra <- read.csv("data/helpful_but_unfrozen.csv")\n'
        'download.file("https://example.com/more.csv", "more.csv")\n')
    with pytest.raises(AnalysisLockViolation, match="unsanctioned IO"):
        lock(repo)

    al = lock(repo, acknowledge_io=True)
    assert al.io_acknowledged
    whys = {f["why"] for f in al.io_findings}
    assert "direct file read outside cos_data()" in whys
    assert "network access" in whys


def test_reseeding_is_found_as_a_declared_call(repo):
    """`cos_seed()` derives the seed from the design, but nothing stops a script
    calling `set.seed(12345)` afterwards. Once the code is locked, changing a
    seed changes a hash -- and the scan makes the call visible in the first place."""
    (repo / "R" / "06_bootstrap.R").write_text("set.seed(12345)\nb <- 1\n")
    findings = scan_io(discover_scripts(str(repo)), str(repo))
    assert any("reseeds outside cos_seed()" in f.why for f in findings)


def test_the_engine_layer_is_exempt_from_the_scan(tmp_path):
    """cos_data() must call fread() or it cannot load anything at all."""
    (tmp_path / "R").mkdir()
    (tmp_path / "R" / "00_setup.R").write_text('d <- data.table::fread(path)\n')
    assert scan_io(["R/00_setup.R"], str(tmp_path)) == []


def test_comments_are_not_findings(repo):
    (repo / "R" / "08_notes.R").write_text('# do not use read.csv here\nx <- 1\n')
    assert not [f for f in scan_io(["R/08_notes.R"], str(repo))]


def test_the_execution_plan_is_the_numbered_scripts_in_order(repo):
    (repo / "R" / "helpers.R").write_text("f <- function() 1\n")
    assert execution_plan(str(repo)) == ["R/03_primary_models.R", "python/05_tables.py"]
