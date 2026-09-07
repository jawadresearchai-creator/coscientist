from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from coscientist.drive import API, FOLDER_MIME, DriveClient, DriveCredentials, DriveError
from research_executor.public_status import validate_job_id, write_public_status


class DriveBridgeError(RuntimeError):
    pass


def _search_exact_name(client: DriveClient, name: str, *, mime_type: str | None = None) -> list[dict[str, Any]]:
    """Search the authenticated Drive for one exact object name.

    The public executor receives only an opaque job ID. It does not need a
    public Drive folder ID or a private path. The authenticated Drive identity
    locates the private job folder by that opaque ID.
    """
    parts = [f"name = '{name}'", "trashed = false"]
    if mime_type:
        parts.append(f"mimeType = '{mime_type}'")
    q = urllib.parse.quote(" and ".join(parts))
    url = f"{API}/files?q={q}&pageSize=100&fields=files(id,name,mimeType,parents)"
    payload = json.loads(client._get(url).decode("utf-8"))  # bounded metadata only
    return payload.get("files", [])


def _unique(items: list[dict[str, Any]], what: str) -> dict[str, Any]:
    if len(items) != 1:
        raise DriveBridgeError(f"expected exactly one {what}; found {len(items)}")
    return items[0]


def _unique_child(client: DriveClient, parent_id: str, name: str, *, folder: bool) -> dict[str, Any]:
    hits = [x for x in client.list_folder(parent_id) if x.get("name") == name]
    if folder:
        hits = [x for x in hits if x.get("mimeType") == FOLDER_MIME]
    else:
        hits = [x for x in hits if x.get("mimeType") != FOLDER_MIME]
    return _unique(hits, f"Drive child {name!r}")


def _optional_child(client: DriveClient, parent_id: str, name: str, *, folder: bool = False) -> dict[str, Any] | None:
    hits = [x for x in client.list_folder(parent_id) if x.get("name") == name]
    if folder:
        hits = [x for x in hits if x.get("mimeType") == FOLDER_MIME]
    else:
        hits = [x for x in hits if x.get("mimeType") != FOLDER_MIME]
    if len(hits) > 1:
        raise DriveBridgeError(f"Drive child {name!r} is ambiguous: {len(hits)} copies")
    return hits[0] if hits else None


def _download_json(client: DriveClient, file_id: str, dest: Path) -> tuple[dict[str, Any], str]:
    sha = client.download(file_id, str(dest))
    raw = dest.read_bytes()
    try:
        data = json.loads(raw.decode("utf-8"))
    except Exception as exc:
        raise DriveBridgeError(f"{dest.name} is not valid UTF-8 JSON") from exc
    return data, sha


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _validate_private_manifest(manifest: dict[str, Any], dispatched_job_id: str) -> None:
    if manifest.get("job_id") != dispatched_job_id:
        raise DriveBridgeError("private manifest job_id does not match dispatched opaque job_id")
    if manifest.get("privacy_class") != "PRIVATE_RESEARCH":
        raise DriveBridgeError("private Drive job manifest must be PRIVATE_RESEARCH")
    if manifest.get("task_type") != "SMOKE_TEST":
        raise DriveBridgeError("Session 03 bridge accepts only SMOKE_TEST")
    execution = manifest.get("execution") or {}
    if execution.get("preferred_backend") != "GITHUB_ACTIONS_PUBLIC":
        raise DriveBridgeError("Session 03 smoke job must target GITHUB_ACTIONS_PUBLIC")
    policy = manifest.get("cost_policy") or {}
    if policy.get("require_zero_cost") is not True or policy.get("automatic_paid_services") is not False:
        raise DriveBridgeError("Session 03 smoke job violates zero-cost policy")
    params = manifest.get("parameters") or {}
    payload = params.get("payload") or {}
    if payload.get("operation") != "sum":
        raise DriveBridgeError("Session 03 smoke job supports only deterministic sum")
    if not isinstance(payload.get("a"), int) or not isinstance(payload.get("b"), int):
        raise DriveBridgeError("Session 03 smoke operands must be integers")


def run_drive_smoke(job_id: str, *, client: DriveClient | None = None, workdir: str | Path | None = None) -> dict[str, Any]:
    validate_job_id(job_id)
    client = client or DriveClient(DriveCredentials.from_env())
    job_folder = _unique(_search_exact_name(client, job_id, mime_type=FOLDER_MIME),
                         f"private Drive job folder named {job_id!r}")

    children = client.list_folder(job_folder["id"])
    manifests = [x for x in children if x.get("name") == "job.json" and x.get("mimeType") != FOLDER_MIME]
    manifest_obj = _unique(manifests, "private job.json")
    checkpoint_folder = _unique_child(client, job_folder["id"], "checkpoints", folder=True)
    results_folder = _unique_child(client, job_folder["id"], "results", folder=True)
    logs_folder = _unique_child(client, job_folder["id"], "logs", folder=True)

    td_context = tempfile.TemporaryDirectory(prefix="cosci-drive-") if workdir is None else None
    root = Path(td_context.name if td_context else workdir)
    root.mkdir(parents=True, exist_ok=True)
    try:
        manifest, manifest_sha = _download_json(client, manifest_obj["id"], root / "job.json")
        _validate_private_manifest(manifest, job_id)

        checkpoint_existing = _optional_child(client, checkpoint_folder["id"], "checkpoint.json")
        if checkpoint_existing:
            checkpoint, _ = _download_json(client, checkpoint_existing["id"], root / "checkpoint-existing.json")
            if checkpoint.get("job_id") != job_id or checkpoint.get("manifest_sha256") != manifest_sha:
                raise DriveBridgeError("existing checkpoint does not belong to this exact private manifest")
        else:
            checkpoint = {
                "schema_version": "cosci.checkpoint/1.0",
                "job_id": job_id,
                "stage": "PRIVATE_MANIFEST_VALIDATED",
                "manifest_sha256": manifest_sha,
                "executor": "GITHUB_ACTIONS_PUBLIC",
                "privacy_class": "PRIVATE_RESEARCH",
            }
            cp_path = root / "checkpoint.json"
            _write_json(cp_path, checkpoint)
            client.upload(str(cp_path), checkpoint_folder["id"], "checkpoint.json")

        result_existing = _optional_child(client, results_folder["id"], "result.json")
        if result_existing:
            result, _ = _download_json(client, result_existing["id"], root / "result-existing.json")
            if (result.get("job_id") != job_id
                    or (result.get("provenance") or {}).get("manifest_sha256") != manifest_sha):
                raise DriveBridgeError("existing result does not belong to this exact private manifest")
            if result.get("status") != "SUCCEEDED":
                raise DriveBridgeError("existing canonical result is not SUCCEEDED")
        else:
            payload = manifest["parameters"]["payload"]
            value = payload["a"] + payload["b"]
            if value != 42:
                raise DriveBridgeError("deterministic smoke result is not 42")
            receipt = hashlib.sha256(f"{job_id}:{manifest_sha}:{value}".encode("utf-8")).hexdigest()
            result = {
                "schema_version": "cosci.result/1.0",
                "job_id": job_id,
                "project_id": manifest.get("project_id"),
                "completed_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                "status": "SUCCEEDED",
                "outputs": [],
                "validation": {
                    "passed": True,
                    "checks": [
                        {"name": "opaque_dispatch_match", "status": "PASS", "detail": None},
                        {"name": "zero_cost_policy", "status": "PASS", "detail": None},
                        {"name": "private_checkpoint", "status": "PASS", "detail": None},
                        {"name": "deterministic_sum", "status": "PASS", "detail": "result=42"},
                    ],
                },
                "provenance": {
                    "executor": "GITHUB_ACTIONS_PUBLIC",
                    "task_type": "SMOKE_TEST",
                    "manifest_sha256": manifest_sha,
                    "deterministic_receipt": receipt,
                },
                "warnings": [],
                "next_state": "WAITING_FOR_RESEARCH_DIRECTOR",
                "decision_packet_artifact_id": None,
                "metrics": {"deterministic_result": value},
            }
            result_path = root / "result.json"
            _write_json(result_path, result)
            client.upload(str(result_path), results_folder["id"], "result.json")

        log_existing = _optional_child(client, logs_folder["id"], "execution.log")
        if not log_existing:
            log_path = root / "execution.log"
            log_path.write_text(
                "\n".join([
                    f"job_id={job_id}",
                    "task_type=SMOKE_TEST",
                    "private_manifest_fetch=PASS",
                    "checkpoint=PASS",
                    "deterministic_execution=PASS",
                    "private_result_publication=PASS",
                    "next_state=WAITING_FOR_RESEARCH_DIRECTOR",
                    "",
                ]),
                encoding="utf-8",
            )
            client.upload(str(log_path), logs_folder["id"], "execution.log")

        return {
            "job_id": job_id,
            "task_type": "SMOKE_TEST",
            "status": "SUCCEEDED",
            "privacy_class": "PUBLIC_EXECUTOR_SAFE",
            "private_manifest_fetch": "PASS",
            "private_checkpoint": "PASS",
            "private_result": "PASS",
            "next_state": "WAITING_FOR_RESEARCH_DIRECTOR",
        }
    finally:
        if td_context:
            td_context.cleanup()


def main() -> int:
    parser = argparse.ArgumentParser(description="Research CoScientist private Drive job bridge")
    parser.add_argument("--job-id", required=True, help="Opaque job ID only")
    parser.add_argument("--public-status", default="out/drive-status.json")
    args = parser.parse_args()

    status = run_drive_smoke(args.job_id)
    public_path = write_public_status(args.job_id, args.public_status, "SMOKE_TEST")
    print(f"Drive bridge PASS for job_id={status['job_id']}; private outputs published")
    print(f"PUBLIC_EXECUTOR_SAFE status written to {public_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
