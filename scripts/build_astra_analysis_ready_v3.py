"""Build and publish the outcome-blind Astra analysis-ready V3 repair snapshot.

Repairs only defects audited in V2:
1. Re-fetch the 4,465 acquired SEC filings whose stored normalized text is only
   a <=5,000-character preview and truthfully rebuild the full-text corpus.
2. Recompute 120/200/250 market betas from exact paired pre-event return rows.
3. Enforce leverage = (short-term debt + long-term debt) / total assets only
   when both debt components are period-matched to total assets.

The V2 snapshot is immutable. V3 is assembled in a run-specific BUILDING folder,
verified, then renamed to its final immutable snapshot name. Event-window returns,
abnormal returns and CARs are never read or calculated.
"""
from __future__ import annotations

import concurrent.futures
import csv
import gzip
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.request
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from coscientist.astra_v3 import (
    CUTOFF_DATE,
    merge_strict_beta_metrics,
    needs_full_text_repair,
    repair_strict_leverage,
    truthful_full_text_status,
)
from coscientist.drive import API, DriveClient, DriveCredentials, FOLDER_MIME
from coscientist.eventstudy_price_metrics import build_strict_pre_event_price_metrics
from coscientist.sec_corpus import SEC_HEADERS, TokenBucketLimiter, extract_filing_sections, html_to_normalized_text

PAPER_ID = "MS-ASTRA-REVALUE-2026"
PARENT_SNAPSHOT = "analysis_ready_data_v2_20260907"
SNAPSHOT = "analysis_ready_data_v3_20260907"
STUDY_FOLDER_ID = "1yx34Mj-k7SsGSuPli8UVuR_E639eeLcb"
PARENT_FOLDER_ID = "10mUaN8Y0SQWO7kdwVyJp105enhXdPZsx"
EXPECTED_CANDIDATES = 5547
EXPECTED_ELIGIBLE_FILINGS = 5234
EXPECTED_PREVIEW_REPAIRS = 4465
WORK = Path("state/astra_v3_work")
INPUT = WORK / "parent_inputs"
SEC_DIR = WORK / "sec_json"
OUTPUT = WORK / "outputs"

REPLACE_NAMES = {
    "firm_pre_event_price_metrics.csv",
    "firm_pre_event_controls_complete.csv",
    "firm_pre_event_controls_complete.parquet",
    "firm_control_provenance.csv",
    "analysis_data_availability_matrix.csv",
    "sec_pre_event_filings_full.zip",
    "sec_corpus_manifest_full.csv",
    "data_completion_summary.json",
    "acquisition_manifest.json",
    "SHA256SUMS.txt",
    "acquisition_receipt.md",
    "drive_roundtrip_verification.json",
}
META_EXCLUDED_FROM_MANIFEST = {
    "acquisition_manifest.json", "SHA256SUMS.txt", "acquisition_receipt.md", "drive_roundtrip_verification.json"
}


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        while chunk := fh.read(8 * 1024 * 1024):
            h.update(chunk)
    return h.hexdigest()


def md5_file(path: Path) -> str:
    h = hashlib.md5()
    with path.open("rb") as fh:
        while chunk := fh.read(8 * 1024 * 1024):
            h.update(chunk)
    return h.hexdigest()


def git_sha() -> str:
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    except Exception:
        return "UNKNOWN"


def drive() -> DriveClient:
    return DriveClient(DriveCredentials.from_env())


def _request_json(client: DriveClient, req: urllib.request.Request, timeout: int = 180) -> dict[str, Any]:
    with client.opener(req, timeout=timeout) as resp:
        body = resp.read()
    return json.loads(body.decode()) if body else {}


def copy_drive_file(client: DriveClient, file_id: str, dest_folder_id: str, name: str) -> str:
    data = json.dumps({"name": name, "parents": [dest_folder_id]}).encode()
    req = urllib.request.Request(
        f"{API}/files/{file_id}/copy?fields=id,name,size,md5Checksum",
        data=data,
        method="POST",
        headers={"Authorization": f"Bearer {client.token()}", "Content-Type": "application/json; charset=UTF-8"},
    )
    return str(_request_json(client, req).get("id", ""))


def rename_drive_file(client: DriveClient, file_id: str, new_name: str) -> None:
    data = json.dumps({"name": new_name}).encode()
    req = urllib.request.Request(
        f"{API}/files/{file_id}?fields=id,name",
        data=data,
        method="PATCH",
        headers={"Authorization": f"Bearer {client.token()}", "Content-Type": "application/json; charset=UTF-8"},
    )
    payload = _request_json(client, req)
    if payload.get("name") != new_name:
        raise RuntimeError(f"Drive rename failed for {file_id}: {payload}")


def download_parent_inputs(client: DriveClient, parent_map: dict[str, dict[str, Any]]) -> None:
    INPUT.mkdir(parents=True, exist_ok=True)
    required = [
        "candidate_firm_universe.csv",
        "us_equity_daily_prices_20250101_20260904.parquet",
        "firm_pre_event_controls_complete.csv",
        "firm_control_provenance.csv",
        "sec_pre_event_filings_full.zip",
        "sec_corpus_manifest_full.csv",
        "acquisition_manifest.json",
    ]
    for name in required:
        if name not in parent_map:
            raise RuntimeError(f"parent V2 missing required input {name}")
        dest = INPUT / name
        print(f"Downloading parent input {name}...", flush=True)
        client.download(parent_map[name]["id"], str(dest))


def load_parent_manifest() -> dict[str, Any]:
    return json.loads((INPUT / "acquisition_manifest.json").read_text(encoding="utf-8"))


def unpack_sec_parent() -> list[Path]:
    if SEC_DIR.exists():
        shutil.rmtree(SEC_DIR)
    SEC_DIR.mkdir(parents=True)
    with zipfile.ZipFile(INPUT / "sec_pre_event_filings_full.zip") as zf:
        zf.extractall(SEC_DIR)
    files = sorted(SEC_DIR.glob("**/*.json"))
    if len(files) != EXPECTED_ELIGIBLE_FILINGS:
        raise RuntimeError(f"V2 SEC ZIP expected {EXPECTED_ELIGIBLE_FILINGS} JSON filings, found {len(files)}")
    return files


def _fetch_sec(url: str, limiter: TokenBucketLimiter) -> bytes:
    last: Exception | None = None
    for attempt in range(1, 7):
        limiter.wait()
        try:
            req = urllib.request.Request(url, headers=SEC_HEADERS)
            with urllib.request.urlopen(req, timeout=60) as resp:
                raw = resp.read()
                if resp.headers.get("Content-Encoding") == "gzip" or raw[:2] == b"\x1f\x8b":
                    raw = gzip.decompress(raw)
            if len(raw) < 500:
                raise RuntimeError(f"SEC payload too short: {len(raw)} bytes")
            return raw
        except Exception as exc:
            last = exc
            time.sleep(min(15.0, 1.75 * attempt))
    raise RuntimeError(f"SEC fetch failed after retries: {last}")


def repair_sec_corpus(files: list[Path], workers: int = 8) -> dict[str, Any]:
    pending: list[tuple[Path, dict[str, Any]]] = []
    for path in files:
        d = json.loads(path.read_text(encoding="utf-8"))
        if str(d.get("filing_date", "")) > CUTOFF_DATE:
            raise RuntimeError(f"post-cutoff SEC filing in parent snapshot: {d.get('ticker')} {d.get('filing_date')}")
        if needs_full_text_repair(d):
            pending.append((path, d))
    if len(pending) != EXPECTED_PREVIEW_REPAIRS:
        raise RuntimeError(f"expected {EXPECTED_PREVIEW_REPAIRS} preview-sized SEC repairs, found {len(pending)}")

    limiter = TokenBucketLimiter(max_per_second=5.5)
    started = time.time()
    failures: list[dict[str, str]] = []
    repaired = 0

    def worker(item: tuple[Path, dict[str, Any]]) -> dict[str, Any]:
        path, d = item
        ticker = str(d.get("ticker", ""))
        try:
            raw = _fetch_sec(str(d.get("source_url", "")), limiter)
            norm, status, reason = html_to_normalized_text(raw)
            sections = extract_filing_sections(norm, str(d.get("form", "")))
            full_flag, truthful_status = truthful_full_text_status(norm, status)
            d.update({
                "source_acquisition_status": "SUCCESS",
                "source_provenance": "SEC_EDGAR_HTTPS_V3_REFETCH",
                "bytes_raw": len(raw),
                "sha256_raw": hashlib.sha256(raw).hexdigest(),
                "text_extraction_status": truthful_status,
                "section_segmentation_status": sections["segmentation_status"],
                "full_text_available": full_flag,
                "normalized_text_length": len(norm),
                "normalized_text": norm,
                "visible_text_preview": norm[:5000],
                "item1_available": sections["item1_available"],
                "item1a_available": sections["item1a_available"],
                "item7_available": sections["item7_available"],
                "foreign_item4_available": sections["foreign_item4_available"],
                "foreign_item3d_available": sections["foreign_item3d_available"],
                "foreign_item5_available": sections["foreign_item5_available"],
                "item1_text": sections["item1"],
                "item1a_text": sections["item1a"],
                "item7_text": sections["item7"],
                "v3_full_text_repair": True,
                "v3_repaired_at_utc": datetime.now(timezone.utc).isoformat(),
                "missing_reason": "NONE" if full_flag == "Y" and sections["segmentation_status"] == "FULL_SECTIONS_SEGMENTED" else (
                    sections["missing_reason"] if full_flag == "Y" else reason
                ),
            })
            path.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
            if full_flag != "Y":
                raise RuntimeError(f"refetched filing still not full text ({len(norm)} chars; {truthful_status})")
            return {"ticker": ticker, "ok": True, "chars": len(norm)}
        except Exception as exc:
            return {"ticker": ticker, "ok": False, "error": str(exc)}

    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(worker, item) for item in pending]
        for n, fut in enumerate(concurrent.futures.as_completed(futures), 1):
            res = fut.result()
            if res["ok"]:
                repaired += 1
            else:
                failures.append({"ticker": res["ticker"], "error": res["error"]})
            if n % 100 == 0 or n == len(futures):
                elapsed = max(time.time() - started, 0.001)
                print(f"SEC V3 repair {n}/{len(futures)}; repaired={repaired}; failures={len(failures)}; rate={n/elapsed:.2f}/s", flush=True)

    if failures:
        (OUTPUT / "sec_v3_failures.json").write_text(json.dumps(failures, indent=2), encoding="utf-8")
        raise RuntimeError(f"SEC V3 repair has {len(failures)} failures; refusing to publish incomplete final V3")
    return {"preview_sized_parent_filings": len(pending), "successfully_refetched": repaired, "failures": 0}


def rebuild_sec_manifest(files: list[Path]) -> tuple[pd.DataFrame, dict[str, Any]]:
    parent = pd.read_csv(INPUT / "sec_corpus_manifest_full.csv", dtype={"ticker": str, "cik": str})
    by_ticker = {str(r["ticker"]): r for r in parent.to_dict("records")}
    rows: list[dict[str, Any]] = []
    for path in files:
        d = json.loads(path.read_text(encoding="utf-8"))
        ticker = str(d.get("ticker", ""))
        rows.append({
            "ticker": ticker, "cik": str(d.get("cik", "")), "form": d.get("form", ""),
            "filing_date": d.get("filing_date", ""), "period_of_report": d.get("period_of_report", ""),
            "accession_number": d.get("accession_number", ""), "source_url": d.get("source_url", ""),
            "source_provenance": d.get("source_provenance", "SEC_EDGAR_HTTPS"),
            "source_acquisition_status": d.get("source_acquisition_status", ""),
            "text_extraction_status": d.get("text_extraction_status", ""),
            "section_segmentation_status": d.get("section_segmentation_status", ""),
            "full_text_available": d.get("full_text_available", "N"),
            "normalized_text_length": int(d.get("normalized_text_length") or 0),
            "item1_available": "Y" if d.get("item1_available") else "N",
            "item1a_available": "Y" if d.get("item1a_available") else "N",
            "item7_available": "Y" if d.get("item7_available") else "N",
            "foreign_item4_available": "Y" if d.get("foreign_item4_available") else "N",
            "foreign_item3d_risk_available": "Y" if d.get("foreign_item3d_available") else "N",
            "foreign_item3d_available": "Y" if d.get("foreign_item3d_available") else "N",
            "foreign_item5_available": "Y" if d.get("foreign_item5_available") else "N",
            "raw_bytes": int(d.get("bytes_raw") or 0), "raw_sha256": d.get("sha256_raw", ""),
            "json_bytes": path.stat().st_size, "json_sha256": sha256_file(path),
            "missing_reason": d.get("missing_reason", ""),
        })
        by_ticker.pop(ticker, None)
    # Preserve the 313 no-eligible-filing rows from the parent manifest.
    for ticker, r in by_ticker.items():
        rows.append({k: r.get(k, "") for k in parent.columns})
    out = pd.DataFrame(rows).sort_values("ticker").reset_index(drop=True)
    if len(out) != EXPECTED_CANDIDATES:
        raise RuntimeError(f"SEC manifest expected {EXPECTED_CANDIDATES} rows, found {len(out)}")
    acquired = out["source_acquisition_status"].astype(str).eq("SUCCESS")
    false_full = acquired & out["full_text_available"].astype(str).eq("Y") & (pd.to_numeric(out["normalized_text_length"], errors="coerce") <= 5000)
    if false_full.any():
        raise RuntimeError(f"truthfulness gate failed: {int(false_full.sum())} <=5000-char filings still labelled full text")
    usable = acquired & out["full_text_available"].astype(str).eq("Y") & (pd.to_numeric(out["normalized_text_length"], errors="coerce") > 5000)
    if int(usable.sum()) != EXPECTED_ELIGIBLE_FILINGS:
        raise RuntimeError(f"full-text completion gate expected {EXPECTED_ELIGIBLE_FILINGS}, got {int(usable.sum())}")
    qa = {
        "target_candidate_rows": len(out),
        "eligible_pre_event_filings": int(acquired.sum()),
        "truthful_full_text_filings": int(usable.sum()),
        "preview_sized_full_text_labels": int(false_full.sum()),
        "no_eligible_filing": int((~acquired).sum()),
        "minimum_full_text_chars": int(pd.to_numeric(out.loc[usable, "normalized_text_length"]).min()),
        "outcome_inspected": "NO",
    }
    return out, qa


def zip_sec(files: list[Path], dest: Path) -> None:
    print(f"Packaging {len(files)} repaired SEC JSON records...", flush=True)
    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED, compresslevel=6, allowZip64=True) as zf:
        for path in files:
            zf.write(path, arcname=f"sec_pre_event_filings_full/{path.name}")


def canonicalize_prices(df: pd.DataFrame) -> pd.DataFrame:
    aliases = {str(c).strip().lower().replace("_", " "): c for c in df.columns}
    rename: dict[Any, str] = {}
    for target, candidates in {
        "Date": ("date", "datetime"),
        "Ticker": ("ticker", "symbol"),
        "Close": ("close",),
        "Adj Close": ("adj close", "adjusted close", "adjclose"),
    }.items():
        for candidate in candidates:
            if candidate in aliases:
                rename[aliases[candidate]] = target
                break
    out = df.rename(columns=rename)
    for required in ("Date", "Ticker", "Close"):
        if required not in out.columns:
            raise RuntimeError(f"price parquet lacks {required}; columns={list(df.columns)}")
    return out


def repair_prices_and_controls() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, dict[str, Any], dict[str, Any]]:
    universe = pd.read_csv(INPUT / "candidate_firm_universe.csv", dtype={"ticker": str, "cik": str})
    candidates = universe.loc[universe["included"].astype(str).eq("Y"), "ticker"].astype(str).tolist()
    if len(candidates) != EXPECTED_CANDIDATES:
        raise RuntimeError(f"expected {EXPECTED_CANDIDATES} operating candidates, found {len(candidates)}")
    prices = canonicalize_prices(pd.read_parquet(INPUT / "us_equity_daily_prices_20250101_20260904.parquet"))
    metrics = build_strict_pre_event_price_metrics(prices, candidates, cutoff=CUTOFF_DATE, benchmark="SPY")
    for w in (120, 200, 250):
        if ((metrics[f"market_beta_{w}"].notna()) & (metrics[f"beta_{w}_n"] != w)).any():
            raise RuntimeError(f"strict beta {w} exact-N gate failed")
    price_qa = {
        "paper_id": PAPER_ID,
        "cutoff_date": CUTOFF_DATE,
        "candidate_firms": len(metrics),
        "benchmark": "SPY",
        "paired_return_obs_gte_120": int((metrics["valid_paired_pre_event_return_obs"] >= 120).sum()),
        "paired_return_obs_gte_200": int((metrics["valid_paired_pre_event_return_obs"] >= 200).sum()),
        "paired_return_obs_gte_250": int((metrics["valid_paired_pre_event_return_obs"] >= 250).sum()),
        "beta_120_exact_n_count": int(metrics["market_beta_120"].notna().sum()),
        "beta_200_exact_n_count": int(metrics["market_beta_200"].notna().sum()),
        "beta_250_exact_n_count": int(metrics["market_beta_250"].notna().sum()),
        "outcome_inspected": "NO",
    }

    controls = pd.read_csv(INPUT / "firm_pre_event_controls_complete.csv", dtype={"ticker": str, "cik": str})
    provenance = pd.read_csv(INPUT / "firm_control_provenance.csv", dtype={"ticker": str, "cik": str})
    controls, provenance, leverage_qa = repair_strict_leverage(controls, provenance)
    controls = merge_strict_beta_metrics(controls, metrics)
    return metrics, controls, provenance, price_qa, leverage_qa


def build_availability(controls: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for r in controls.to_dict("records"):
        row = {"ticker": r.get("ticker", ""), "cik": r.get("cik", "")}
        for col in (
            "has_120_pre_event_obs", "has_200_pre_event_obs", "has_250_pre_event_obs",
            "has_market_cap", "has_profitability", "has_leverage", "has_intangible_assets",
            "has_rd_proxy", "has_rd_to_revenue", "has_sic", "has_market_beta_120",
            "has_market_beta_200", "has_market_beta_250", "leverage_period_consistent",
            "roa_period_consistent", "intangibles_period_consistent", "rd_period_consistent",
            "cross_accession_fallback",
        ):
            row[col] = bool(r.get(col))
        row["pre_event_form"] = r.get("pre_event_form", "")
        row["valid_paired_pre_event_return_obs"] = int(r.get("valid_paired_pre_event_return_obs") or 0)
        row["analysis_ready_baseline"] = bool(
            r.get("pre_event_form") and r.get("has_120_pre_event_obs") and r.get("has_market_cap") and r.get("has_sic")
        )
        rows.append(row)
    return pd.DataFrame(rows)


def write_outputs(sec_files: list[Path], sec_repair_qa: dict[str, Any], sec_manifest: pd.DataFrame, sec_qa: dict[str, Any],
                  metrics: pd.DataFrame, controls: pd.DataFrame, provenance: pd.DataFrame,
                  price_qa: dict[str, Any], leverage_qa: dict[str, Any]) -> dict[str, str]:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    metrics.to_csv(OUTPUT / "firm_pre_event_price_metrics.csv", index=False)
    controls.to_csv(OUTPUT / "firm_pre_event_controls_complete.csv", index=False)
    controls.to_parquet(OUTPUT / "firm_pre_event_controls_complete.parquet", index=False)
    provenance.to_csv(OUTPUT / "firm_control_provenance.csv", index=False)
    build_availability(controls).to_csv(OUTPUT / "analysis_data_availability_matrix.csv", index=False)
    sec_manifest.to_csv(OUTPUT / "sec_corpus_manifest_full.csv", index=False)
    zip_sec(sec_files, OUTPUT / "sec_pre_event_filings_full.zip")
    (OUTPUT / "price_metrics_v3_qa.json").write_text(json.dumps(price_qa, indent=2), encoding="utf-8")
    audit = {
        "paper_id": PAPER_ID, "parent_snapshot": PARENT_SNAPSHOT, "snapshot": SNAPSHOT,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(), "git_commit_sha": git_sha(),
        "pre_event_cutoff_date": CUTOFF_DATE, "outcome_inspected": "NO",
        "repairs": {"sec_full_text": sec_repair_qa, "sec_truthfulness": sec_qa,
                    "strict_beta_windows": price_qa, "strict_leverage": leverage_qa},
    }
    (OUTPUT / "v3_repair_audit.json").write_text(json.dumps(audit, indent=2), encoding="utf-8")
    summary = {
        "paper_id": PAPER_ID, "snapshot_id": SNAPSHOT, "parent_snapshot_id": PARENT_SNAPSHOT,
        "registry_status": "REPAIR", "paper_stage": "DEVELOPING", "git_commit_sha": git_sha(),
        "generated_at_utc": datetime.now(timezone.utc).isoformat(), "outcome_inspected": "NO",
        "outcome_blind_declaration": "Astra event-window outcomes were not analyzed or summarized during V3 repair.",
        "pre_event_cutoff_date": CUTOFF_DATE,
        "sec_filing_corpus": sec_qa,
        "strict_beta": price_qa,
        "strict_leverage": leverage_qa,
        "numerical_controls": {
            "candidate_firms": len(controls),
            "market_cap_count": int(controls["has_market_cap"].fillna(False).astype(bool).sum()),
            "profitability_count": int(controls["has_profitability"].fillna(False).astype(bool).sum()),
            "strict_leverage_count": int(controls["has_leverage"].fillna(False).astype(bool).sum()),
            "intangibles_count": int(controls["has_intangible_assets"].fillna(False).astype(bool).sum()),
            "rd_proxy_count": int(controls["has_rd_proxy"].fillna(False).astype(bool).sum()),
            "sic_count": int(controls["has_sic"].fillna(False).astype(bool).sum()),
        },
    }
    (OUTPUT / "data_completion_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return {p.name: sha256_file(p) for p in OUTPUT.iterdir() if p.is_file()}


def publish(client: DriveClient, parent_map: dict[str, dict[str, Any]], parent_manifest: dict[str, Any], local_hashes: dict[str, str]) -> str:
    existing_final = [f for f in client.list_folder(STUDY_FOLDER_ID) if f.get("name") == SNAPSHOT and f.get("mimeType") == FOLDER_MIME]
    if existing_final:
        raise RuntimeError(f"immutable final snapshot {SNAPSHOT} already exists: {existing_final[0]['id']}")
    run_id = os.environ.get("GITHUB_RUN_ID", str(int(time.time())))
    building_name = f"{SNAPSHOT}__BUILDING_{run_id}"
    folder_id = client.create_folder(building_name, STUDY_FOLDER_ID)
    print(f"Created Drive build folder {building_name}: {folder_id}", flush=True)

    parent_hashes = {f["name"]: f["sha256"] for f in parent_manifest.get("files", [])}
    # Server-side copy every unchanged scientific input from immutable V2.
    for name, meta in sorted(parent_map.items()):
        if name in REPLACE_NAMES or name in META_EXCLUDED_FROM_MANIFEST:
            continue
        if meta.get("mimeType") == FOLDER_MIME:
            continue
        print(f"Drive-copy unchanged parent file {name}", flush=True)
        copy_drive_file(client, meta["id"], folder_id, name)

    for path in sorted(OUTPUT.iterdir()):
        if path.is_file():
            print(f"Upload repaired file {path.name} ({path.stat().st_size:,} bytes)", flush=True)
            client.upload(str(path), folder_id, path.name)

    # Construct V3 SHA-256 manifest from parent immutable hashes + local repaired hashes.
    remote = {f["name"]: f for f in client.list_folder(folder_id)}
    entries = []
    for name, meta in sorted(remote.items()):
        if name in META_EXCLUDED_FROM_MANIFEST:
            continue
        if name in local_hashes:
            sha = local_hashes[name]
        elif name in parent_hashes:
            sha = parent_hashes[name]
        else:
            raise RuntimeError(f"no authoritative SHA-256 provenance for remote V3 file {name}")
        entries.append({"name": name, "bytes": int(meta.get("size", 0)), "sha256": sha})
    manifest = {
        "paper_id": PAPER_ID, "snapshot_id": SNAPSHOT, "parent_snapshot_id": PARENT_SNAPSHOT,
        "git_commit_sha": git_sha(), "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "outcome_inspected": "NO",
        "outcome_blind_declaration": "Astra event-window outcomes were not analyzed or summarized during V3 repair.",
        "repair_scope": ["SEC_FULL_TEXT_PREVIEW_TRUNCATION", "STRICT_PAIRED_RETURN_BETAS", "STRICT_DEBT_COMPONENT_LEVERAGE"],
        "file_count": len(entries), "total_bytes": sum(e["bytes"] for e in entries), "files": entries,
    }
    manifest_path = OUTPUT / "acquisition_manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    sums_path = OUTPUT / "SHA256SUMS.txt"
    sums_path.write_text("\n".join(f"{e['sha256']}  {e['name']}" for e in entries) + "\n", encoding="utf-8")
    receipt = OUTPUT / "acquisition_receipt.md"
    receipt.write_text(
        "# Management Science CoScientist Data Acquisition Receipt (V3 Repair)\n\n"
        f"- **Paper ID**: `{PAPER_ID}`\n- **Snapshot**: `{SNAPSHOT}`\n- **Parent Snapshot**: `{PARENT_SNAPSHOT}`\n"
        f"- **Git Commit SHA**: `{git_sha()}`\n- **Pre-Event Cutoff**: `{CUTOFF_DATE}`\n- **Outcome Inspected**: **NO**\n"
        "- **Repair Scope**: SEC full-text truncation; exact paired-return beta windows; strict component-defined leverage.\n"
        f"- **Files**: {len(entries)}\n- **Payload**: {manifest['total_bytes']:,} bytes\n\n"
        "V2 remains immutable. V3 was built in a run-specific staging folder and promoted only after repair gates passed.\n",
        encoding="utf-8",
    )
    for path in (manifest_path, sums_path, receipt):
        client.upload(str(path), folder_id, path.name)

    # Verification: SHA-256 round-trip all small repaired files; MD5/size check the large SEC ZIP.
    remote = {f["name"]: f for f in client.list_folder(folder_id)}
    verification: dict[str, Any] = {"paper_id": PAPER_ID, "snapshot": SNAPSHOT, "outcome_inspected": "NO", "checks": []}
    verify_dir = WORK / "verify"
    verify_dir.mkdir(parents=True, exist_ok=True)
    for name in sorted(local_hashes):
        lp = OUTPUT / name
        rm = remote.get(name)
        if not rm:
            raise RuntimeError(f"uploaded repaired file missing remotely: {name}")
        if name == "sec_pre_event_filings_full.zip":
            remote_md5 = str(rm.get("md5Checksum") or "")
            local_md5 = md5_file(lp)
            ok = int(rm.get("size", -1)) == lp.stat().st_size and (not remote_md5 or remote_md5 == local_md5)
            verification["checks"].append({"name": name, "method": "size+md5", "status": "PASS" if ok else "FAIL"})
        else:
            dest = verify_dir / name
            got = client.download(rm["id"], str(dest), local_hashes[name])
            ok = got == local_hashes[name]
            verification["checks"].append({"name": name, "method": "sha256-roundtrip", "status": "PASS" if ok else "FAIL"})
            dest.unlink(missing_ok=True)
        if not ok:
            raise RuntimeError(f"Drive verification failed for {name}")
    verification["status"] = "PASS"
    verification["verified_at_utc"] = datetime.now(timezone.utc).isoformat()
    verify_path = OUTPUT / "drive_roundtrip_verification.json"
    verify_path.write_text(json.dumps(verification, indent=2), encoding="utf-8")
    client.upload(str(verify_path), folder_id, verify_path.name)

    rename_drive_file(client, folder_id, SNAPSHOT)
    final = [f for f in client.list_folder(STUDY_FOLDER_ID) if f.get("name") == SNAPSHOT and f.get("mimeType") == FOLDER_MIME]
    if len(final) != 1 or final[0]["id"] != folder_id:
        raise RuntimeError("final snapshot promotion verification failed")
    print(f"PROMOTED_IMMUTABLE_SNAPSHOT {SNAPSHOT} {folder_id}", flush=True)
    return folder_id


def main() -> int:
    if WORK.exists():
        shutil.rmtree(WORK)
    OUTPUT.mkdir(parents=True)
    client = drive()
    parent_files = client.list_folder(PARENT_FOLDER_ID)
    parent_map = {f["name"]: f for f in parent_files}
    download_parent_inputs(client, parent_map)
    parent_manifest = load_parent_manifest()
    if parent_manifest.get("snapshot_id") != PARENT_SNAPSHOT or parent_manifest.get("paper_id") != PAPER_ID:
        raise RuntimeError("parent manifest identity mismatch")
    if parent_manifest.get("outcome_inspected") != "NO":
        raise RuntimeError("parent snapshot is not outcome-blind")

    sec_files = unpack_sec_parent()
    sec_repair_qa = repair_sec_corpus(sec_files)
    sec_manifest, sec_qa = rebuild_sec_manifest(sec_files)
    metrics, controls, provenance, price_qa, leverage_qa = repair_prices_and_controls()
    local_hashes = write_outputs(sec_files, sec_repair_qa, sec_manifest, sec_qa, metrics, controls, provenance, price_qa, leverage_qa)
    folder_id = publish(client, parent_map, parent_manifest, local_hashes)
    result = {
        "status": "PASS", "paper_id": PAPER_ID, "snapshot": SNAPSHOT, "drive_folder_id": folder_id,
        "git_commit_sha": git_sha(), "outcome_inspected": "NO", "sec_repaired": sec_repair_qa["successfully_refetched"],
        "strict_leverage_available": leverage_qa["strict_leverage_available"],
        "beta_120_count": price_qa["beta_120_exact_n_count"],
        "beta_200_count": price_qa["beta_200_exact_n_count"],
        "beta_250_count": price_qa["beta_250_exact_n_count"],
    }
    print(json.dumps(result, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
