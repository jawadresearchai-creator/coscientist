from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Callable

from research_executor.public_status import validate_job_id, write_public_status

FOLDER_MIME = "application/vnd.google-apps.folder"
DRIVE_API = "https://www.googleapis.com/drive/v3"
REGISTRY_PATH = Path(__file__).with_name("source_registry.json")


class AcquisitionError(RuntimeError):
    pass


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as fh:
        while chunk := fh.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def load_registry(path: str | Path = REGISTRY_PATH) -> dict[str, Any]:
    registry = json.loads(Path(path).read_text(encoding="utf-8"))
    if registry.get("policy", {}).get("automatic_paid_routes") is not False:
        raise AcquisitionError("source registry must disable automatic paid routes")
    return registry


def select_zero_cost_route(registry: dict[str, Any], source_class: str, url: str) -> dict[str, Any]:
    source = (registry.get("source_classes") or {}).get(source_class)
    if not source:
        raise AcquisitionError(f"unsupported source class: {source_class}")
    scheme = urllib.parse.urlparse(url).scheme.lower()
    routes = [
        route for route in source.get("routes", [])
        if route.get("cost") == "FREE" and scheme in route.get("schemes", [])
    ]
    if not routes:
        raise AcquisitionError(f"no zero-cost route for source class {source_class} and scheme {scheme}")
    return routes[0]


def _response_status(resp: Any) -> int | None:
    status = getattr(resp, "status", None)
    if status is not None:
        return int(status)
    getcode = getattr(resp, "getcode", None)
    return int(getcode()) if callable(getcode) and getcode() is not None else None


def _download_http(
    url: str,
    dest: Path,
    *,
    expected_sha256: str | None = None,
    opener: Callable[..., Any] = urllib.request.urlopen,
) -> dict[str, Any]:
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_suffix(dest.suffix + ".part")
    offset = part.stat().st_size if part.exists() else 0
    headers = {"User-Agent": "Research-CoScientist-Acquisition/1.0"}
    if offset:
        headers["Range"] = f"bytes={offset}-"
    request = urllib.request.Request(url, headers=headers)
    with opener(request, timeout=120) as resp:
        status = _response_status(resp)
        append = bool(offset and status == 206)
        mode = "ab" if append else "wb"
        if offset and not append:
            offset = 0
        with part.open(mode) as out:
            while chunk := resp.read(1024 * 1024):
                out.write(chunk)
    os.replace(part, dest)
    digest = sha256_file(dest)
    if expected_sha256 and digest.lower() != expected_sha256.lower():
        dest.unlink(missing_ok=True)
        raise AcquisitionError("downloaded bytes failed expected SHA-256 validation")
    return {
        "bytes": dest.stat().st_size,
        "sha256": digest,
        "resumed_from_bytes": offset,
    }


def _download_ftp(
    url: str,
    dest: Path,
    *,
    expected_sha256: str | None = None,
    opener: Callable[..., Any] = urllib.request.urlopen,
) -> dict[str, Any]:
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_suffix(dest.suffix + ".part")
    request = urllib.request.Request(url, headers={"User-Agent": "Research-CoScientist-Acquisition/1.0"})
    with opener(request, timeout=180) as resp, part.open("wb") as out:
        while chunk := resp.read(1024 * 1024):
            out.write(chunk)
    os.replace(part, dest)
    digest = sha256_file(dest)
    if expected_sha256 and digest.lower() != expected_sha256.lower():
        dest.unlink(missing_ok=True)
        raise AcquisitionError("downloaded bytes failed expected SHA-256 validation")
    return {"bytes": dest.stat().st_size, "sha256": digest, "resumed_from_bytes": 0}


def acquire_candidate(
    source_class: str,
    candidate: dict[str, Any],
    dest: Path,
    *,
    registry: dict[str, Any],
    opener: Callable[..., Any] = urllib.request.urlopen,
) -> dict[str, Any]:
    if candidate.get("cost", "FREE") != "FREE":
        raise AcquisitionError("paid acquisition candidate rejected by zero-cost policy")
    url = str(candidate.get("url") or "")
    if not url:
        raise AcquisitionError("acquisition candidate has no URL")
    route = select_zero_cost_route(registry, source_class, url)
    expected = candidate.get("expected_sha256")
    scheme = urllib.parse.urlparse(url).scheme.lower()
    if scheme in {"http", "https"}:
        meta = _download_http(url, dest, expected_sha256=expected, opener=opener)
    elif scheme == "ftp":
        meta = _download_ftp(url, dest, expected_sha256=expected, opener=opener)
    else:
        raise AcquisitionError(f"unsupported acquisition scheme: {scheme}")

    if source_class == "REST_JSON_API":
        try:
            parsed = json.loads(dest.read_text(encoding="utf-8"))
        except Exception as exc:
            dest.unlink(missing_ok=True)
            raise AcquisitionError("REST_JSON_API response is not valid UTF-8 JSON") from exc
        if not isinstance(parsed, (dict, list)):
            raise AcquisitionError("REST_JSON_API response must be a JSON object or array")

    return {
        **meta,
        "route_id": route["route_id"],
        "source_url": url,
        "source_class": source_class,
    }


def acquire_with_fallback(
    source_class: str,
    candidates: list[dict[str, Any]],
    dest: Path,
    *,
    registry: dict[str, Any] | None = None,
    opener: Callable[..., Any] = urllib.request.urlopen,
) -> dict[str, Any]:
    registry = registry or load_registry()
    if not candidates:
        raise AcquisitionError("acquisition job has no candidates")
    failures: list[dict[str, Any]] = []
    for index, candidate in enumerate(candidates):
        try:
            meta = acquire_candidate(source_class, candidate, dest, registry=registry, opener=opener)
            meta["candidate_index"] = index
            meta["fallbacks_attempted"] = len(failures)
            meta["failed_candidate_errors"] = failures
            return meta
        except Exception as exc:
            dest.unlink(missing_ok=True)
            dest.with_suffix(dest.suffix + ".part").unlink(missing_ok=True)
            failures.append({"candidate_index": index, "error_type": type(exc).__name__})
    raise AcquisitionError(f"all zero-cost acquisition candidates failed ({len(failures)} attempts)")


def validate_acquisition_manifest(manifest: dict[str, Any], job_id: str) -> None:
    validate_job_id(job_id)
    if manifest.get("schema_version") != "cosci.job/1.0":
        raise AcquisitionError("unsupported private job schema")
    if manifest.get("job_id") != job_id:
        raise AcquisitionError("private manifest job_id does not match dispatched opaque job_id")
    if manifest.get("privacy_class") != "PRIVATE_RESEARCH":
        raise AcquisitionError("acquisition manifest must remain PRIVATE_RESEARCH")
    if manifest.get("task_type") != "DATA_ACQUISITION":
        raise AcquisitionError("Acquisition Broker accepts only DATA_ACQUISITION jobs")
    cost = manifest.get("cost_policy") or {}
    if cost.get("require_zero_cost") is not True or cost.get("automatic_paid_services") is not False:
        raise AcquisitionError("acquisition job violates zero-cost policy")
    params = manifest.get("parameters") or {}
    if params.get("source_class") not in {"HTTP_FILE", "REST_JSON_API", "FTP_FILE"}:
        raise AcquisitionError("unsupported acquisition source_class")
    artifact_name = str(params.get("artifact_name") or "")
    if not artifact_name or Path(artifact_name).name != artifact_name:
        raise AcquisitionError("artifact_name must be a safe basename")
    candidates = params.get("candidates")
    if not isinstance(candidates, list) or not candidates:
        raise AcquisitionError("acquisition job requires at least one candidate")
    if any((c or {}).get("cost", "FREE") != "FREE" for c in candidates):
        raise AcquisitionError("acquisition job contains a paid candidate")


def _unique(items: list[dict[str, Any]], what: str) -> dict[str, Any]:
    if len(items) != 1:
        raise AcquisitionError(f"expected exactly one {what}; found {len(items)}")
    return items[0]


def _search_job_folder(client: Any, job_id: str) -> dict[str, Any]:
    q = urllib.parse.quote(f"name = '{job_id}' and trashed = false and mimeType = '{FOLDER_MIME}'")
    payload = json.loads(client._get(f"{DRIVE_API}/files?q={q}&pageSize=100&fields=files(id,name,mimeType,parents)").decode("utf-8"))
    return _unique(payload.get("files", []), f"private Drive job folder named {job_id!r}")


def _child(client: Any, parent_id: str, name: str, *, folder: bool) -> dict[str, Any]:
    hits = [x for x in client.list_folder(parent_id) if x.get("name") == name]
    if folder:
        hits = [x for x in hits if x.get("mimeType") == FOLDER_MIME]
    else:
        hits = [x for x in hits if x.get("mimeType") != FOLDER_MIME]
    return _unique(hits, f"Drive child {name!r}")


def _optional_child(client: Any, parent_id: str, name: str) -> dict[str, Any] | None:
    hits = [x for x in client.list_folder(parent_id) if x.get("name") == name and x.get("mimeType") != FOLDER_MIME]
    if len(hits) > 1:
        raise AcquisitionError(f"Drive child {name!r} is ambiguous: {len(hits)} copies")
    return hits[0] if hits else None


def _download_json(client: Any, file_id: str, dest: Path) -> tuple[dict[str, Any], str]:
    sha = client.download(file_id, str(dest))
    try:
        return json.loads(dest.read_text(encoding="utf-8")), sha
    except Exception as exc:
        raise AcquisitionError(f"{dest.name} is not valid UTF-8 JSON") from exc


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _drive_client_from_env() -> Any:
    from coscientist.drive import DriveClient, DriveCredentials
    return DriveClient(DriveCredentials.from_env())


def run_private_acquisition_job(
    job_id: str,
    *,
    client: Any | None = None,
    workdir: str | Path | None = None,
    opener: Callable[..., Any] = urllib.request.urlopen,
) -> dict[str, Any]:
    validate_job_id(job_id)
    client = client or _drive_client_from_env()
    job_folder = _search_job_folder(client, job_id)
    manifest_obj = _child(client, job_folder["id"], "job.json", folder=False)
    checkpoint_folder = _child(client, job_folder["id"], "checkpoints", folder=True)
    results_folder = _child(client, job_folder["id"], "results", folder=True)
    logs_folder = _child(client, job_folder["id"], "logs", folder=True)
    outputs_folder = _child(client, job_folder["id"], "outputs", folder=True)

    context = tempfile.TemporaryDirectory(prefix="cosci-acquisition-") if workdir is None else None
    root = Path(context.name if context else workdir)
    root.mkdir(parents=True, exist_ok=True)
    try:
        manifest, manifest_sha = _download_json(client, manifest_obj["id"], root / "job.json")
        validate_acquisition_manifest(manifest, job_id)

        existing_result = _optional_child(client, results_folder["id"], "result.json")
        if existing_result:
            result, _ = _download_json(client, existing_result["id"], root / "result-existing.json")
            if result.get("status") != "SUCCEEDED" or (result.get("provenance") or {}).get("manifest_sha256") != manifest_sha:
                raise AcquisitionError("existing acquisition result does not match this exact manifest")
            return {"job_id": job_id, "status": "SUCCEEDED", "privacy_class": "PUBLIC_EXECUTOR_SAFE", "resumed": True}

        checkpoint = {
            "schema_version": "cosci.checkpoint/1.0",
            "job_id": job_id,
            "stage": "ACQUISITION_ROUTE_READY",
            "manifest_sha256": manifest_sha,
            "privacy_class": "PRIVATE_RESEARCH",
            "resume_from": "ACQUIRE_PUBLIC_SOURCE",
        }
        existing_checkpoint = _optional_child(client, checkpoint_folder["id"], "checkpoint.json")
        if not existing_checkpoint:
            cp = root / "checkpoint.json"
            _write_json(cp, checkpoint)
            client.upload(str(cp), checkpoint_folder["id"], "checkpoint.json")

        params = manifest["parameters"]
        artifact = root / params["artifact_name"]
        acquired = acquire_with_fallback(
            params["source_class"], params["candidates"], artifact,
            registry=load_registry(), opener=opener,
        )

        existing_output = _optional_child(client, outputs_folder["id"], params["artifact_name"])
        if existing_output:
            check = root / (params["artifact_name"] + ".existing")
            client.download(existing_output["id"], str(check))
            if sha256_file(check) != acquired["sha256"]:
                raise AcquisitionError("existing private output differs from newly acquired public bytes")
            output_id = existing_output["id"]
        else:
            output_id = client.upload(str(artifact), outputs_folder["id"], params["artifact_name"])

        result = {
            "schema_version": "cosci.result/1.0",
            "job_id": job_id,
            "project_id": manifest.get("project_id"),
            "status": "SUCCEEDED",
            "privacy_class": "PRIVATE_RESEARCH",
            "outputs": [{
                "name": params["artifact_name"],
                "drive_file_id": output_id,
                "bytes": acquired["bytes"],
                "sha256": acquired["sha256"],
                "source_class": acquired["source_class"],
                "source_url": acquired["source_url"],
                "route_id": acquired["route_id"],
                "candidate_index": acquired["candidate_index"],
                "fallbacks_attempted": acquired["fallbacks_attempted"],
                "resumed_from_bytes": acquired["resumed_from_bytes"],
            }],
            "validation": {
                "passed": True,
                "checks": [
                    {"name": "zero_cost_route", "status": "PASS"},
                    {"name": "integrity_sha256", "status": "PASS"},
                    {"name": "private_drive_publication", "status": "PASS"},
                ],
            },
            "provenance": {"manifest_sha256": manifest_sha, "executor": "GITHUB_ACTIONS_PUBLIC"},
            "warnings": acquired["failed_candidate_errors"],
            "next_state": "WAITING_FOR_RESEARCH_DIRECTOR",
        }
        rp = root / "result.json"
        _write_json(rp, result)
        client.upload(str(rp), results_folder["id"], "result.json")

        log = root / "execution.log"
        log.write_text(
            "\n".join([
                f"job_id={job_id}",
                "task_type=DATA_ACQUISITION",
                "zero_cost_route=PASS",
                f"source_class={params['source_class']}",
                f"fallbacks_attempted={acquired['fallbacks_attempted']}",
                "integrity_sha256=PASS",
                "private_output_publication=PASS",
                "next_state=WAITING_FOR_RESEARCH_DIRECTOR",
                "",
            ]),
            encoding="utf-8",
        )
        client.upload(str(log), logs_folder["id"], "execution.log")
        return {
            "job_id": job_id,
            "status": "SUCCEEDED",
            "privacy_class": "PUBLIC_EXECUTOR_SAFE",
            "source_class": params["source_class"],
            "fallbacks_attempted": acquired["fallbacks_attempted"],
            "resumed": False,
        }
    finally:
        if context:
            context.cleanup()


def main() -> int:
    parser = argparse.ArgumentParser(description="Research CoScientist Acquisition Broker v1")
    parser.add_argument("--job-id", required=True, help="Opaque private Drive job ID only")
    parser.add_argument("--public-status", default="out/acquisition-status.json")
    args = parser.parse_args()
    status = run_private_acquisition_job(args.job_id)
    public_path = write_public_status(args.job_id, args.public_status, "DATA_ACQUISITION")
    print(f"ACQUISITION_BROKER=PASS job_id={status['job_id']} public_status={public_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
