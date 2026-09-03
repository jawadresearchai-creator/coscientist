from coscientist.agriculture import (
    AgricultureResearchMode,
    MolecularPlan,
    PlanVerdict,
    evaluate_molecular_plan,
)


def test_public_omics_route_is_allowed_with_accession_provenance():
    decision = evaluate_molecular_plan(
        MolecularPlan(
            mode=AgricultureResearchMode.PUBLIC_DATA,
            uses_public_omics=True,
            public_provenance_declared=True,
            public_accessions_declared=True,
        )
    )
    assert decision.verdict is PlanVerdict.PASS
    assert decision.public_omics_allowed
    assert not decision.new_wet_lab_allowed


def test_unavailable_new_molecular_wet_lab_is_blocked():
    decision = evaluate_molecular_plan(
        MolecularPlan(
            mode=AgricultureResearchMode.NEW_EXPERIMENT,
            generates_new_molecular_measurements=True,
            wet_lab_explicitly_enabled=False,
        )
    )
    assert decision.verdict is PlanVerdict.BLOCKED
    assert not decision.new_wet_lab_allowed


def test_public_omics_without_provenance_is_blocked():
    decision = evaluate_molecular_plan(
        MolecularPlan(
            mode=AgricultureResearchMode.PUBLIC_DATA,
            uses_public_omics=True,
            public_provenance_declared=False,
            public_accessions_declared=True,
        )
    )
    assert decision.verdict is PlanVerdict.BLOCKED


def test_public_omics_without_accession_is_blocked():
    decision = evaluate_molecular_plan(
        MolecularPlan(
            mode=AgricultureResearchMode.PUBLIC_DATA,
            uses_public_omics=True,
            public_provenance_declared=True,
            public_accessions_declared=False,
        )
    )
    assert decision.verdict is PlanVerdict.BLOCKED


def test_hybrid_route_allows_explicit_new_wet_lab_plus_public_omics():
    decision = evaluate_molecular_plan(
        MolecularPlan(
            mode=AgricultureResearchMode.HYBRID,
            generates_new_molecular_measurements=True,
            uses_public_omics=True,
            wet_lab_explicitly_enabled=True,
            public_provenance_declared=True,
            public_accessions_declared=True,
        )
    )
    assert decision.verdict is PlanVerdict.PASS
    assert decision.public_omics_allowed
    assert decision.new_wet_lab_allowed
