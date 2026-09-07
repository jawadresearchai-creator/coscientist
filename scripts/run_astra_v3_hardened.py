"""Final hardened Astra V3 launcher.

Execution repairs enforced here:
- storage-level price cutoff before pandas materialization;
- SEC annual filing re-selection from live submissions metadata when a parent
  selection is invalid or non-substantive;
- complete-submission retrieval for Form 40-F so incorporated annual exhibits
  are represented in the textual corpus;
- current-period XBRL fact selection globally across US-GAAP/IFRS/tag choices;
- full 5,547-firm control rematerialization from official SEC nightly bulk data;
- strict paired-return betas and strict component-defined leverage;
- corrected filing index, cutoff audit, provenance, manifest and receipt.
"""
from __future__ import annotations

import concurrent.futures
import gzip
import hashlib
import importlib.util
import json
import os
import re
import shutil
import time
import urllib.request
import zipfile
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from coscientist import eventstudy_controls as ec
from coscientist.eventstudy_fact_selection import (
    extract_best_fact_latest,
    extract_shares_outstanding_latest,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
BUILDER_PATH = REPO_ROOT / "scripts" / "build_astra_analysis_ready_v3.py"
PRICE_FILE = "us_equity_daily_prices_20250101_20260904.parquet"
CUTOFF = "2026-08-06"
ANNUAL_FORMS = {"10-K", "20-F", "40-F"}
COMPANYFACTS_BULK = "https://www.sec.gov/Archives/edgar/daily-index/xbrl/companyfacts.zip"
SUBMISSIONS_BULK = "https://www.sec.gov/Archives/edgar/daily-index/bulkdata/submissions.zip"

spec = importlib.util.spec_from_file_location("astra_v3_builder", BUILDER_PATH)
if spec is None or spec.loader is None:
    raise RuntimeError(f"cannot load builder at {BUILDER_PATH}")
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)

_ORIGINAL_READ_PARQUET = pd.read_parquet
_ORIGINAL_DOWNLOAD_INPUTS = builder.download_parent_inputs
_ORIGINAL_WRITE_OUTPUTS = builder.write_outputs
_CORRECTED_SELECTIONS: dict[str, dict[str, Any]] = {}
_SHARES_AUDIT: pd.DataFrame | None = None
_CUTOFF_AUDIT: dict[str, Any] | None = None
_ACCOUNTING_QA: dict[str, Any] | None = None

# These V2 artifacts are superseded by V3 and must not be server-side copied.
builder.REPLACE_NAMES.update({
    "pre_event_filing_index.csv",
    "shares_cutoff_audit.csv",
    "pre_event_cutoff_audit.json",
    "accounting_fact_selection_v3_qa.json",
})


def _download_parent_inputs(client, parent_map):
    _ORIGINAL_DOWNLOAD_INPUTS(client, parent_map)
    name = "pre_event_filing_index.csv"
    if name not in parent_map:
        raise RuntimeError("parent V2 missing pre_event_filing_index.csv")
    client.download(parent_map[name]["id"], str(builder.INPUT / name))


def _submission_fallback_url(primary_url: str) -> str | None:
    m = re.match(
        r"^https://www\.sec\.gov/Archives/edgar/data/(\d+)/(\d{18})/[^/?#]+",
        primary_url,
        re.I,
    )
    if not m:
        return None
    cik, compact = m.groups()
    accession = f"{compact[:10]}-{compact[10:12]}-{compact[12:]}"
    return f"https://www.sec.gov/Archives/edgar/data/{cik}/{compact}/{accession}.txt"


def _http_fetch(url: str, limiter=None, attempts: int = 10, min_bytes: int = 500) -> bytes:
    last: Exception | None = None
    for attempt in range(1, attempts + 1):
        if limiter is not None:
            limiter.wait()
        try:
            headers = dict(builder.SEC_HEADERS)
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=90) as resp:
                raw = resp.read()
                if resp.headers.get("Content-Encoding") == "gzip" or raw[:2] == b"\x1f\x8b":
                    raw = gzip.decompress(raw)
            if len(raw) < min_bytes:
                raise RuntimeError(f"SEC payload too short: {len(raw)} bytes")
            return raw
        except Exception as exc:
            last = exc
            if attempt < attempts:
                time.sleep(min(20.0, 1.8 * attempt))
    raise RuntimeError(f"SEC fetch failed after {attempts} attempts for {url}: {last}")


def _fetch_json(url: str, limiter=None) -> dict[str, Any]:
    return json.loads(_http_fetch(url, limiter=limiter, attempts=8, min_bytes=50).decode("utf-8"))


def _primary_url(cik: str, accession: str, primary_document: str) -> str:
    cik_int = str(int(str(cik)))
    compact = accession.replace("-", "")
    return f"https://www.sec.gov/Archives/edgar/data/{cik_int}/{compact}/{primary_document}"


def _complete_submission_url(cik: str, accession: str) -> str:
    cik_int = str(int(str(cik)))
    compact = accession.replace("-", "")
    return f"https://www.sec.gov/Archives/edgar/data/{cik_int}/{compact}/{accession}.txt"


def _filing_payload(url: str, form: str, accession: str, cik: str, limiter) -> tuple[bytes, str]:
    """Return substantive filing bytes and provenance method."""
    # 40-F annual content is largely incorporated via exhibits, so prefer the
    # complete submission rather than the short wrapper form.
    candidates: list[tuple[str, str]] = []
    if form == "40-F":
        candidates.append((_complete_submission_url(cik, accession), "SEC_EDGAR_COMPLETE_SUBMISSION_40F"))
        candidates.append((url, "SEC_EDGAR_PRIMARY_DOCUMENT"))
    else:
        candidates.append((url, "SEC_EDGAR_PRIMARY_DOCUMENT"))
        fallback = _submission_fallback_url(url)
        if fallback:
            candidates.append((fallback, "SEC_EDGAR_COMPLETE_SUBMISSION_FALLBACK"))

    last_error: Exception | None = None
    for fetch_url, method in candidates:
        try:
            raw = _http_fetch(fetch_url, limiter=limiter)
            norm, _, _ = builder.html_to_normalized_text(raw)
            if len(norm) > 5000:
                return raw, method
            last_error = RuntimeError(f"substantive text under 5000 chars ({len(norm)}) at {fetch_url}")
        except Exception as exc:
            last_error = exc
    raise RuntimeError(str(last_error or "no substantive filing payload"))


def _latest_valid_annual_from_submissions(cik: str, limiter) -> dict[str, str] | None:
    cik10 = f"{int(str(cik)):010d}"
    sub = _fetch_json(f"https://data.sec.gov/submissions/CIK{cik10}.json", limiter=limiter)
    recent = sub.get("filings", {}).get("recent", {})
    forms = recent.get("form", [])
    candidates: list[dict[str, str]] = []
    for i, form in enumerate(forms):
        if form not in ANNUAL_FORMS:
            continue
        filed = str(recent.get("filingDate", [""] * len(forms))[i])
        if not filed or filed > CUTOFF:
            continue
        accession = str(recent.get("accessionNumber", [""] * len(forms))[i])
        primary = str(recent.get("primaryDocument", [""] * len(forms))[i])
        report = str(recent.get("reportDate", [""] * len(forms))[i])
        if not accession or not primary:
            continue
        candidates.append({
            "form": form,
            "filing_date": filed,
            "period_of_report": report,
            "accession_number": accession,
            "primary_document": primary,
            "source_url": _primary_url(cik, accession, primary),
        })
    if not candidates:
        return None
    candidates.sort(key=lambda x: (x["filing_date"], x["period_of_report"], x["accession_number"]), reverse=True)
    return candidates[0]


def _apply_index_corrections() -> None:
    path = builder.INPUT / "pre_event_filing_index.csv"
    idx = pd.read_csv(path, dtype=str).fillna("")
    for ticker, corr in _CORRECTED_SELECTIONS.items():
        mask = idx["ticker"].astype(str).eq(ticker)
        if int(mask.sum()) != 1:
            raise RuntimeError(f"filing-index correction expected one row for {ticker}, found {int(mask.sum())}")
        for col, key in (
            ("form", "form"),
            ("filing_date", "filing_date"),
            ("period_of_report", "period_of_report"),
            ("accession_number", "accession_number"),
            ("primary_document", "primary_document"),
            ("filing_url", "source_url"),
        ):
            idx.loc[mask, col] = corr[key]
        idx.loc[mask, "retrieved_at_utc"] = datetime.now(timezone.utc).isoformat()
        idx.loc[mask, "sha256"] = corr["raw_sha256"]
        idx.loc[mask, "bytes"] = str(corr["raw_bytes"])
        idx.loc[mask, "status"] = "RESELECTED_AND_VERIFIED_V3"
    idx.to_csv(path, index=False)


def _repair_sec_corpus(files: list[Path], workers: int = 8) -> dict[str, Any]:
    pending: list[tuple[Path, dict[str, Any]]] = []
    for path in files:
        d = json.loads(path.read_text(encoding="utf-8"))
        if str(d.get("filing_date", "")) > CUTOFF:
            raise RuntimeError(f"post-cutoff SEC filing in parent snapshot: {d.get('ticker')} {d.get('filing_date')}")
        if builder.needs_full_text_repair(d) or str(d.get("form", "")) == "40-F":
            pending.append((path, d))
    if len(pending) < builder.EXPECTED_PREVIEW_REPAIRS:
        raise RuntimeError(f"expected at least {builder.EXPECTED_PREVIEW_REPAIRS} SEC repairs, found {len(pending)}")

    limiter = builder.TokenBucketLimiter(max_per_second=4.5)
    started = time.time()
    failures: list[dict[str, str]] = []
    repaired = 0
    reselected = 0

    def worker(item: tuple[Path, dict[str, Any]]) -> dict[str, Any]:
        path, d = item
        ticker = str(d.get("ticker", ""))
        try:
            original = {
                "form": str(d.get("form", "")),
                "filing_date": str(d.get("filing_date", "")),
                "period_of_report": str(d.get("period_of_report", "")),
                "accession_number": str(d.get("accession_number", "")),
                "source_url": str(d.get("source_url", "")),
            }
            selected = dict(original)
            selected["primary_document"] = selected["source_url"].rsplit("/", 1)[-1]
            try:
                raw, source_method = _filing_payload(
                    selected["source_url"], selected["form"], selected["accession_number"], str(d.get("cik", "")), limiter
                )
            except Exception as original_exc:
                replacement = _latest_valid_annual_from_submissions(str(d.get("cik", "")), limiter)
                if not replacement:
                    raise RuntimeError(f"invalid parent annual filing and no valid SEC annual replacement: {original_exc}")
                raw, source_method = _filing_payload(
                    replacement["source_url"], replacement["form"], replacement["accession_number"], str(d.get("cik", "")), limiter
                )
                selected = replacement

            norm, status, reason = builder.html_to_normalized_text(raw)
            full_flag, truthful_status = builder.truthful_full_text_status(norm, status)
            if full_flag != "Y":
                raise RuntimeError(f"filing remains non-substantive after repair: {len(norm)} chars")
            sections = builder.extract_filing_sections(norm, selected["form"])
            selection_changed = selected["accession_number"] != original["accession_number"]
            if selection_changed:
                corr = dict(selected)
                corr["raw_sha256"] = hashlib.sha256(raw).hexdigest()
                corr["raw_bytes"] = len(raw)
                _CORRECTED_SELECTIONS[ticker] = corr

            d.update({
                "form": selected["form"],
                "filing_date": selected["filing_date"],
                "period_of_report": selected["period_of_report"],
                "accession_number": selected["accession_number"],
                "source_url": selected["source_url"],
                "source_acquisition_status": "SUCCESS",
                "source_provenance": source_method,
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
                "v3_selection_repaired": selection_changed,
                "v3_previous_selection": original if selection_changed else None,
                "v3_repaired_at_utc": datetime.now(timezone.utc).isoformat(),
                "missing_reason": "NONE" if sections["segmentation_status"] == "FULL_SECTIONS_SEGMENTED" else sections["missing_reason"],
            })
            path.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
            return {"ticker": ticker, "ok": True, "reselected": selection_changed, "chars": len(norm)}
        except Exception as exc:
            return {"ticker": ticker, "ok": False, "reselected": False, "error": str(exc)}

    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(worker, item) for item in pending]
        for n, fut in enumerate(concurrent.futures.as_completed(futures), 1):
            res = fut.result()
            if res["ok"]:
                repaired += 1
                reselected += int(bool(res["reselected"]))
            else:
                failures.append({"ticker": res["ticker"], "error": res["error"]})
            if n % 100 == 0 or n == len(futures):
                elapsed = max(time.time() - started, 0.001)
                print(f"SEC V3 final repair {n}/{len(futures)}; repaired={repaired}; reselected={reselected}; failures={len(failures)}; rate={n/elapsed:.2f}/s", flush=True)

    if failures:
        builder.OUTPUT.mkdir(parents=True, exist_ok=True)
        (builder.OUTPUT / "sec_v3_failures.json").write_text(json.dumps(failures, indent=2), encoding="utf-8")
        raise RuntimeError(f"SEC V3 final repair has {len(failures)} failures; refusing publication")
    _apply_index_corrections()
    return {
        "targeted_filings": len(pending),
        "successfully_reparsed": repaired,
        "canonical_filing_reselections": reselected,
        "reselected_tickers": sorted(_CORRECTED_SELECTIONS),
        "forty_f_complete_submission_policy": True,
        "failures": 0,
    }


def _predicate_filtered_read_parquet(path, *args, **kwargs):
    p = Path(path)
    if p.name != PRICE_FILE:
        return _ORIGINAL_READ_PARQUET(path, *args, **kwargs)
    schema = pq.read_schema(p)
    if "Date" not in schema.names:
        raise RuntimeError(f"price parquet lacks Date column; schema={schema}")
    typ = schema.field("Date").type
    if pa.types.is_timestamp(typ):
        cutoff_value = pd.Timestamp(CUTOFF).to_pydatetime()
    elif pa.types.is_date32(typ) or pa.types.is_date64(typ):
        cutoff_value = date.fromisoformat(CUTOFF)
    elif pa.types.is_string(typ) or pa.types.is_large_string(typ):
        cutoff_value = CUTOFF
    else:
        raise RuntimeError(f"unsupported Date parquet type for predicate filtering: {typ}")
    filters = list(kwargs.pop("filters", []))
    filters.append(("Date", "<=", cutoff_value))
    df = _ORIGINAL_READ_PARQUET(path, *args, filters=filters, **kwargs)
    parsed = pd.to_datetime(df["Date"], errors="coerce")
    if parsed.notna().any() and parsed.max() > pd.Timestamp(CUTOFF):
        raise RuntimeError(f"predicate-filter boundary failed; max materialized date={parsed.max()}")
    print(f"PRICE_PREDICATE_FILTER_PASS cutoff={CUTOFF} rows={len(df)} max_date={parsed.max() if parsed.notna().any() else 'NA'}", flush=True)
    return df


def _download_bulk_zip(url: str, dest: Path) -> None:
    if dest.exists() and dest.stat().st_size > 0:
        return
    headers = {"User-Agent": builder.SEC_HEADERS["User-Agent"], "Accept-Encoding": "identity"}
    last: Exception | None = None
    for attempt in range(1, 4):
        try:
            print(f"Downloading SEC bulk archive {url} -> {dest.name}", flush=True)
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=900) as resp, dest.open("wb") as out:
                while True:
                    chunk = resp.read(8 * 1024 * 1024)
                    if not chunk:
                        break
                    out.write(chunk)
            with zipfile.ZipFile(dest) as zf:
                bad = zf.testzip()
                if bad:
                    raise RuntimeError(f"corrupt member {bad}")
            print(f"SEC_BULK_ZIP_PASS {dest.name} bytes={dest.stat().st_size}", flush=True)
            return
        except Exception as exc:
            last = exc
            dest.unlink(missing_ok=True)
            time.sleep(4 * attempt)
    raise RuntimeError(f"failed SEC bulk archive {url}: {last}")


def _repair_prices_and_controls():
    global _SHARES_AUDIT, _CUTOFF_AUDIT, _ACCOUNTING_QA
    universe = pd.read_csv(builder.INPUT / "candidate_firm_universe.csv", dtype={"ticker": str, "cik": str})
    candidates = universe.loc[universe["included"].astype(str).eq("Y"), "ticker"].astype(str).tolist()
    if len(candidates) != builder.EXPECTED_CANDIDATES:
        raise RuntimeError(f"expected {builder.EXPECTED_CANDIDATES} candidates, found {len(candidates)}")

    prices = builder.canonicalize_prices(pd.read_parquet(builder.INPUT / PRICE_FILE))
    metrics = builder.build_strict_pre_event_price_metrics(prices, candidates, cutoff=CUTOFF, benchmark="SPY")
    metrics.to_csv(builder.INPUT / "firm_pre_event_price_metrics.csv", index=False)
    for w in (120, 200, 250):
        bad = metrics[metrics[f"market_beta_{w}"].notna() & (metrics[f"beta_{w}_n"] != w)]
        if not bad.empty:
            raise RuntimeError(f"strict beta {w} exact-N gate failed for {len(bad)} rows")

    bulk = builder.WORK / "sec_bulk"
    bulk.mkdir(parents=True, exist_ok=True)
    _download_bulk_zip(COMPANYFACTS_BULK, bulk / "companyfacts.zip")
    _download_bulk_zip(SUBMISSIONS_BULK, bulk / "submissions.zip")

    # Globally replace the legacy order-dependent selectors for this complete
    # rematerialization. The reusable selector itself is committed and tested.
    ec.extract_best_fact = extract_best_fact_latest
    ec.extract_shares_outstanding = extract_shares_outstanding_latest
    controls, provenance, shares_audit, cutoff_audit = ec.materialize_pre_event_controls(builder.INPUT, bulk, cutoff=CUTOFF)
    controls, provenance, leverage_qa = builder.repair_strict_leverage(controls, provenance)
    controls = builder.merge_strict_beta_metrics(controls, metrics)

    # Mandatory regression invariant for the filing-selection defect that
    # exposed the stale accounting bug.
    barrick = controls.loc[controls["ticker"].astype(str).eq("B")]
    if len(barrick) != 1:
        raise RuntimeError(f"Barrick invariant expected one B row, found {len(barrick)}")
    b = barrick.iloc[0]
    if str(b.get("pre_event_form")) != "40-F" or str(b.get("pre_event_filing_date")) != "2026-02-27" or str(b.get("period_of_report")) != "2025-12-31":
        raise RuntimeError(f"Barrick canonical annual filing was not corrected: {b[['pre_event_form','pre_event_filing_date','period_of_report']].to_dict()}")
    for col in ("net_income_filed", "total_assets_filed", "revenue_filed"):
        val = str(b.get(col) or "")
        if val and val < "2025-01-01":
            raise RuntimeError(f"Barrick stale-accounting invariant failed: {col}={val}")

    _SHARES_AUDIT = shares_audit
    _CUTOFF_AUDIT = cutoff_audit
    accounting_dates = []
    for col in ("net_income_filed", "total_assets_filed", "total_debt_filed", "combined_intangibles_filed", "rd_expense_filed", "revenue_filed"):
        if col in controls.columns:
            accounting_dates.extend([x for x in controls[col].dropna().astype(str).tolist() if x])
    _ACCOUNTING_QA = {
        "selector": "CURRENT_PERIOD_GLOBAL_ACROSS_TAXONOMIES_V3",
        "candidate_firms": len(controls),
        "corrected_canonical_filing_count": len(_CORRECTED_SELECTIONS),
        "corrected_canonical_filing_tickers": sorted(_CORRECTED_SELECTIONS),
        "max_accounting_source_filing_date": max(accounting_dates) if accounting_dates else "",
        "min_accounting_source_filing_date": min(accounting_dates) if accounting_dates else "",
        "barrick_pre_event_form": str(b.get("pre_event_form")),
        "barrick_pre_event_filing_date": str(b.get("pre_event_filing_date")),
        "barrick_net_income_filed": str(b.get("net_income_filed") or ""),
        "barrick_total_assets_filed": str(b.get("total_assets_filed") or ""),
        "barrick_revenue_filed": str(b.get("revenue_filed") or ""),
        "outcome_inspected": "NO",
    }
    if cutoff_audit.get("audit_status") != "PASS":
        raise RuntimeError(f"full rematerialization cutoff audit failed: {cutoff_audit}")

    price_qa = {
        "paper_id": builder.PAPER_ID,
        "cutoff_date": CUTOFF,
        "candidate_firms": len(metrics),
        "benchmark": "SPY",
        "paired_return_obs_gte_120": int((metrics["valid_paired_pre_event_return_obs"] >= 120).sum()),
        "paired_return_obs_gte_200": int((metrics["valid_paired_pre_event_return_obs"] >= 200).sum()),
        "paired_return_obs_gte_250": int((metrics["valid_paired_pre_event_return_obs"] >= 250).sum()),
        "beta_120_exact_n_count": int(metrics["market_beta_120"].notna().sum()),
        "beta_200_exact_n_count": int(metrics["market_beta_200"].notna().sum()),
        "beta_250_exact_n_count": int(metrics["market_beta_250"].notna().sum()),
        "storage_level_cutoff_filter": True,
        "outcome_inspected": "NO",
    }
    return metrics, controls, provenance, price_qa, leverage_qa


def _write_outputs(sec_files, sec_repair_qa, sec_manifest, sec_qa, metrics, controls, provenance, price_qa, leverage_qa):
    hashes = _ORIGINAL_WRITE_OUTPUTS(sec_files, sec_repair_qa, sec_manifest, sec_qa, metrics, controls, provenance, price_qa, leverage_qa)
    if _SHARES_AUDIT is None or _CUTOFF_AUDIT is None or _ACCOUNTING_QA is None:
        raise RuntimeError("full-control audit artifacts were not materialized")
    shutil.copy2(builder.INPUT / "pre_event_filing_index.csv", builder.OUTPUT / "pre_event_filing_index.csv")
    _SHARES_AUDIT.to_csv(builder.OUTPUT / "shares_cutoff_audit.csv", index=False)
    (builder.OUTPUT / "pre_event_cutoff_audit.json").write_text(json.dumps(_CUTOFF_AUDIT, indent=2), encoding="utf-8")
    (builder.OUTPUT / "accounting_fact_selection_v3_qa.json").write_text(json.dumps(_ACCOUNTING_QA, indent=2), encoding="utf-8")

    audit_path = builder.OUTPUT / "v3_repair_audit.json"
    audit = json.loads(audit_path.read_text(encoding="utf-8"))
    audit.setdefault("repairs", {})["canonical_filing_reselection"] = {
        "count": len(_CORRECTED_SELECTIONS), "tickers": sorted(_CORRECTED_SELECTIONS)
    }
    audit["repairs"]["accounting_fact_selection"] = _ACCOUNTING_QA
    audit["repairs"]["full_pre_event_cutoff_audit"] = _CUTOFF_AUDIT
    audit_path.write_text(json.dumps(audit, indent=2), encoding="utf-8")

    summary_path = builder.OUTPUT / "data_completion_summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    summary["canonical_filing_reselection"] = {"count": len(_CORRECTED_SELECTIONS), "tickers": sorted(_CORRECTED_SELECTIONS)}
    summary["accounting_fact_selection"] = _ACCOUNTING_QA
    summary["cutoff_audit_v3"] = _CUTOFF_AUDIT
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")

    for name in (
        "pre_event_filing_index.csv", "shares_cutoff_audit.csv", "pre_event_cutoff_audit.json",
        "accounting_fact_selection_v3_qa.json", "v3_repair_audit.json", "data_completion_summary.json",
    ):
        hashes[name] = builder.sha256_file(builder.OUTPUT / name)
    return hashes


def _publish(client, parent_map, parent_manifest, local_hashes):
    existing_final = [f for f in client.list_folder(builder.STUDY_FOLDER_ID) if f.get("name") == builder.SNAPSHOT and f.get("mimeType") == builder.FOLDER_MIME]
    if existing_final:
        raise RuntimeError(f"immutable final snapshot {builder.SNAPSHOT} already exists: {existing_final[0]['id']}")
    run_id = os.environ.get("GITHUB_RUN_ID", str(int(time.time())))
    building_name = f"{builder.SNAPSHOT}__BUILDING_{run_id}"
    folder_id = client.create_folder(building_name, builder.STUDY_FOLDER_ID)
    print(f"Created Drive build folder {building_name}: {folder_id}", flush=True)

    parent_hashes = {f["name"]: f["sha256"] for f in parent_manifest.get("files", [])}
    for name, meta in sorted(parent_map.items()):
        if name in builder.REPLACE_NAMES or name in builder.META_EXCLUDED_FROM_MANIFEST or meta.get("mimeType") == builder.FOLDER_MIME:
            continue
        builder.copy_drive_file(client, meta["id"], folder_id, name)
    for path in sorted(builder.OUTPUT.iterdir()):
        if path.is_file():
            print(f"Upload repaired file {path.name} ({path.stat().st_size:,} bytes)", flush=True)
            client.upload(str(path), folder_id, path.name)

    remote = {f["name"]: f for f in client.list_folder(folder_id)}
    entries = []
    for name, meta in sorted(remote.items()):
        if name in builder.META_EXCLUDED_FROM_MANIFEST:
            continue
        if name in local_hashes:
            sha = local_hashes[name]
        elif name in parent_hashes:
            sha = parent_hashes[name]
        else:
            raise RuntimeError(f"no authoritative SHA-256 provenance for remote V3 file {name}")
        entries.append({"name": name, "bytes": int(meta.get("size", 0)), "sha256": sha})

    scope = [
        "SEC_FULL_TEXT_PREVIEW_TRUNCATION",
        "CANONICAL_ANNUAL_FILING_RESELECTION",
        "FORM_40F_COMPLETE_SUBMISSION_TEXT",
        "CURRENT_PERIOD_CROSS_TAXONOMY_ACCOUNTING_FACT_SELECTION",
        "FULL_NUMERICAL_CONTROL_REMATERIALIZATION",
        "STRICT_PAIRED_RETURN_BETAS",
        "STRICT_DEBT_COMPONENT_LEVERAGE",
        "STORAGE_LEVEL_PRE_EVENT_PRICE_FILTER",
    ]
    manifest = {
        "paper_id": builder.PAPER_ID, "snapshot_id": builder.SNAPSHOT, "parent_snapshot_id": builder.PARENT_SNAPSHOT,
        "git_commit_sha": builder.git_sha(), "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "outcome_inspected": "NO",
        "outcome_blind_declaration": "Astra event-window outcomes were not analyzed or summarized during V3 repair.",
        "repair_scope": scope,
        "file_count": len(entries), "total_bytes": sum(e["bytes"] for e in entries), "files": entries,
    }
    manifest_path = builder.OUTPUT / "acquisition_manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    sums_path = builder.OUTPUT / "SHA256SUMS.txt"
    sums_path.write_text("\n".join(f"{e['sha256']}  {e['name']}" for e in entries) + "\n", encoding="utf-8")
    receipt = builder.OUTPUT / "acquisition_receipt.md"
    receipt.write_text(
        "# Management Science CoScientist Data Acquisition Receipt (V3 Final Repair)\n\n"
        f"- **Paper ID**: `{builder.PAPER_ID}`\n- **Snapshot**: `{builder.SNAPSHOT}`\n- **Parent Snapshot**: `{builder.PARENT_SNAPSHOT}`\n"
        f"- **Git Commit SHA**: `{builder.git_sha()}`\n- **Pre-Event Cutoff**: `{CUTOFF}`\n- **Outcome Inspected**: **NO**\n"
        "- **Repair Scope**: canonical SEC annual selection; complete SEC full text including 40-F submissions; current-period cross-taxonomy accounting rematerialization; exact paired-return betas; strict component-defined leverage; storage-level pre-event price filtering.\n"
        f"- **Files**: {len(entries)}\n- **Payload**: {manifest['total_bytes']:,} bytes\n\n"
        "All prior snapshots remain immutable. V3 was promoted only after deterministic repair and integrity gates passed.\n",
        encoding="utf-8",
    )
    for path in (manifest_path, sums_path, receipt):
        client.upload(str(path), folder_id, path.name)

    remote = {f["name"]: f for f in client.list_folder(folder_id)}
    verification: dict[str, Any] = {
        "paper_id": builder.PAPER_ID, "snapshot": builder.SNAPSHOT, "outcome_inspected": "NO",
        "verification_scope": "REPAIRED_AND_REGENERATED_PAYLOADS", "checks": []
    }
    verify_dir = builder.WORK / "verify"
    verify_dir.mkdir(parents=True, exist_ok=True)
    for name in sorted(local_hashes):
        lp = builder.OUTPUT / name
        rm = remote.get(name)
        if not rm:
            raise RuntimeError(f"uploaded repaired file missing remotely: {name}")
        if name == "sec_pre_event_filings_full.zip":
            remote_md5 = str(rm.get("md5Checksum") or "")
            local_md5 = builder.md5_file(lp)
            ok = int(rm.get("size", -1)) == lp.stat().st_size and (not remote_md5 or remote_md5 == local_md5)
            method = "size+md5"
        else:
            dest = verify_dir / name
            got = client.download(rm["id"], str(dest), local_hashes[name])
            ok = got == local_hashes[name]
            dest.unlink(missing_ok=True)
            method = "sha256-roundtrip"
        verification["checks"].append({"name": name, "method": method, "status": "PASS" if ok else "FAIL"})
        if not ok:
            raise RuntimeError(f"Drive verification failed for {name}")
    verification["status"] = "PASS"
    verification["verified_at_utc"] = datetime.now(timezone.utc).isoformat()
    verify_path = builder.OUTPUT / "drive_roundtrip_verification.json"
    verify_path.write_text(json.dumps(verification, indent=2), encoding="utf-8")
    client.upload(str(verify_path), folder_id, verify_path.name)

    builder.rename_drive_file(client, folder_id, builder.SNAPSHOT)
    final = [f for f in client.list_folder(builder.STUDY_FOLDER_ID) if f.get("name") == builder.SNAPSHOT and f.get("mimeType") == builder.FOLDER_MIME]
    if len(final) != 1 or final[0]["id"] != folder_id:
        raise RuntimeError("final snapshot promotion verification failed")
    print(f"PROMOTED_IMMUTABLE_SNAPSHOT {builder.SNAPSHOT} {folder_id}", flush=True)
    return folder_id


builder.download_parent_inputs = _download_parent_inputs
builder.repair_sec_corpus = _repair_sec_corpus
builder.pd.read_parquet = _predicate_filtered_read_parquet
builder.repair_prices_and_controls = _repair_prices_and_controls
builder.write_outputs = _write_outputs
builder.publish = _publish


if __name__ == "__main__":
    raise SystemExit(builder.main())
