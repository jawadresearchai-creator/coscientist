"""The R/Python joint.

The econometrics are R; the results bridge is Python. This is the contract
between them, and these are the ways an analysis can lie to the bridge.
"""
import json
import os
import shutil
import subprocess

import pytest

from coscientist.emit import (EmitError, collect, emit_done, emit_row,
                              read_stream, record_from_row, stream_stem)
from coscientist.freeze import FreezeManifest
from coscientist.manuscript import new_bundle

R_AVAILABLE = shutil.which("Rscript") is not None
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def fm():
    return FreezeManifest(candidate_id="C705", question="q", estimand="e", design="did",
                          sample_definition="s", treatment="t", outcome="o",
                          dataset_hashes={"panel.csv": "a" * 64})


def good(**over):
    row = {"token": "h1", "label": "Exposure x Post", "value": 2.41, "units": "pp",
           "se": 0.44, "ci_low": 1.52, "ci_high": 3.31, "p_value": 0.0003,
           "n_obs": 3246, "n_clusters": 118, "script": "R/03.R", "family": "primary"}
    row.update(over)
    return row


def stream(tmp_path, rows, *, seal=True, script="R/03.R"):
    """Name the file the way `emitter()` does.

    The filename is derived from the executing script, and that is what makes
    it a third witness alongside the sentinel and each record's own claim.
    """
    d = tmp_path / "streams"
    d.mkdir(exist_ok=True)
    path = str(d / f"{stream_stem(script)}.results.jsonl")
    with open(path, "w") as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")
        if seal:
            fh.write(json.dumps({"__complete__": True}) + "\n")
    return str(d)


def test_a_well_formed_record_survives():
    rec = record_from_row(good())
    assert rec.token == "h1" and rec.n_clusters == 118


def test_broom_column_names_are_the_native_vocabulary():
    """`tidy(feols(...))` goes straight through, with no rename step.

    Requiring the analyst to rename columns by hand would reintroduce the
    transcription this whole bridge exists to remove.
    """
    rec = record_from_row({"token": "h1", "label": "l", "script": "R/03.R",
                           "estimate": 2.41, "std.error": 0.44, "conf.low": 1.5,
                           "conf.high": 3.3, "p.value": 0.01, "nobs": 100})
    assert (rec.value, rec.se, rec.ci_low, rec.ci_high, rec.p_value, rec.n_obs) == \
           (2.41, 0.44, 1.5, 3.3, 0.01, 100)


def test_an_alias_collision_is_refused_rather_than_resolved():
    with pytest.raises(EmitError, match="both set"):
        record_from_row(good(estimate=9.99))


@pytest.mark.parametrize("bad,why", [
    ({"ci_low": 2.0, "ci_high": 3.0, "value": 1.0}, "estimate outside its own interval"),
    ({"ci_low": 3.0, "ci_high": 2.0}, "bounds swapped"),
    ({"ci_low": 1.0, "ci_high": None}, "half an interval"),
    ({"se": -0.4}, "negative standard error"),
    ({"p_value": 1.4}, "p outside [0, 1]"),
    ({"n_obs": 10, "n_clusters": 50}, "more clusters than observations"),
    ({"family": "headline"}, "unrecognised family"),
])
def test_statistics_that_cannot_be_true_are_refused(bad, why):
    """Each of these is the signature of arguments passed in the wrong order.

    That mistake produces a table which reads perfectly well, so it has to be
    caught mechanically rather than by rereading.
    """
    with pytest.raises(EmitError):
        record_from_row(good(**bad))


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_non_finite_values_are_refused(value):
    """A model that did not converge returns NaN.

    `float()` accepts it, the validator then compares NaN to prose, every
    comparison is false, and every check passes vacuously. A number that is
    not a number is not a result.
    """
    with pytest.raises(EmitError, match="non-finite|not numeric|is nan|is inf"):
        record_from_row(good(value=value))


def test_a_record_must_name_the_script_that_produced_it():
    with pytest.raises(EmitError, match="script"):
        record_from_row(good(script=""))


def test_a_truncated_stream_refuses_the_whole_bundle(tmp_path):
    """The failure this contract exists for.

    A script that dies two thirds through leaves a well-formed file containing
    half an analysis. The records in it are real, which is exactly what makes
    them dangerous: they assemble into a bundle that looks finished, and no
    artefact anywhere says the models stopped running.
    """
    d = stream(tmp_path, [good()], seal=False)
    with pytest.raises(EmitError, match="completion sentinel"):
        collect(d, new_bundle(fm()))


def test_a_sealed_stream_assembles(tmp_path):
    b = collect(stream(tmp_path, [good()]), new_bundle(fm()))
    assert b.get("h1").value == 2.41


def test_records_appended_after_the_seal_are_refused(tmp_path):
    d = tmp_path / "s"
    d.mkdir()
    p = d / "03.R.results.jsonl"
    p.write_text(json.dumps(good()) + "\n"
                 + json.dumps({"__complete__": True}) + "\n"
                 + json.dumps(good(token="h2")) + "\n")
    with pytest.raises(EmitError, match="after the completion sentinel"):
        read_stream(str(p))


def test_the_same_token_from_two_scripts_is_refused(tmp_path):
    d = tmp_path / "s"
    d.mkdir()
    for script in ("R/03.R", "R/04.R"):
        (d / f"{stream_stem(script)}.results.jsonl").write_text(
            json.dumps(good(script=script)) + "\n" + json.dumps({"__complete__": True}) + "\n")
    with pytest.raises(EmitError, match="emitted by both"):
        collect(str(d), new_bundle(fm()))


def test_a_result_whose_script_was_never_hashed_is_refused(tmp_path):
    """The provenance chain runs design -> data -> code -> results.

    A record naming a script outside the hashed set is a number whose code is
    not in that chain, so the bundle cannot answer what produced it.
    """
    d = stream(tmp_path, [good(script="R/not_in_repo.R")], script="R/not_in_repo.R")
    with pytest.raises(EmitError, match="never hashed"):
        collect(d, new_bundle(fm()), known_scripts=["R/03.R"])


def test_an_empty_results_directory_is_refused(tmp_path):
    (tmp_path / "streams").mkdir()
    with pytest.raises(EmitError, match="no .* streams"):
        collect(str(tmp_path / "streams"), new_bundle(fm()))


def test_a_partial_line_is_reported_as_such(tmp_path):
    d = tmp_path / "s"
    d.mkdir()
    (d / "03.R.results.jsonl").write_text('{"token": "h1", "lab')
    with pytest.raises(EmitError, match="not valid JSON"):
        read_stream(str(d / "03.R.results.jsonl"))


def test_the_python_emitter_validates_before_writing(tmp_path):
    p = str(tmp_path / "s" / "03.R.results.jsonl")
    with pytest.raises(EmitError):
        emit_row(p, **good(se=-1.0))
    emit_row(p, **good())
    emit_done(p, "R/03.R")
    assert collect(os.path.dirname(p), new_bundle(fm())).get("h1").value == 2.41


@pytest.mark.skipif(not R_AVAILABLE, reason="Rscript not installed")
def test_the_r_emitter_writes_what_python_reads(tmp_path):
    """The contract test. Both halves, one round trip, no hand-written JSON.

    Everything above tests the Python side against JSON that Python wrote. This
    is the only test that proves the two languages actually agree, and it is
    the one that would have caught the emitter creating its stream in a
    directory it never made.
    """
    out = tmp_path / "streams"
    script = tmp_path / "probe.R"
    script.write_text(f'''
source("{ROOT}/R/emit.R")
em <- emitter("R/03.R", dir = "{out}")
td <- data.frame(term = "treat_post", estimate = 2.4137, std.error = 0.4471,
                 conf.low = 1.5219, conf.high = 3.3055, p.value = 0.00031,
                 stringsAsFactors = FALSE)
em$tidy(td, keep = "treat_post", prefix = "h1_", units = "pp",
        n_obs = 3246L, n_clusters = 118L, model = "TWFE")
em$done()
''')
    proc = subprocess.run(["Rscript", str(script)], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr

    b = collect(str(out), new_bundle(fm()), known_scripts=["R/03.R"])
    rec = b.get("h1_treat_post")
    # Full double precision, not jsonlite's four-decimal default -- a rounded
    # coefficient would validate against itself and nothing would report it.
    assert rec.value == 2.4137 and rec.se == 0.4471
    assert rec.render("ci") == "1.52 to 3.31"
    assert rec.n_clusters == 118


@pytest.mark.skipif(not R_AVAILABLE, reason="Rscript not installed")
def test_an_r_script_that_dies_never_seals_its_stream(tmp_path):
    out = tmp_path / "streams"
    script = tmp_path / "dies.R"
    script.write_text(f'''
source("{ROOT}/R/emit.R")
em <- emitter("R/09.R", dir = "{out}")
em$row(token = "ok1", label = "a real coefficient", value = 1.5, se = 0.2)
stop("model 2 failed to converge")
''')
    assert subprocess.run(["Rscript", str(script)], capture_output=True).returncode != 0
    with pytest.raises(EmitError, match="completion sentinel"):
        collect(str(out), new_bundle(fm()))


@pytest.mark.skipif(not R_AVAILABLE, reason="Rscript not installed")
def test_r_refuses_a_nan_standard_error(tmp_path):
    """`is.na(NaN)` is TRUE in R, so NaN took the NA branch and was dropped.

    A singular cluster variance matrix returns a NaN standard error routinely.
    Dropped like an absent one, it emitted a record reporting an estimate with
    no uncertainty at all -- and nothing downstream could tell that apart from
    a coefficient legitimately reported without an SE.
    """
    script = tmp_path / "nan.R"
    script.write_text(f'''
source("{ROOT}/R/emit.R")
em <- emitter("R/x.R", dir = "{tmp_path}/s")
em$row(token = "a", label = "l", value = 1.0, se = NaN)
''')
    proc = subprocess.run(["Rscript", str(script)], capture_output=True, text=True)
    assert proc.returncode != 0
    assert "NaN" in proc.stderr and "uncertainty" in proc.stderr


def test_a_stream_cannot_attribute_itself_to_another_script(tmp_path):
    """Provenance was a self-reported string.

    The collector checked that a record's script was among the hashed set. It
    never checked that THIS stream came from THAT script -- so a result could
    name a different, real, hashed script as its producer and pass every check.
    The stream filename is derived by the runner from the file it executed, so
    it is the third witness alongside the sentinel and the record's own claim.
    """
    d = tmp_path / "s"
    d.mkdir()
    (d / "03_actual.R.results.jsonl").write_text(
        json.dumps(good(script="R/04_other.R")) + "\n"
        + json.dumps({"__complete__": True, "script": "R/03_actual.R"}) + "\n")
    with pytest.raises(EmitError, match="same locked script|did not produce it"):
        collect(str(d), new_bundle(fm()),
                known_scripts=["R/03_actual.R", "R/04_other.R"])


@pytest.mark.skipif(not R_AVAILABLE, reason="Rscript not installed")
def test_r_takes_its_identity_from_the_runner(tmp_path):
    """The workflow exports the script it is executing; the script cannot
    disagree with it."""
    script = tmp_path / "probe.R"
    script.write_text(
        'source("%s/R/emit.R")\n'
        'em <- emitter("R/lying_about_myself.R", dir = "%s/streams")\n'
        % (ROOT, tmp_path))
    env = dict(os.environ, COSCIENTIST_CURRENT_SCRIPT="R/03_actual.R")
    proc = subprocess.run(["Rscript", str(script)], capture_output=True,
                          text=True, env=env)
    assert proc.returncode != 0
    assert "cannot name a script other than the one producing it" in proc.stderr


@pytest.mark.skipif(not R_AVAILABLE, reason="Rscript not installed")
def test_r_refuses_to_emit_with_no_identity_at_all(tmp_path):
    script = tmp_path / "probe.R"
    script.write_text('source("%s/R/emit.R")\nem <- emitter(dir = "%s/s")\n'
                      % (ROOT, tmp_path))
    env = {k: v for k, v in os.environ.items() if k != "COSCIENTIST_CURRENT_SCRIPT"}
    proc = subprocess.run(["Rscript", str(script)], capture_output=True,
                          text=True, env=env)
    assert proc.returncode != 0 and "needs a script identity" in proc.stderr
