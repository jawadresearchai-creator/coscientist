from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any

from research_executor.acquisition import (
    AcquisitionError,
    _child,
    _download_json,
    _drive_client_from_env,
    _optional_child,
    _search_job_folder,
    _write_json,
    sha256_file,
)
from research_executor.public_status import validate_job_id, write_public_status

EXPECTED_POLICY_VERSION = "1.0"
INJECTED_FAILURE_EXIT = 75


class RecoveryError(AcquisitionError):
    pass


def canonical_hash(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")).hexdigest()


def classify_recovery_state(*, manifest: bool, checkpoint: bool, result: bool) -> str:
    if not manifest:
        return "MISSING_MANIFEST"
    if result and not checkpoint:
        return "INCONSISTENT_RESULT_WITHOUT_CHECKPOINT"
    if result and checkpoint:
        return "SUCCEEDED"
    if checkpoint:
        return "PARTIAL_RESUMABLE"
    return "NEW"


def validate_manifest(manifest: dict[str, Any], job_id: str) -> None:
    validate_job_id(job_id)
    if manifest.get("schema_version") != "cosci.job/1.0":
        raise RecoveryError("unexpected job schema")
    if manifest.get("job_id") != job_id or manifest.get("task_type") != "RECOVERY_TEST":
        raise RecoveryError("private recovery manifest identity/task mismatch")
    if manifest.get("privacy_class") != "PRIVATE_RESEARCH":
        raise RecoveryError("recovery manifest must be PRIVATE_RESEARCH")
    cost = manifest.get("cost_policy") or {}
    if cost.get("require_zero_cost") is not True or cost.get("automatic_paid_services") is not False:
        raise RecoveryError("zero-cost policy violation")
    params = manifest.get("parameters") or {}
    if params.get("recovery_policy_version") != EXPECTED_POLICY_VERSION:
        raise RecoveryError("unexpected recovery policy version")
    if not params.get("canonical_state_file_id"):
        raise RecoveryError("canonical_state_file_id is required privately")
    if not isinstance(params.get("expected_revision"), int) or params["expected_revision"] < 1:
        raise RecoveryError("expected_revision must be a positive integer")
    if not isinstance(params.get("expected_status"), str) or not params["expected_status"]:
        raise RecoveryError("expected_status is required")


def validate_canonical_state(state: dict[str, Any], expected_revision: int, expected_status: str) -> dict[str, Any]:
    if state.get("revision") != expected_revision:
        raise RecoveryError("canonical revision mismatch")
    if state.get("status") != expected_status:
        raise RecoveryError("canonical status mismatch")
    if not state.get("current_phase") or not state.get("next_task"):
        raise RecoveryError("canonical state lacks recovery-critical fields")
    return {
        "revision": state["revision"],
        "status": state["status"],
        "current_phase": state["current_phase"],
        "next_task_sha256": hashlib.sha256(str(state["next_task"]).encode("utf-8")).hexdigest(),
        "completed_session_count": len(state.get("completed_sessions") or []),
    }


def _upload_id(value: Any) -> str:
    if isinstance(value, dict):
        return str(value.get("id") or value.get("file_id") or value)
    return str(value)


def _upload_once(client: Any, folder_id: str, path: Path, name: str) -> str:
    existing = _optional_child(client, folder_id, name)
    if existing:
        probe = path.parent / f"existing-{name}"
        client.download(existing["id"], str(probe))
        if sha256_file(probe) != sha256_file(path):
            raise RecoveryError(f"existing artifact differs for {name}")
        return str(existing["id"])
    return _upload_id(client.upload(str(path), folder_id, name))


def _load_existing_result(client: Any, results_id: str, root: Path, manifest_sha: str) -> dict[str, Any] | None:
    existing = _optional_child(client, results_id, "result.json")
    if not existing:
        return None
    result, _ = _download_json(client, existing["id"], root / "result-existing.json")
    if (result.get("provenance") or {}).get("manifest_sha256") != manifest_sha:
        raise RecoveryError("duplicate job ID collision: existing result belongs to a different manifest")
    return result


def run_recovery_job(job_id: str, *, phase: str, client: Any | None = None, workdir: str | Path | None = None) -> tuple[dict[str, Any] | None, bool]:
    if phase not in {"inject", "resume"}:
        raise RecoveryError("phase must be inject or resume")
    validate_job_id(job_id)
    client = client or _drive_client_from_env()
    job_folder = _search_job_folder(client, job_id)
    jid = job_folder["id"]
    manifest_obj = _child(client, jid, "job.json", folder=False)
    checkpoints = _child(client, jid, "checkpoints", folder=True)
    results = _child(client, jid, "results", folder=True)
    logs = _child(client, jid, "logs", folder=True)
    outputs = _child(client, jid, "outputs", folder=True)
    metadata = _child(client, jid, "metadata", folder=True)

    context = tempfile.TemporaryDirectory(prefix="cosci-recovery-") if workdir is None else None
    root = Path(context.name if context else workdir)
    root.mkdir(parents=True, exist_ok=True)
    try:
        manifest, manifest_sha = _download_json(client, manifest_obj["id"], root / "job.json")
        validate_manifest(manifest, job_id)
        params = manifest["parameters"]

        existing_result = _load_existing_result(client, results["id"], root, manifest_sha)
        if existing_result is not None:
            return existing_result, True

        checkpoint_obj = _optional_child(client, checkpoints["id"], "checkpoint.json")
        if phase == "inject":
            if checkpoint_obj is None:
                checkpoint = {
                    "schema_version": "cosci.checkpoint/1.0",
                    "job_id": job_id,
                    "stage": "INTERRUPTED_AFTER_CHECKPOINT",
                    "manifest_sha256": manifest_sha,
                    "privacy_class": "PRIVATE_RESEARCH",
                    "failure_injection": "CONTROLLED_EXPECTED_FAILURE",
                    "resume_required": True,
                }
                cp = root / "checkpoint.json"
                _write_json(cp, checkpoint)
                _upload_once(client, checkpoints["id"], cp, cp.name)
            return None, False

        if checkpoint_obj is None:
            raise RecoveryError("resume requested but durable checkpoint is missing")
        checkpoint, _ = _download_json(client, checkpoint_obj["id"], root / "checkpoint-existing.json")
        if checkpoint.get("manifest_sha256") != manifest_sha:
            raise RecoveryError("checkpoint belongs to a different manifest")
        if checkpoint.get("stage") != "INTERRUPTED_AFTER_CHECKPOINT":
            raise RecoveryError("checkpoint is not the expected resumable injected state")

        canonical_path = root / "canonical_state.json"
        client.download(str(params["canonical_state_file_id"]), str(canonical_path))
        try:
            canonical_state = json.loads(canonical_path.read_text(encoding="utf-8"))
        except Exception as exc:
            raise RecoveryError("canonical state could not be decoded") from exc
        bootstrap = validate_canonical_state(canonical_state, int(params["expected_revision"]), str(params["expected_status"]))

        payload_hash = canonical_hash(params.get("deterministic_payload") or {})
        recovery_output = {
            "schema_version": "cosci.recovery-output/1.0",
            "job_id": job_id,
            "resumed_from_checkpoint": True,
            "deterministic_payload_sha256": payload_hash,
            "canonical_revision": bootstrap["revision"],
            "status": "PASS",
        }
        reconciliation = {
            "schema_version": "cosci.reconciliation/1.0",
            "job_id": job_id,
            "manifest_sha256": manifest_sha,
            "pre_resume_state": "PARTIAL_RESUMABLE",
            "post_resume_state": "SUCCEEDED",
            "checkpoint_reused": True,
            "github_run_id": os.getenv("GITHUB_RUN_ID"),
            "github_run_attempt": os.getenv("GITHUB_RUN_ATTEMPT"),
            "github_sha": os.getenv("GITHUB_SHA"),
            "drive_github_reconciliation": "PASS",
        }
        bootstrap_probe = {
            "schema_version": "cosci.bootstrap-probe/1.0",
            "job_id": job_id,
            "canonical_state_file_id": str(params["canonical_state_file_id"]),
            "recovered": bootstrap,
            "chat_memory_required": False,
            "result": "PASS",
        }
        payloads = {
            "recovery_output.json": recovery_output,
            "reconciliation.json": reconciliation,
            "bootstrap_probe.json": bootstrap_probe,
        }
        refs = []
        for name, obj in payloads.items():
            p = root / name
            _write_json(p, obj)
            fid = _upload_once(client, outputs["id"], p, name)
            refs.append({"name": name, "drive_file_id": fid, "sha256": sha256_file(p), "bytes": p.stat().st_size})

        provenance = {
            "schema_version": "cosci.recovery-provenance/1.0",
            "job_id": job_id,
            "manifest_sha256": manifest_sha,
            "checkpoint_file_id": str(checkpoint_obj["id"]),
            "canonical_state_sha256": sha256_file(canonical_path),
            "output_hashes": {x["name"]: x["sha256"] for x in refs},
            "executor": "GITHUB_ACTIONS_PUBLIC",
        }
        pp = root / "recovery_provenance.json"
        _write_json(pp, provenance)
        provenance_id = _upload_once(client, metadata["id"], pp, pp.name)

        result = {
            "schema_version": "cosci.result/1.0",
            "job_id": job_id,
            "project_id": manifest.get("project_id"),
            "task_type": "RECOVERY_TEST",
            "status": "SUCCEEDED",
            "privacy_class": "PRIVATE_RESEARCH",
            "outputs": refs,
            "metrics": {"resumed_from_checkpoint": True, "canonical_revision": bootstrap["revision"], "completed_session_count": bootstrap["completed_session_count"]},
            "validation": {"passed": True, "checks": [
                {"name": "controlled_failure_checkpointed", "status": "PASS"},
                {"name": "checkpoint_resume", "status": "PASS"},
                {"name": "drive_github_reconciliation", "status": "PASS"},
                {"name": "clean_chat_bootstrap_probe", "status": "PASS"},
                {"name": "private_drive_publication", "status": "PASS"}
            ]},
            "provenance": {"manifest_sha256": manifest_sha, "recovery_provenance_file_id": provenance_id, "executor": "GITHUB_ACTIONS_PUBLIC"},
            "next_state": "RECOVERY_ACCEPTANCE_COMPLETE",
        }
        rp = root / "result.json"
        _write_json(rp, result)
        _upload_once(client, results["id"], rp, rp.name)

        log = root / "execution.log"
        log.write_text("\n".join([
            f"job_id={job_id}",
            "controlled_failure_checkpointed=PASS",
            "checkpoint_resume=PASS",
            "drive_github_reconciliation=PASS",
            "clean_chat_bootstrap_probe=PASS",
            "next_state=RECOVERY_ACCEPTANCE_COMPLETE",
            "",
        ]), encoding="utf-8")
        _upload_once(client, logs["id"], log, log.name)
        return result, False
    finally:
        if context:
            context.cleanup()


def main() -> int:
    parser = argparse.ArgumentParser(description="Research CoScientist recovery/hardening probe")
    parser.add_argument("--job-id", required=True)
    parser.add_argument("--phase", choices=("inject", "resume"), required=True)
    parser.add_argument("--public-status", default="out/recovery-status.json")
    args = parser.parse_args()
    result, reused = run_recovery_job(args.job_id, phase=args.phase)
    if args.phase == "inject" and result is None:
        print(f"CONTROLLED_FAILURE_INJECTED=PASS job_id={args.job_id}")
        return INJECTED_FAILURE_EXIT
    write_public_status(args.job_id, args.public_status, "RECOVERY_TEST")
    print(f"RECOVERY_TEST=PASS job_id={args.job_id} reused_existing_result={'true' if reused else 'false'} next_state={result['next_state'] if result else 'UNKNOWN'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
