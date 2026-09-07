from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
import re
import shutil
import subprocess
import tarfile
import tempfile
from pathlib import Path
from typing import Any

from research_executor.acquisition import (
    AcquisitionError, _child, _download_json, _drive_client_from_env,
    _optional_child, _search_job_folder, _write_json, sha256_file,
)
from research_executor.public_status import validate_job_id, write_public_status

_SAMPLE_RE = re.compile(r"^(GSM\d+).*?(W|JA)_(W|JA)_rep(\d+).*\.csv\.gz$", re.I)
EXPECTED_GROUPS = ("W_W", "W_JA", "JA_W", "JA_JA")


class FactorialRNASeqError(AcquisitionError):
    pass


def _upload_once(client: Any, folder_id: str, path: Path, name: str) -> str:
    existing = _optional_child(client, folder_id, name)
    if existing:
        probe = path.parent / f"existing-{name}"
        client.download(existing["id"], str(probe))
        if sha256_file(probe) != sha256_file(path):
            raise FactorialRNASeqError(f"existing Drive artifact differs for {name}")
        return existing["id"]
    uploaded = client.upload(str(path), folder_id, name)
    return uploaded["id"] if isinstance(uploaded, dict) else str(uploaded)


def _normalized_header(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (value or "").strip().lower()).strip()


def detect_columns(headers: list[str]) -> tuple[str, str]:
    if not headers:
        raise FactorialRNASeqError("CSV has no header")
    normalized = {h: _normalized_header(h) for h in headers}

    gene_candidates = [
        h for h, n in normalized.items()
        if any(token in n for token in ("gene", "ensembl", "locus", "feature"))
    ]
    gene_col = gene_candidates[0] if gene_candidates else headers[0]

    raw_candidates = [
        h for h, n in normalized.items()
        if "count" in n and "cpm" not in n and "million" not in n and ("raw" in n or n == "count" or "read count" in n)
    ]
    if not raw_candidates:
        raw_candidates = [
            h for h, n in normalized.items()
            if "count" in n and "cpm" not in n and "million" not in n and h != gene_col
        ]
    if not raw_candidates:
        raise FactorialRNASeqError(f"could not identify raw-count column from headers={headers}")
    return gene_col, raw_candidates[0]


def parse_sample_file(path: Path) -> tuple[dict[str, int], dict[str, str]]:
    match = _SAMPLE_RE.match(path.name)
    if not match:
        raise FactorialRNASeqError(f"unexpected sample filename: {path.name}")
    gsm, prior, challenge, rep = match.groups()
    prior = prior.upper(); challenge = challenge.upper()
    group = f"{prior}_{challenge}"
    if group not in EXPECTED_GROUPS:
        raise FactorialRNASeqError(f"unexpected group in {path.name}")

    with gzip.open(path, "rt", encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        headers = list(reader.fieldnames or [])
        gene_col, count_col = detect_columns(headers)
        counts: dict[str, int] = {}
        for row in reader:
            gene = str(row.get(gene_col) or "").strip()
            if not gene:
                continue
            raw = str(row.get(count_col) or "").strip()
            try:
                value = int(float(raw))
            except ValueError as exc:
                raise FactorialRNASeqError(f"non-numeric raw count for {gene} in {path.name}") from exc
            if value < 0:
                raise FactorialRNASeqError("negative raw count encountered")
            counts[gene] = value
    if not counts:
        raise FactorialRNASeqError(f"no counts parsed from {path.name}")
    prior, challenge = group.split("_")
    return counts, {
        "sample_id": gsm,
        "group": group,
        "replicate": rep,
        "prior_ja": prior,
        "challenge_ja": challenge,
        "filename": path.name,
        "gene_column": gene_col,
        "raw_count_column": count_col,
    }


def safe_extract_csv_gz(archive: Path, dest: Path) -> list[Path]:
    dest.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive, "r:*") as tar:
        members = [m for m in tar.getmembers() if m.isfile()]
        selected = []
        for member in members:
            name = Path(member.name).name
            if name.endswith(".csv.gz"):
                if "/" in member.name.strip("/").replace("\\", "/") and Path(member.name).name != member.name:
                    pass
                target = dest / name
                src = tar.extractfile(member)
                if src is None:
                    raise FactorialRNASeqError(f"cannot read archive member {member.name}")
                with target.open("wb") as out:
                    shutil.copyfileobj(src, out)
                selected.append(target)
    if len(selected) != 16:
        raise FactorialRNASeqError(f"expected 16 CSV.GZ files, found {len(selected)}")
    if len({p.name for p in selected}) != 16:
        raise FactorialRNASeqError("duplicate sample basenames in archive")
    return sorted(selected)


def build_matrix(sample_paths: list[Path], expected_groups: dict[str, list[str]], out_dir: Path) -> tuple[Path, Path, dict[str, Any]]:
    parsed = []
    gene_sets = []
    for path in sample_paths:
        counts, meta = parse_sample_file(path)
        parsed.append((counts, meta))
        gene_sets.append(set(counts))

    expected_accessions = {gsm for values in expected_groups.values() for gsm in values}
    observed_accessions = {meta["sample_id"] for _, meta in parsed}
    if observed_accessions != expected_accessions:
        missing = sorted(expected_accessions - observed_accessions)
        extra = sorted(observed_accessions - expected_accessions)
        raise FactorialRNASeqError(f"sample reconciliation failed missing={missing} extra={extra}")

    genes = sorted(set.intersection(*gene_sets))
    if not genes:
        raise FactorialRNASeqError("sample files have no common genes")
    if any(len(gset) != len(genes) for gset in gene_sets):
        raise FactorialRNASeqError("gene sets differ across deposited sample files")

    parsed.sort(key=lambda x: x[1]["sample_id"])
    counts_path = out_dir / "counts_matrix.tsv"
    with counts_path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh, delimiter="\t", lineterminator="\n")
        writer.writerow(["gene_id"] + [m["sample_id"] for _, m in parsed])
        for gene in genes:
            writer.writerow([gene] + [c[gene] for c, _ in parsed])

    samples_path = out_dir / "samples.tsv"
    with samples_path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh, delimiter="\t", lineterminator="\n")
        writer.writerow(["sample_id", "group", "replicate", "prior_ja", "challenge_ja"])
        for _, m in parsed:
            writer.writerow([m["sample_id"], m["group"], m["replicate"], m["prior_ja"], m["challenge_ja"]])

    metadata = {
        "sample_count": len(parsed),
        "gene_count": len(genes),
        "groups": {g: sum(1 for _, m in parsed if m["group"] == g) for g in EXPECTED_GROUPS},
        "columns": {m["sample_id"]: {"gene": m["gene_column"], "raw_count": m["raw_count_column"]} for _, m in parsed},
    }
    if any(metadata["groups"].get(g) != 4 for g in EXPECTED_GROUPS):
        raise FactorialRNASeqError(f"expected 4 replicates per group, got {metadata['groups']}")
    return counts_path, samples_path, metadata


def validate_manifest(manifest: dict[str, Any], job_id: str) -> None:
    validate_job_id(job_id)
    if manifest.get("job_id") != job_id or manifest.get("task_type") != "ANALYSIS":
        raise FactorialRNASeqError("analysis manifest identity/task mismatch")
    if manifest.get("privacy_class") != "PRIVATE_RESEARCH":
        raise FactorialRNASeqError("analysis manifest must be private")
    cost = manifest.get("cost_policy") or {}
    if cost.get("require_zero_cost") is not True or cost.get("automatic_paid_services") is not False:
        raise FactorialRNASeqError("zero-cost policy violation")
    p = manifest.get("parameters") or {}
    if p.get("analysis_kind") != "FACTORIAL_RNASEQ_DESEQ2":
        raise FactorialRNASeqError("unexpected analysis_kind")
    if (p.get("analysis") or {}).get("version") != "deseq2-factorial/1.0":
        raise FactorialRNASeqError("analysis version must be locked")
    groups = p.get("groups") or {}
    if set(groups) != set(EXPECTED_GROUPS) or any(len(groups[g]) != 4 for g in EXPECTED_GROUPS):
        raise FactorialRNASeqError("pilot requires four declared groups with four replicates each")


def _read_tsv_summary(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh, delimiter="\t"))
    sig = [r for r in rows if r.get("padj") not in ("", "NA", None) and float(r["padj"]) < 0.05]
    sig_lfc1 = [r for r in sig if abs(float(r.get("log2FoldChange") or 0)) >= 1]
    top = []
    for r in rows[:10]:
        top.append({
            "gene_id": r.get("gene_id"),
            "log2FoldChange": None if r.get("log2FoldChange") in ("", "NA", None) else float(r["log2FoldChange"]),
            "padj": None if r.get("padj") in ("", "NA", None) else float(r["padj"]),
        })
    return {"tested_genes": len(rows), "fdr_lt_0_05": len(sig), "fdr_lt_0_05_abs_lfc_ge_1": len(sig_lfc1), "top10": top}


def run_job(job_id: str, client: Any | None = None, workdir: str | Path | None = None) -> dict[str, Any]:
    validate_job_id(job_id)
    client = client or _drive_client_from_env()
    job_folder = _search_job_folder(client, job_id)
    manifest_obj = _child(client, job_folder["id"], "job.json", folder=False)
    checkpoints = _child(client, job_folder["id"], "checkpoints", folder=True)
    results = _child(client, job_folder["id"], "results", folder=True)
    logs = _child(client, job_folder["id"], "logs", folder=True)
    outputs = _child(client, job_folder["id"], "outputs", folder=True)
    metadata_folder = _child(client, job_folder["id"], "metadata", folder=True)

    ctx = tempfile.TemporaryDirectory(prefix="cosci-s11-rnaseq-") if workdir is None else None
    root = Path(ctx.name if ctx else workdir)
    root.mkdir(parents=True, exist_ok=True)
    try:
        manifest, manifest_sha = _download_json(client, manifest_obj["id"], root / "job.json")
        validate_manifest(manifest, job_id)
        existing = _optional_child(client, results["id"], "result.json")
        if existing:
            result, _ = _download_json(client, existing["id"], root / "result-existing.json")
            if result.get("status") != "SUCCEEDED" or (result.get("provenance") or {}).get("manifest_sha256") != manifest_sha:
                raise FactorialRNASeqError("existing result does not match manifest")
            return result

        params = manifest["parameters"]
        source_job = _search_job_folder(client, params["source_acquisition_job_id"])
        source_results = _child(client, source_job["id"], "results", folder=True)
        acq_result_obj = _child(client, source_results["id"], "result.json", folder=False)
        acq_result, _ = _download_json(client, acq_result_obj["id"], root / "acquisition_result.json")
        if acq_result.get("status") != "SUCCEEDED":
            raise FactorialRNASeqError("source acquisition is not successful")
        candidates = [x for x in acq_result.get("outputs") or [] if x.get("name") == params["source_artifact_name"]]
        if len(candidates) != 1:
            raise FactorialRNASeqError("source acquisition result does not identify exactly one expected artifact")
        source_ref = candidates[0]
        source_file_id = str(source_ref.get("drive_file_id") or "")
        if not source_file_id:
            raise FactorialRNASeqError("source acquisition result is missing drive_file_id")

        checkpoint = {
            "schema_version":"cosci.checkpoint/1.0","job_id":job_id,"stage":"SOURCE_ACQUISITION_LINKED",
            "manifest_sha256":manifest_sha,"source_acquisition_job_id":params["source_acquisition_job_id"],
            "source_sha256":source_ref["sha256"],"privacy_class":"PRIVATE_RESEARCH"
        }
        cp = root / "checkpoint.json"; _write_json(cp, checkpoint); _upload_once(client, checkpoints["id"], cp, cp.name)

        archive = root / params["source_artifact_name"]
        client.download(source_file_id, str(archive))
        if sha256_file(archive) != source_ref["sha256"]:
            raise FactorialRNASeqError("acquired archive SHA-256 mismatch at analysis handoff")

        sample_paths = safe_extract_csv_gz(archive, root / "samples")
        counts_path, samples_path, matrix_meta = build_matrix(sample_paths, params["groups"], root)

        script = Path(__file__).with_name("r_scripts") / "factorial_deseq2.R"
        if not script.exists():
            raise FactorialRNASeqError("tracked DESeq2 R script is missing")
        r_out = root / "r_output"; r_out.mkdir()
        cmd = [
            "Rscript", "--vanilla", str(script), str(counts_path), str(samples_path), str(r_out),
            str((params["analysis"] or {}).get("low_count_filter_total", 10)),
            str((params["analysis"] or {}).get("alpha", 0.05)),
        ]
        proc = subprocess.run(cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=1200)
        (root / "r_console.log").write_text(proc.stdout, encoding="utf-8")
        if proc.returncode != 0:
            raise FactorialRNASeqError(f"DESeq2 failed with exit code {proc.returncode}")

        expected = [
            "W_JA_vs_W_W.tsv","JA_W_vs_W_W.tsv","JA_JA_vs_JA_W.tsv","interaction.tsv",
            "pca_coordinates.tsv","pca.png","deseq2_sessionInfo.txt","results_names.txt"
        ]
        for name in expected:
            if not (r_out / name).exists():
                raise FactorialRNASeqError(f"DESeq2 output missing: {name}")

        summaries = {name[:-4]: _read_tsv_summary(r_out / name) for name in expected[:4]}
        analysis_summary = {
            "schema_version":"cosci.factorial-rnaseq-summary/1.0",
            "series":params["expected_series"],
            "design":params["analysis"]["design"],
            "matrix":matrix_meta,
            "primary_contrast":"interaction",
            "contrasts":summaries,
            "interpretation_status":"WAITING_FOR_RESEARCH_DIRECTOR",
        }
        summary_path = root / "analysis_summary.json"; _write_json(summary_path, analysis_summary)

        upload_paths = [
            summary_path, counts_path, samples_path,
            *(r_out / n for n in expected if n not in ("deseq2_sessionInfo.txt","results_names.txt")),
        ]
        output_refs = []
        for path in upload_paths:
            fid = _upload_once(client, outputs["id"], path, path.name)
            output_refs.append({"name":path.name,"drive_file_id":fid,"sha256":sha256_file(path),"bytes":path.stat().st_size})

        env_files = []
        for name in ("deseq2_sessionInfo.txt","results_names.txt"):
            path = r_out / name
            fid = _upload_once(client, metadata_folder["id"], path, name)
            env_files.append({"name":name,"drive_file_id":fid,"sha256":sha256_file(path)})
        console = root / "r_console.log"
        console_id = _upload_once(client, logs["id"], console, "r_console.log")

        provenance = {
            "schema_version":"cosci.factorial-rnaseq-provenance/1.0",
            "manifest_sha256":manifest_sha,
            "source_acquisition_job_id":params["source_acquisition_job_id"],
            "source_drive_file_id":source_file_id,
            "source_sha256":source_ref["sha256"],
            "executor":"GITHUB_ACTIONS_PUBLIC",
            "analysis":"deseq2-factorial/1.0",
            "design":params["analysis"]["design"],
            "outputs":{x["name"]:x["sha256"] for x in output_refs},
        }
        prov_path = root / "provenance.json"; _write_json(prov_path, provenance)
        prov_id = _upload_once(client, metadata_folder["id"], prov_path, prov_path.name)

        result = {
            "schema_version":"cosci.result/1.0","job_id":job_id,"project_id":manifest["project_id"],
            "task_type":"ANALYSIS","analysis_kind":"FACTORIAL_RNASEQ_DESEQ2","status":"SUCCEEDED",
            "privacy_class":"PRIVATE_RESEARCH","outputs":output_refs,
            "metadata":{"provenance_file_id":prov_id,"environment_files":env_files,"r_console_log_file_id":console_id},
            "metrics":{
                "sample_count":matrix_meta["sample_count"],"gene_count":matrix_meta["gene_count"],
                "interaction_fdr_lt_0_05":summaries["interaction"]["fdr_lt_0_05"],
                "interaction_fdr_lt_0_05_abs_lfc_ge_1":summaries["interaction"]["fdr_lt_0_05_abs_lfc_ge_1"],
            },
            "validation":{"passed":True,"checks":[
                {"name":"zero_cost_route","status":"PASS"},{"name":"acquisition_analysis_sha256_handoff","status":"PASS"},
                {"name":"sample_reconciliation_16_of_16","status":"PASS"},{"name":"raw_count_column_not_cpm","status":"PASS"},
                {"name":"deseq2_factorial_model","status":"PASS"},{"name":"bh_fdr_outputs","status":"PASS"},
                {"name":"private_drive_publication","status":"PASS"}
            ]},
            "provenance":{"manifest_sha256":manifest_sha,"executor":"GITHUB_ACTIONS_PUBLIC","analysis_version":"deseq2-factorial/1.0"},
            "next_state":"WAITING_FOR_RESEARCH_DIRECTOR"
        }
        rp=root/"result.json"; _write_json(rp,result); client.upload(str(rp),results["id"],rp.name)
        lp=root/"execution.log"; lp.write_text(
            f"job_id={job_id}\nanalysis=FACTORIAL_RNASEQ_DESEQ2\nsamples=16\ngenes={matrix_meta['gene_count']}\nprivate_drive_publication=PASS\n",
            encoding="utf-8"
        ); _upload_once(client, logs["id"], lp, lp.name)
        return result
    finally:
        if ctx:
            ctx.cleanup()


def main() -> int:
    parser=argparse.ArgumentParser()
    parser.add_argument("--job-id",required=True)
    parser.add_argument("--public-status",default="out/factorial-rnaseq-status.json")
    args=parser.parse_args()
    result=run_job(args.job_id)
    write_public_status(args.job_id,args.public_status,"ANALYSIS")
    print(f"Factorial RNA-seq analysis PASS for job_id={args.job_id}; next_state={result['next_state']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
