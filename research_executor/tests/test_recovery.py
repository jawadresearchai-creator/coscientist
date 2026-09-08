import pytest

from research_executor.recovery import RecoveryError, classify_recovery_state, validate_canonical_state, validate_manifest


def manifest():
    return {
        "schema_version": "cosci.job/1.0",
        "job_id": "COSCI-S12-RECOVERY-001",
        "project_id": "RESEARCH-COSCIENTIST-BUILD",
        "task_type": "RECOVERY_TEST",
        "privacy_class": "PRIVATE_RESEARCH",
        "cost_policy": {"require_zero_cost": True, "automatic_paid_services": False},
        "parameters": {
            "recovery_policy_version": "1.0",
            "canonical_state_file_id": "private-file-id",
            "expected_revision": 12,
            "expected_status": "SESSION_11_COMPLETE_READY_FOR_SESSION_12",
        },
    }


def test_recovery_state_classifier():
    assert classify_recovery_state(manifest=True, checkpoint=False, result=False) == "NEW"
    assert classify_recovery_state(manifest=True, checkpoint=True, result=False) == "PARTIAL_RESUMABLE"
    assert classify_recovery_state(manifest=True, checkpoint=True, result=True) == "SUCCEEDED"
    assert classify_recovery_state(manifest=True, checkpoint=False, result=True) == "INCONSISTENT_RESULT_WITHOUT_CHECKPOINT"
    assert classify_recovery_state(manifest=False, checkpoint=False, result=False) == "MISSING_MANIFEST"


def test_manifest_accepts_zero_cost_private_recovery_job():
    validate_manifest(manifest(), "COSCI-S12-RECOVERY-001")


def test_manifest_rejects_paid_route():
    m = manifest()
    m["cost_policy"]["automatic_paid_services"] = True
    with pytest.raises(RecoveryError):
        validate_manifest(m, "COSCI-S12-RECOVERY-001")


def test_canonical_state_probe_accepts_exact_state():
    got = validate_canonical_state({
        "revision": 12,
        "status": "SESSION_11_COMPLETE_READY_FOR_SESSION_12",
        "current_phase": "implementation_session_12_hardening_and_recovery",
        "next_task": "Session 12 hardening",
        "completed_sessions": [{}] * 11,
    }, 12, "SESSION_11_COMPLETE_READY_FOR_SESSION_12")
    assert got["revision"] == 12
    assert got["completed_session_count"] == 11


def test_canonical_state_probe_rejects_stale_revision():
    with pytest.raises(RecoveryError):
        validate_canonical_state({
            "revision": 11,
            "status": "SESSION_10_COMPLETE_READY_FOR_SESSION_11",
            "current_phase": "old",
            "next_task": "old",
        }, 12, "SESSION_11_COMPLETE_READY_FOR_SESSION_12")
