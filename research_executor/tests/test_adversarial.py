from research_executor.adversarial import classify_finding, synthesize


def f(fid, ftype, severity="CRITICAL", status="OPEN", role="DOMAIN_REVIEWER"):
    return {
        "finding_id": fid,
        "finding_type": ftype,
        "severity": severity,
        "status": status,
        "reviewer_role": role,
        "evidence": ["fixture:evidence"],
    }


def test_unsupported_claim_is_rewrite_not_hard_stop():
    x = classify_finding(f("F1", "UNSUPPORTED_CLAIM"), 1)
    assert not x["true_hard_stop"]
    assert x["recommended_disposition"] == "REWRITE_OR_REMOVE_CLAIM"


def test_statistical_error_is_repair_not_project_failure():
    x = classify_finding(f("F2", "STATISTICAL_ERROR"), 1)
    assert not x["true_hard_stop"]
    assert "CORRECT_ANALYSIS" in x["recommended_disposition"]


def test_weak_data_is_reframed():
    x = classify_finding(f("F3", "WEAK_OR_NULL_DATA"), 1)
    assert x["recommended_disposition"] == "REFRAME_AS_EXPLORATORY_NULL_OR_NEGATIVE_RESULT"


def test_cycle_one_allows_one_repair_loop():
    s = synthesize([f("F4", "UNSUPPORTED_CLAIM")], 1)
    assert s["next_state"] == "REPAIR_AND_REVIEW"


def test_cycle_two_proceeds_with_nonfatal_limitations():
    s = synthesize([
        f("F5", "INTERPRETATION_DISAGREEMENT"),
        f("F6", "WEAK_OR_NULL_DATA"),
    ], 2)
    assert s["next_state"] == "READY_FOR_FINAL_PACKAGE_WITH_LIMITATIONS"
    assert not s["hard_stops"]


def test_only_narrow_integrity_ethics_case_stops():
    s = synthesize([f("F7", "FABRICATION_OR_DECEPTION_REQUIRED")], 2)
    assert s["next_state"] == "RESEARCH_DIRECTOR_ESCALATION"
    assert s["hard_stops"] == ["F7"]
