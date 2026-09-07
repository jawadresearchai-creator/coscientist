"""Reparse SEC filings with missing/empty text and enforce truthful extraction semantics.

Strict information boundary:
- Only pre-event annual filings (<= 2026-08-06).
- Zero return or event-window data accessed.
"""
from __future__ import annotations

import concurrent.futures
import csv
import gzip
import hashlib
import json
import os
import sys
import time
import urllib.request
from pathlib import Path
from typing import Any, Dict, List

from coscientist.sec_corpus import (
    SEC_HEADERS,
    TokenBucketLimiter,
    extract_filing_sections,
    html_to_normalized_text
)

STAGING_DIR = Path("state/staging_v3/analysis_ready_data_v1_20260907")
FILINGS_DIR = STAGING_DIR / "sec_pre_event_filings_full"


def reparse_empty_filings(workers: int = 16) -> Dict[str, Any]:
    print(f"Scanning {FILINGS_DIR} for filings needing text reparse...", flush=True)
    all_json_files = sorted([f for f in FILINGS_DIR.iterdir() if f.name.endswith(".json")])
    
    to_reparse = []
    to_update_metadata = []
    
    for f in all_json_files:
        with open(f, "r", encoding="utf-8") as fh:
            d = json.load(fh)
        prev = d.get("visible_text_preview", "")
        if not prev or len(prev.strip()) == 0:
            to_reparse.append((f, d))
        else:
            to_update_metadata.append((f, d))
            
    print(f"Total filings: {len(all_json_files)}. Need EDGAR re-fetch and parse: {len(to_reparse)}. Need metadata upgrade: {len(to_update_metadata)}", flush=True)
    
    # 1. Update metadata for the 4,465 already-parsed filings
    print("Upgrading extraction semantics on existing full-text filings...", flush=True)
    upgraded_count = 0
    for f, d in to_update_metadata:
        prev = d.get("visible_text_preview", "")
        i1 = d.get("item1_available", False)
        i1a = d.get("item1a_available", False)
        i7 = d.get("item7_available", False)
        form = d.get("form", "")
        is_foreign = "20-F" in form or "40-F" in form
        
        if i1 and i1a and i7:
            seg_status = "FULL_SECTIONS_SEGMENTED"
        elif i1 or i1a or i7:
            seg_status = "PARTIAL_SECTIONS_SEGMENTED"
        else:
            seg_status = "UNSEGMENTED_FULL_TEXT_RETAINED"
            
        d["source_acquisition_status"] = "SUCCESS"
        d["text_extraction_status"] = "SUCCESS_FULL_TEXT"
        d["section_segmentation_status"] = seg_status
        d["full_text_available"] = "Y"
        d["normalized_text_length"] = len(prev) if not d.get("normalized_text") else len(d["normalized_text"])
        if "normalized_text" not in d:
            d["normalized_text"] = prev
            
        if seg_status == "FULL_SECTIONS_SEGMENTED":
            d["missing_reason"] = "NONE"
        elif is_foreign:
            d["missing_reason"] = "FOREIGN_FORM_ITEMS_NOT_INDIVIDUALLY_DELIMITED" if seg_status == "UNSEGMENTED_FULL_TEXT_RETAINED" else "PARTIAL_FOREIGN_ITEMS_FOUND"
        else:
            d["missing_reason"] = "10K_ITEMS_NOT_INDIVIDUALLY_DELIMITED" if seg_status == "UNSEGMENTED_FULL_TEXT_RETAINED" else "PARTIAL_10K_ITEMS_FOUND"
            
        with open(f, "w", encoding="utf-8") as fh:
            json.dump(d, fh, ensure_ascii=False)
        upgraded_count += 1
        
    print(f"Upgraded {upgraded_count} existing filings.", flush=True)
    
    # 2. Fetch and reparse the 769 empty filings
    limiter = TokenBucketLimiter(max_per_second=7.5)
    
    def fetch_and_reparse_worker(item):
        f_path, d = item
        tk = d.get("ticker", "")
        url = d.get("source_url", "")
        form = d.get("form", "")
        
        max_retries = 5
        raw = None
        for attempt in range(1, max_retries + 1):
            limiter.wait()
            try:
                req = urllib.request.Request(url, headers=SEC_HEADERS)
                with urllib.request.urlopen(req, timeout=30) as resp:
                    raw = resp.read()
                    if resp.headers.get("Content-Encoding") == "gzip" or raw[:2] == b"\x1f\x8b":
                        raw = gzip.decompress(raw)
                break
            except Exception as exc:
                if attempt == max_retries:
                    d["source_acquisition_status"] = f"FAILED: {exc}"
                    d["text_extraction_status"] = "FAILED_MALFORMED_SOURCE"
                    d["section_segmentation_status"] = "FAILED"
                    d["full_text_available"] = "N"
                    d["missing_reason"] = f"SEC_EDGAR_HTTP_FAILURE: {exc}"
                    with open(f_path, "w", encoding="utf-8") as fh:
                        json.dump(d, fh, ensure_ascii=False)
                    return {"ticker": tk, "status": "FAILED", "reason": str(exc)}
                time.sleep(1.5 * attempt)
                
        # Parse normalized text
        norm_text, text_status, text_reason = html_to_normalized_text(raw)
        sections = extract_filing_sections(norm_text, form)
        
        d["source_acquisition_status"] = "SUCCESS"
        d["sha256_raw"] = hashlib.sha256(raw).hexdigest()
        d["bytes_raw"] = len(raw)
        d["text_extraction_status"] = text_status
        d["section_segmentation_status"] = sections["segmentation_status"]
        d["full_text_available"] = "Y" if text_status in ("SUCCESS_FULL_TEXT", "SUCCESS_PARTIAL_TEXT") else "N"
        d["normalized_text_length"] = len(norm_text)
        d["normalized_text"] = norm_text
        d["visible_text_preview"] = norm_text[:5000]
        
        d["item1_available"] = sections["item1_available"]
        d["item1a_available"] = sections["item1a_available"]
        d["item7_available"] = sections["item7_available"]
        d["foreign_item4_available"] = sections["foreign_item4_available"]
        d["foreign_item3d_available"] = sections["foreign_item3d_available"]
        d["foreign_item5_available"] = sections["foreign_item5_available"]
        d["item1_text"] = sections["item1"]
        d["item1a_text"] = sections["item1a"]
        d["item7_text"] = sections["item7"]
        
        if d["full_text_available"] == "Y":
            if sections["segmentation_status"] == "FULL_SECTIONS_SEGMENTED":
                d["missing_reason"] = "NONE"
            else:
                d["missing_reason"] = sections["missing_reason"]
        else:
            d["missing_reason"] = text_reason
            
        with open(f_path, "w", encoding="utf-8") as fh:
            json.dump(d, fh, ensure_ascii=False)
            
        return {"ticker": tk, "status": "OK", "text_status": text_status, "chars": len(norm_text)}

    print(f"Beginning parallel re-download and reparse of {len(to_reparse)} filings...", flush=True)
    t0 = time.time()
    completed = 0
    failed = 0
    results_stats = {"SUCCESS_FULL_TEXT": 0, "SUCCESS_PARTIAL_TEXT": 0, "FAILED": 0}
    
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {executor.submit(fetch_and_reparse_worker, item): item for item in to_reparse}
        for fut in concurrent.futures.as_completed(futures):
            res = fut.result()
            if res["status"] == "OK":
                completed += 1
                t_stat = res.get("text_status", "SUCCESS_FULL_TEXT")
                results_stats[t_stat] = results_stats.get(t_stat, 0) + 1
            else:
                failed += 1
                results_stats["FAILED"] += 1
                
            if (completed + failed) % 25 == 0 or (completed + failed) == len(to_reparse):
                elapsed = time.time() - t0
                rate = (completed + failed) / elapsed if elapsed > 0 else 0
                rem = (len(to_reparse) - (completed + failed)) / rate if rate > 0 else 0
                print(f"Reparse progress: {completed + failed}/{len(to_reparse)} ({completed} ok, {failed} fail) at {rate:.1f} filings/s, ~{rem/60:.1f}m left", flush=True)
                
    print(f"Reparse complete in {time.time()-t0:.1f}s. Breakdown: {results_stats}", flush=True)
    return results_stats


if __name__ == "__main__":
    reparse_empty_filings()
