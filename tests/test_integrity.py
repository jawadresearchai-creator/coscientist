"""Package-level integrity. These are the tests that catch what unit tests cannot."""
import importlib
import pkgutil
import re
import tomllib
from pathlib import Path

import pytest

import coscientist

ROOT = Path(__file__).resolve().parents[1]


def _modules():
    return [m.name for m in pkgutil.iter_modules(coscientist.__path__)]


@pytest.mark.parametrize("name", _modules())
def test_every_production_module_imports(name):
    """The regression test for a module nobody imports.

    `bq.py` kept `from .budget import Ledger` after that class was renamed, so
    the whole BigQuery route was unusable across two releases while the suite
    reported everything green -- because no test imported it. A green suite
    that never loads a module says nothing about that module.
    """
    importlib.import_module(f"coscientist.{name}")


def test_version_is_declared_in_exactly_one_place():
    """A package that reported 4.0.0, 4.0.1 and 4.1.0 at once cannot do provenance."""
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text())
    assert pyproject["project"].get("version") is None, "version must be dynamic"
    attr = pyproject["tool"]["setuptools"]["dynamic"]["version"]["attr"]
    assert attr == "coscientist.__version__"
    assert re.fullmatch(r"\d+\.\d+\.\d+", coscientist.__version__)


def test_freeze_manifest_records_the_real_engine_version():
    from coscientist.freeze import FreezeManifest
    m = FreezeManifest(candidate_id="X", question="q", estimand="e", design="d",
                       sample_definition="s", treatment="t", outcome="o",
                       dataset_hashes={"a": "b"})
    assert m.engine_version == coscientist.__version__


def test_readme_test_count_is_not_stale():
    """Documentation that lies about the suite is documentation nobody can trust."""
    readme = (ROOT / "README.md").read_text()
    claimed = re.search(r"(\d+)\s+tests", readme)
    assert claimed, "README should state the test count"
    import subprocess
    out = subprocess.run(["python3", "-m", "pytest", "--collect-only", "-q"],
                         cwd=ROOT, capture_output=True, text=True).stdout
    actual = re.search(r"(\d+)\s+tests? collected", out)
    if actual:
        assert int(claimed.group(1)) == int(actual.group(1)), (
            f"README says {claimed.group(1)}, suite has {actual.group(1)}")


def test_every_guarantee_names_a_real_test():
    """Close the loop that four reviews kept finding.

    Every guarantee the documentation claims is listed in GUARANTEES.yaml and
    names the test that enforces it. Adding a claim without adding a test now
    breaks the build, rather than waiting for a reviewer to notice that the
    code implements a narrower version of the promise.
    """
    import yaml
    spec = yaml.safe_load((ROOT / "GUARANTEES.yaml").read_text())
    guarantees = spec["guarantees"]
    assert len(guarantees) >= 25

    defined = set()
    for path in (ROOT / "tests").glob("test_*.py"):
        defined |= set(re.findall(r"^def (test_\w+)", path.read_text(), re.M))

    missing = [g["id"] for g in guarantees if g["test"] not in defined]
    assert not missing, f"guarantees with no enforcing test: {missing}"


def test_every_guarantee_is_well_formed():
    import yaml
    for g in yaml.safe_load((ROOT / "GUARANTEES.yaml").read_text())["guarantees"]:
        assert g["id"].isupper()
        assert g["claim"].endswith("."), f"{g['id']}: claim should be a sentence"
        assert g["test"].startswith("test_")


GUARANTEE_REF = re.compile(r"\[GUARANTEE: ([A-Z0-9_]+)\]")


def _guarantees():
    import yaml
    return yaml.safe_load((ROOT / "GUARANTEES.yaml").read_text())["guarantees"]


def _doc_references():
    refs: dict[str, set[str]] = {}
    for path in list(ROOT.glob("*.md")) + list(ROOT.glob("**/*.md")):
        if ".git" in path.parts:
            continue
        for gid in GUARANTEE_REF.findall(path.read_text()):
            refs.setdefault(gid, set()).add(path.name)
    return refs


def test_every_documented_guarantee_reference_resolves():
    """The registry, made mechanically true in the first direction.

    `test_every_guarantee_names_a_real_test` stops a guarantee from naming a
    test that does not exist. This stops the documentation from citing a
    guarantee that does not exist -- including the two `}` typos and the
    digit-blind regex that this test caught on its first run.
    """
    ids = {g["id"] for g in _guarantees()}
    dangling = {gid: sorted(where) for gid, where in _doc_references().items()
                if gid not in ids}
    assert not dangling, f"documented guarantees with no registry entry: {dangling}"


def test_every_guarantee_is_claimed_somewhere():
    """And in the second direction.

    A guarantee nobody claims is a test with no promise attached -- which is
    how the registry would rot into a parallel document that drifts from the
    documentation it was built to police. Five reviews found the same shape
    each time: the docs claim a guarantee, the code implements a narrower one.
    Both directions have to be mechanical for that to stop recurring.
    """
    referenced = set(_doc_references())
    unclaimed = sorted({g["id"] for g in _guarantees()} - referenced)
    assert not unclaimed, (
        f"guarantees enforced by a test but claimed in no document: {unclaimed}"
    )
