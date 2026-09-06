"""Package-level integrity. These tests catch what isolated unit tests cannot."""
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
    """Every production module must import; an unimported module can hide for releases."""
    importlib.import_module(f"coscientist.{name}")


def test_version_is_declared_in_exactly_one_place():
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


def _guarantee_files() -> list[Path]:
    """Base guarantees plus versioned operating-model extensions.

    Keeping the historical registry immutable-ish makes migrations reviewable,
    while all files are still treated as one executable registry.
    """
    return sorted(ROOT.glob("GUARANTEES*.yaml"))


def _guarantees():
    import yaml
    guarantees = []
    for path in _guarantee_files():
        spec = yaml.safe_load(path.read_text()) or {}
        guarantees.extend(spec.get("guarantees", []))
    return guarantees


def test_guarantee_ids_are_unique_across_registry_files():
    ids = [g["id"] for g in _guarantees()]
    duplicates = sorted({gid for gid in ids if ids.count(gid) > 1})
    assert not duplicates, f"duplicate guarantee ids: {duplicates}"


def test_every_guarantee_names_a_real_test():
    """Every claimed deterministic guarantee names the test that enforces it."""
    guarantees = _guarantees()
    assert len(guarantees) >= 25

    defined = set()
    for path in (ROOT / "tests").glob("test_*.py"):
        defined |= set(re.findall(r"^def (test_\w+)", path.read_text(), re.M))

    missing = [g["id"] for g in guarantees if g["test"] not in defined]
    assert not missing, f"guarantees with no enforcing test: {missing}"


def test_every_guarantee_is_well_formed():
    for g in _guarantees():
        assert g["id"].isupper()
        assert g["claim"].endswith("."), f"{g['id']}: claim should be a sentence"
        assert g["test"].startswith("test_")


GUARANTEE_REF = re.compile(r"\[GUARANTEE: ([A-Z0-9_]+)\]")


def _doc_references():
    refs: dict[str, set[str]] = {}
    for path in list(ROOT.glob("*.md")) + list(ROOT.glob("**/*.md")):
        if ".git" in path.parts:
            continue
        for gid in GUARANTEE_REF.findall(path.read_text()):
            refs.setdefault(gid, set()).add(path.name)
    return refs


def test_every_documented_guarantee_reference_resolves():
    ids = {g["id"] for g in _guarantees()}
    dangling = {gid: sorted(where) for gid, where in _doc_references().items()
                if gid not in ids}
    assert not dangling, f"documented guarantees with no registry entry: {dangling}"


def test_every_guarantee_is_claimed_somewhere():
    """A test with no documented promise is another form of registry drift."""
    referenced = set(_doc_references())
    unclaimed = sorted({g["id"] for g in _guarantees()} - referenced)
    assert not unclaimed, (
        f"guarantees enforced by a test but claimed in no document: {unclaimed}"
    )
