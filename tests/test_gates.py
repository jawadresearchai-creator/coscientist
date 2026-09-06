"""Admission/data/power gates for the single active paper."""
import numpy as np
import pandas as pd

from coscientist.gates import g1_failure_collision, g3_access, g4_power
from coscientist.lake import LakeCatalog
from coscientist.models import Candidate, Construct, Necessity, Verdict
from coscientist.registry import SourceRegistry

REG = "registry/sources.yaml"
CATALOG = "registry/lake_catalog.example.json"


def _reg_cat():
    return SourceRegistry.load(REG), LakeCatalog.load(CATALOG)


def test_legacy_candidate_memory_is_ignored():
    c = Candidate(id="LEGACY-001", title="x", question="y", design="did")
    memory = {
        "retired_lineages": ["LEGACY-001"],
        "recycle_prohibitions": ["x"],
    }
    assert g1_failure_collision(c, memory).verdict is Verdict.PASS


def test_g1_enforces_current_hard_prohibition():
    c = Candidate(id="MS001", title="Unlawful private data study", question="q", design="panel")
    assert g1_failure_collision(
        c, {"hard_prohibitions": ["unlawful private data"]}
    ).verdict is Verdict.FAIL


def test_g1_passes_a_clean_current_study():
    c = Candidate(id="MS002", title="Tariff pass-through in energy inputs",
                  question="does it?", design="did")
    assert g1_failure_collision(c, {}).verdict is Verdict.PASS


def _synthetic(tmp_path, sources, datasets):
    import json
    import yaml
    reg_p = tmp_path / "sources.yaml"
    reg_p.write_text(yaml.safe_dump({"sources": sources}))
    cat_p = tmp_path / "catalog.json"
    cat_p.write_text(json.dumps({"datasets": datasets}))
    return SourceRegistry.load(str(reg_p)), LakeCatalog.load(str(cat_p))


def test_g3_uses_lake_before_external_source(tmp_path):
    reg, cat = _synthetic(
        tmp_path,
        [{"id": "OPEN_EXTERNAL", "name": "external", "access_class": "OPEN",
          "redistributable": True, "url": "https://external.example/",
          "concepts": ["outcome_y"], "granularity": "unit-period"}],
        [{"id": "LAKE_COPY", "domain": "x", "concepts": ["outcome_y"],
          "granularity": "unit-period"}],
    )
    c = Candidate(id="MS003", title="t", question="q", design="did", constructs=[
        Construct("y", "outcome", ["outcome_y"], preferred_source="OPEN_EXTERNAL",
                  granularity="unit-period"),
    ])
    calls = []
    res = g3_access(c, reg, cat, reach_probe=lambda url: calls.append(url) or True)
    assert res.verdict is Verdict.PASS
    assert c.constructs[0].preferred_source == "LAKE_COPY"
    assert calls == []
    assert res.evidence["resolved"][0]["route"] == "lake"


def test_g3_kills_when_no_substitute_exists_anywhere():
    reg, cat = _reg_cat()
    c = Candidate(id="MS004", title="t", question="q", design="event_study", constructs=[
        Construct("car", "outcome", ["security_price", "returns"], preferred_source="CRSP"),
    ])
    assert g3_access(c, reg, cat).verdict is Verdict.FAIL


def test_g3_passes_an_open_source_or_exact_lake_copy():
    reg, cat = _reg_cat()
    c = Candidate(id="MS005", title="t", question="q", design="panel", constructs=[
        Construct("fundamentals", "outcome", ["firm_fundamentals"],
                  preferred_source="SEC_BULK", granularity="firm-quarter"),
    ])
    res = g3_access(c, reg, cat)
    assert res.verdict is Verdict.PASS
    assert c.constructs[0].preferred_source == "SEC_FUNDAMENTALS_QUARTERLY"


def test_g3_does_not_pass_an_unmapped_primary_construct():
    reg, cat = _reg_cat()
    c = Candidate(id="MS006", title="t", question="q", design="did", constructs=[
        Construct("ceo_turnover", "outcome", ["executive_governance"], preferred_source=None),
    ])
    res = g3_access(c, reg, cat)
    assert res.verdict is not Verdict.PASS
    assert any(b["source"] is None for b in res.evidence["blocked"])


def test_g3_resolves_an_unmapped_construct_from_lake_before_registry():
    reg, cat = _reg_cat()
    c = Candidate(id="MS007", title="t", question="q", design="panel", constructs=[
        Construct("fundamentals", "outcome", ["firm_fundamentals"], preferred_source=None,
                  granularity="firm-quarter"),
    ])
    assert g3_access(c, reg, cat).verdict is Verdict.PASS
    assert c.constructs[0].preferred_source == "SEC_FUNDAMENTALS_QUARTERLY"


def test_g4_defers_without_a_declared_effect_size():
    c = Candidate(id="MS008", title="t", question="q", design="did")
    df = pd.DataFrame({"u": [0, 0, 1, 1] * 10, "t": [0, 1] * 20, "y": np.arange(40.0)})
    assert g4_power(c, df, unit="u", time="t", outcome="y", pre_period_end=2).verdict is Verdict.DEFER


def test_g4_kills_an_underpowered_design():
    rng = np.random.default_rng(3)
    rows = [(u, t, rng.normal(0, 10.5)) for u in range(60) for t in range(12)]
    df = pd.DataFrame(rows, columns=["u", "t", "y"])
    c = Candidate(id="MS009", title="t", question="q", design="did", plausible_effect=0.021)
    res = g4_power(c, df, unit="u", time="t", outcome="y", pre_period_end=12)
    assert res.verdict is Verdict.FAIL
    assert "underpowered" in res.reason.lower()


def test_g4_defers_rather_than_killing_inside_the_margin():
    rng = np.random.default_rng(5)
    rows = [(u, t, rng.normal(0, 1.0)) for u in range(200) for t in range(10)]
    df = pd.DataFrame(rows, columns=["u", "t", "y"])
    probe = g4_power(
        Candidate(id="P", title="t", question="q", design="did", plausible_effect=1.0),
        df, unit="u", time="t", outcome="y", pre_period_end=10,
    )
    marginal = probe.evidence["mde"] / 1.5
    c = Candidate(id="MS010", title="t", question="q", design="did", plausible_effect=marginal)
    res = g4_power(c, df, unit="u", time="t", outcome="y", pre_period_end=10)
    assert res.verdict is Verdict.DEFER
    assert "G4B" in res.reason


def test_g4_blocks_rather_than_fails_on_leaked_data():
    rng = np.random.default_rng(9)
    rows = [(u, t, rng.normal(0, 1.0)) for u in range(60) for t in range(20)]
    df = pd.DataFrame(rows, columns=["u", "t", "y"])
    c = Candidate(id="MS011", title="t", question="q", design="did", plausible_effect=0.5)
    res = g4_power(c, df, unit="u", time="t", outcome="y", pre_period_end=12)
    assert res.verdict is Verdict.BLOCKED
    assert not res.verdict.retires_candidate
    assert "LEAKAGE" in res.reason


def test_only_a_scientific_fail_retires_a_candidate():
    assert Verdict.FAIL.retires_candidate
    for v in (Verdict.BLOCKED, Verdict.DEFER, Verdict.RESCOPE, Verdict.PASS):
        assert not v.retires_candidate


def test_g3_prefers_exact_lake_patent_data_over_external_routes():
    reg, cat = _reg_cat()
    c = Candidate(id="MS012", title="t", question="q", design="panel", constructs=[
        Construct("tech_class", "treatment", ["patent"], preferred_source=None,
                  granularity="patent"),
    ])
    res = g3_access(c, reg, cat)
    assert res.verdict is Verdict.PASS
    assert c.constructs[0].preferred_source == "PATENT_GRANTS"


def test_g3_switches_to_an_open_alternative_when_the_preferred_route_is_gated():
    reg = SourceRegistry.load(REG)
    cat = LakeCatalog()
    c = Candidate(id="MS013", title="t", question="q", design="did", constructs=[
        Construct("lapse", "outcome", ["maintenance_fee", "lapse"],
                  preferred_source="USPTO_ODP", granularity="patent-event"),
    ])
    res = g3_access(c, reg, cat)
    assert res.verdict is Verdict.PASS
    assert c.constructs[0].preferred_source == "EPO_INPADOC"
    assert any(t["why"].startswith("access_class") for t in res.evidence["resolved"][0]["rejected"])


def test_g3_falls_through_to_a_second_route_when_the_first_is_unreachable(tmp_path):
    reg, cat = _synthetic(
        tmp_path,
        [{"id": "AAA_DEAD", "name": "dead", "access_class": "OPEN", "redistributable": True,
          "url": "https://dead.example/", "concepts": ["thing"]},
         {"id": "BBB_LIVE", "name": "live", "access_class": "OPEN", "redistributable": True,
          "url": "https://live.example/", "concepts": ["thing"]}],
        [],
    )
    c = Candidate(id="MS014", title="t", question="q", design="panel", constructs=[
        Construct("x", "treatment", ["thing"], preferred_source=None),
    ])
    res = g3_access(c, reg, cat, reach_probe=lambda u: "dead" not in u)
    assert res.verdict is Verdict.PASS
    assert c.constructs[0].preferred_source == "BBB_LIVE"
    assert res.evidence["resolved"][0]["rejected"][0]["why"] == "unreachable"


def test_g3_rescopes_to_loose_lake_fit_only_after_exact_and_external_fail(tmp_path):
    reg, cat = _synthetic(
        tmp_path,
        [{"id": "GATED", "name": "g", "access_class": "IDENTITY_GATED",
          "redistributable": True, "concepts": ["thing"], "granularity": "unit-period"}],
        [{"id": "COARSE_LAKE", "domain": "x", "concepts": ["thing"],
          "granularity": "country-year"}],
    )
    c = Candidate(id="MS015", title="t", question="q", design="panel", constructs=[
        Construct("x", "outcome", ["thing"], preferred_source="GATED", granularity="unit-period"),
    ])
    res = g3_access(c, reg, cat)
    assert res.verdict is Verdict.RESCOPE
    assert res.evidence["rescope_targets"] == ["x"]
    assert res.evidence["blocked"][0]["tried"]


def test_g3_rejects_a_reachable_source_that_does_not_fit(tmp_path):
    reg, cat = _synthetic(
        tmp_path,
        [{"id": "WRONG_GRAIN", "name": "coarse", "access_class": "OPEN",
          "redistributable": True, "concepts": ["patent_events"],
          "granularity": "country-year", "coverage_start": "2025-01-01"},
         {"id": "RIGHT_GRAIN", "name": "fine", "access_class": "OPEN",
          "redistributable": True, "concepts": ["patent_events"],
          "granularity": "patent-event", "coverage_start": "2008-01-01"}],
        [],
    )
    c = Candidate(id="MS016", title="t", question="q", design="did", constructs=[
        Construct("events", "outcome", ["patent_events"], preferred_source="WRONG_GRAIN",
                  granularity="patent-event", coverage_start="2010-01-01"),
    ])
    res = g3_access(c, reg, cat)
    assert res.verdict is Verdict.PASS
    assert res.evidence["resolved"][0]["chose"] == "RIGHT_GRAIN"
    rejected = {r["source"]: r["why"] for r in res.evidence["resolved"][0]["rejected"]}
    assert "granularity" in rejected["WRONG_GRAIN"]


def test_g3_rejects_a_source_whose_coverage_starts_too_late(tmp_path):
    reg, cat = _synthetic(
        tmp_path,
        [{"id": "TOO_RECENT", "name": "recent", "access_class": "OPEN",
          "redistributable": True, "concepts": ["patent_events"],
          "granularity": "patent-event", "coverage_start": "2020-01-01"}],
        [],
    )
    c = Candidate(id="MS017", title="t", question="q", design="did", constructs=[
        Construct("events", "outcome", ["patent_events"], granularity="patent-event",
                  coverage_start="2010-01-01"),
    ])
    res = g3_access(c, reg, cat)
    assert res.verdict is Verdict.FAIL
    assert "starts 2020-01-01" in str(res.evidence["blocked"])


def test_g3_defers_rather_than_dropping_an_important_control(tmp_path):
    reg, cat = _synthetic(
        tmp_path,
        [{"id": "GATED", "name": "g", "access_class": "IDENTITY_GATED",
          "redistributable": True, "concepts": ["governance"]},
         {"id": "OPEN_Y", "name": "o", "access_class": "OPEN",
          "redistributable": True, "concepts": ["outcome_y"]}],
        [],
    )
    c = Candidate(id="MS018", title="t", question="q", design="did", constructs=[
        Construct("y", "outcome", ["outcome_y"], preferred_source="OPEN_Y"),
        Construct("gov", "control", ["governance"], preferred_source="GATED",
                  necessity=Necessity.IMPORTANT),
    ])
    assert g3_access(c, reg, cat).verdict is Verdict.DEFER


def test_g3_passes_when_only_an_optional_control_is_blocked(tmp_path):
    reg, cat = _synthetic(
        tmp_path,
        [{"id": "GATED", "name": "g", "access_class": "IDENTITY_GATED",
          "redistributable": True, "concepts": ["nice_to_have"]},
         {"id": "OPEN_Y", "name": "o", "access_class": "OPEN",
          "redistributable": True, "concepts": ["outcome_y"]}],
        [],
    )
    c = Candidate(id="MS019", title="t", question="q", design="did", constructs=[
        Construct("y", "outcome", ["outcome_y"], preferred_source="OPEN_Y"),
        Construct("extra", "control", ["nice_to_have"], preferred_source="GATED",
                  necessity=Necessity.OPTIONAL),
    ])
    res = g3_access(c, reg, cat)
    assert res.verdict is Verdict.PASS
    assert res.evidence["droppable"] == ["extra"]


def test_g3_will_not_reroute_a_frozen_candidate(tmp_path):
    import pytest
    from coscientist.models import FrozenDesignViolation

    reg, cat = _synthetic(
        tmp_path,
        [{"id": "OPEN_Y", "name": "o", "access_class": "OPEN",
          "redistributable": True, "concepts": ["outcome_y"]}],
        [],
    )
    c = Candidate(id="MS020", title="t", question="q", design="did", constructs=[
        Construct("y", "outcome", ["outcome_y"], preferred_source=None),
    ])
    c.freeze_id, c.freeze_hash = "DF-MS020-x", "f" * 64
    with pytest.raises(FrozenDesignViolation, match="g3_access"):
        g3_access(c, reg, cat)
