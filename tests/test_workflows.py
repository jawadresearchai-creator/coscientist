"""The workflows are code. Nothing was testing them.

`cycle.yml` -- the deterministic heartbeat of the whole engine -- shipped for
several versions containing `run: echo "TODO: orchestrator."`. A bare `: ` in a
plain YAML scalar is a syntax error, so GitHub would have refused to load the
file at all. The suite was green throughout, because the suite tested Python
and the workflows are YAML and shell.

These tests close that gap, and encode the two structural rules that the
workflows exist to enforce.
"""
import glob
import re
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = sorted(glob.glob(str(ROOT / ".github/workflows/*.yml")))


def _steps(doc):
    for job in (doc.get("jobs") or {}).values():
        for step in job.get("steps", []) or []:
            yield step


@pytest.mark.parametrize("path", WORKFLOWS, ids=lambda p: Path(p).name)
def test_every_workflow_is_valid_yaml(path):
    doc = yaml.safe_load(Path(path).read_text())
    assert doc.get("jobs"), f"{Path(path).name} defines no jobs"


@pytest.mark.parametrize("path", WORKFLOWS, ids=lambda p: Path(p).name)
def test_every_run_block_is_valid_shell(path):
    """`bash -n` on every `run:` body.

    A shell error inside a workflow surfaces only when the job runs, which for
    a scheduled workflow can be days later and for the analysis workflow is
    forty minutes into a build.
    """
    doc = yaml.safe_load(Path(path).read_text())
    for step in _steps(doc):
        script = step.get("run")
        if not script:
            continue
        # ${{ }} expressions are substituted by Actions before bash sees them;
        # replace with a literal so the parse reflects the real shape.
        cleaned = re.sub(r"\$\{\{[^}]*\}\}", "EXPR", script)
        proc = subprocess.run(["bash", "-n"], input=cleaned, capture_output=True, text=True)
        assert proc.returncode == 0, (
            f"{Path(path).name} step {step.get('name')!r}: {proc.stderr.strip()}"
        )


@pytest.mark.parametrize("path", WORKFLOWS, ids=lambda p: Path(p).name)
def test_no_workflow_commits_research_state_to_git(path):
    """Results and designs live in Drive. The repository holds code.

    `analysis.yml` used to end with `git add -f results/manuscript/`, which
    contradicted the rule the rest of the system is built on. The day this
    repository went public that step would have published an unsubmitted
    finding, and nothing anywhere would have flagged it.
    """
    text = Path(path).read_text()
    forbidden = ("results/", "reports/", "state/freeze", "state/candidates", "data/")
    for line in text.splitlines():
        stripped = line.strip()
        # A comment explaining why we no longer do this is the opposite of
        # doing it; scan the executable lines only.
        if stripped.startswith("#") or "git add" not in line:
            continue
        for token in forbidden:
            assert token not in line, (
                f"{Path(path).name} commits {token} to git: {line.strip()}"
            )


def test_analysis_runs_its_stages_in_the_only_safe_order():
    """State/analysis lock first; only then may outcome bytes be fetched.

    The code lock is the pre-specification boundary, so checking it after outcome
    download is too late even if edited code is never executed. Dataset hash
    verification necessarily follows the fetch; execution follows both gates.
    """
    doc = yaml.safe_load((ROOT / ".github/workflows/analysis.yml").read_text())
    body = [(s.get("name", ""), s.get("run", "") or "") for s in _steps(doc)]
    joined = "\n".join(f"{n}\n{r}" for n, r in body)

    def at(needle):
        idx = joined.find(needle)
        assert idx >= 0, f"analysis.yml never runs {needle!r}"
        return idx

    assert at("coscientist state-fetch") < at("coscientist analysis-verify"), \
        "the frozen design and analysis lock must be bootstrapped before verification"
    assert at("coscientist analysis-verify") < at("coscientist gms-fetch"), \
        "the analysis lock must be verified before any outcome bytes are fetched"
    assert at("coscientist gms-fetch") < at("coscientist freeze-verify"), \
        "dataset verification must run on data that has already been fetched"
    # `coscientist plan` is what drives the analysis loop. Matching bare
    # "Rscript" also hit the setup step's error message, which is advice, not a
    # stage -- a needle loose enough to match prose is not testing order.
    assert at("coscientist freeze-verify") < at("coscientist plan"), \
        "analysis must not start before the datasets are confirmed as frozen"
    assert at("coscientist plan") < at("coscientist collect"), \
        "the bridge is assembled from streams the analysis has written"
    assert at("coscientist collect") < at("coscientist provenance"), \
        "prose is checked against a bundle that exists"


def test_analysis_never_writes_to_the_repository():
    doc = yaml.safe_load((ROOT / ".github/workflows/analysis.yml").read_text())
    assert doc["permissions"]["contents"] == "read", (
        "the analysis job writes results to Drive, not to git; write permission "
        "on the repository is capability it should not hold"
    )


def test_analysis_removes_the_data_it_fetched():
    doc = yaml.safe_load((ROOT / ".github/workflows/analysis.yml").read_text())
    runs = "\n".join(s.get("run", "") or "" for s in _steps(doc))
    assert "rm -rf data" in runs, (
        "fetched datasets must not outlive the run; on a self-hosted runner "
        "they would persist between jobs"
    )


def test_the_analysis_verifies_its_code_lock_before_running_anything():
    """The design freeze says which question; the analysis lock says which
    analysis. Verifying it after execution would be reporting, not gating."""
    doc = yaml.safe_load((ROOT / ".github/workflows/analysis.yml").read_text())
    joined = "\n".join(f"{s.get('name','')}\n{s.get('run','') or ''}" for s in _steps(doc))
    verify = joined.find("coscientist analysis-verify")
    fetch = joined.find("coscientist gms-fetch")
    r_run = joined.find("coscientist plan")
    assert verify >= 0, "analysis.yml never verifies the analysis lock"
    assert verify < fetch, "the code lock must be checked before outcome access"
    assert verify < r_run, "the code lock must be checked before the code runs"


def test_only_verified_results_reach_the_authoritative_folder():
    """This step used to carry `if: always()`.

    So when an R script died halfway, the collector correctly refused the
    incomplete analysis and the job failed -- and then this step uploaded the
    partial streams and half-drawn figures to the authoritative results folder
    anyway, undoing the stream-sealing architecture two steps above it.
    """
    doc = yaml.safe_load((ROOT / ".github/workflows/analysis.yml").read_text())
    publish = [s for s in _steps(doc) if "coscientist publish" in (s.get("run", "") or "")]
    assert publish, "analysis.yml never publishes"
    confirmatory = [s for s in publish if "--mode confirmatory" in (s.get("run", "") or "")]
    assert len(confirmatory) == 1, "exactly one authoritative confirmatory publish step is required"
    cond = confirmatory[0].get("if", "")
    assert "success()" in cond and "strict_provenance != 'false'" in cond, (
        "the authoritative folder must receive only successful strict-provenance runs"
    )
    exploratory = [s for s in publish if "--mode exploratory" in (s.get("run", "") or "")]
    assert len(exploratory) == 1, "loose-provenance runs need a separate development route"
    assert "GDRIVE_DEVELOPMENT_RESULTS_FOLDER_ID" in (exploratory[0].get("run", "") or ""), (
        "exploratory results must never share the confirmatory destination"
    )


def test_failed_runs_are_kept_but_quarantined():
    doc = yaml.safe_load((ROOT / ".github/workflows/analysis.yml").read_text())
    failure_steps = [s for s in _steps(doc) if s.get("if") == "failure()"]
    assert failure_steps, "a failed run should still leave diagnostics for debugging"
    names = [(s.get("with") or {}).get("name", "") for s in failure_steps]
    assert any("FAILED" in n for n in names), (
        "wreckage must be named so nobody mistakes it for a result"
    )


def test_every_runner_declares_the_script_it_is_executing():
    """Provenance is the runner's identity, not a string the script chooses."""
    doc = yaml.safe_load((ROOT / ".github/workflows/analysis.yml").read_text())
    runs = [s.get("run", "") or "" for s in _steps(doc)]
    launches = [line.strip() for body in runs for line in body.splitlines()
                if re.search(r'\b(Rscript|python)\s+"?\$', line)]
    assert launches, "analysis.yml never launches an analysis script"
    for line in launches:
        assert "COSCIENTIST_CURRENT_SCRIPT" in line, (
            f"a script is launched without declaring which script it is: {line}"
        )
    assert any("Rscript" in l for l in launches), "no R runner"
    assert any("python" in l for l in launches), "no Python runner"
