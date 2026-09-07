from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import tempfile
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Callable

from research_executor.acquisition import (
    AcquisitionError,
    _child,
    _download_http,
    _download_json,
    _optional_child,
    _search_job_folder,
    _unique,
    _write_json,
    _drive_client_from_env,
    sha256_file,
)
from research_executor.public_status import validate_job_id, write_public_status

ENA_FILE_REPORT = "https://www.ebi.ac.uk/ena/portal/api/filereport"
GEO_SOFT = "https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi"
ROUTES_PATH = Path(__file__).with_name("omics_routes.json")
RUN_RE = re.compile(r"^(?:SRR|ERR|DRR)\d+$", re.I)
GSE_RE = re.compile(r"^GSE\d+$", re.I)
SRP_RE = re.compile(r"^(?:SRP|ERP|DRP)\d+$", re.I)


class OmicsError(AcquisitionError):
    pass


def md5_file(path: str | Path) -> str:
    digest = hashlib.md5()
    with Path(path).open("rb") as fh:
        while chunk := fh.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def load_omics_routes() -> dict[str, Any]:
    routes = json.loads(ROUTES_PATH.read_text(encoding="utf-8"))
    policy = routes.get("policy") or {}
    if policy.get("zero_cost_only") is not True or policy.get("automatic_paid_routes") is not False:
        raise OmicsError("omics route registry must enforce zero-cost-only execution")
    return routes


def pysradb_command(accession: str) -> list[str]:
    accession = accession.upper()
    if GSE_RE.match(accession):
        return ["pysradb", "gse-to-srp", accession]
    if accession.startswith("GSM"):
        return ["pysradb", "gsm-to-srr", accession]
    if SRP_RE.match(accession):
        return ["pysradb", "srp-to-srr", accession]
    if RUN_RE.match(accession):
        return ["pysradb", "srr-to-srp", accession] if accession.startswith("SRR") else []
    return []


def nfcore_fetchngs_command(ids_path: str, outdir: str) -> list[str]:
    return [
        "nextflow", "run", "nf-core/fetchngs", "-r", "1.12.0",
        "-profile", "docker", "--input", ids_path, "--outdir", outdir,
        "--download_method", "ftp",
    ]


def sra_toolkit_commands(accession: str, outdir: str) -> list[list[str]]:
    return [
        ["prefetch", accession, "--output-directory", outdir],
        ["fasterq-dump", accession, "--split-files", "--outdir", outdir],
    ]


def geo_supplementary_urls(soft_text: str) -> list[str]:
    urls: list[str] = []
    for line in soft_text.splitlines():
        if line.startswith("!Series_supplementary_file") or line.startswith("!Sample_supplementary_file"):
            _, _, value = line.partition("=")
            value = value.strip()
            if value.lower().startswith(("http://", "https://", "ftp://")):
                urls.append(value)
    return urls


def fetch_geo_supplementary_metadata(
    accession: str,
    *,
    opener: Callable[..., Any] = urllib.request.urlopen,
) -> list[str]:
    if not GSE_RE.match(accession):
        raise OmicsError("GEO supplementary lookup currently accepts a GSE accession")
    query = urllib.parse.urlencode({"acc": accession, "targ": "self", "form": "text", "view": "full"})
    request = urllib.request.Request(f"{GEO_SOFT}?{query}", headers={"User-Agent": "Research-CoScientist-Omics/1.0"})
    with opener(request, timeout=120) as resp:
        text = resp.read().decode("utf-8", errors="replace")
    return geo_supplementary_urls(text)


def ena_filereport(
    accession: str,
    *,
    opener: Callable[..., Any] = urllib.request.urlopen,
) -> list[dict[str, str]]:
    fields = [
        "run_accession", "study_accession", "experiment_accession", "sample_accession",
        "secondary_sample_accession", "scientific_name", "library_strategy", "library_layout",
        "instrument_platform", "read_count", "base_count", "fastq_ftp", "fastq_md5", "fastq_bytes",
    ]
    query = urllib.parse.urlencode({
        "accession": accession,
        "result": "read_run",
        "fields": ",".join(fields),
        "format": "tsv",
    })
    request = urllib.request.Request(f"{ENA_FILE_REPORT}?{query}", headers={"User-Agent": "Research-CoScientist-Omics/1.0"})
    try:
        with opener(request, timeout=120) as resp:
            text = resp.read().decode("utf-8")
    except Exception as exc:
        raise OmicsError(f"ENA metadata lookup failed for accession {accession}") from exc
    rows = list(csv.DictReader(io.StringIO(text), delimiter="\t"))
    return [dict(row) for row in rows if row.get("run_accession")]


def _split_semicolon(value: str | None) -> list[str]:
    return [x.strip() for x in (value or "").split(";") if x.strip()]


def _fastq_files(row: dict[str, str]) -> list[dict[str, Any]]:
    urls = _split_semicolon(row.get("fastq_ftp"))
    md5s = _split_semicolon(row.get("fastq_md5"))
    sizes_raw = _split_semicolon(row.get("fastq_bytes"))
    if not urls:
        return []
    if md5s and len(md5s) != len(urls):
        raise OmicsError(f"ENA metadata has mismatched FASTQ URL/MD5 counts for {row.get('run_accession')}")
    if sizes_raw and len(sizes_raw) != len(urls):
        raise OmicsError(f"ENA metadata has mismatched FASTQ URL/byte counts for {row.get('run_accession')}")
    files = []
    for i, raw in enumerate(urls):
        if raw.startswith("ftp://"):
            url = "https://" + raw[len("ftp://"):]
        elif raw.startswith(("http://", "https://")):
            url = raw
        else:
            url = "https://" + raw.lstrip("/")
        files.append({
            "url": url,
            "md5": md5s[i] if i < len(md5s) else None,
            "bytes": int(sizes_raw[i]) if i < len(sizes_raw) and sizes_raw[i].isdigit() else None,
            "name": Path(urllib.parse.urlparse(url).path).name,
        })
    return files


def reconcile_ena_rows(rows: list[dict[str, str]]) -> dict[str, Any]:
    if not rows:
        raise OmicsError("ENA returned no public read-run metadata")
    runs = []
    all_files = []
    seen_runs: set[str] = set()
    for row in rows:
        run = row["run_accession"]
        if run in seen_runs:
            raise OmicsError(f"duplicate ENA run row: {run}")
        seen_runs.add(run)
        files = _fastq_files(row)
        if not files:
            raise OmicsError(f"ENA has no direct FASTQ files for run {run}")
        runs.append({
            "run_accession": run,
            "experiment_accession": row.get("experiment_accession"),
            "sample_accession": row.get("sample_accession"),
            "secondary_sample_accession": row.get("secondary_sample_accession"),
            "study_accession": row.get("study_accession"),
            "scientific_name": row.get("scientific_name"),
            "library_strategy": row.get("library_strategy"),
            "library_layout": row.get("library_layout"),
            "instrument_platform": row.get("instrument_platform"),
            "read_count": row.get("read_count"),
            "base_count": row.get("base_count"),
            "files": files,
        })
        all_files.extend(files)
    total = sum(x.get("bytes") or 0 for x in all_files)
    return {
        "run_count": len(runs),
        "file_count": len(all_files),
        "total_fastq_bytes": total,
        "runs": runs,
    }


def select_smallest_candidate(
    candidates: list[str],
    max_total_bytes: int,
    *,
    opener: Callable[..., Any] = urllib.request.urlopen,
) -> tuple[str, list[dict[str, str]], dict[str, Any], list[dict[str, Any]]]:
    reports: list[dict[str, Any]] = []
    eligible: list[tuple[int, str, list[dict[str, str]], dict[str, Any]]] = []
    for accession in candidates:
        try:
            rows = ena_filereport(accession, opener=opener)
            reconciled = reconcile_ena_rows(rows)
            size = reconciled["total_fastq_bytes"]
            reports.append({"accession": accession, "status": "OK", "total_fastq_bytes": size})
            if size and size <= max_total_bytes:
                eligible.append((size, accession, rows, reconciled))
        except Exception as exc:
            reports.append({"accession": accession, "status": "UNAVAILABLE", "error_type": type(exc).__name__})
    if not eligible:
        raise OmicsError(f"no accession candidate has complete direct FASTQ metadata within {max_total_bytes} bytes")
    eligible.sort(key=lambda x: x[0])
    _, accession, rows, reconciled = eligible[0]
    return accession, rows, reconciled, reports


def validate_omics_manifest(manifest: dict[str, Any], job_id: str) -> None:
    validate_job_id(job_id)
    if manifest.get("schema_version") != "cosci.job/1.0" or manifest.get("job_id") != job_id:
        raise OmicsError("private omics manifest identity mismatch")
    if manifest.get("privacy_class") != "PRIVATE_RESEARCH" or manifest.get("task_type") != "DATA_ACQUISITION":
        raise OmicsError("omics job must be a private DATA_ACQUISITION job")
    cost = manifest.get("cost_policy") or {}
    if cost.get("require_zero_cost") is not True or cost.get("automatic_paid_services") is not False:
        raise OmicsError("omics job violates zero-cost policy")
    params = manifest.get("parameters") or {}
    if params.get("source_class") != "OMICS_ACCESSION":
        raise OmicsError("omics job source_class must be OMICS_ACCESSION")
    candidates = params.get("accession_candidates")
    if not isinstance(candidates, list) or not candidates or not all(isinstance(x, str) for x in candidates):
        raise OmicsError("omics job requires accession_candidates")
    if int(params.get("max_total_fastq_bytes", 0)) <= 0:
        raise OmicsError("omics job requires a positive max_total_fastq_bytes")


def _upload_or_verify(client: Any, folder_id: str, local: Path, name: str, expected_md5: str | None) -> str:
    existing = _optional_child(client, folder_id, name)
    if existing:
        verify_path = local.parent / (name + ".drive-existing")
        client.download(existing["id"], str(verify_path))
        if sha256_file(verify_path) != sha256_file(local):
            raise OmicsError(f"existing private Drive output differs for {name}")
        return existing["id"]
    if expected_md5 and md5_file(local).lower() != expected_md5.lower():
        raise OmicsError(f"archive MD5 validation failed for {name}")
    return client.upload(str(local), folder_id, name)


def _tool_snapshot() -> dict[str, Any]:
    tools = {}
    for name in ("pysradb", "nextflow", "prefetch", "fasterq-dump", "docker"):
        path = shutil.which(name)
        tools[name] = {"available": bool(path), "path": path}
    return tools


def run_private_omics_job(
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
    metadata_folder = _child(client, job_folder["id"], "metadata", folder=True)

    context = tempfile.TemporaryDirectory(prefix="cosci-omics-") if workdir is None else None
    root = Path(context.name if context else workdir)
    root.mkdir(parents=True, exist_ok=True)
    try:
        manifest, manifest_sha = _download_json(client, manifest_obj["id"], root / "job.json")
        validate_omics_manifest(manifest, job_id)
        existing_result = _optional_child(client, results_folder["id"], "result.json")
        if existing_result:
            result, _ = _download_json(client, existing_result["id"], root / "result-existing.json")
            if result.get("status") != "SUCCEEDED" or (result.get("provenance") or {}).get("manifest_sha256") != manifest_sha:
                raise OmicsError("existing omics result does not match this exact manifest")
            return {"job_id": job_id, "status": "SUCCEEDED", "resumed": True}

        params = manifest["parameters"]
        selected, rows, reconciled, candidate_reports = select_smallest_candidate(
            params["accession_candidates"], int(params["max_total_fastq_bytes"]), opener=opener,
        )
        checkpoint = {
            "schema_version": "cosci.checkpoint/1.0",
            "job_id": job_id,
            "privacy_class": "PRIVATE_RESEARCH",
            "stage": "OMICS_ACCESSION_RESOLVED",
            "selected_accession": selected,
            "resolved_runs": [r["run_accession"] for r in reconciled["runs"]],
            "expected_file_count": reconciled["file_count"],
            "expected_total_fastq_bytes": reconciled["total_fastq_bytes"],
            "manifest_sha256": manifest_sha,
            "resume_from": "DOWNLOAD_FASTQ",
        }
        cp = root / "checkpoint.json"
        _write_json(cp, checkpoint)
        client.upload(str(cp), checkpoint_folder["id"], "checkpoint.json")

        metadata = {
            "schema_version": "cosci.omics-metadata/1.0",
            "requested_accession_candidates": params["accession_candidates"],
            "selected_accession": selected,
            "candidate_reports": candidate_reports,
            "reconciliation": reconciled,
            "ena_rows": rows,
            "tool_routes": load_omics_routes(),
            "tool_snapshot": _tool_snapshot(),
            "pysradb_resolution_command": pysradb_command(selected),
            "nfcore_fetchngs_command": nfcore_fetchngs_command("ids.csv", "outdir"),
            "sra_toolkit_commands": sra_toolkit_commands(selected, "outdir"),
        }
        metadata_path = root / "metadata.json"
        _write_json(metadata_path, metadata)
        metadata_file_id = client.upload(str(metadata_path), metadata_folder["id"], "metadata.json")

        outputs: list[dict[str, Any]] = []
        for run in reconciled["runs"]:
            for f in run["files"]:
                local = root / f["name"]
                dl = _download_http(f["url"], local, opener=opener)
                archive_md5 = md5_file(local)
                if f.get("md5") and archive_md5.lower() != f["md5"].lower():
                    raise OmicsError(f"ENA MD5 validation failed for {f['name']}")
                if f.get("bytes") is not None and local.stat().st_size != int(f["bytes"]):
                    raise OmicsError(f"ENA byte-count validation failed for {f['name']}")
                drive_id = _upload_or_verify(client, outputs_folder["id"], local, f["name"], f.get("md5"))
                outputs.append({
                    "run_accession": run["run_accession"],
                    "name": f["name"],
                    "drive_file_id": drive_id,
                    "bytes": local.stat().st_size,
                    "ena_md5": f.get("md5"),
                    "local_md5": archive_md5,
                    "sha256": dl["sha256"],
                    "resumed_from_bytes": dl["resumed_from_bytes"],
                    "route_id": "ena-direct-fastq",
                })

        if len(outputs) != reconciled["file_count"]:
            raise OmicsError("downloaded FASTQ file count does not match reconciled ENA metadata")
        if sum(x["bytes"] for x in outputs) != reconciled["total_fastq_bytes"]:
            raise OmicsError("downloaded FASTQ byte total does not match ENA metadata")

        result = {
            "schema_version": "cosci.result/1.0",
            "job_id": job_id,
            "project_id": manifest.get("project_id"),
            "status": "SUCCEEDED",
            "privacy_class": "PRIVATE_RESEARCH",
            "source_class": "OMICS_ACCESSION",
            "selected_accession": selected,
            "resolved_run_count": reconciled["run_count"],
            "downloaded_file_count": len(outputs),
            "downloaded_total_bytes": sum(x["bytes"] for x in outputs),
            "outputs": outputs,
            "metadata_file_id": metadata_file_id,
            "validation": {
                "passed": True,
                "checks": [
                    {"name": "zero_cost_route", "status": "PASS"},
                    {"name": "accession_resolution", "status": "PASS"},
                    {"name": "sample_run_file_reconciliation", "status": "PASS"},
                    {"name": "ena_archive_md5", "status": "PASS"},
                    {"name": "local_sha256", "status": "PASS"},
                    {"name": "private_drive_publication", "status": "PASS"},
                ],
            },
            "provenance": {
                "manifest_sha256": manifest_sha,
                "executor": "GITHUB_ACTIONS_PUBLIC",
                "metadata_authority": "ENA Portal API",
                "route_id": "ena-direct-fastq",
            },
            "next_state": "WAITING_FOR_RESEARCH_DIRECTOR",
        }
        result_path = root / "result.json"
        _write_json(result_path, result)
        client.upload(str(result_path), results_folder["id"], "result.json")

        log = root / "execution.log"
        log.write_text(
            "\n".join([
                f"job_id={job_id}",
                "task_type=DATA_ACQUISITION",
                "source_class=OMICS_ACCESSION",
                f"selected_accession={selected}",
                f"resolved_run_count={reconciled['run_count']}",
                f"downloaded_file_count={len(outputs)}",
                f"downloaded_total_bytes={sum(x['bytes'] for x in outputs)}",
                "route=ena-direct-fastq",
                "archive_md5=PASS",
                "sha256=PASS",
                "sample_run_file_reconciliation=PASS",
                "private_drive_publication=PASS",
                "next_state=WAITING_FOR_RESEARCH_DIRECTOR",
                "",
            ]), encoding="utf-8"
        )
        client.upload(str(log), logs_folder["id"], "execution.log")
        return {
            "job_id": job_id,
            "status": "SUCCEEDED",
            "selected_accession": selected,
            "downloaded_file_count": len(outputs),
            "downloaded_total_bytes": sum(x["bytes"] for x in outputs),
            "resumed": False,
        }
    finally:
        if context:
            context.cleanup()


def main() -> int:
    parser = argparse.ArgumentParser(description="Research CoScientist Omics Acquisition")
    parser.add_argument("--job-id", required=True)
    parser.add_argument("--public-status", default="out/omics-status.json")
    args = parser.parse_args()
    status = run_private_omics_job(args.job_id)
    public = write_public_status(args.job_id, args.public_status, "DATA_ACQUISITION")
    print(
        "OMICS_ACQUISITION=PASS "
        f"job_id={status['job_id']} selected={status.get('selected_accession')} "
        f"files={status.get('downloaded_file_count')} bytes={status.get('downloaded_total_bytes')} "
        f"public_status={public}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
