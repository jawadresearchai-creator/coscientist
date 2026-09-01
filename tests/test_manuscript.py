"""The S5 to S6 handoff: small, machine-readable, hash-stamped."""
import json
import os

from coscientist.freeze import FreezeManifest
from coscientist.manuscript import ResultRecord, ResultsBundle, new_bundle


def fm():
    return FreezeManifest(candidate_id="C705", question="q", estimand="e", design="did",
                          sample_definition="s", treatment="t", outcome="o",
                          dataset_hashes={"panel": "a" * 64})


def filled():
    b = new_bundle(fm())
    b.add(ResultRecord(token="h1", label="LEGACY_NLP x POST", value=2.4137, units="pp",
                       se=0.4471, ci_low=1.5219, ci_high=3.3055, p_value=0.0003,
                       n_obs=3246, n_clusters=118, model="LPM", script="R/03.R",
                       family="primary", adjusted="Holm"))
    b.add(ResultRecord(token="rb1", label="24m bandwidth", value=2.28,
                       family="robustness", script="R/08.R"))
    b.figures = [{"id": "Figure_1", "question": "Does exposure change lapse?",
                  "script": "R/10_figures.R", "result_token": "h1", "dpi": 600}]
    return b


def test_bundle_carries_the_freeze_hash():
    b = filled()
    assert b.freeze_hash == fm().freeze_hash
    assert b.freeze_id.startswith("DF-C705-")


def test_write_emits_the_bridge_files(tmp_path):
    out = str(tmp_path / "manuscript")
    written = filled().write(out)
    names = {os.path.basename(p) for p in written}
    assert {"PRIMARY_RESULTS.csv", "EFFECT_SIZES.csv",
            "RESULTS_SUMMARY.md", "ANALYSIS_MANIFEST.json"} <= names
    assert "FIGURE_MANIFEST.md" in names


def test_primary_csv_excludes_robustness_rows(tmp_path):
    out = str(tmp_path / "m")
    filled().write(out)
    primary = open(os.path.join(out, "PRIMARY_RESULTS.csv")).read()
    effects = open(os.path.join(out, "EFFECT_SIZES.csv")).read()
    assert "h1" in primary and "rb1" not in primary
    assert "rb1" in effects


def test_summary_is_readable_and_names_provenance(tmp_path):
    out = str(tmp_path / "m")
    filled().write(out)
    text = open(os.path.join(out, "RESULTS_SUMMARY.md")).read()
    for expected in ("`h1`", "95% CI", "R/03.R", "Holm", "3246"):
        assert expected in text, expected


def test_manifest_answers_which_dataset_made_this(tmp_path):
    """The question a reviewer asks: which data produced Figure 4?"""
    out = str(tmp_path / "m")
    filled().write(out)
    raw = json.load(open(os.path.join(out, "ANALYSIS_MANIFEST.json")))
    assert raw["freeze_hash"] == fm().freeze_hash
    assert raw["figures"][0]["result_token"] == "h1"


def test_bundle_round_trips_through_its_manifest(tmp_path):
    out = str(tmp_path / "m")
    filled().write(out)
    back = ResultsBundle.from_manifest(os.path.join(out, "ANALYSIS_MANIFEST.json"))
    assert back.freeze_hash == fm().freeze_hash
    assert back.get("h1").ci_high == 3.3055


def test_editing_anything_in_the_manifest_is_detected(tmp_path):
    """`results_hash` covered the records and nothing else.

    So a figure entry could be repointed at a different picture, or the
    recorded commit swapped, and the manifest still verified. The digest has to
    cover everything the manifest asserts, not just the part that is numeric.
    """
    import pytest

    from coscientist.manuscript import BridgeError

    out = str(tmp_path / "m")
    filled().write(out)
    path = os.path.join(out, "ANALYSIS_MANIFEST.json")

    raw = json.load(open(path))
    raw["figures"][0]["result_token"] = "a_result_that_does_not_exist"
    json.dump(raw, open(path, "w"), indent=2, sort_keys=True)

    with pytest.raises(BridgeError, match="altered since it was written"):
        ResultsBundle.from_manifest(path)


def test_editing_a_result_value_is_detected(tmp_path):
    import pytest

    from coscientist.manuscript import BridgeError

    out = str(tmp_path / "m")
    filled().write(out)
    path = os.path.join(out, "ANALYSIS_MANIFEST.json")
    raw = json.load(open(path))
    raw["records"][0]["value"] = 9.99
    json.dump(raw, open(path, "w"), indent=2, sort_keys=True)
    with pytest.raises(BridgeError, match="altered since it was written"):
        ResultsBundle.from_manifest(path)


def test_the_bundle_records_what_produced_it(tmp_path):
    """Design -> data -> code -> results. Without the code and the environment
    the chain stops at the data, and 'reproducible' stops at the repo boundary."""
    from coscientist.cli import environment_hash

    b = filled()
    b.script_hashes = {"R/03.R": "c" * 64}
    b.env_hash = environment_hash(".")
    out = str(tmp_path / "m")
    b.write(out)
    raw = json.load(open(os.path.join(out, "ANALYSIS_MANIFEST.json")))
    assert raw["script_hashes"]["R/03.R"] == "c" * 64
    assert len(raw["env_hash"]) == 64


def test_a_manifest_stripped_of_its_integrity_hashes_is_refused(tmp_path):
    """The identical mistake already fixed once in FreezeManifest.load.

    `if stored and stored != ...` made the check conditional on the very field
    an attacker would remove. Delete `results_hash` and `bundle_hash` and the
    manifest loaded happily with a rewritten estimate. Fixing the instance in
    the freeze did not stop the class reappearing in the bundle, which is the
    whole reason GUARANTEES.yaml exists.
    """
    import pytest

    from coscientist.manuscript import BridgeError

    out = str(tmp_path / "m")
    filled().write(out)
    path = os.path.join(out, "ANALYSIS_MANIFEST.json")

    for dropped in (["bundle_hash"], ["results_hash"], ["bundle_hash", "results_hash"]):
        raw = json.load(open(path))
        for key in dropped:
            raw.pop(key, None)
        raw["records"][0]["value"] = 999.0
        target = path + ".tampered"
        json.dump(raw, open(target, "w"))
        with pytest.raises(BridgeError):
            ResultsBundle.from_manifest(target)
