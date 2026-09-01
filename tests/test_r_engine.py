"""The R engine layer: what stops an R script from quietly leaving the design.

Python enforces the freeze on the way in -- `fetch_frozen` downloads exactly
the datasets the manifest names. Nothing stopped an R script reading a file
that happened to be sitting in data/ alongside them. One
`read.csv("data/extra_helpful_panel.csv")` and the analysis is no longer the
analysis that was frozen, with no artefact anywhere recording the change.
"""
import csv
import hashlib
import os
import random
import shutil
import subprocess

import pytest

from coscientist.freeze import FreezeManifest

R_AVAILABLE = shutil.which("Rscript") is not None
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
pytestmark = pytest.mark.skipif(not R_AVAILABLE, reason="Rscript not installed")


@pytest.fixture
def frozen(tmp_path):
    """A real freeze over a real dataset on disk."""
    data = tmp_path / "data"
    data.mkdir()
    panel = data / "panel.csv"
    rng = random.Random(3)
    with open(panel, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["firm", "quarter", "y", "treat_post"])
        for f in range(1, 21):
            for q in range(1, 9):
                on = int(f <= 10 and q >= 5)
                w.writerow([f, q, round(10 + 2.4 * on + rng.gauss(0, 1), 4), on])
    digest = hashlib.sha256(panel.read_bytes()).hexdigest()

    (tmp_path / "state").mkdir()
    manifest = FreezeManifest(
        candidate_id="C705", question="q", estimand="e", design="did",
        sample_definition="20 firms x 8 quarters", treatment="t", outcome="o",
        dataset_hashes={"panel.csv": digest})
    manifest.save(str(tmp_path / "state" / "freeze.json"))

    # A file in the same directory that the freeze does not name.
    (data / "extra_helpful_panel.csv").write_text("tempting,but,unfrozen\n1,2,3\n")
    return tmp_path


def run_r(tmp_path, body: str) -> subprocess.CompletedProcess:
    script = tmp_path / "probe.R"
    script.write_text(
        f'source("{ROOT}/R/00_setup.R")\n'
        f'cos_setup(freeze_path = "{tmp_path}/state/freeze.json", '
        f'data_dir = "{tmp_path}/data")\n' + body)
    return subprocess.run(["Rscript", str(script)], capture_output=True, text=True)


def test_r_loads_a_frozen_dataset(frozen):
    out = run_r(frozen, 'd <- cos_data("panel.csv"); cat("ROWS", nrow(d), "\\n")')
    assert out.returncode == 0, out.stderr
    assert "ROWS 160" in out.stdout


def test_r_refuses_a_dataset_the_freeze_does_not_name(frozen):
    """The guard that makes the outcome lock hold on the R side too."""
    out = run_r(frozen, 'cos_data("extra_helpful_panel.csv")')
    assert out.returncode != 0
    assert "not a frozen dataset" in out.stderr


def test_r_refuses_bytes_that_have_drifted(frozen):
    out = run_r(frozen, '''
write("21,9,99,1", file = file.path("''' + str(frozen) + '''", "data", "panel.csv"),
      append = TRUE)
cos_data("panel.csv")
''')
    assert out.returncode != 0
    assert "not the bytes the design was frozen against" in out.stderr


def test_the_seed_is_a_function_of_the_design(frozen):
    """A seed the analyst picks is a free parameter.

    A bootstrap that comes out unfavourably can be re-run under a different
    seed until it does not, and no artefact records that it happened. Deriving
    the seed from the freeze hash makes the draws a function of the design:
    same design, same draws, and changing the draws means changing the design.
    """
    body = 'cat("SEED", cos_seed(), "DRAW", round(rnorm(1), 6), "\\n")'
    first, second = run_r(frozen, body), run_r(frozen, body)
    assert first.returncode == 0, first.stderr
    assert first.stdout.strip() == second.stdout.strip()

    # A different design must not reproduce the same draws.
    other = FreezeManifest(
        candidate_id="C706", question="a different question", estimand="e",
        design="did", sample_definition="s", treatment="t", outcome="o",
        dataset_hashes={"panel.csv": "b" * 64})
    os.remove(frozen / "state" / "freeze.json")
    other.save(str(frozen / "state" / "freeze.json"))
    assert run_r(frozen, body).stdout.strip() != first.stdout.strip()


def test_r_will_not_start_without_a_freeze(tmp_path):
    script = tmp_path / "p.R"
    script.write_text(f'source("{ROOT}/R/00_setup.R")\n'
                      f'cos_setup(freeze_path = "{tmp_path}/absent.json")\n')
    out = subprocess.run(["Rscript", str(script)], capture_output=True, text=True)
    assert out.returncode != 0 and "no freeze manifest" in out.stderr
