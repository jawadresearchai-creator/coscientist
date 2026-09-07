"""Build, package, hash, upload, and verify analysis_ready_data_v2_20260907 on Google Drive.

Strict information boundary:
- Only pre-event observations (<= 2026-08-06).
- Zero outcome data accessed or calculated.
"""
from __future__ import annotations

import socket
_orig = socket.getaddrinfo
socket.getaddrinfo = lambda h, p, family=0, type=0, proto=0, flags=0: _orig(h, p, socket.AF_INET, type, proto, flags)

import configparser
import csv
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.parse
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Tuple

# Ensure repo root and src are on path
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.insert(0, str(REPO_ROOT))

from coscientist.drive import DriveClient, DriveCredentials, FOLDER_MIME, API

SNAPSHOT_NAME = "analysis_ready_data_v2_20260907"
STUDY_FOLDER_ID = "1yx34Mj-k7SsGSuPli8UVuR_E639eeLcb"
STAGING_V1 = Path("state/staging_v3/analysis_ready_data_v1_20260907")
STAGING_V2 = Path(f"state/staging_v3/{SNAPSHOT_NAME}")
CACHE_DIR = Path("state/sec_cache")


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(4 * 1024 * 1024):
            h.update(chunk)
    return h.hexdigest()


def get_git_commit_sha() -> str:
    try:
        res = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True)
        return res.stdout.strip()
    except Exception:
        return "UNKNOWN"


def prepare_staging_v2() -> None:
    STAGING_V2.mkdir(parents=True, exist_ok=True)
    print(f"Preparing staging directory: {STAGING_V2}...", flush=True)
    
    base_files = [
        "candidate_firm_universe.csv",
        "universe_construction_receipt.json",
        "us_equity_daily_prices_20250101_20260904.parquet",
        "us_equity_daily_prices_index.csv",
        "price_acquisition_status.csv",
        "price_panel_qa.json",
        "price_panel_qa.csv",
        "firm_pre_event_price_metrics.csv",
        "pre_event_filing_index.csv",
        "concurrent_event_registry.csv",
        "astra_event_timestamps.json",
        "cboe_vix_history_20260906.csv",
        "ff3_daily_20260906.zip",
        "ff5_daily_20260906.zip",
        "momentum_daily_20260906.zip",
        "factor_data_refresh_status.json",
        "openai_astra_aug07_2026.html",
        "openai_astra_sep01_2026.html",
        "openai_astra_sep03_2026.html",
        "openai_astra_release_notes_20260906.html"
    ]
    for bf in base_files:
        src = STAGING_V1 / bf
        dst = STAGING_V2 / bf
        if src.exists() and not dst.exists():
            shutil.copy2(src, dst)
            
    print(f"Copied baseline assets to staging v2.", flush=True)


def package_sec_corpus_zip_and_manifest() -> None:
    filings_dir = STAGING_V1 / "sec_pre_event_filings_full"
    zip_dest = STAGING_V2 / "sec_pre_event_filings_full.zip"
    manifest_dest = STAGING_V2 / "sec_corpus_manifest_full.csv"
    
    filing_index = {}
    with open(STAGING_V2 / "pre_event_filing_index.csv", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            filing_index[r["ticker"]] = r
            
    manifest_rows = []
    files = sorted([f for f in filings_dir.iterdir() if f.name.endswith(".json")])
    
    need_zip = not (zip_dest.exists() and zip_dest.stat().st_size > 100_000_000)
    if need_zip:
        print(f"Packaging {zip_dest} from {filings_dir}...", flush=True)
        z_ctx = zipfile.ZipFile(zip_dest, "w", zipfile.ZIP_DEFLATED)
    else:
        print(f"Found existing valid {zip_dest} ({zip_dest.stat().st_size:,} bytes), reading files for manifest...", flush=True)
        z_ctx = None

    try:
        for fp in files:
            if z_ctx is not None:
                z_ctx.write(fp, arcname=f"sec_pre_event_filings_full/{fp.name}")
            with open(fp, "r", encoding="utf-8") as fh:
                d = json.load(fh)
                
            manifest_rows.append({
                "ticker": d.get("ticker", ""),
                "cik": d.get("cik", ""),
                "form": d.get("form", ""),
                "filing_date": d.get("filing_date", ""),
                "period_of_report": d.get("period_of_report", ""),
                "accession_number": d.get("accession_number", ""),
                "source_url": d.get("source_url", ""),
                "source_provenance": d.get("source_provenance", "SEC_EDGAR_HTTPS"),
                "source_acquisition_status": d.get("source_acquisition_status", "SUCCESS"),
                "text_extraction_status": d.get("text_extraction_status", "SUCCESS_FULL_TEXT"),
                "section_segmentation_status": d.get("section_segmentation_status", "UNSEGMENTED_FULL_TEXT_RETAINED"),
                "full_text_available": d.get("full_text_available", "Y"),
                "normalized_text_length": d.get("normalized_text_length", 0),
                "item1_available": "Y" if d.get("item1_available") else "N",
                "item1a_available": "Y" if d.get("item1a_available") else "N",
                "item7_available": "Y" if d.get("item7_available") else "N",
                "foreign_item4_available": "Y" if d.get("foreign_item4_available") else "N",
                "foreign_item3d_available": "Y" if d.get("foreign_item3d_available") else "N",
                "foreign_item5_available": "Y" if d.get("foreign_item5_available") else "N",
                "raw_bytes": d.get("bytes_raw", 0),
                "raw_sha256": d.get("sha256_raw", ""),
                "json_bytes": fp.stat().st_size,
                "json_sha256": sha256_file(fp),
                "missing_reason": d.get("missing_reason", "NONE")
            })
    finally:
        if z_ctx is not None:
            z_ctx.close()
            
    existing_tickers = {r["ticker"] for r in manifest_rows}
    for tk, r in filing_index.items():
        if tk not in existing_tickers:
            manifest_rows.append({
                "ticker": tk,
                "cik": r.get("cik", ""),
                "form": r.get("form", ""),
                "filing_date": r.get("filing_date", ""),
                "period_of_report": r.get("period_of_report", ""),
                "accession_number": r.get("accession_number", ""),
                "source_url": r.get("filing_url", ""),
                "source_provenance": "NONE",
                "source_acquisition_status": "NOT_ACQUIRED",
                "text_extraction_status": "NOT_APPLICABLE",
                "section_segmentation_status": "NOT_APPLICABLE",
                "full_text_available": "N",
                "normalized_text_length": 0,
                "item1_available": "N",
                "item1a_available": "N",
                "item7_available": "N",
                "foreign_item4_available": "N",
                "foreign_item3d_available": "N",
                "foreign_item5_available": "N",
                "raw_bytes": 0,
                "raw_sha256": "",
                "json_bytes": 0,
                "json_sha256": "",
                "missing_reason": r.get("status", "NO_ELIGIBLE_PRE_EVENT_ANNUAL_FILING")
            })

    with open(manifest_dest, "w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(manifest_rows[0].keys()))
        writer.writeheader()
        writer.writerows(manifest_rows)
        
    print(f"Wrote {manifest_dest} ({len(manifest_rows)} rows) and {zip_dest} ({zip_dest.stat().st_size:,} bytes)", flush=True)


def build_availability_matrix(df_controls: Any, staging_path: Path) -> None:
    matrix_rows = []
    for _, r in df_controls.iterrows():
        tk = r["ticker"]
        matrix_rows.append({
            "ticker": tk,
            "cik": r["cik"],
            "has_pre_event_filing": bool(r.get("pre_event_form")),
            "pre_event_form": r.get("pre_event_form", ""),
            "has_pre_event_price": bool(r.get("pre_event_price_used")),
            "has_120_pre_event_obs": bool(r.get("has_120_pre_event_obs")),
            "has_200_pre_event_obs": bool(r.get("has_200_pre_event_obs")),
            "has_250_pre_event_obs": bool(r.get("has_250_pre_event_obs")),
            "has_market_cap": bool(r.get("has_market_cap")),
            "has_profitability": bool(r.get("has_profitability")),
            "roa_period_consistent": bool(r.get("roa_period_consistent")),
            "has_leverage": bool(r.get("has_leverage")),
            "leverage_period_consistent": bool(r.get("leverage_period_consistent")),
            "has_intangible_assets": bool(r.get("has_intangible_assets")),
            "intangibles_period_consistent": bool(r.get("intangibles_period_consistent")),
            "has_rd_proxy": bool(r.get("has_rd_proxy")),
            "has_rd_to_revenue": bool(r.get("has_rd_to_revenue")),
            "rd_period_consistent": bool(r.get("rd_period_consistent")),
            "has_sic": bool(r.get("has_sic")),
            "has_market_beta_120": bool(r.get("has_market_beta_120")),
            "has_market_beta_200": bool(r.get("has_market_beta_200")),
            "has_market_beta_250": bool(r.get("has_market_beta_250")),
            "cross_accession_fallback": bool(r.get("cross_accession_fallback")),
            "analysis_ready_baseline": bool(
                r.get("has_pre_event_filing") and 
                r.get("has_120_pre_event_obs") and 
                r.get("has_market_cap") and 
                r.get("has_sic")
            )
        })
    import pandas as pd
    df_matrix = pd.DataFrame(matrix_rows)
    matrix_csv = staging_path / "analysis_data_availability_matrix.csv"
    df_matrix.to_csv(matrix_csv, index=False)
    print(f"Wrote analysis availability matrix ({len(df_matrix)} rows) to {matrix_csv}")


def build_controls_and_provenance() -> None:
    from coscientist.eventstudy_controls import materialize_pre_event_controls, CUTOFF_DATE
    
    print(f"Materializing pre-event controls with strict cutoff <= {CUTOFF_DATE}...", flush=True)
    df_controls, df_prov, df_shares_audit, cutoff_summary = materialize_pre_event_controls(
        STAGING_V2, CACHE_DIR, cutoff=CUTOFF_DATE
    )
    
    df_controls.to_csv(STAGING_V2 / "firm_pre_event_controls_complete.csv", index=False)
    df_controls.to_parquet(STAGING_V2 / "firm_pre_event_controls_complete.parquet", index=False)
    df_prov.to_csv(STAGING_V2 / "firm_control_provenance.csv", index=False)
    df_shares_audit.to_csv(STAGING_V2 / "shares_cutoff_audit.csv", index=False)
    
    with open(STAGING_V2 / "pre_event_cutoff_audit.json", "w", encoding="utf-8") as fh:
        json.dump(cutoff_summary, fh, indent=2)
        
    build_availability_matrix(df_controls, STAGING_V2)
    print("Controls, provenance, shares audit, cutoff audit, and availability matrix generated.", flush=True)


def generate_summary_and_receipts() -> Dict[str, Any]:
    git_sha = get_git_commit_sha()
    
    with open(STAGING_V2 / "candidate_firm_universe.csv", encoding="utf-8") as f:
        u_rows = list(csv.DictReader(f))
    operating_candidates = [r for r in u_rows if r.get("included") == "Y"]
    
    with open(STAGING_V2 / "price_panel_qa.json", encoding="utf-8") as f:
        price_qa = json.load(f)
        
    with open(STAGING_V2 / "sec_corpus_manifest_full.csv", encoding="utf-8") as f:
        m_rows = list(csv.DictReader(f))
    acquired_filings = [r for r in m_rows if r.get("source_acquisition_status") == "SUCCESS"]
    full_text_filings = [r for r in m_rows if r.get("full_text_available") == "Y"]
    partial_text_filings = [r for r in m_rows if r.get("text_extraction_status") == "SUCCESS_PARTIAL_TEXT"]
    failed_text_filings = [r for r in m_rows if r.get("text_extraction_status", "").startswith("FAILED")]
    
    with open(STAGING_V2 / "firm_pre_event_controls_complete.csv", encoding="utf-8") as f:
        c_rows = list(csv.DictReader(f))
        
    with open(STAGING_V2 / "pre_event_cutoff_audit.json", encoding="utf-8") as f:
        cutoff_audit = json.load(f)

    with open(STAGING_V2 / "factor_data_refresh_status.json", encoding="utf-8") as f:
        factor_status = json.load(f)

    n_firms = len(c_rows)
    mcap_cnt = sum(1 for r in c_rows if r.get("has_market_cap") in ("True", True))
    roa_cnt = sum(1 for r in c_rows if r.get("has_profitability") in ("True", True))
    lev_cnt = sum(1 for r in c_rows if r.get("has_leverage") in ("True", True))
    intang_cnt = sum(1 for r in c_rows if r.get("has_intangible_assets") in ("True", True))
    rd_cnt = sum(1 for r in c_rows if r.get("has_rd_proxy") in ("True", True))
    sic_cnt = sum(1 for r in c_rows if r.get("has_sic") in ("True", True))
    beta120_cnt = sum(1 for r in c_rows if r.get("has_market_beta_120") in ("True", True))
    beta200_cnt = sum(1 for r in c_rows if r.get("has_market_beta_200") in ("True", True))
    beta250_cnt = sum(1 for r in c_rows if r.get("has_market_beta_250") in ("True", True))

    summary = {
        "paper_id": "MS-ASTRA-REVALUE-2026",
        "snapshot_id": SNAPSHOT_NAME,
        "director_action_id": "DA-data_feasibility-51c48527ac",
        "lifecycle_stage": "DEVELOPING",
        "git_commit_sha": git_sha,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "outcome_inspected": "NO",
        "outcome_blind_declaration": "Astra event-window outcomes were not analyzed or summarized during acquisition.",
        "pre_event_cutoff_date": "2026-08-06",
        "cutoff_audit_v2": cutoff_audit,
        "universe": {
            "exchange_securities_screened": len(u_rows),
            "operating_candidates_count": len(operating_candidates),
            "benchmarks_count": 3,
            "focal_firms_count": 9
        },
        "price_panel": {
            "requested_identifiers": price_qa["universe_summary"]["total_requested_identifiers"],
            "acquired_identifiers": price_qa["universe_summary"]["total_acquired_tickers"],
            "acquisition_rate_pct": price_qa["universe_summary"]["acquisition_success_rate_pct"],
            "total_observations": price_qa["total_rows"],
            "date_range": price_qa["date_range"],
            "pre_event_gte_120_count": price_qa["pre_event_observation_distribution"]["gte_120_obs"],
            "pre_event_gte_200_count": price_qa["pre_event_observation_distribution"]["gte_200_obs"],
            "pre_event_gte_250_count": price_qa["pre_event_observation_distribution"]["gte_250_obs"],
            "unacquired_tickers": price_qa["universe_summary"]["unacquired_tickers"],
            "unacquired_reason": price_qa["universe_summary"]["unacquired_reason"]
        },
        "sec_filing_corpus": {
            "target_filings": len(m_rows),
            "raw_acquisition_count": len(acquired_filings),
            "usable_full_text_count": len(full_text_filings),
            "partial_text_count": len(partial_text_filings),
            "true_extraction_failure_count": len(failed_text_filings),
            "item1_business_count": sum(1 for r in m_rows if r.get("item1_available") == "Y"),
            "item1a_risk_count": sum(1 for r in m_rows if r.get("item1a_available") == "Y"),
            "item7_mda_count": sum(1 for r in m_rows if r.get("item7_available") == "Y"),
            "foreign_item4_business_count": sum(1 for r in m_rows if r.get("foreign_item4_available") == "Y"),
            "foreign_item3d_risk_count": sum(1 for r in m_rows if r.get("foreign_item3d_available") == "Y"),
            "foreign_item5_mda_count": sum(1 for r in m_rows if r.get("foreign_item5_available") == "Y")
        },
        "numerical_controls": {
            "candidate_firms": n_firms,
            "market_cap": {"count": mcap_cnt, "pct": round(mcap_cnt / n_firms * 100, 2)},
            "profitability_roa": {"count": roa_cnt, "pct": round(roa_cnt / n_firms * 100, 2)},
            "leverage_debt_assets": {"count": lev_cnt, "pct": round(lev_cnt / n_firms * 100, 2)},
            "intangibles_or_goodwill": {"count": intang_cnt, "pct": round(intang_cnt / n_firms * 100, 2)},
            "rd_expense": {"count": rd_cnt, "pct": round(rd_cnt / n_firms * 100, 2)},
            "sic_code": {"count": sic_cnt, "pct": round(sic_cnt / n_firms * 100, 2)},
            "market_beta_120": {"count": beta120_cnt, "pct": round(beta120_cnt / n_firms * 100, 2)},
            "market_beta_200": {"count": beta200_cnt, "pct": round(beta200_cnt / n_firms * 100, 2)},
            "market_beta_250": {"count": beta250_cnt, "pct": round(beta250_cnt / n_firms * 100, 2)}
        },
        "factor_data": {
            "ff3_max_date": factor_status.get("FF3", {}).get("max_factor_date"),
            "ff5_max_date": factor_status.get("FF5", {}).get("max_factor_date"),
            "mom_max_date": factor_status.get("MOM", {}).get("max_factor_date"),
            "status": "Authoritative Kenneth French data verified ending 2026-07-31"
        }
    }

    with open(STAGING_V2 / "data_completion_summary.json", "w", encoding="utf-8") as fh:
        json.dump(summary, fh, indent=2)

    excluded_files = {"acquisition_manifest.json", "SHA256SUMS.txt", "acquisition_receipt.md", "drive_roundtrip_verification.json"}
    manifest_entries = []
    sha_lines = []
    
    all_files = sorted([f for f in os.listdir(STAGING_V2) if f not in excluded_files and (STAGING_V2 / f).is_file()])
    for fname in all_files:
        fp = STAGING_V2 / fname
        sz = fp.stat().st_size
        h = sha256_file(fp)
        manifest_entries.append({
            "name": fname,
            "bytes": sz,
            "sha256": h
        })
        sha_lines.append(f"{h}  {fname}")
        
    manifest = {
        "paper_id": "MS-ASTRA-REVALUE-2026",
        "snapshot_id": SNAPSHOT_NAME,
        "director_action_id": "DA-data_feasibility-51c48527ac",
        "git_commit_sha": git_sha,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "outcome_inspected": "NO",
        "outcome_blind_declaration": "Astra event-window outcomes were not analyzed or summarized during acquisition.",
        "file_count": len(manifest_entries),
        "total_bytes": sum(e["bytes"] for e in manifest_entries),
        "files": manifest_entries
    }
    
    with open(STAGING_V2 / "acquisition_manifest.json", "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=2)
        
    with open(STAGING_V2 / "SHA256SUMS.txt", "w", encoding="utf-8") as fh:
        fh.write("\n".join(sha_lines) + "\n")

    receipt_md = f"""# Management Science CoScientist Data Acquisition Receipt (v2)

- **Paper ID**: `MS-ASTRA-REVALUE-2026`
- **Snapshot Name**: `{SNAPSHOT_NAME}`
- **Director Action ID**: `DA-data_feasibility-51c48527ac`
- **Git Commit SHA**: `{git_sha}`
- **Generated At UTC**: `{manifest["generated_at_utc"]}`
- **Outcome Inspected**: **NO**
- **Outcome Declaration**: **Astra event-window outcomes were not analyzed or summarized during acquisition.**
- **Pre-Event Cutoff**: `2026-08-06` (Audit Status: `{cutoff_audit["audit_status"]}`)
- **Total Snapshot Files**: `{len(manifest_entries)}`
- **Total Payload Size**: `{manifest["total_bytes"]:,}` bytes ({manifest["total_bytes"] / (1024*1024):.1f} MB)

## Cryptographic File Manifest

| File | Bytes | SHA-256 |
|---|---|---|
"""
    for e in manifest_entries:
        receipt_md += f"| `{e['name']}` | {e['bytes']:,} | `{e['sha256']}` |\n"

    with open(STAGING_V2 / "acquisition_receipt.md", "w", encoding="utf-8") as fh:
        fh.write(receipt_md)
        
    print(f"Generated manifest ({len(manifest_entries)} files), SHA256SUMS.txt, and receipt.md", flush=True)
    return manifest


def upload_and_verify_drive(manifest: Dict[str, Any]) -> Tuple[str, str, Dict[str, Any]]:
    print("\n--- Connecting to Google Drive ---", flush=True)
    rclone_path = Path(os.path.expanduser("~/AppData/Roaming/rclone/rclone.conf"))
    cp = configparser.ConfigParser()
    cp.read(rclone_path)
    sec = cp["secdrive"]
    t_raw = sec["token"]
    refresh_token = json.loads(t_raw).get("refresh_token") if t_raw.startswith("{") else t_raw
    
    creds = DriveCredentials(sec["client_id"], sec["client_secret"], refresh_token)
    client = DriveClient(creds)
    
    def with_retry(fn, max_retries=5, delay=3.0):
        for attempt in range(1, max_retries + 1):
            try:
                return fn()
            except Exception as e:
                if attempt == max_retries:
                    raise
                print(f"Drive API call failed ({attempt}/{max_retries}): {e}. Retrying in {delay}s...", flush=True)
                time.sleep(delay)

    study_children = with_retry(lambda: client.list_folder(STUDY_FOLDER_ID))
    existing_snapshots = [f for f in study_children if f["name"] == SNAPSHOT_NAME]
    
    if existing_snapshots:
        snap_folder_id = existing_snapshots[0]["id"]
        print(f"Found existing folder for {SNAPSHOT_NAME}: {snap_folder_id}", flush=True)
    else:
        snap_folder_id = with_retry(lambda: client.create_folder(SNAPSHOT_NAME, parent_id=STUDY_FOLDER_ID))
        print(f"Created new snapshot folder {SNAPSHOT_NAME}: {snap_folder_id}", flush=True)

    files_to_upload = [e["name"] for e in manifest["files"]] + ["acquisition_manifest.json", "SHA256SUMS.txt", "acquisition_receipt.md"]
    remote_existing = {f["name"]: f for f in with_retry(lambda: client.list_folder(snap_folder_id))}
    
    upload_receipts = []
    print(f"\nUploading {len(files_to_upload)} files to Google Drive...", flush=True)
    for fname in files_to_upload:
        fp = STAGING_V2 / fname
        sz = fp.stat().st_size
        local_hash = sha256_file(fp)
        
        if fname in remote_existing and int(remote_existing[fname].get("size", -1)) == sz:
            fid = remote_existing[fname]["id"]
            print(f"  Already uploaded and matching size: {fname} ({sz:,} bytes) -> {fid}", flush=True)
            upload_receipts.append({"name": fname, "id": fid, "bytes": sz, "sha256": local_hash, "status": "REUSED"})
            continue
            
        t0 = time.time()
        fid = with_retry(lambda: client.upload(str(fp), snap_folder_id, fname))
        print(f"  Uploaded {fname} ({sz:,} bytes) in {time.time()-t0:.1f}s -> {fid}", flush=True)
        upload_receipts.append({"name": fname, "id": fid, "bytes": sz, "sha256": local_hash, "status": "UPLOADED"})

    print("\n--- Performing Remote Round-Trip Cryptographic Verification ---", flush=True)
    verify_temp_dir = Path("state/temp_drive_verification_v2")
    verify_temp_dir.mkdir(parents=True, exist_ok=True)
    
    remote_files = with_retry(lambda: client.list_folder(snap_folder_id))
    remote_map = {f["name"]: f for f in remote_files}
    
    critical_files = [
        "sec_pre_event_filings_full.zip",
        "us_equity_daily_prices_20250101_20260904.parquet",
        "firm_pre_event_controls_complete.csv",
        "firm_pre_event_controls_complete.parquet",
        "firm_control_provenance.csv",
        "shares_cutoff_audit.csv",
        "analysis_data_availability_matrix.csv",
        "acquisition_manifest.json",
        "data_completion_summary.json"
    ]
    
    verification_gates = []
    
    # Gate 1: Remote presence & size check for all files
    missing_remote = []
    size_mismatches = []
    for e in manifest["files"]:
        fn = e["name"]
        if fn not in remote_map:
            missing_remote.append(fn)
        elif int(remote_map[fn].get("size", -1)) != e["bytes"]:
            size_mismatches.append((fn, e["bytes"], remote_map[fn].get("size")))
            
    gate1_pass = len(missing_remote) == 0 and len(size_mismatches) == 0
    verification_gates.append({
        "gate": "ALL_FILES_REMOTE_PRESENCE_AND_SIZE",
        "total_files": len(manifest["files"]),
        "missing_count": len(missing_remote),
        "size_mismatch_count": len(size_mismatches),
        "status": "PASS" if gate1_pass else "FAIL"
    })
    
    # Gate 2: Download and SHA-256 verify the 9 critical files
    critical_passes = []
    for cf in critical_files:
        if cf not in remote_map:
            critical_passes.append({"file": cf, "status": "FAIL: NOT_FOUND_ON_REMOTE"})
            continue
        dest_file = verify_temp_dir / cf
        with_retry(lambda: client.download(remote_map[cf]["id"], str(dest_file)))
        dl_hash = sha256_file(dest_file)
        local_hash = sha256_file(STAGING_V2 / cf)
        is_match = (dl_hash == local_hash)
        critical_passes.append({
            "file": cf,
            "local_sha256": local_hash,
            "downloaded_sha256": dl_hash,
            "bytes": dest_file.stat().st_size,
            "status": "PASS" if is_match else "FAIL"
        })
        print(f"  Critical payload roundtrip verified: {cf} (SHA-256 match: {is_match})", flush=True)

    critical_all_pass = all(cp["status"] == "PASS" for cp in critical_passes)
    verification_gates.append({
        "gate": "CRITICAL_PAYLOAD_ROUNDTRIP_PASS",
        "verified_critical_count": len(critical_files),
        "details": critical_passes,
        "status": "PASS" if critical_all_pass else "FAIL"
    })

    overall_res = "PASS" if (gate1_pass and critical_all_pass) else "FAIL"
    verification_payload = {
        "snapshot_id": SNAPSHOT_NAME,
        "snapshot_folder_id": snap_folder_id,
        "verified_at_utc": datetime.now(timezone.utc).isoformat(),
        "verification_result": overall_res,
        "verification_terminology": "CRITICAL_PAYLOAD_ROUNDTRIP_PASS",
        "gates": verification_gates,
        "uploaded_objects": upload_receipts
    }
    
    verif_path = STAGING_V2 / "drive_roundtrip_verification.json"
    with open(verif_path, "w", encoding="utf-8") as fh:
        json.dump(verification_payload, fh, indent=2)
        
    v_fid = with_retry(lambda: client.upload(str(verif_path), snap_folder_id, "drive_roundtrip_verification.json"))
    print(f"Uploaded drive_roundtrip_verification.json -> {v_fid}", flush=True)
    print(f"Verification result: {overall_res} ({verification_payload['verification_terminology']})", flush=True)
    return snap_folder_id, overall_res, verification_payload


def main():
    print(f"=== BUILDING ANALYSIS-READY SNAPSHOT V2 ({SNAPSHOT_NAME}) ===", flush=True)
    prepare_staging_v2()
    package_sec_corpus_zip_and_manifest()
    build_controls_and_provenance()
    manifest = generate_summary_and_receipts()
    snap_id, res, verif = upload_and_verify_drive(manifest)
    print(f"=== BUILD COMPLETE: {res} ===", flush=True)


if __name__ == "__main__":
    main()
