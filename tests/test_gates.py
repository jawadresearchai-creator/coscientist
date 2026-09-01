"""The gauntlet, in cost order."""
import numpy as np
import pandas as pd

from coscientist.gates import g1_failure_collision, g3_access, g4_power
from coscientist.lake import LakeCatalog
from coscientist.models import Candidate, Construct, Necessity, Verdict
from coscientist.registry import SourceRegistry

REG = "registry/sources.yaml"
CATALOG = "registry/lake_catalog.example.json"

FAILURE_MEMORY = {
    "retired_lineages": ["C311", "C704"],
    "recycle_prohibitions": ["generic chatgpt to open-source contributor exit"],
}


def _reg_cat():
    return SourceRegistry.load(REG), LakeCatalog.load(CATALOG)


def test_g1_kills_a_retired_lineage():
    c = Candidate(id="C311", title="x", question="y", design="did")
    assert g1_failure_collision(c, FAILURE_MEMORY).verdict is Verdict.FAIL


def test_g1_kills_a_standing_prohibition():
    c = Candidate(id="C950", title="Generic ChatGPT to open-source contributor exit",
                  question="does it?", design="did")
    assert g1_failure_collision(c, FAILURE_MEMORY).verdict is Verdict.FAIL


def test_g1_passes_a_clean_candidate():
    c = Candidate(id="C951", title="Tariff pass-through in energy inputs",
                  question="does it?", design="did")
    assert g1_failure_collision(c, FAILURE_MEMORY).verdict is Verdict.PASS


def _synthetic(tmp_path, sources, datasets):
    """A registry and catalog built for one branch, so the branch is reachable."""
    import json
    import yaml
    reg_p = tmp_path / "sources.yaml"
    reg_p.write_text(yaml.safe_dump({"sources": sources}))
    cat_p = tmp_path / "catalog.json"
    cat_p.write_text(json.dumps({"datasets": datasets}))
    return SourceRegistry.load(str(reg_p)), LakeCatalog.load(str(cat_p))


def test_g3_rescopes_only_when_no_external_route_exists(tmp_path):
    """The lake is the last resort, not the second."""
    reg, cat = _synthetic(
        tmp_path,
        [{"id": "GATED", "name": "gated", "access_class": "IDENTITY_GATED",
          "redistributable": True, "concepts": ["exotic_outcome"]}],
        [{"id": "LAKE_COPY", "domain": "x", "concepts": ["exotic_outcome"],
          "granularity": "unit-period"}],
    )
    c = Candidate(id="C705", title="t", question="q", design="did", constructs=[
        Construct("y", "outcome", ["exotic_outcome"], preferred_source="GATED",
                  granularity="unit-period"),
    ])
    res = g3_access(c, reg, cat)
    assert res.verdict is Verdict.RESCOPE
    assert "y" in res.evidence["rescope_targets"]


def test_g3_kills_when_no_substitute_exists_anywhere():
    reg, cat = _reg_cat()
    c = Candidate(id="C960", title="t", question="q", design="event_study", constructs=[
        Construct("car", "outcome", ["security_price", "returns"], preferred_source="CRSP"),
    ])
    assert g3_access(c, reg, cat).verdict is Verdict.FAIL


def test_g3_passes_an_open_source():
    reg, cat = _reg_cat()
    c = Candidate(id="C961", title="t", question="q", design="panel", constructs=[
        Construct("fundamentals", "outcome", ["firm_fundamentals"],
                  preferred_source="SEC_BULK", granularity="firm-quarter"),
    ])
    assert g3_access(c, reg, cat).verdict is Verdict.PASS


def test_g3_does_not_pass_an_unmapped_primary_construct():
    """The regression test for the pass-through bug.

    A primary construct with no preferred_source was skipped entirely, so a
    candidate whose only outcome had no data source at all returned PASS with
    the reason 'every construct maps to an admissible, reachable source'.
    """
    reg, cat = _reg_cat()
    c = Candidate(id="C970", title="t", question="q", design="did", constructs=[
        Construct("ceo_turnover", "outcome", ["executive_governance"],
                  preferred_source=None),
    ])
    res = g3_access(c, reg, cat)
    assert res.verdict is not Verdict.PASS
    assert any(b["source"] is None for b in res.evidence["blocked"])


def test_g3_resolves_an_unmapped_construct_from_the_registry():
    """Unmapped is not automatically fatal: try the registry by concept first."""
    reg, cat = _reg_cat()
    c = Candidate(id="C971", title="t", question="q", design="panel", constructs=[
        Construct("fundamentals", "outcome", ["firm_fundamentals"], preferred_source=None),
    ])
    assert g3_access(c, reg, cat).verdict is Verdict.PASS
    assert c.constructs[0].preferred_source is not None


def test_g4_defers_without_a_declared_effect_size():
    c = Candidate(id="C962", title="t", question="q", design="did")
    df = pd.DataFrame({"u": [0, 0, 1, 1] * 10, "t": [0, 1] * 20, "y": np.arange(40.0)})
    res = g4_power(c, df, unit="u", time="t", outcome="y", pre_period_end=2)
    assert res.verdict is Verdict.DEFER


def test_g4_kills_an_underpowered_design():
    rng = np.random.default_rng(3)
    rows = [(u, t, rng.normal(0, 10.5)) for u in range(60) for t in range(12)]
    df = pd.DataFrame(rows, columns=["u", "t", "y"])
    c = Candidate(id="C716", title="t", question="q", design="did", plausible_effect=0.021)
    res = g4_power(c, df, unit="u", time="t", outcome="y", pre_period_end=12)
    assert res.verdict is Verdict.FAIL
    assert "underpowered" in res.reason.lower()


def test_g4_defers_rather_than_killing_inside_the_margin():
    """A generic approximation may end the hopeless, not adjudicate the close."""
    rng = np.random.default_rng(5)
    rows = [(u, t, rng.normal(0, 1.0)) for u in range(200) for t in range(10)]
    df = pd.DataFrame(rows, columns=["u", "t", "y"])
    probe = g4_power(
        Candidate(id="P", title="t", question="q", design="did", plausible_effect=1.0),
        df, unit="u", time="t", outcome="y", pre_period_end=10,
    )
    marginal = probe.evidence["mde"] / 1.5          # ratio 1.5, inside the 3x margin
    c = Candidate(id="C980", title="t", question="q", design="did",
                  plausible_effect=marginal)
    res = g4_power(c, df, unit="u", time="t", outcome="y", pre_period_end=10)
    assert res.verdict is Verdict.DEFER
    assert "G4B" in res.reason


def test_g4_blocks_rather_than_fails_on_leaked_data():
    """An input-contract failure must not read as a scientific verdict.

    A candidate handed a contaminated panel by the orchestrator has not been
    shown to be underpowered; it has not been tested at all. Retiring it would
    kill good science on an engine bug.
    """
    rng = np.random.default_rng(9)
    rows = [(u, t, rng.normal(0, 1.0)) for u in range(60) for t in range(20)]
    df = pd.DataFrame(rows, columns=["u", "t", "y"])
    c = Candidate(id="C981", title="t", question="q", design="did", plausible_effect=0.5)
    res = g4_power(c, df, unit="u", time="t", outcome="y", pre_period_end=12)
    assert res.verdict is Verdict.BLOCKED
    assert not res.verdict.retires_candidate
    assert "LEAKAGE" in res.reason


def test_only_a_scientific_fail_retires_a_candidate():
    assert Verdict.FAIL.retires_candidate
    for v in (Verdict.BLOCKED, Verdict.DEFER, Verdict.RESCOPE, Verdict.PASS):
        assert not v.retires_candidate


def test_g3_prefers_a_keyless_source_over_a_credentialed_one():
    """The alternate-source bug.

    by_concept ranked alphabetically, so BIGQUERY_PUBLIC beat PATENTSVIEW_S3
    and the engine chose a source whose credentials were absent, then rescoped
    -- ignoring an equally good OPEN route sitting right beside it.
    """
    reg, cat = _reg_cat()
    c = Candidate(id="C990", title="t", question="q", design="panel", constructs=[
        Construct("tech_class", "treatment", ["patent"], preferred_source=None),
    ])
    res = g3_access(c, reg, cat)
    assert res.verdict is Verdict.PASS
    assert c.constructs[0].preferred_source == "PATENTSVIEW_S3"


def test_g3_switches_to_an_open_alternative_when_the_preferred_route_is_gated():
    """C705's real shape.

    Pinned to USPTO_ODP (identity-gated), with EPO_INPADOC in the registry --
    OPEN, same concepts. Alternate discovery used to run only when no source
    was named at all, so this went to the lake with a usable route in hand.
    """
    reg, cat = _reg_cat()
    c = Candidate(id="C705", title="t", question="q", design="did", constructs=[
        Construct("lapse", "outcome", ["maintenance_fee", "lapse"],
                  preferred_source="USPTO_ODP", granularity="patent-event"),
    ])
    res = g3_access(c, reg, cat)
    assert res.verdict is Verdict.PASS
    assert c.constructs[0].preferred_source == "EPO_INPADOC"
    assert any(t["why"].startswith("access_class") for t in res.evidence["resolved"][0]["rejected"])


def test_g3_falls_through_to_a_second_route_when_the_first_is_unreachable(tmp_path):
    """One dead OPEN route must not stop an identical live one being used."""
    reg, cat = _synthetic(
        tmp_path,
        [{"id": "AAA_DEAD", "name": "dead", "access_class": "OPEN", "redistributable": True,
          "url": "https://dead.example/", "concepts": ["thing"]},
         {"id": "BBB_LIVE", "name": "live", "access_class": "OPEN", "redistributable": True,
          "url": "https://live.example/", "concepts": ["thing"]}],
        [],
    )
    c = Candidate(id="C992", title="t", question="q", design="panel", constructs=[
        Construct("x", "treatment", ["thing"], preferred_source=None),
    ])
    res = g3_access(c, reg, cat, reach_probe=lambda u: "dead" not in u)
    assert res.verdict is Verdict.PASS
    assert c.constructs[0].preferred_source == "BBB_LIVE"
    assert res.evidence["resolved"][0]["rejected"][0]["why"] == "unreachable"


def test_g3_only_reaches_the_lake_after_every_external_route_fails():
    reg, cat = _reg_cat()
    c = Candidate(id="C993", title="t", question="q", design="event_study", constructs=[
        Construct("car", "outcome", ["security_price", "returns"], preferred_source="CRSP"),
    ])
    res = g3_access(c, reg, cat)
    assert res.verdict is Verdict.FAIL
    assert res.evidence["blocked"][0]["tried"], "the trail must record what was attempted"


def test_g3_rejects_a_reachable_source_that_does_not_fit(tmp_path):
    """Reachable is not the same as suitable.

    "Usable" once meant only "responds and vaguely measures the concept", so a
    country-year source covering 2025-2026 was assigned to a construct needing
    patent-event data over 2010-2024. Once G3 substitutes sources on its own,
    fit has to be checked before the route is accepted, or the gate quietly
    swaps in data that cannot answer the question.
    """
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
    c = Candidate(id="C900", title="t", question="q", design="did", constructs=[
        # Pinned to the unsuitable one, so the fit check is what rejects it.
        # Ranked ordering would otherwise have reached the right source first
        # and never evaluated the wrong one at all.
        Construct("events", "outcome", ["patent_events"], preferred_source="WRONG_GRAIN",
                  granularity="patent-event", coverage_start="2010-01-01"),
    ])
    res = g3_access(c, reg, cat)
    assert res.verdict is Verdict.PASS
    chose = res.evidence["resolved"][0]
    assert chose["chose"] == "RIGHT_GRAIN"
    rejected = {r["source"]: r["why"] for r in chose["rejected"]}
    assert "granularity" in rejected["WRONG_GRAIN"]


def test_g3_rejects_a_source_whose_coverage_starts_too_late(tmp_path):
    reg, cat = _synthetic(
        tmp_path,
        [{"id": "TOO_RECENT", "name": "recent", "access_class": "OPEN",
          "redistributable": True, "concepts": ["patent_events"],
          "granularity": "patent-event", "coverage_start": "2020-01-01"}],
        [],
    )
    c = Candidate(id="C901", title="t", question="q", design="did", constructs=[
        Construct("events", "outcome", ["patent_events"], granularity="patent-event",
                  coverage_start="2010-01-01"),
    ])
    res = g3_access(c, reg, cat)
    assert res.verdict is Verdict.FAIL
    assert "starts 2020-01-01" in str(res.evidence["blocked"])


def test_g3_defers_rather_than_dropping_an_important_control(tmp_path):
    """Necessity, not role.

    "Only non-primary constructs blocked, so they can be dropped" passed
    candidates whose confound-absorbing covariate had no source at all.
    Dropping an IMPORTANT construct weakens the claim, which is a judgment
    call and therefore a ticket, not a mechanical PASS.
    """
    reg, cat = _synthetic(
        tmp_path,
        [{"id": "GATED", "name": "g", "access_class": "IDENTITY_GATED",
          "redistributable": True, "concepts": ["governance"]},
         {"id": "OPEN_Y", "name": "o", "access_class": "OPEN",
          "redistributable": True, "concepts": ["outcome_y"]}],
        [],
    )
    c = Candidate(id="C902", title="t", question="q", design="did", constructs=[
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
    c = Candidate(id="C903", title="t", question="q", design="did", constructs=[
        Construct("y", "outcome", ["outcome_y"], preferred_source="OPEN_Y"),
        Construct("extra", "control", ["nice_to_have"], preferred_source="GATED",
                  necessity=Necessity.OPTIONAL),
    ])
    res = g3_access(c, reg, cat)
    assert res.verdict is Verdict.PASS and res.evidence["droppable"] == ["extra"]


def test_g3_will_not_reroute_a_frozen_candidate(tmp_path):
    """Rewriting preferred_source alters scientific state.

    G3 was quietly re-routing frozen candidates: below the outcome lock, a gate
    changing which data the design uses is the design changing after the
    results are visible.
    """
    import pytest

    from coscientist.models import FrozenDesignViolation

    reg, cat = _synthetic(
        tmp_path,
        [{"id": "GATED", "name": "g", "access_class": "IDENTITY_GATED",
          "redistributable": True, "concepts": ["outcome_y"]},
         {"id": "OPEN_Y", "name": "o", "access_class": "OPEN",
          "redistributable": True, "concepts": ["outcome_y"]}],
        [],
    )
    c = Candidate(id="C904", title="t", question="q", design="did", constructs=[
        Construct("y", "outcome", ["outcome_y"], preferred_source="GATED"),
    ])
    c.freeze_id, c.freeze_hash = "DF-C904-x", "f" * 64
    with pytest.raises(FrozenDesignViolation, match="g3_access"):
        g3_access(c, reg, cat)
