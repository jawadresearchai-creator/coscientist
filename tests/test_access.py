"""The registry must never let an inadmissible source into a frozen design."""
import pytest

from coscientist.models import AccessClass
from coscientist.registry import SourceRegistry, RegistryError

REG = "registry/sources.yaml"


def test_registry_loads():
    reg = SourceRegistry.load(REG)
    assert len(reg.sources) > 10


def test_identity_gated_and_paid_are_inadmissible():
    reg = SourceRegistry.load(REG)
    assert not reg.get("USPTO_ODP").admissible, "USPTO ODP is identity-gated"
    assert not reg.get("CRSP").admissible, "CRSP is paid"


def test_open_sources_need_no_secret():
    reg = SourceRegistry.load(REG)
    sec = reg.get("SEC_BULK")
    assert sec.access_class is AccessClass.OPEN
    assert sec.secret_names == []
    ok, missing = sec.secrets_present({})
    assert ok and missing == []


def test_secrets_list_is_not_a_census_of_sources():
    """The A8 regression test.

    A credentials list was once read as the set of available data. Assert the
    thing that makes that impossible: admissible sources that carry no secret.
    """
    reg = SourceRegistry.load(REG)
    keyless = [s for s in reg.admissible() if not s.secret_names]
    assert len(keyless) >= 5
    ids = {s.id for s in keyless}
    assert {"SEC_BULK", "FAMA_FRENCH", "EPO_INPADOC"} <= ids


def test_archivable_requires_redistribution_rights():
    reg = SourceRegistry.load(REG)
    acled = reg.get("ACLED")
    assert acled.admissible, "ACLED is keyed and the key is held"
    assert not acled.archivable(), "but its licence forbids redistribution"


def test_concept_lookup_finds_maintenance_route():
    reg = SourceRegistry.load(REG)
    hits = reg.by_concept(["maintenance_fee"])
    assert hits and hits[0].id == "EPO_INPADOC"
    assert all(h.admissible for h in hits)


def test_unknown_source_raises():
    reg = SourceRegistry.load(REG)
    with pytest.raises(RegistryError):
        reg.get("NOT_A_SOURCE")
