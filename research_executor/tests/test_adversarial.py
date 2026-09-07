from research_executor.adversarial import classify, review_findings, AdversarialError


def finding(fid, ftype, severity="CRITICAL", status="OPEN", **kw):
    return {
        "finding_id": fid,
        "finding_type": ftype,
        "severity": severity,
        "status": status,
        "reviewer_role": kw.get("reviewer_role", "METHODOLOGY_REVIEWER"),
        "evidence": kw.get("evidence", ["fixture:1"]),
        **{k: v for k, v in kw.items() if k not in {"reviewer_role", "evidence"}},
    }


def test_release_blocker_open_blocks():
    x = classify(finding("F1", "UNSUPPORTED_CENTRAL_CLAIM"))
    assert x["release_blocker"] and x["automatic_release_block"]


def test_critical_nonblocker_requires_judgment_not_hard_stop():
    x = classify(finding("F2", "INTERPRETATION_DISAGREEMENT"))
    assert not x["release_blocker"]
    assert not x["automatic_release_block"]
    assert x["research_director_judgment_required"]


def test_resolved_release_blocker_no_longer_blocks():
    x = classify(finding(
        "F3", "CENTRAL_STATISTICAL_ERROR", status="RESOLVED",
        research_director_action="RESOLVE"
    ))
    assert x["release_blocker"] and not x["automatic_release_block"]


def test_release_blocker_cannot_be_waived_directly():
    try:
        classify(finding(
            "F4", "DATA_INTEGRITY_FAILURE", status="WAIVED",
            research_director_action="WAIVE_WITH_RATIONALE"
        ))
    except AdversarialError:
        pass
    else:
        raise AssertionError("expected waiver rejection")


def test_editor_blocks_only_release_blockers():
    _, reviewed = review_findings([
        finding("F5", "INTERPRETATION_DISAGREEMENT"),
        finding("F6", "UNSUPPORTED_CENTRAL_CLAIM"),
    ])
    assert reviewed["editor_synthesis"]["release_gate"] == "BLOCKED"
    assert reviewed["editor_synthesis"]["unresolved_release_blockers"] == ["F6"]
    assert "F5" in reviewed["editor_synthesis"]["research_director_judgment_required"]


def test_editor_ready_after_blocker_resolved():
    _, reviewed = review_findings([
        finding(
            "F7", "UNSUPPORTED_CENTRAL_CLAIM", status="RESOLVED",
            research_director_action="RESOLVE"
        ),
        finding("F8", "INTERPRETATION_DISAGREEMENT"),
    ])
    assert reviewed["editor_synthesis"]["release_gate"] == "READY_FOR_RESEARCH_DIRECTOR"
    assert not reviewed["editor_synthesis"]["unresolved_release_blockers"]
