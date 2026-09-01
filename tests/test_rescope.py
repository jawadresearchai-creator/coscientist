"""Lake-bounded rescope: redefine within the lake's limits, and stay honest."""
import pytest

from coscientist.lake import LakeCatalog
from coscientist.models import Candidate, Construct, Necessity
from coscientist.rescope import (
    MAX_RESCOPES, RescopeRefused, apply_rescope, propose_rescope,
)

CATALOG = "registry/lake_catalog.example.json"


def candidate(**kw):
    base = dict(
        id="C900",
        title="Legacy NLP patent retention after a general-purpose model shock",
        question="Do owners prune legacy NLP patents at scheduled maintenance windows?",
        design="did",
        constructs=[
            Construct("lapse", "outcome", ["maintenance_fee", "lapse"],
                      preferred_source="USPTO_ODP", granularity="patent-event"),
            Construct("tech_class", "treatment", ["cpc_at_issue", "patent"],
                      preferred_source="PATENTSVIEW_S3", granularity="patent"),
            Construct("gdp", "control", ["macro_output"], preferred_source="SOME_MISSING",
                      necessity=Necessity.OPTIONAL),
        ],
        plausible_effect=0.02,
    )
    base.update(kw)
    return Candidate(**base)


def test_rescope_finds_the_lake_substitute():
    cat = LakeCatalog.load(CATALOG)
    prop = propose_rescope(candidate(), cat, ["lapse"])
    assert prop.feasible
    assert prop.swaps[0].to_dataset == "PATENT_LEGAL_EVENTS"


def test_rescope_forces_novelty_and_power_to_be_re_run():
    """A rescoped study is a different study; inherited gate passes would rot."""
    cat = LakeCatalog.load(CATALOG)
    prop = propose_rescope(candidate(), cat, ["lapse"])
    assert set(prop.gates_to_rerun) == {"G2", "G4"}


def test_optional_control_is_dropped_not_fatal():
    cat = LakeCatalog.load(CATALOG)
    prop = propose_rescope(candidate(), cat, ["gdp"])
    assert prop.feasible
    assert prop.dropped_controls == ["gdp"]


def test_primary_construct_with_no_substitute_is_infeasible():
    cat = LakeCatalog.load(CATALOG)
    cand = candidate(constructs=[
        Construct("returns", "outcome", ["security_price", "returns"],
                  preferred_source="CRSP", granularity="security-day"),
    ])
    prop = propose_rescope(cand, cat, ["returns"])
    assert not prop.feasible
    assert "returns" in prop.unresolved


def test_apply_rescope_rewrites_the_construct_and_keeps_lineage():
    cat = LakeCatalog.load(CATALOG)
    cand = candidate()
    prop = propose_rescope(cand, cat, ["lapse"])
    apply_rescope(cand, prop)
    lapse = next(c for c in cand.constructs if c.name == "lapse")
    assert lapse.preferred_source == "PATENT_LEGAL_EVENTS"
    assert cand.rescope_count == 1
    assert cand.status == "RESCOPED"
    assert cand.lineage[-1]["event"] == "rescope"


def _freeze(c):
    from coscientist.freeze import freeze_candidate, manifest_from_candidate
    # No treatment/outcome overrides: manifest_from_candidate derives them from
    # the constructs, and overriding them is now precisely the disagreement
    # freeze_candidate refuses.
    freeze_candidate(c, manifest_from_candidate(
        c, estimand="e", sample_definition="s",
        dataset_hashes={"panel": "a" * 64}))
    return c


def test_rescope_is_refused_below_the_outcome_lock():
    """The rule that separates design work from specification search."""
    cat = LakeCatalog.load(CATALOG)
    with pytest.raises(RescopeRefused, match="frozen"):
        propose_rescope(_freeze(candidate()), cat, ["lapse"])


def test_flipping_the_status_string_does_not_unlock_a_frozen_design():
    """The regression test for a lock made of a mutable label.

    `status = "ACTIVE"` on a frozen candidate re-enabled rescoping. The lock is
    the freeze hash; status is display only.
    """
    cat = LakeCatalog.load(CATALOG)
    cand = _freeze(candidate())
    cand.status = "ACTIVE"
    assert cand.is_frozen()
    with pytest.raises(RescopeRefused, match="frozen"):
        propose_rescope(cand, cat, ["lapse"])


def test_an_unfrozen_candidate_with_a_frozen_label_is_not_locked():
    """The mirror case: a stray status string must not block legitimate work."""
    cat = LakeCatalog.load(CATALOG)
    cand = candidate(status="FROZEN")
    assert not cand.is_frozen()
    assert propose_rescope(cand, cat, ["lapse"]).feasible


def test_rescope_budget_is_bounded():
    cat = LakeCatalog.load(CATALOG)
    cand = candidate(rescope_count=MAX_RESCOPES)
    prop = propose_rescope(cand, cat, ["lapse"])
    assert not prop.feasible
    assert "budget exhausted" in prop.note


def test_an_essential_control_is_never_dropped_silently():
    """The regression test for fail-open control dropping.

    role == "control" once made a construct mechanically expendable. A
    covariate absorbing the principal confound is not optional merely because
    of its role string, and dropping it changes what the design identifies.
    """
    cat = LakeCatalog.load(CATALOG)
    cand = candidate(constructs=[
        Construct("industry_decline", "control", ["unobtainable_concept"],
                  preferred_source="SOME_MISSING"),
    ])
    prop = propose_rescope(cand, cat, ["industry_decline"])
    assert not prop.feasible
    assert "industry_decline" in prop.unresolved
    assert prop.dropped_controls == []


def test_necessity_defaults_to_essential():
    c = Construct("x", "control", ["y"])
    assert c.necessity is Necessity.ESSENTIAL
    assert not c.droppable()


def test_a_stale_pre_freeze_proposal_cannot_be_applied_after_freeze():
    """The proposal was valid when made. It is not valid when applied.

    Only propose_rescope checked the lock, so generating a proposal before the
    freeze and applying it afterwards walked straight through the outcome
    boundary. A proposal's validity is time-dependent.
    """
    cat = LakeCatalog.load(CATALOG)
    cand = candidate()
    prop = propose_rescope(cand, cat, ["lapse"])     # legitimate, pre-freeze
    assert prop.feasible
    _freeze(cand)
    with pytest.raises(RescopeRefused, match="frozen"):
        apply_rescope(cand, prop)
