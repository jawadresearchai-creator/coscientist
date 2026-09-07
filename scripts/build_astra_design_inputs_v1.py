"""Build outcome-blind design inputs and close Astra DESIGN_CLOSURE.

Information boundary: this script reads only pre-event SEC text, pre-event
controls/betas and pre-period residual volatility. It may read the frozen price
panel only to certify benchmark availability; it never calculates/reads event-
window firm returns, abnormal returns, CARs or winner/loser classifications.

Outputs are published to a separate immutable design-input folder. The V3 data
snapshot is never mutated. Only after exposure, power, provenance and Drive
round-trip gates pass does the paper-local Director receive DESIGN_CLOSURE=PASS.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import math
import os
import re
import shutil
import subprocess
import urllib.request
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pyarrow.parquet as pq

from coscientist.astra_exposure import CORE_PATTERNS, FRONTIER_PATTERNS, stats, standardized, term_counts
from coscientist.director import ActionKind, DirectorState, apply_answer, ensure_action
from coscientist.drive import API, DriveClient, DriveCredentials, FOLDER_MIME
from coscientist.sec_corpus import SEC_HEADERS
from coscientist.single_paper import PaperStage, SinglePaperState

PAPER_ID = "MS-ASTRA-REVALUE-2026"
STUDY_FOLDER_ID = "1yx34Mj-k7SsGSuPli8UVuR_E639eeLcb"
V3_FOLDER_ID = "17Suu1CiQ0lFJF5mKNxMOpBN-44BsvlmH"
DESIGN_NAME = "design_inputs_v1_20260907"
CUTOFF = "2026-08-06"
PRIMARY_EVENT_DATE = "2026-09-03"
PRIMARY_EVENT_TIMESTAMP = "2026-09-03T19:32:00Z"
WORK = Path(".astra_design_v1")
INP = WORK / "inputs"
OUT = WORK / "outputs"

SOURCE = {
    "sec_pre_event_filings_full.zip": ("1-ywr43c6AQCU-CbVQR-RNKxJ4nazsweU", "d5614ebe462f447e700fe82c19ca520c8a54838b37c622e9a2d439a935a2b95e"),
    "firm_pre_event_price_metrics.csv": ("1Q1gW5E8SY-ZlFXHyp7yBwMJ20mSZH5cm", "5dd34481bcff8df2ad37046f4aa84e9dcd95f05ea4714ac1c138990d24db1ec9"),
    "firm_pre_event_controls_complete.csv": ("16rBa_uX1Oyb6TeCbw0yiUWMljzXcd3Uj", "3f4cf546abdbbe883fbded63743c2f731ebb86ed2df27b50068fddf2c0beae25"),
    "us_equity_daily_prices_20250101_20260904.parquet": ("1yT2sg3oW9reAgEABhrx2c1eYsI5Ea_Xi", "9d3037916c2a6f25d3188af424be5b83bc185a0575fb6330742e65bbf441d7ed"),
    "astra_event_timestamps.json": ("1SkoqGyd2woSC6wlDTYV2Jt32L3ktggkZ", "838d993651a5df0bb833d4989d4c1d6ab6d442316466b2865ab4e144e37cd843"),
    "candidate_firm_universe.csv": ("1fkApMJfh-sSa7kO8CWPuVhvd5SF3aTkN", "4d00d4e36206a7f405b75b91dd0288809de185008fc8173e59299bf08f188ae5"),
}
EXPECTED_FILINGS = 5234
EXPECTED_CANDIDATES = 5547
SEC_CURRENT_FORMS = {"8-K", "8-K/A", "6-K", "6-K/A"}


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def git_sha() -> str:
    return subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        while chunk := fh.read(8 * 1024 * 1024):
            h.update(chunk)
    return h.hexdigest()


def _bool(s: pd.Series) -> pd.Series:
    return s.astype(str).str.upper().isin(["TRUE", "Y", "YES", "1"])


def _num(s: pd.Series) -> pd.Series:
    return pd.to_numeric(s.replace("", np.nan), errors="coerce")


def _one_named(client: DriveClient, folder: str, name: str) -> dict[str, Any]:
    hits = [f for f in client.list_folder(folder) if f.get("name") == name]
    if len(hits) != 1:
        raise RuntimeError(f"expected exactly one {name!r} under {folder}, found {len(hits)}")
    return hits[0]


def _patch_json(client: DriveClient, file_id: str, path: Path) -> None:
    payload = path.read_bytes()
    req = urllib.request.Request(
        f"https://www.googleapis.com/upload/drive/v3/files/{file_id}?uploadType=media&fields=id",
        data=payload, method="PATCH",
        headers={"Authorization": f"Bearer {client.token()}", "Content-Type": "application/json", "Content-Length": str(len(payload))},
    )
    with client.opener(req, timeout=120) as resp:
        resp.read()


def _rename(client: DriveClient, file_id: str, name: str) -> None:
    payload = json.dumps({"name": name}).encode()
    req = urllib.request.Request(
        f"{API}/files/{file_id}?fields=id,name", data=payload, method="PATCH",
        headers={"Authorization": f"Bearer {client.token()}", "Content-Type": "application/json"},
    )
    with client.opener(req, timeout=120) as resp:
        got = json.loads(resp.read().decode())
    if got.get("name") != name:
        raise RuntimeError(f"Drive rename failed: {got}")


def download_sources(client: DriveClient) -> None:
    INP.mkdir(parents=True, exist_ok=True)
    for name, (fid, expected) in SOURCE.items():
        print(f"download {name}", flush=True)
        got = client.download(fid, str(INP / name), expected_sha256=expected)
        if got != expected:
            raise RuntimeError(f"source hash mismatch for {name}")


def certify_benchmarks() -> dict[str, Any]:
    schema = pq.read_schema(INP / "us_equity_daily_prices_20250101_20260904.parquet")
    cols = set(schema.names)
    if not {"Date", "Ticker"}.issubset(cols):
        raise RuntimeError(f"price panel missing Date/Ticker: {schema.names}")
    panel = pq.read_table(
        INP / "us_equity_daily_prices_20250101_20260904.parquet",
        columns=["Ticker", "Date"],
        filters=[("Ticker", "in", ["SPY", "QQQ", "IWM"]), ("Date", "<=", CUTOFF)],
    ).to_pandas()
    found = {x: int((panel["Ticker"].astype(str) == x).sum()) for x in ["SPY", "QQQ", "IWM"]}
    if any(v < 251 for v in found.values()):
        raise RuntimeError(f"benchmark history incomplete: {found}")
    return {"certified_pre_event_rows": found, "outcome_inspected": "NO"}


def build_exposure() -> tuple[pd.DataFrame, dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    aggregate_terms = {name: 0 for name, _ in CORE_PATTERNS}
    aggregate_frontier = {name: 0 for name, _ in FRONTIER_PATTERNS}
    with zipfile.ZipFile(INP / "sec_pre_event_filings_full.zip") as zf:
        names = [n for n in zf.namelist() if n.lower().endswith(".json")]
        if len(names) != EXPECTED_FILINGS:
            raise RuntimeError(f"expected {EXPECTED_FILINGS} SEC JSON files, found {len(names)}")
        for i, name in enumerate(names, 1):
            d = json.loads(zf.read(name).decode("utf-8"))
            filing_date = str(d.get("filing_date", ""))
            text = str(d.get("normalized_text", ""))
            if filing_date > CUTOFF:
                raise RuntimeError(f"post-cutoff filing in exposure corpus: {d.get('ticker')} {filing_date}")
            if str(d.get("full_text_available", "")).upper() != "Y" or len(text) <= 5000:
                raise RuntimeError(f"non-full filing in exposure corpus: {d.get('ticker')}")
            full = stats(text)
            frontier = stats(text, frontier=True)
            business_text = str(d.get("item1_text", "") or "")
            risk_text = str(d.get("item1a_text", "") or "")
            business = stats(business_text) if business_text else None
            risk = stats(risk_text) if risk_text else None
            for k, v in term_counts(text).items():
                aggregate_terms[k] += v
            for k, v in term_counts(text, frontier=True).items():
                aggregate_frontier[k] += v
            rows.append({
                "ticker": str(d.get("ticker", "")), "cik": str(d.get("cik", "")),
                "form": str(d.get("form", "")), "filing_date": filing_date,
                "period_of_report": str(d.get("period_of_report", "")),
                "accession_number": str(d.get("accession_number", "")),
                "full_words": full.words, "core_ai_mentions": full.mentions,
                "core_ai_per_10k_words": full.per_10k_words,
                "core_ai_log1p_per_10k": full.log1p_per_10k,
                "any_core_ai": full.any_mention,
                "frontier_ai_mentions": frontier.mentions,
                "frontier_ai_per_10k_words": frontier.per_10k_words,
                "business_section_available": bool(business_text),
                "business_words": business.words if business else 0,
                "business_core_ai_mentions": business.mentions if business else 0,
                "business_core_ai_per_10k_words": business.per_10k_words if business else np.nan,
                "risk_section_available": bool(risk_text),
                "risk_words": risk.words if risk else 0,
                "risk_core_ai_mentions": risk.mentions if risk else 0,
                "risk_core_ai_per_10k_words": risk.per_10k_words if risk else np.nan,
                "outcome_inspected": "NO",
            })
            if i % 500 == 0:
                print(f"exposure {i}/{len(names)}", flush=True)
    df = pd.DataFrame(rows)
    if len(df) != EXPECTED_FILINGS or df["ticker"].nunique() != EXPECTED_FILINGS:
        raise RuntimeError("exposure filing/ticker count mismatch")

    metrics = pd.read_csv(INP / "firm_pre_event_price_metrics.csv", dtype=str, keep_default_na=False)
    controls = pd.read_csv(INP / "firm_pre_event_controls_complete.csv", dtype=str, keep_default_na=False)
    exact200 = _num(metrics["market_beta_200"]).notna() & _num(metrics["beta_200_n"]).eq(200)
    eligible200 = set(metrics.loc[exact200, "ticker"])
    df["primary_beta200_eligible"] = df["ticker"].isin(eligible200)
    base = df["primary_beta200_eligible"] & ~df["ticker"].eq("NVDA")
    if int(base.sum()) < 4500:
        raise RuntimeError(f"primary base sample unexpectedly small: {int(base.sum())}")
    z = standardized(df.loc[base, "core_ai_log1p_per_10k"].astype(float).tolist())
    df["primary_ai_exposure_z"] = np.nan
    df.loc[base, "primary_ai_exposure_z"] = z

    out = OUT / "astra_pre_event_ai_exposure_v1.csv"
    df.sort_values("ticker").to_csv(out, index=False)
    base_vals = df.loc[base, "core_ai_log1p_per_10k"].astype(float)
    qa = {
        "paper_id": PAPER_ID, "version": "ASTRA_AI_EXPOSURE_V1",
        "generated_at_utc": now(), "pre_event_cutoff": CUTOFF,
        "filings_scored": len(df), "primary_base_eligible_non_nvda": int(base.sum()),
        "primary_definition": "z(log1p(core AI lexical mentions per 10,000 full-filing words)); z-score estimated among truthful-fulltext + exact-beta200 + non-NVDA firms",
        "any_core_ai_share_all_fulltext": float(df["any_core_ai"].mean()),
        "zero_core_ai_share_primary_base": float((df.loc[base, "core_ai_mentions"] == 0).mean()),
        "primary_log1p_quantiles": {str(q): float(base_vals.quantile(q)) for q in [0, .25, .5, .75, .9, .95, .99, 1]},
        "business_section_available": int(df["business_section_available"].sum()),
        "risk_section_available": int(df["risk_section_available"].sum()),
        "forms": df["form"].value_counts().to_dict(),
        "aggregate_core_term_counts": aggregate_terms,
        "aggregate_frontier_term_counts": aggregate_frontier,
        "lexicon_core_terms": [name for name, _ in CORE_PATTERNS],
        "lexicon_frontier_terms": [name for name, _ in FRONTIER_PATTERNS],
        "outcome_inspected": "NO",
    }
    (OUT / "exposure_v1_qa.json").write_text(json.dumps(qa, indent=2, sort_keys=True), encoding="utf-8")
    return df, qa


def fetch_sec_master(date: str) -> str:
    compact = date.replace("-", "")
    url = f"https://www.sec.gov/Archives/edgar/daily-index/2026/QTR3/master.{compact}.idx"
    req = urllib.request.Request(url, headers=SEC_HEADERS)
    with urllib.request.urlopen(req, timeout=60) as resp:
        raw = resp.read()
    return raw.decode("latin-1", errors="replace")


def build_current_report_flags(exposure: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, Any]]:
    candidates = pd.read_csv(INP / "candidate_firm_universe.csv", dtype=str, keep_default_na=False)
    included = candidates[candidates["included"].astype(str).str.upper().isin(["TRUE", "Y", "YES", "1"])]
    cik_to_ticker = {str(c).lstrip("0"): t for c, t in zip(included["cik"], included["ticker"]) if str(c).strip()}
    records: list[dict[str, str]] = []
    for date in ["2026-09-03", "2026-09-04"]:
        text = fetch_sec_master(date)
        for line in text.splitlines():
            parts = line.split("|")
            if len(parts) != 5 or not parts[0].strip().isdigit():
                continue
            cik, company, form, filed, filename = [p.strip() for p in parts]
            if form.upper() not in SEC_CURRENT_FORMS:
                continue
            ticker = cik_to_ticker.get(cik.lstrip("0"))
            if not ticker:
                continue
            records.append({"ticker": ticker, "cik": cik, "company": company, "form": form.upper(), "filed": filed, "filename": filename})
    raw = pd.DataFrame(records)
    flags = exposure[["ticker", "cik"]].copy()
    for date, suffix in [("2026-09-03", "sep03"), ("2026-09-04", "sep04")]:
        subset = raw[raw["filed"].eq(date)] if not raw.empty else raw
        grouped = subset.groupby("ticker")["form"].agg(lambda s: ";".join(sorted(set(s)))) if not subset.empty else pd.Series(dtype=str)
        flags[f"current_report_{suffix}"] = flags["ticker"].isin(set(subset["ticker"])) if not subset.empty else False
        flags[f"current_report_forms_{suffix}"] = flags["ticker"].map(grouped).fillna("")
    flags["current_report_sep03_or_sep04"] = flags["current_report_sep03"] | flags["current_report_sep04"]
    flags["outcome_inspected"] = "NO"
    flags.to_csv(OUT / "event_day_sec_current_report_flags_v1.csv", index=False)
    qa = {
        "source": "SEC EDGAR daily master indexes", "dates": ["2026-09-03", "2026-09-04"],
        "forms": sorted(SEC_CURRENT_FORMS), "flagged_unique_firms": int(flags["current_report_sep03_or_sep04"].sum()),
        "raw_matching_filings": len(raw), "use": "secondary clean-news robustness only; daily index has filing date but not a reliable causal-news classification",
        "outcome_inspected": "NO",
    }
    (OUT / "event_day_sec_current_report_flags_v1_qa.json").write_text(json.dumps(qa, indent=2, sort_keys=True), encoding="utf-8")
    return flags, qa


def build_event_design(benchmark_qa: dict[str, Any]) -> dict[str, Any]:
    stored = json.loads((INP / "astra_event_timestamps.json").read_text(encoding="utf-8"))
    launch_meta = stored.get("openai_astra_sep03_2026.html", {})
    if launch_meta.get("date_published") != "2026-09-03T13:15:00Z":
        raise RuntimeError(f"stored launch metadata changed: {launch_meta.get('date_published')}")
    design = {
        "paper_id": PAPER_ID, "version": "ASTRA_EVENT_DESIGN_V1", "generated_at_utc": now(),
        "pre_event_cutoff": CUTOFF,
        "primary_event": {
            "date": PRIMARY_EVENT_DATE,
            "public_timestamp_utc": PRIMARY_EVENT_TIMESTAMP,
            "public_timestamp_basis": "OpenAI public social launch post independently verified before outcome access",
            "public_post_url": "https://x.com/OpenAI/status/2095595742975197690",
            "stored_webpage_date_published_metadata": launch_meta.get("date_published"),
            "timing_rule": "Because webpage metadata and verifiable public communication timing differ and the public social launch occurred late in the regular session, daily-data primary window spans event day and next trading day.",
        },
        "primary_window": {"label": "CAR[0,+1]", "dates": ["2026-09-03", "2026-09-04"]},
        "secondary_timing_outcomes": [
            {"label": "AR[0]", "date": "2026-09-03"},
            {"label": "AR[+1]", "date": "2026-09-04"},
        ],
        "sequence_events": [
            {"date": "2026-08-07", "role": "precursor cyber-capability disclosure", "url": "https://openai.com/index/responding-next-frontier-critical-cyber-capabilities/"},
            {"date": "2026-08-26", "role": "Hugging Face incident / Astra safeguard disclosure", "url": "https://openai.com/index/hugging-face-incident-and-the-road-ahead/"},
            {"date": "2026-09-01", "role": "Astra critical-capability disclosure", "url": "https://openai.com/index/path-to-astra/"},
            {"date": "2026-09-03", "role": "commercial launch", "url": "https://openai.com/index/gpt-6-astra/"},
        ],
        "documentation_event": {"date": "2026-09-06", "status": "not primary and not currently analyzable because Sep 7 is NYSE Labor Day holiday and frozen price panel ends Sep 4"},
        "expected_return": {
            "primary": "SPY market model with latest exactly 200 paired pre-event returns through 2026-08-06",
            "robustness": ["SPY exact 120", "SPY exact 250", "QQQ exact 200 recalculated from frozen pre-event panel", "IWM exact 200 recalculated from frozen pre-event panel"],
            "fama_french_status": "FF3/FF5/MOM retained as background/pre-period resources only; frozen factor files end 2026-07-31 and are not the primary event-day expected-return model",
            "benchmark_certification": benchmark_qa,
        },
        "known_confound_policy": {
            "primary_direct_exclusion": ["NVDA"],
            "reason": "NVIDIA announced its acquisition of Hugging Face on 2026-09-03, a direct same-day firm-specific confound.",
            "include_all_robustness": True,
            "sep01_sequence_direct_exclusions": ["NVDA", "CRWD"],
            "event_day_current_report_rule": "Do not exclude from primary solely for filing an 8-K/6-K; use the frozen Sep3/Sep4 current-report flag as a prespecified clean-news robustness sample.",
            "macro": "Broad Sep3 market rally/Waller monetary-policy news handled by market benchmark; industry fixed effects and benchmark sensitivities address heterogeneous common exposure.",
        },
        "outcome_inspected": "NO",
    }
    (OUT / "event_design_v1.json").write_text(json.dumps(design, indent=2, sort_keys=True), encoding="utf-8")
    confounds = [
        ["2026-09-01", "NVIDIA/CrowdStrike", "SafeMind / agentic cybersecurity collaboration", "NVDA;CRWD", "DIRECT_SEQUENCE_CONFOUND", "https://blogs.nvidia.com/blog/nvidia-crowdstrike-fal-con-2026/"],
        ["2026-09-03", "NVIDIA/Hugging Face", "NVIDIA announced acquisition of Hugging Face for $12.9303B", "NVDA", "DIRECT_PRIMARY_EVENT_CONFOUND", "https://blogs.nvidia.com/blog/nvidia-to-acquire-hugging-face/"],
        ["2026-09-03", "US macro market", "Broad equity rally associated with dovish Waller comments", "MARKET_WIDE", "MACRO_COMMON_SHOCK", "https://www.reuters.com/commentary/reuters-open-interest/global-markets-trading-day-graphic-2026-09-03/"],
    ]
    with (OUT / "concurrent_event_registry_v2.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh); w.writerow(["date", "entity", "event", "affected_tickers", "classification", "source_url", "outcome_inspected"])
        for row in confounds: w.writerow(row + ["NO"])
    return design


def build_power(exposure: pd.DataFrame) -> tuple[dict[str, Any], pd.DataFrame]:
    metrics = pd.read_csv(INP / "firm_pre_event_price_metrics.csv", dtype=str, keep_default_na=False)
    controls = pd.read_csv(INP / "firm_pre_event_controls_complete.csv", dtype=str, keep_default_na=False)
    merged = exposure.merge(metrics[["ticker", "market_beta_200", "beta_200_n", "residual_vol_200"]], on="ticker", how="left").merge(
        controls[["ticker", "market_cap", "profitability_roa", "sic2", "has_market_cap", "has_profitability", "has_sic2", "has_leverage", "has_rd_to_revenue", "has_intangible_assets"]], on="ticker", how="left")
    merged["log_market_cap"] = np.log(_num(merged["market_cap"]))
    merged["roa"] = _num(merged["profitability_roa"])
    merged["residvol"] = _num(merged["residual_vol_200"])
    merged["x"] = _num(merged["primary_ai_exposure_z"])
    adjusted = merged[
        merged["x"].notna() & merged["log_market_cap"].notna() & merged["roa"].notna() & merged["residvol"].notna() & merged["sic2"].astype(str).ne("")
    ].copy()
    n = len(adjusted)
    if n < 4000:
        raise RuntimeError(f"adjusted power sample unexpectedly small: {n}")
    dummies = pd.get_dummies(adjusted["sic2"].astype(str), prefix="sic2", drop_first=True, dtype=float)
    X = np.column_stack([np.ones(n), adjusted["log_market_cap"].to_numpy(float), adjusted["roa"].to_numpy(float), adjusted["residvol"].to_numpy(float), dummies.to_numpy(float)])
    y = adjusted["x"].to_numpy(float)
    coef = np.linalg.lstsq(X, y, rcond=None)[0]
    resid_x = y - X @ coef
    sxx = float(np.sum(resid_x ** 2))
    residual_x_var = float(np.mean(resid_x ** 2))
    if sxx <= 0:
        raise RuntimeError("exposure has no residual variation after baseline controls")
    rv = adjusted["residvol"].to_numpy(float)
    cap99 = float(np.quantile(rv, .99))
    rv_win = np.minimum(rv, cap99)
    daily_rms = float(np.sqrt(np.mean(rv_win ** 2)))
    car2_sigma = math.sqrt(2.0) * daily_rms
    z975, z80, z90 = 1.959963984540054, 0.8416212335729143, 1.2815515655446004
    mde80 = (z975 + z80) * car2_sigma / math.sqrt(sxx)
    mde90 = (z975 + z90) * car2_sigma / math.sqrt(sxx)
    status = "PASS" if mde80 <= .005 else "REPAIR"
    power = {
        "status": status, "method": "Outcome-blind pre-period analytic MDE for one-SD exposure coefficient after residualizing exposure on baseline controls",
        "sample_n": n, "baseline_controls": ["log market cap", "profitability ROA", "residual_vol_200", "SIC2 fixed effects"],
        "exposure_residual_variance": residual_x_var, "exposure_residual_sxx": sxx,
        "daily_residual_vol_winsor99_cap": cap99, "daily_residual_vol_winsor99_rms": daily_rms,
        "two_day_car_noise_sigma": car2_sigma,
        "mde80_return_units": mde80, "mde80_percentage_points": mde80 * 100,
        "mde90_return_units": mde90, "mde90_percentage_points": mde90 * 100,
        "pass_threshold_mde80_return_units": .005,
        "plausible_effect": "Design threshold: ability to detect <=0.50 percentage-point CAR difference per one-SD pre-event AI-engagement exposure at 80% power.",
        "evidence": "Uses only pre-event SPY market-model residual volatility through 2026-08-06 and frozen pre-event covariates; no event outcome accessed.",
        "nested_control_availability_in_adjusted_sample": {
            "strict_leverage": int(_bool(adjusted["has_leverage"]).sum()),
            "rd_to_revenue": int(_bool(adjusted["has_rd_to_revenue"]).sum()),
            "intangibles_any_proxy": int(_bool(adjusted["has_intangible_assets"]).sum()),
        },
        "outcome_inspected": "NO",
    }
    (OUT / "power_mde_v1.json").write_text(json.dumps(power, indent=2, sort_keys=True), encoding="utf-8")
    sample = adjusted[["ticker", "x", "log_market_cap", "roa", "residvol", "sic2"]].copy()
    sample.rename(columns={"x": "primary_ai_exposure_z", "roa": "profitability_roa", "residvol": "residual_vol_200"}, inplace=True)
    sample["outcome_inspected"] = "NO"
    sample.sort_values("ticker").to_csv(OUT / "primary_adjusted_sample_v1.csv", index=False)
    return power, sample


def build_manifest(files: list[str]) -> dict[str, Any]:
    payload = {
        "paper_id": PAPER_ID, "design_inputs": DESIGN_NAME, "generated_at_utc": now(), "git_commit_sha": git_sha(),
        "outcome_inspected": "NO", "source_snapshot": "analysis_ready_data_v3_20260907",
        "source_hashes": {name: sha for name, (_, sha) in SOURCE.items()},
        "files": [{"name": name, "bytes": (OUT / name).stat().st_size, "sha256": sha256_file(OUT / name)} for name in files],
    }
    (OUT / "design_input_manifest.json").write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    return payload


def publish(client: DriveClient, files: list[str]) -> tuple[str, dict[str, str]]:
    root = client.list_folder(STUDY_FOLDER_ID)
    existing = [f for f in root if f.get("name") == DESIGN_NAME and f.get("mimeType") == FOLDER_MIME]
    if existing:
        raise RuntimeError(f"immutable final design folder already exists: {DESIGN_NAME}")
    building_name = f"{DESIGN_NAME}__BUILDING_{uuid.uuid4().hex[:10]}"
    folder_id = client.create_folder(building_name, STUDY_FOLDER_ID)
    remote_ids: dict[str, str] = {}
    try:
        for name in files + ["design_input_manifest.json"]:
            remote_ids[name] = client.upload(str(OUT / name), folder_id, name)
        # Roundtrip every new payload because they are all small.
        verify_dir = WORK / "roundtrip"; verify_dir.mkdir(parents=True, exist_ok=True)
        for name, fid in remote_ids.items():
            expected = sha256_file(OUT / name)
            got = client.download(fid, str(verify_dir / name), expected_sha256=expected)
            if got != expected:
                raise RuntimeError(f"roundtrip hash mismatch {name}")
        _rename(client, folder_id, DESIGN_NAME)
        return folder_id, {name: sha256_file(OUT / name) for name in remote_ids}
    except BaseException:
        raise


def close_design(client: DriveClient, folder_id: str, hashes: dict[str, str], power: dict[str, Any], sample: pd.DataFrame) -> dict[str, Any]:
    if power.get("status") != "PASS":
        raise RuntimeError(f"power gate is not PASS: {power}")
    paper_meta = _one_named(client, STUDY_FOLDER_ID, "paper_state.json")
    director_meta = _one_named(client, STUDY_FOLDER_ID, "director.json")
    paper_path, director_path = WORK / "paper_state.json", WORK / "director.json"
    client.download(paper_meta["id"], str(paper_path)); client.download(director_meta["id"], str(director_path))
    paper = SinglePaperState.load(str(paper_path)); director = DirectorState.load(str(director_path))
    action = ensure_action(paper, director, None)
    if not action or action.kind is not ActionKind.DESIGN_CLOSURE or action.id != "DA-design_closure-2c16848d8c":
        raise RuntimeError(f"unexpected Director action: {action}")
    dataset_hashes = {
        "analysis_ready_data_v3_20260907/firm_pre_event_controls_complete.csv": SOURCE["firm_pre_event_controls_complete.csv"][1],
        "analysis_ready_data_v3_20260907/firm_pre_event_price_metrics.csv": SOURCE["firm_pre_event_price_metrics.csv"][1],
        "analysis_ready_data_v3_20260907/us_equity_daily_prices_20250101_20260904.parquet": SOURCE["us_equity_daily_prices_20250101_20260904.parquet"][1],
        f"{DESIGN_NAME}/astra_pre_event_ai_exposure_v1.csv": hashes["astra_pre_event_ai_exposure_v1.csv"],
        f"{DESIGN_NAME}/event_design_v1.json": hashes["event_design_v1.json"],
        f"{DESIGN_NAME}/event_day_sec_current_report_flags_v1.csv": hashes["event_day_sec_current_report_flags_v1.csv"],
        f"{DESIGN_NAME}/primary_adjusted_sample_v1.csv": hashes["primary_adjusted_sample_v1.csv"],
    }
    answer = {
        "action_id": action.id, "decision": "PASS",
        "estimand": "Cross-sectional coefficient on a one-SD increase in pre-event annual-filing AI engagement for SPY-market-model CAR[0,+1]; interpreted as differential launch-day revaluation association, not a causal effect of AI adoption.",
        "design": "Outcome-blind cross-sectional common-shock U.S.-listed-firm event study with pre-event textual exposure fixed before outcome access.",
        "sample_definition": f"Primary adjusted sample N={len(sample)}: truthful-fulltext 10-K/20-F/40-F issuer, exact 200-paired-return SPY market model through 2026-08-06, nonmissing market cap/ROA/SIC2/residual volatility, excluding NVDA as a known Sep3 direct confound. No outcome-dependent exclusions.",
        "treatment": "primary_ai_exposure_z = z(log1p(core AI lexical mentions per 10,000 words in latest eligible pre-event full annual filing)); standardized among fulltext+exact-beta200+non-NVDA base-eligible firms.",
        "outcome": "Primary: SPY market-model cumulative abnormal return CAR[0,+1] over 2026-09-03 and 2026-09-04. Secondary timing diagnostics: AR[0] and AR[+1].",
        "controls": ["log market capitalization", "profitability ROA", "SIC2 fixed effects", "pre-event residual_vol_200"],
        "exclusions": [
            "NVDA excluded from primary because of the prespecified Sep3 NVIDIA-Hugging Face acquisition confound; include-all robustness restores it.",
            "No primary exclusion based on event-window return magnitude or sign.",
            "Sep3/Sep4 8-K/6-K filing flag used only for prespecified clean-news robustness, not automatic primary exclusion.",
            "Leverage, R&D/revenue and intangibles enter nested robustness samples only; no imputation and no forced complete-case primary sample.",
        ],
        "window_start": "2026-09-03", "window_end": "2026-09-04", "pre_period_end": CUTOFF,
        "models": [
            "PRIMARY: cross-sectional OLS of SPY200 CAR[0,+1] on primary_ai_exposure_z + log market cap + ROA + residual_vol_200 + SIC2 FE; HC3 standard errors.",
            "Inference sensitivity: SIC2-clustered standard errors where cluster count/support is adequate.",
            "Expected-return sensitivity: SPY exact-120 and exact-250; QQQ exact-200; IWM exact-200, all pre-event estimated.",
            "Timing sensitivity: AR[0] and AR[+1] separately.",
            "Exposure sensitivity: any-core-AI indicator; full-filing frontier/generative intensity; business-section and risk-section intensities where available.",
            "Confound sensitivity: include-all sample and clean-news sample excluding firms with frozen Sep3/Sep4 SEC current-report flags.",
            "Nested firm-characteristic heterogeneity: strict leverage, R&D/revenue, and intangibles without imputation; each reports its own N.",
            "Sequence sensitivities: Aug7, Aug26, Sep1 and Sep3 disclosures using the same Aug6-frozen expected-return model; Sep1 excludes NVDA and CRWD for direct concurrent collaboration news.",
        ],
        "primary_contrasts": [
            "Two-sided primary coefficient on primary_ai_exposure_z in the SPY200 CAR[0,+1] model.",
            "Secondary timing decomposition AR[0] vs AR[+1].",
            "Secondary opportunity/frontier exposure variants and sequence-event coefficients.",
        ],
        "multiplicity_policy": "One confirmatory primary exposure coefficient at two-sided alpha=0.05. Secondary exposure/timing/robustness coefficient family controlled by Benjamini-Hochberg FDR q=0.10; sequence-event family separately BH-FDR q=0.10. Robustness models are not promoted to primary based on significance.",
        "dataset_hashes": dataset_hashes,
        "power": {
            "status": "PASS", "method": power["method"], "plausible_effect": power["plausible_effect"],
            "mde": f"80% MDE={power['mde80_percentage_points']:.4f} percentage points and 90% MDE={power['mde90_percentage_points']:.4f} percentage points per one-SD exposure in the prespecified adjusted sample.",
            "evidence": power["evidence"],
        },
    }
    result = apply_answer(paper, director, answer, None)
    if result != "DESIGN_READY" or paper.stage is not PaperStage.DESIGN_READY:
        raise RuntimeError(f"Director did not reach DESIGN_READY: {result}/{paper.stage}")
    next_action = ensure_action(paper, director, None)
    if not next_action or next_action.kind is not ActionKind.PRE_FREEZE_AUDIT:
        raise RuntimeError(f"expected PRE_FREEZE_AUDIT after design closure, got {next_action}")
    paper.save(); director.save(); _patch_json(client, paper_meta["id"], paper_path); _patch_json(client, director_meta["id"], director_path)
    return {"stage": paper.stage.value, "design_folder_id": folder_id, "pending_action_id": next_action.id, "pending_action_kind": next_action.kind.value, "outcome_inspected": "NO"}


def main() -> int:
    if WORK.exists(): shutil.rmtree(WORK)
    INP.mkdir(parents=True); OUT.mkdir(parents=True)
    client = DriveClient(DriveCredentials.from_env())
    download_sources(client)
    benchmark_qa = certify_benchmarks()
    exposure, exposure_qa = build_exposure()
    flags, flags_qa = build_current_report_flags(exposure)
    event_design = build_event_design(benchmark_qa)
    power, sample = build_power(exposure)
    if power["status"] != "PASS":
        raise RuntimeError(f"outcome-blind design power gate requires repair: {power}")
    files = [
        "astra_pre_event_ai_exposure_v1.csv", "exposure_v1_qa.json",
        "event_day_sec_current_report_flags_v1.csv", "event_day_sec_current_report_flags_v1_qa.json",
        "event_design_v1.json", "concurrent_event_registry_v2.csv",
        "power_mde_v1.json", "primary_adjusted_sample_v1.csv",
    ]
    manifest = build_manifest(files)
    folder_id, hashes = publish(client, files)
    state = close_design(client, folder_id, hashes, power, sample)
    print(json.dumps({"status": "PASS", "design_inputs": DESIGN_NAME, "manifest": manifest, "exposure_qa": exposure_qa, "event_flags_qa": flags_qa, "power": power, "state": state}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
