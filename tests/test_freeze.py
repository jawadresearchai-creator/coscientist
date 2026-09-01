"""The outcome lock as a hash, not a flag."""
import json
import pytest

from coscientist.freeze import (
    FreezeError, FreezeManifest, FreezeViolation, freeze_candidate, hash_file, require_freeze,
)
from coscientist.models import Candidate, Construct


QUESTION = "Do owners prune legacy NLP patents at scheduled maintenance windows?"
DESIGN = "did"


def cand(**kw):
    # The candidate carries the constructs its manifest describes. Building the
    # two independently is exactly what freeze_candidate now refuses: a
    # candidate whose treatment is T frozen against a manifest naming WRONG-T
    # meant the object and its authoritative freeze described different
    # experiments from the moment of freezing.
    base = dict(
        id="C705", title="t", question=QUESTION, design=DESIGN,
        constructs=[
            Construct("LEGACY_NLP at-issue classification", "treatment"),
            Construct("NONPAYMENT_LAPSE", "outcome"),
            Construct("grant_cohort", "control"),
            Construct("maintenance_stage", "control"),
        ],
    )
    base.update(kw)
    return Candidate(**base)


def manifest(**kw):
    base = dict(
        candidate_id="C705",
        question=QUESTION,
        estimand="ATT on nonpayment lapse at the 11.5-year stage",
        design="did",
        sample_definition="US utility patents, clean pre/post maintenance windows 2018-2024",
        treatment="LEGACY_NLP at-issue classification",
        outcome="NONPAYMENT_LAPSE",
        controls=["grant_cohort", "maintenance_stage"],
        exclusions=["design patents", "transition windows"],
        models=["LPM with owner x quarter x stage FE"],
        multiplicity_policy="Holm within the secondary family",
        dataset_hashes={"panel": "a" * 64},
    )
    base.update(kw)
    return FreezeManifest(**base)


def test_hash_is_order_independent():
    a = manifest(controls=["b", "a"], exclusions=["y", "x"])
    b = manifest(controls=["a", "b"], exclusions=["x", "y"])
    assert a.freeze_hash == b.freeze_hash


def test_hash_ignores_provenance_metadata():
    """The same design frozen twice is the same design."""
    a = manifest(frozen_at="2026-01-01T00:00:00+00:00", note="first")
    b = manifest(frozen_at="2026-08-31T00:00:00+00:00", note="second")
    assert a.freeze_hash == b.freeze_hash


@pytest.mark.parametrize("field,value", [
    ("question", "a different question"),
    ("estimand", "LATE instead"),
    ("outcome", "PERSISTENT_LAPSE_12M"),
    ("sample_definition", "2018-2023 only"),
    ("multiplicity_policy", "none"),
])
def test_every_design_change_moves_the_hash(field, value):
    before = manifest()
    after = manifest(**{field: value})
    assert before.freeze_hash != after.freeze_hash


def test_changing_the_dataset_moves_the_hash():
    assert manifest().freeze_hash != manifest(dataset_hashes={"panel": "b" * 64}).freeze_hash


def test_freezing_requires_a_dataset_hash():
    c = cand()
    with pytest.raises(FreezeError, match="dataset hash"):
        freeze_candidate(c, manifest(dataset_hashes={}))


def test_freezing_requires_the_design_to_be_specified():
    c = cand()
    with pytest.raises(FreezeError, match="estimand"):
        freeze_candidate(c, manifest(estimand="   "))


def test_freeze_is_a_one_way_door():
    c = cand()
    freeze_candidate(c, manifest())
    with pytest.raises(FreezeError, match="one-way door"):
        freeze_candidate(c, manifest())


def test_freeze_records_the_hash_in_lineage():
    c = cand()
    m = manifest()
    fid = freeze_candidate(c, m)
    assert c.status == "FROZEN"
    assert c.lineage[-1]["freeze_hash"] == m.freeze_hash
    assert fid == m.freeze_id


def test_manifest_mismatch_is_refused():
    c = cand(id="C716")
    with pytest.raises(FreezeError, match="manifest is for"):
        freeze_candidate(c, manifest())


def test_a_tampered_manifest_file_is_detected(tmp_path):
    """The regression test for a flag-based lock.

    status == FROZEN could be edited by anyone. A hash cannot.
    """
    p = tmp_path / "freeze.json"
    manifest().save(str(p))
    raw = json.loads(p.read_text())
    raw["outcome"] = "a quietly different outcome"
    p.write_text(json.dumps(raw))
    with pytest.raises(FreezeViolation, match="altered since it was written"):
        FreezeManifest.load(str(p))


def test_round_trip_survives_save_and_load(tmp_path):
    p = tmp_path / "freeze.json"
    m = manifest()
    m.save(str(p))
    assert FreezeManifest.load(str(p)).freeze_hash == m.freeze_hash


def test_downstream_artifact_must_carry_the_right_hash():
    m = manifest()
    require_freeze(m, m.freeze_id)
    with pytest.raises(FreezeViolation, match="POST_FREEZE_CHANGE"):
        require_freeze(m, "DF-C705-deadbeefcafe")


def test_dataset_hash_is_stable(tmp_path):
    f = tmp_path / "panel.csv"
    f.write_text("cik,year,y\n1,2020,0.5\n")
    assert hash_file(str(f)) == hash_file(str(f))
    assert len(hash_file(str(f))) == 64


def test_a_manifest_without_a_stored_hash_is_invalid(tmp_path):
    """The regression test for the fail-open tamper check.

    `if stored_hash:` meant deleting the checksum disabled verification: strip
    the field, edit the question, and the altered design loaded clean under a
    freshly computed id.
    """
    p = tmp_path / "freeze.json"
    raw = manifest().to_dict()
    del raw["freeze_hash"]
    del raw["freeze_id"]
    raw["question"] = "an entirely different question"
    p.write_text(json.dumps(raw))
    with pytest.raises(FreezeViolation, match="no stored hash"):
        FreezeManifest.load(str(p))


def test_a_mismatched_stored_id_is_invalid(tmp_path):
    p = tmp_path / "freeze.json"
    raw = manifest().to_dict()
    raw["freeze_id"] = "DF-C705-000000000000"
    p.write_text(json.dumps(raw))
    with pytest.raises(FreezeViolation, match="derives"):
        FreezeManifest.load(str(p))


def test_resaving_the_same_design_does_not_rewrite_its_history(tmp_path):
    """Same hash must be a no-op, not an overwrite.

    The id stayed stable while frozen_at, engine_version and note were quietly
    replaced -- the freeze's own provenance was rewritable.
    """
    p = tmp_path / "freeze.json"
    manifest(note="original", frozen_at="2026-01-01T00:00:00+00:00").save(str(p))
    returned = manifest(note="rewritten", frozen_at="2099-01-01T00:00:00+00:00").save(str(p))
    on_disk = json.loads(p.read_text())
    assert on_disk["note"] == "original"
    assert on_disk["frozen_at"].startswith("2026")
    assert returned == manifest().freeze_id


def test_there_is_no_replace_escape_hatch():
    import inspect
    assert "allow_replace" not in inspect.signature(FreezeManifest.save).parameters


def test_freezing_sets_the_hash_on_the_candidate():
    c = cand()
    assert not c.is_frozen()
    m = manifest()
    freeze_candidate(c, m)
    assert c.is_frozen()
    assert c.freeze_hash == m.freeze_hash
    assert c.freeze_id == m.freeze_id


def test_drifted_dataset_blocks(tmp_path):
    """freeze-verify once printed DRIFTED and then 'freeze intact', exit 0."""
    from coscientist.freeze import require_datasets, verify_datasets
    data = tmp_path / "data"
    data.mkdir()
    f = data / "panel.csv"
    f.write_text("a,b\n1,2\n")
    m = manifest(dataset_hashes={"panel.csv": hash_file(str(f))})
    assert all(c.ok for c in verify_datasets(m, str(data)))
    require_datasets(m, str(data))

    f.write_text("a,b\n9,9\n")
    assert [c.status for c in verify_datasets(m, str(data))] == ["DRIFTED"]
    with pytest.raises(FreezeViolation, match="not as frozen"):
        require_datasets(m, str(data))

    f.unlink()
    assert [c.status for c in verify_datasets(m, str(data))] == ["MISSING"]
    with pytest.raises(FreezeViolation, match="not as frozen"):
        require_datasets(m, str(data))


def test_a_manifest_that_disagrees_with_its_candidate_is_refused():
    """Two independently built objects could describe different studies.

    freeze_candidate only compared ids, so a candidate asking QUESTION A could
    be frozen against a manifest describing QUESTION B -- and the in-memory
    object and its authoritative freeze disagreed from the moment of creation.
    """
    with pytest.raises(FreezeError, match="disagree on question"):
        freeze_candidate(cand(question="a different question"), manifest())
    with pytest.raises(FreezeError, match="disagree on design"):
        freeze_candidate(cand(design="event_study"), manifest())


def test_manifest_from_candidate_cannot_disagree():
    from coscientist.freeze import manifest_from_candidate
    from coscientist.models import Construct
    c = cand(constructs=[
        Construct("nlp", "treatment", ["x"]),
        Construct("lapse", "outcome", ["y"]),
        Construct("cohort", "control", ["z"]),
    ])
    m = manifest_from_candidate(
        c, estimand="ATT on lapse", sample_definition="clean windows 2018-2024",
        dataset_hashes={"panel": "a" * 64})
    assert m.question == c.question and m.design == c.design
    assert m.treatment == "nlp" and m.outcome == "lapse" and m.controls == ["cohort"]
    freeze_candidate(c, m)          # agrees by construction
    assert c.is_frozen()


def test_the_freeze_marker_cannot_be_cleared():
    """A lock that can be set to None is not a lock."""
    from coscientist.models import FrozenDesignViolation
    c = cand()
    freeze_candidate(c, manifest())
    with pytest.raises(FrozenDesignViolation, match="cannot be changed or cleared"):
        c.freeze_hash = None
    assert c.is_frozen()


def test_a_frozen_candidate_cannot_be_edited():
    """The write-once guard covered the marker, not the thing it marks.

    `candidate.freeze_hash = None` was refused, so the lock could not be
    cleared -- while `candidate.question = "..."` succeeded, so the design it
    described drifted freely around a hash that could no longer be removed.
    """
    from coscientist.models import FrozenDesignViolation

    c = cand()
    freeze_candidate(c, manifest())
    for attr, value in (("question", "a different question"), ("design", "panel"),
                        ("title", "renamed"), ("plausible_effect", 99.0),
                        ("constructs", [])):
        with pytest.raises(FrozenDesignViolation, match="is frozen"):
            setattr(c, attr, value)
    # Bookkeeping stays writable: these are labels, not science.
    c.status = "PUBLISHED"
    c.log("note", detail="still allowed")


def test_sealing_reaches_the_constructs():
    """Guarding the candidate left the object graph open one level down.

    `candidate.constructs[0].preferred_source = "OTHER"` never touched the
    candidate at all, and swapping which data measures the treatment below the
    outcome lock is as much a design change as rewriting the question.
    """
    from coscientist.models import FrozenDesignViolation

    c = cand()
    freeze_candidate(c, manifest())
    with pytest.raises(FrozenDesignViolation, match="sealed"):
        c.constructs[0].preferred_source = "SOME_OTHER_SOURCE"
    with pytest.raises(FrozenDesignViolation, match="sealed"):
        c.constructs[0].granularity = "country-year"


def test_a_candidate_and_its_freeze_must_describe_one_study():
    """SHARED covered only the two plain strings.

    So a candidate whose treatment was T could be frozen against a manifest
    naming WRONG-T, and the in-memory object and its authoritative freeze
    described different experiments from the instant of freezing.
    """
    c = cand()
    with pytest.raises(FreezeError, match="treatment"):
        freeze_candidate(c, manifest(treatment="SOMETHING ELSE ENTIRELY"))

    c2 = cand()
    with pytest.raises(FreezeError, match="outcome"):
        freeze_candidate(c2, manifest(outcome="A DIFFERENT OUTCOME"))

    c3 = cand()
    with pytest.raises(FreezeError, match="controls"):
        freeze_candidate(c3, manifest(controls=["not", "these"]))
