from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import platform
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any, Iterable

from research_executor.acquisition import (
    AcquisitionError,
    _child,
    _download_json,
    _optional_child,
    _search_job_folder,
    _write_json,
    _drive_client_from_env,
    sha256_file,
)
from research_executor.public_status import validate_job_id, write_public_status

ROUTES_PATH = Path(__file__).with_name("analysis_routes.json")


class AnalysisError(AcquisitionError):
    pass


def load_analysis_routes() -> dict[str, Any]:
    routes = json.loads(ROUTES_PATH.read_text(encoding="utf-8"))
    policy = routes.get("policy") or {}
    required = {
        "zero_cost_only": True,
        "automatic_paid_routes": False,
        "raw_inputs_immutable": True,
        "derived_outputs_separate": True,
        "environment_capture_required": True,
        "checkpoint_required": True,
    }
    for key, expected in required.items():
        if policy.get(key) is not expected:
            raise AnalysisError(f"analysis route policy must enforce {key}={expected}")
    return routes


def python_adapter_command(module: str, *args: str) -> list[str]:
    return [sys.executable, "-m", module, *args]


def r_adapter_command(script: str, *args: str) -> list[str]:
    return ["Rscript", "--vanilla", script, *args]


def nextflow_adapter_command(pipeline: str, *args: str) -> list[str]:
    return ["nextflow", "run", pipeline, *args]


def nfcore_adapter_command(pipeline: str, revision: str, *args: str) -> list[str]:
    name = pipeline if pipeline.startswith("nf-core/") else f"nf-core/{pipeline}"
    return ["nextflow", "run", name, "-r", revision, *args]


def canonical_sha256(value: Any) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def validate_analysis_manifest(manifest: dict[str, Any], job_id: str) -> None:
    validate_job_id(job_id)
    if manifest.get("schema_version") != "cosci.job/1.0" or manifest.get("job_id") != job_id:
        raise AnalysisError("private analysis manifest identity mismatch")
    if manifest.get("privacy_class") != "PRIVATE_RESEARCH" or manifest.get("task_type") != "ANALYSIS":
        raise AnalysisError("analysis job must be a private ANALYSIS job")
    cost = manifest.get("cost_policy") or {}
    if cost.get("require_zero_cost") is not True or cost.get("automatic_paid_services") is not False:
        raise AnalysisError("analysis job violates zero-cost policy")
    params = manifest.get("parameters") or {}
    if params.get("analysis_kind") != "FASTQ_QC_SUMMARY":
        raise AnalysisError("Session 06 v1 accepts FASTQ_QC_SUMMARY")
    inputs = params.get("inputs")
    if not isinstance(inputs, list) or len(inputs) != 1:
        raise AnalysisError("Session 06 v1 requires exactly one immutable input")
    inp = inputs[0]
    required = ("name", "drive_file_id", "expected_sha256", "source_result_file_id")
    if any(not isinstance(inp.get(k), str) or not inp.get(k) for k in required):
        raise AnalysisError("analysis input is missing immutable reference fields")
    digest = inp["expected_sha256"].lower()
    if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
        raise AnalysisError("analysis input expected_sha256 must be a SHA-256 hex digest")
    analysis = params.get("analysis") or {}
    if analysis.get("route_id") != "python-builtin" or analysis.get("version") != "fastq-qc/1.0":
        raise AnalysisError("Session 06 acceptance requires locked python-builtin fastq-qc/1.0")


def _open_fastq(path: Path):
    if path.name.lower().endswith(".gz"):
        return gzip.open(path, "rt", encoding="utf-8", newline="")
    return path.open("rt", encoding="utf-8", newline="")


def fastq_qc_summary(path: str | Path) -> dict[str, Any]:
    p = Path(path)
    reads = 0
    total_bases = 0
    gc_bases = 0
    n_bases = 0
    quality_sum = 0
    min_len: int | None = None
    max_len = 0
    with _open_fastq(p) as fh:
        while True:
            header = fh.readline()
            if header == "":
                break
            seq = fh.readline()
            plus = fh.readline()
            qual = fh.readline()
            if not seq or not plus or not qual:
                raise AnalysisError("truncated FASTQ record")
            header = header.rstrip("\r\n")
            seq = seq.rstrip("\r\n")
            plus = plus.rstrip("\r\n")
            qual = qual.rstrip("\r\n")
            if not header.startswith("@"):
                raise AnalysisError("FASTQ header does not start with @")
            if not plus.startswith("+"):
                raise AnalysisError("FASTQ separator does not start with +")
            if len(seq) != len(qual):
                raise AnalysisError("FASTQ sequence and quality lengths differ")
            length = len(seq)
            reads += 1
            total_bases += length
            upper = seq.upper()
            gc_bases += upper.count("G") + upper.count("C")
            n_bases += upper.count("N")
            quality_sum += sum(ord(ch) - 33 for ch in qual)
            min_len = length if min_len is None else min(min_len, length)
            max_len = max(max_len, length)
    if reads == 0 or total_bases == 0:
        raise AnalysisError("FASTQ contains no non-empty reads")
    return {
        "schema_version": "cosci.fastq-qc/1.0",
        "read_count": reads,
        "total_bases": total_bases,
        "min_read_length": min_len,
        "max_read_length": max_len,
        "mean_read_length": round(total_bases / reads, 6),
        "gc_bases": gc_bases,
        "gc_fraction": round(gc_bases / total_bases, 8),
        "n_bases": n_bases,
        "n_fraction": round(n_bases / total_bases, 8),
        "mean_phred33": round(quality_sum / total_bases, 6),
    }


def _tool_version(command: str) -> dict[str, Any]:
    path = shutil.which(command)
    return {"available": bool(path), "path": path}


def environment_snapshot() -> dict[str, Any]:
    return {
        "schema_version": "cosci.environment/1.0",
        "python": {
            "version": platform.python_version(),
            "implementation": platform.python_implementation(),
            "executable": sys.executable,
        },
        "platform": {
            "system": platform.system(),
            "release": platform.release(),
            "machine": platform.machine(),
        },
        "tools": {
            "Rscript": _tool_version("Rscript"),
            "nextflow": _tool_version("nextflow"),
            "docker": _tool_version("docker"),
        },
        "github": {
            "repository": os.getenv("GITHUB_REPOSITORY"),
            "sha": os.getenv("GITHUB_SHA"),
            "run_id": os.getenv("GITHUB_RUN_ID"),
            "run_attempt": os.getenv("GITHUB_RUN_ATTEMPT"),
        },
    }


def _upload_json_once(client: Any, folder_id: str, path: Path, name: str) -> str:
    existing = _optional_child(client, folder_id, name)
    if existing:
        check = path.parent / f"drive-existing-{name}"
        client.download(existing["id"], str(check))
        if sha256_file(check) != sha256_file(path):
            raise AnalysisError(f"existing private Drive artifact differs for {name}")
        return existing["id"]
    return client.upload(str(path), folder_id, name)


def run_private_analysis_job(
    job_id: str,
    *,
    client: Any | None = None,
    workdir: str | Path | None = None,
) -> dict[str, Any]:
    validate_job_id(job_id)
    load_analysis_routes()
    client = client or _drive_client_from_env()
    job_folder = _search_job_folder(client, job_id)
    manifest_obj = _child(client, job_folder["id"], "job.json", folder=False)
    checkpoint_folder = _child(client, job_folder["id"], "checkpoints", folder=True)
    results_folder = _child(client, job_folder["id"], "results", folder=True)
    logs_folder = _child(client, job_folder["id"], "logs", folder=True)
    outputs_folder = _child(client, job_folder["id"], "outputs", folder=True)
    metadata_folder = _child(client, job_folder["id"], "metadata", folder=True)

    context = tempfile.TemporaryDirectory(prefix="cosci-analysis-") if workdir is None else None
    root = Path(context.name if context else workdir)
    root.mkdir(parents=True, exist_ok=True)
    try:
        manifest, manifest_sha = _download_json(client, manifest_obj["id"], root / "job.json")
        validate_analysis_manifest(manifest, job_id)
        existing_result = _optional_child(client, results_folder["id"], "result.json")
        if existing_result:
            result, _ = _download_json(client, existing_result["id"], root / "result-existing.json")
            if result.get("status") != "SUCCEEDED" or (result.get("provenance") or {}).get("manifest_sha256") != manifest_sha:
                raise AnalysisError("existing analysis result does not match this exact manifest")
            return {"job_id": job_id, "status": "SUCCEEDED", "resumed": True}

        params = manifest["parameters"]
        inp = params["inputs"][0]
        analysis = params["analysis"]
        definition_sha = canonical_sha256(analysis)

        checkpoint = {
            "schema_version": "cosci.checkpoint/1.0",
            "job_id": job_id,
            "privacy_class": "PRIVATE_RESEARCH",
            "stage": "ANALYSIS_INPUT_LOCKED",
            "manifest_sha256": manifest_sha,
            "analysis_definition_sha256": definition_sha,
            "input_name": inp["name"],
            "input_drive_file_id": inp["drive_file_id"],
            "input_expected_sha256": inp["expected_sha256"],
            "resume_from": "EXECUTE_ANALYSIS",
        }
        cp_path = root / "checkpoint.json"
        _write_json(cp_path, checkpoint)
        _upload_json_once(client, checkpoint_folder["id"], cp_path, "checkpoint.json")

        input_path = root / Path(inp["name"]).name
        downloaded_sha = client.download(inp["drive_file_id"], str(input_path))
        actual_sha = sha256_file(input_path)
        if downloaded_sha != actual_sha or actual_sha.lower() != inp["expected_sha256"].lower():
            raise AnalysisError("immutable analysis input SHA-256 mismatch")

        qc = fastq_qc_summary(input_path)
        qc["input_name"] = inp["name"]
        qc["input_sha256"] = actual_sha
        qc["analysis_definition_sha256"] = definition_sha
        qc_path = root / "fastq_qc.json"
        _write_json(qc_path, qc)
        qc_sha = sha256_file(qc_path)
        qc_file_id = _upload_json_once(client, outputs_folder["id"], qc_path, "fastq_qc.json")

        env = environment_snapshot()
        env["analysis_definition_sha256"] = definition_sha
        env["manifest_sha256"] = manifest_sha
        env_path = root / "environment.json"
        _write_json(env_path, env)
        env_sha = sha256_file(env_path)
        env_file_id = _upload_json_once(client, metadata_folder["id"], env_path, "environment.json")

        provenance = {
            "schema_version": "cosci.analysis-provenance/1.0",
            "executor": "GITHUB_ACTIONS_PUBLIC",
            "manifest_sha256": manifest_sha,
            "analysis_definition_sha256": definition_sha,
            "route_id": analysis["route_id"],
            "analysis_version": analysis["version"],
            "input": {
                "name": inp["name"],
                "drive_file_id": inp["drive_file_id"],
                "source_result_file_id": inp["source_result_file_id"],
                "sha256": actual_sha,
            },
            "derived": {
                "fastq_qc_file_id": qc_file_id,
                "fastq_qc_sha256": qc_sha,
                "environment_file_id": env_file_id,
                "environment_sha256": env_sha,
            },
        }
        provenance_path = root / "provenance.json"
        _write_json(provenance_path, provenance)
        provenance_file_id = _upload_json_once(client, metadata_folder["id"], provenance_path, "provenance.json")

        result = {
            "schema_version": "cosci.result/1.0",
            "job_id": job_id,
            "project_id": manifest.get("project_id"),
            "status": "SUCCEEDED",
            "privacy_class": "PRIVATE_RESEARCH",
            "task_type": "ANALYSIS",
            "analysis_kind": params["analysis_kind"],
            "outputs": [
                {
                    "name": "fastq_qc.json",
                    "drive_file_id": qc_file_id,
                    "sha256": qc_sha,
                    "kind": "DERIVED_DATA",
                }
            ],
            "metadata": {
                "environment_file_id": env_file_id,
                "provenance_file_id": provenance_file_id,
            },
            "metrics": {
                "read_count": qc["read_count"],
                "total_bases": qc["total_bases"],
                "gc_fraction": qc["gc_fraction"],
                "mean_read_length": qc["mean_read_length"],
                "mean_phred33": qc["mean_phred33"],
            },
            "validation": {
                "passed": True,
                "checks": [
                    {"name": "zero_cost_route", "status": "PASS"},
                    {"name": "immutable_input_sha256", "status": "PASS"},
                    {"name": "fastq_structural_validation", "status": "PASS"},
                    {"name": "derived_output_sha256", "status": "PASS"},
                    {"name": "environment_capture", "status": "PASS"},
                    {"name": "raw_derived_separation", "status": "PASS"},
                    {"name": "private_drive_publication", "status": "PASS"},
                ],
            },
            "provenance": {
                "manifest_sha256": manifest_sha,
                "analysis_definition_sha256": definition_sha,
                "executor": "GITHUB_ACTIONS_PUBLIC",
                "route_id": analysis["route_id"],
                "analysis_version": analysis["version"],
            },
            "next_state": "WAITING_FOR_RESEARCH_DIRECTOR",
        }
        result_path = root / "result.json"
        _write_json(result_path, result)
        client.upload(str(result_path), results_folder["id"], "result.json")

        log_path = root / "execution.log"
        log_path.write_text(
            "\n".join([
                f"job_id={job_id}",
                "task_type=ANALYSIS",
                "analysis_kind=FASTQ_QC_SUMMARY",
                "immutable_input_sha256=PASS",
                "fastq_structural_validation=PASS",
                "derived_output_sha256=PASS",
                "environment_capture=PASS",
                "raw_derived_separation=PASS",
                "private_drive_publication=PASS",
                "next_state=WAITING_FOR_RESEARCH_DIRECTOR",
                "",
            ]),
            encoding="utf-8",
        )
        client.upload(str(log_path), logs_folder["id"], "execution.log")

        return {
            "job_id": job_id,
            "status": "SUCCEEDED",
            "analysis_kind": params["analysis_kind"],
            "derived_file_count": 1,
            "resumed": False,
        }
    finally:
        if context:
            context.cleanup()


def main() -> int:
    parser = argparse.ArgumentParser(description="Research CoScientist deterministic analysis engine")
    parser.add_argument("--job-id", required=True)
    parser.add_argument("--public-status", default="out/analysis-status.json")
    args = parser.parse_args()
    status = run_private_analysis_job(args.job_id)
    public = write_public_status(args.job_id, args.public_status, "ANALYSIS")
    print(f"DETERMINISTIC_ANALYSIS=PASS job_id={status['job_id']} public_status={public}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
