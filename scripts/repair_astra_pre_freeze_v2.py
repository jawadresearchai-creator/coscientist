"""Repair Astra pre-freeze design defects without accessing Astra outcomes.

V1 remains immutable. This script:
1. records the hostile PRE_FREEZE_AUDIT as REPAIR locally;
2. fixes EDGAR current-report joins with normalized CIKs;
3. constructs an Astra-specific frontier/generative exposure candidate;
4. estimates empirical MDE from common pre-event placebo windows;
5. applies an outcome-blind promotion rule for the primary exposure;
6. publishes a new immutable design_inputs_v2_20260907 folder;
7. replaces only the paper-local pre-freeze design record, preserving V1 history;
8. leaves a fresh PRE_FREEZE_AUDIT pending.

No Sep-3/Sep-4 firm return, abnormal return, CAR, sign, rank, winner/loser label,
or other Astra event-window outcome is calculated or read.
"""
from __future__ import annotations

import csv
import hashlib
import json
import math
import os
import shutil
import subprocess
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pyarrow.parquet as pq

from coscientist.astra_design_repair import (
    choose_primary_exposure,
    empirical_mde,
    normalize_cik,
    parse_sec_master_current_reports,
    placebo_coefficients,
    residualized_exposure,
)
from coscientist.astra_exposure import standardized
from coscientist.director import ActionKind, DirectorState, apply_answer, ensure_action
from coscientist.drive import API, DriveClient, DriveCredentials, FOLDER_MIME
from coscientist.sec_corpus import SEC_HEADERS
from coscientist.single_paper import PaperStage, SinglePaperState

PAPER_ID = "MS-ASTRA-REVALUE-2026"
STUDY_FOLDER_ID = "1yx34Mj-k7SsGSuPli8UVuR_E639eeLcb"
DESIGN_V1_FOLDER_ID = "1I8q0Oxo-8FIxLjGSTlG44p4x36pfhXGR"
DESIGN_V2 = "design_inputs_v2_20260907"
CUTOFF = "2026-08-06"
WORK = Path(".astra_pre_freeze_v2")
INP = WORK / "inputs"
OUT = WORK / "outputs"
SEC_CURRENT_FORMS = {"8-K", "8-K/A", "6-K", "6-K/A"}

SOURCE = {
    "astra_pre_event_ai_exposure_v1.csv": (
        "15kIjDYtWgCNFgWIBXBz3X-XxQs8i0-8H",
        "ce551cfdbd0a752032e4d0f513a2dee8f40a8e3d472421192905b7970e30b027",
    ),
    "event_design_v1.json": (
        "1E9BSXmZ67G5TVTFDVbqLZozlaN7ejhLI",
        "829becaf3cc3ac097941a703c1d79d5d5389b613ed2395a7d462ded99725bfca",
    ),
    "firm_pre_event_price_metrics.csv": (
        "1Q1gW5E8SY-ZlFXHyp7yBwMJ20mSZH5cm",
        "5dd34481bcff8df2ad37046f4aa84e9dcd95f05ea4714ac1c138990d24db1ec9",
    ),
    "firm_pre_event_controls_complete.csv": (
        "16rBa_uX1Oyb6TeCbw0yiUWMljzXcd3Uj",
        "3f4cf546abdbbe883fbded63743c2f731ebb86ed2df27b50068fddf2c0beae25",
    ),
    "us_equity_daily_prices_20250101_20260904.parquet": (
        "1yT2sg3oW9reAgEABhrx2c1eYsI5Ea_Xi",
        "9d3037916c2a6f25d3188af424be5b83bc185a0575fb6330742e65bbf441d7ed",
    ),
}


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


def _num(s: pd.Series) -> pd.Series:
    return pd.to_numeric(s.replace("", np.nan), errors="coerce")


def _bool(s: pd.Series) -> pd.Series:
    return s.astype(str).str.upper().isin(["TRUE", "Y", "YES", "1"])


def _one_named(client: DriveClient, folder_id: str, name: str) -> dict[str, Any]:
    hits = [f for f in client.list_folder(folder_id) if f.get("name") == name]
    if len(hits) != 1:
        raise RuntimeError(f"expected exactly one {name!r} under {folder_id}, found {len(hits)}")
    return hits[0]


def _patch_json(client: DriveClient, file_id: str, path: Path) -> None:
    payload = path.read_bytes()
    req = urllib.request.Request(
        f"https://www.googleapis.com/upload/drive/v3/files/{file_id}?uploadType=media&fields=id",
        data=payload,
        method="PATCH",
        headers={
            "Authorization": f"Bearer {client.token()}",
            "Content-Type": "application/json",
            "Content-Length": str(len(payload)),
        },
    )
    with client.opener(req, timeout=120) as resp:
        resp.read()


def _rename(client: DriveClient, file_id: str, name: str) -> None:
    payload = json.dumps({"name": name}).encode()
    req = urllib.request.Request(
        f"{API}/files/{file_id}?fields=id,name",
        data=payload,
        method="PATCH",
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
            raise RuntimeError(f"source hash mismatch: {name}")


def fetch_sec_master(date: str) -> str:
    compact = date.replace("-", "")
    url = f"https://www.sec.gov/Archives/edgar/daily-index/2026/QTR3/master.{compact}.idx"
    req = urllib.request.Request(url, headers=SEC_HEADERS)
    with urllib.request.urlopen(req, timeout=60) as resp:
        return resp.read().decode("latin-1", errors="replace")


def corrected_current_report_flags(exposure: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, Any]]:
    cik_to_ticker: dict[str, str] = {}
    for cik, ticker in zip(exposure["cik"], exposure["ticker"]):
        norm = normalize_cik(cik)
        if norm:
            cik_to_ticker[norm] = str(ticker)

    records: list[dict[str, str]] = []
    raw_by_date: dict[str, int] = {}
    for date in ["2026-09-03", "2026-09-04"]:
        parsed = parse_sec_master_current_reports(fetch_sec_master(date), SEC_CURRENT_FORMS)
        raw_by_date[date] = int(len(parsed))
        if parsed.empty:
            continue
        for row in parsed.to_dict("records"):
            ticker = cik_to_ticker.get(str(row["cik_norm"]), "")
            if not ticker:
                continue
            records.append({
                "ticker": ticker,
                "cik": row["cik"],
                "company": row["company"],
                "form": row["form"],
                "filed": row["filed"],
                "filename": row["filename"],
            })
    raw = pd.DataFrame(records)
    if raw.empty:
        raise RuntimeError(f"corrected EDGAR join still produced zero candidate current reports; raw master counts={raw_by_date}")

    flags = exposure[["ticker", "cik"]].copy()
    for date, suffix in [("2026-09-03", "sep03"), ("2026-09-04", "sep04")]:
        subset = raw.loc[raw["filed"].eq(date)]
        grouped = subset.groupby("ticker")["form"].agg(lambda s: ";".join(sorted(set(s)))) if not subset.empty else pd.Series(dtype=str)
        flags[f"current_report_{suffix}"] = flags["ticker"].isin(set(subset["ticker"]))
        flags[f"current_report_forms_{suffix}"] = flags["ticker"].map(grouped).fillna("")
    flags["current_report_sep03_or_sep04"] = flags["current_report_sep03"] | flags["current_report_sep04"]
    flags["outcome_inspected"] = "NO"
    flagged = int(flags["current_report_sep03_or_sep04"].sum())
    if flagged < 10:
        raise RuntimeError(f"corrected EDGAR join implausibly flags only {flagged} firms")

    checks: dict[str, Any] = {}
    for cik, date, label in [("1397187", "2026-09-03", "LULU"), ("802481", "2026-09-04", "PPC")]:
        ticker = cik_to_ticker.get(cik)
        if ticker:
            suffix = "sep03" if date.endswith("03") else "sep04"
            hit = bool(flags.loc[flags["ticker"].eq(ticker), f"current_report_{suffix}"].iloc[0])
            checks[label] = {"ticker": ticker, "date": date, "flagged": hit}
            if not hit:
                raise RuntimeError(f"known SEC current report did not survive corrected join: {label}/{ticker}/{date}")

    flags.sort_values("ticker").to_csv(OUT / "event_day_sec_current_report_flags_v2.csv", index=False)
    qa = {
        "status": "PASS",
        "source": "SEC EDGAR daily master indexes",
        "dates": ["2026-09-03", "2026-09-04"],
        "forms": sorted(SEC_CURRENT_FORMS),
        "raw_master_current_reports_by_date": raw_by_date,
        "candidate_matching_filings": int(len(raw)),
        "flagged_unique_firms": flagged,
        "known_filing_checks": checks,
        "repair": "CIKs normalized before EDGAR-to-design-universe join; V1 false-zero artifact superseded, not overwritten.",
        "use": "prespecified clean-news robustness only; filing presence is not a causal-news classification and does not trigger primary exclusion",
        "outcome_inspected": "NO",
    }
    (OUT / "event_day_sec_current_report_flags_v2_qa.json").write_text(json.dumps(qa, indent=2, sort_keys=True), encoding="utf-8")
    return flags, qa


def build_exposure_v2(exposure: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, Any]]:
    required = {"ticker", "primary_beta200_eligible", "primary_ai_exposure_z", "frontier_ai_per_10k_words"}
    missing = required - set(exposure.columns)
    if missing:
        raise RuntimeError(f"V1 exposure missing V2 prerequisites: {sorted(missing)}")
    out = exposure.copy()
    base = _bool(out["primary_beta200_eligible"]) & ~out["ticker"].eq("NVDA")
    frontier_rate = _num(out["frontier_ai_per_10k_words"]).fillna(0.0)
    out["frontier_ai_log1p_per_10k"] = np.log1p(frontier_rate.clip(lower=0))
    out["frontier_ai_exposure_z"] = np.nan
    out.loc[base, "frontier_ai_exposure_z"] = standardized(out.loc[base, "frontier_ai_log1p_per_10k"].astype(float).tolist())
    nonzero_share = float((frontier_rate.loc[base] > 0).mean())
    qa = {
        "status": "PASS",
        "base_n": int(base.sum()),
        "frontier_nonzero_share": nonzero_share,
        "frontier_log1p_quantiles": {str(q): float(out.loc[base, "frontier_ai_log1p_per_10k"].quantile(q)) for q in [0, .25, .5, .75, .9, .95, .99, 1]},
        "promotion_min_nonzero_share": 0.25,
        "outcome_inspected": "NO",
    }
    return out, qa


def adjusted_sample(exposure: pd.DataFrame, metrics: pd.DataFrame, controls: pd.DataFrame) -> pd.DataFrame:
    merged = exposure.merge(
        metrics[["ticker", "market_beta_200", "alpha_200", "beta_200_n", "residual_vol_200"]], on="ticker", how="left"
    ).merge(
        controls[["ticker", "market_cap", "profitability_roa", "sic2"]], on="ticker", how="left"
    )
    merged["log_market_cap"] = np.log(_num(merged["market_cap"]))
    merged["roa"] = _num(merged["profitability_roa"])
    merged["residvol"] = _num(merged["residual_vol_200"])
    merged["beta"] = _num(merged["market_beta_200"])
    merged["alpha"] = _num(merged["alpha_200"])
    merged["broad"] = _num(merged["primary_ai_exposure_z"])
    merged["frontier"] = _num(merged["frontier_ai_exposure_z"])
    use = merged[
        merged["broad"].notna() & merged["frontier"].notna() & merged["log_market_cap"].notna() &
        merged["roa"].notna() & merged["residvol"].notna() & merged["beta"].notna() & merged["alpha"].notna() &
        _num(merged["beta_200_n"]).eq(200) & merged["sic2"].astype(str).ne("") & ~merged["ticker"].eq("NVDA")
    ].copy()
    if len(use) < 4000:
        raise RuntimeError(f"adjusted V2 design sample unexpectedly small: {len(use)}")
    return use


def _control_matrix(df: pd.DataFrame) -> np.ndarray:
    dummies = pd.get_dummies(df["sic2"].astype(str), prefix="sic2", drop_first=True, dtype=float)
    return np.column_stack([
        np.ones(len(df)),
        df["log_market_cap"].to_numpy(float),
        df["roa"].to_numpy(float),
        df["residvol"].to_numpy(float),
        dummies.to_numpy(float),
    ])


def empirical_placebo_power(sample: pd.DataFrame) -> tuple[dict[str, Any], pd.DataFrame, pd.DataFrame]:
    tickers = sorted(set(sample["ticker"].astype(str)))
    read_tickers = tickers + ["SPY"]
    schema = pq.read_schema(INP / "us_equity_daily_prices_20250101_20260904.parquet")
    price_col = "Adj Close" if "Adj Close" in schema.names else "Close"
    table = pq.read_table(
        INP / "us_equity_daily_prices_20250101_20260904.parquet",
        columns=["Date", "Ticker", price_col],
        filters=[("Ticker", "in", read_tickers), ("Date", "<=", CUTOFF)],
    )
    px = table.to_pandas()
    px["Date"] = pd.to_datetime(px["Date"], errors="coerce")
    px[price_col] = pd.to_numeric(px[price_col], errors="coerce")
    px = px.dropna(subset=["Date", price_col]).drop_duplicates(["Ticker", "Date"], keep="last")
    wide = px.pivot(index="Date", columns="Ticker", values=price_col).sort_index()
    if "SPY" not in wide.columns:
        raise RuntimeError("SPY missing from pre-event placebo panel")
    rets = wide.pct_change(fill_method=None).replace([np.inf, -np.inf], np.nan)
    spy = rets["SPY"]

    by_ticker = sample.set_index("ticker")
    available = [t for t in tickers if t in rets.columns]
    firm = rets[available]
    alpha = by_ticker.loc[available, "alpha"].astype(float)
    beta = by_ticker.loc[available, "beta"].astype(float)
    ar = firm.sub(alpha, axis=1).sub(spy.to_numpy()[:, None] * beta.to_numpy()[None, :], axis=0)
    # Deterministic adaptive pre-event calibration: use the longest of 100/90/80/70/60
    # recent common trading days that retains >=3,500 complete firms.
    common_dates = list(ar.index[spy.notna()])
    selected_dates: list[pd.Timestamp] | None = None
    balanced_tickers: list[str] = []
    for n_dates in [100, 90, 80, 70, 60]:
        dates = common_dates[-n_dates:]
        block = ar.loc[dates]
        good = list(block.columns[block.notna().all(axis=0)])
        if len(good) >= 3500:
            selected_dates, balanced_tickers = dates, good
            break
    if selected_dates is None:
        raise RuntimeError("could not construct >=3500-firm balanced pre-event placebo panel on 60-100 recent days")

    bal = sample.set_index("ticker").loc[balanced_tickers].copy()
    X = _control_matrix(bal)
    rx_broad, sxx_broad = residualized_exposure(bal["broad"].to_numpy(float), X)
    rx_frontier, sxx_frontier = residualized_exposure(bal["frontier"].to_numpy(float), X)
    ar_bal = ar.loc[selected_dates, balanced_tickers].T.to_numpy(float)
    if ar_bal.shape[1] % 2:
        ar_bal = ar_bal[:, 1:]
        selected_dates = selected_dates[1:]
    placebo_car = ar_bal[:, 0::2] + ar_bal[:, 1::2]
    broad_b = placebo_coefficients(rx_broad, sxx_broad, placebo_car)
    frontier_b = placebo_coefficients(rx_frontier, sxx_frontier, placebo_car)
    broad_mde = empirical_mde(broad_b)
    frontier_mde = empirical_mde(frontier_b)

    coeff = pd.DataFrame({
        "window": np.arange(1, len(broad_b) + 1),
        "date_1": [str(pd.Timestamp(selected_dates[i]).date()) for i in range(0, len(selected_dates), 2)],
        "date_2": [str(pd.Timestamp(selected_dates[i]).date()) for i in range(1, len(selected_dates), 2)],
        "broad_ai_coefficient": broad_b,
        "frontier_ai_coefficient": frontier_b,
        "outcome_inspected": "NO",
    })
    coeff.to_csv(OUT / "placebo_coefficients_v2.csv", index=False)
    result = {
        "status": "PASS",
        "method": "Empirical pre-event common-date placebo-window MDE using fixed SPY200 market-model residuals and the same cross-sectional controls; common pseudo-event columns preserve cross-firm dependence.",
        "balanced_sample_n": int(len(bal)),
        "placebo_trading_days": int(len(selected_dates)),
        "placebo_windows": int(len(broad_b)),
        "first_placebo_date": str(pd.Timestamp(selected_dates[0]).date()),
        "last_placebo_date": str(pd.Timestamp(selected_dates[-1]).date()),
        "broad": {
            "coefficient_sd": broad_mde.coefficient_sd,
            "mde80_return_units": broad_mde.mde80,
            "mde80_percentage_points": broad_mde.mde80 * 100,
            "mde90_return_units": broad_mde.mde90,
            "mde90_percentage_points": broad_mde.mde90 * 100,
            "placebo_abs_p95": broad_mde.placebo_abs_p95,
        },
        "frontier": {
            "coefficient_sd": frontier_mde.coefficient_sd,
            "mde80_return_units": frontier_mde.mde80,
            "mde80_percentage_points": frontier_mde.mde80 * 100,
            "mde90_return_units": frontier_mde.mde90,
            "mde90_percentage_points": frontier_mde.mde90 * 100,
            "placebo_abs_p95": frontier_mde.placebo_abs_p95,
        },
        "pass_threshold_mde80_return_units": 0.005,
        "outcome_inspected": "NO",
    }
    (OUT / "placebo_power_v2.json").write_text(json.dumps(result, indent=2, sort_keys=True), encoding="utf-8")
    return result, bal.reset_index(), coeff


def build_event_design_v2(v1: dict[str, Any], chosen: str, flags_qa: dict[str, Any], power: dict[str, Any]) -> dict[str, Any]:
    d = json.loads(json.dumps(v1))
    d["version"] = "ASTRA_EVENT_DESIGN_V2"
    d["generated_at_utc"] = now()
    d["primary_exposure"] = {
        "column": chosen,
        "promotion_rule": "Before outcome access, use frontier_ai_exposure_z when frontier mention nonzero share >=25% and empirical placebo MDE80 <=0.50pp; otherwise retain broad primary_ai_exposure_z only if its empirical MDE80 <=0.50pp.",
        "frontier_definition": "z(log1p(frontier/generative AI lexical mentions per 10,000 words in latest eligible pre-event annual filing))",
        "broad_definition": "z(log1p(core AI lexical mentions per 10,000 words in latest eligible pre-event annual filing))",
    }
    d["known_confound_policy"]["event_day_current_report_rule"] = (
        "Corrected CIK-normalized Sep3/Sep4 EDGAR 8-K/6-K flags are used only for prespecified clean-news robustness; no automatic primary exclusion."
    )
    d["current_report_flag_qa"] = flags_qa
    d["power"] = power
    d["outcome_inspected"] = "NO"
    (OUT / "event_design_v2.json").write_text(json.dumps(d, indent=2, sort_keys=True), encoding="utf-8")
    return d


def publish(client: DriveClient, files: list[str]) -> tuple[str, dict[str, str]]:
    root = client.list_folder(STUDY_FOLDER_ID)
    finals = [f for f in root if f.get("name") == DESIGN_V2 and f.get("mimeType") == FOLDER_MIME]
    if finals:
        raise RuntimeError(f"immutable {DESIGN_V2} already exists")
    folder_id = client.create_folder(f"{DESIGN_V2}__BUILDING_{uuid.uuid4().hex[:10]}", STUDY_FOLDER_ID)
    ids: dict[str, str] = {}
    for name in files:
        ids[name] = client.upload(str(OUT / name), folder_id, name)
    verify = WORK / "roundtrip"; verify.mkdir(parents=True, exist_ok=True)
    hashes: dict[str, str] = {}
    for name, fid in ids.items():
        expected = sha256_file(OUT / name)
        got = client.download(fid, str(verify / name), expected_sha256=expected)
        if got != expected:
            raise RuntimeError(f"roundtrip mismatch: {name}")
        hashes[name] = expected
    _rename(client, folder_id, DESIGN_V2)
    return folder_id, hashes


def update_design_state(client: DriveClient, folder_id: str, hashes: dict[str, str], chosen: str,
                        power: dict[str, Any], exposure_qa: dict[str, Any], flags_qa: dict[str, Any],
                        adjusted_n: int) -> dict[str, Any]:
    paper_meta = _one_named(client, STUDY_FOLDER_ID, "paper_state.json")
    director_meta = _one_named(client, STUDY_FOLDER_ID, "director.json")
    paper_path, director_path = WORK / "paper_state.json", WORK / "director.json"
    client.download(paper_meta["id"], str(paper_path)); client.download(director_meta["id"], str(director_path))
    paper = SinglePaperState.load(str(paper_path)); director = DirectorState.load(str(director_path))
    action = ensure_action(paper, director, None)
    if paper.stage is not PaperStage.DESIGN_READY or not action or action.kind is not ActionKind.PRE_FREEZE_AUDIT:
        raise RuntimeError(f"expected DESIGN_READY/PRE_FREEZE_AUDIT, got {paper.stage}/{action}")

    audit_answer = {
        "action_id": action.id,
        "decision": "REPAIR",
        "findings": [
            "V1 Sep3/Sep4 EDGAR current-report robustness artifact was false-zero and contradicted known SEC 8-K filings.",
            "V1 analytic MDE treated firm residual noise too independently for a single common event date and did not preserve cross-firm dependence.",
            "V1 broad AI lexical exposure is close to prior ChatGPT/10-K AI-engagement event-study measurement, so an Astra-specific frontier/generative exposure requires a pre-outcome adequacy gate.",
        ],
        "repair_summary": "Build immutable design V2 with CIK-normalized event-day SEC flags, empirical common-date pre-event placebo power, and a pre-outcome frontier-exposure promotion gate; keep V1 immutable and do not inspect Astra outcomes.",
        "novelty_closure": "REPAIR",
        "measurement": "REPAIR",
        "identification": "PASS subject to corrected confound robustness",
        "power": "REPAIR",
        "access_licence_ethics": "PASS",
    }
    result = apply_answer(paper, director, audit_answer, None)
    if result != "REPAIR":
        raise RuntimeError(f"pre-freeze audit repair did not apply: {result}")

    old_design = json.loads(json.dumps(director.records.get("design_closure", {})))
    if old_design.get("decision") != "PASS":
        raise RuntimeError("cannot repair V2 without a PASS V1 design closure record")
    director.records["design_closure_v1_pre_audit_repair"] = {"updated_at": now(), **old_design}

    chosen_label = "frontier/generative AI filing intensity" if chosen == "frontier_ai_exposure_z" else "broad AI filing intensity"
    mde = power["frontier" if chosen == "frontier_ai_exposure_z" else "broad"]
    new_design = json.loads(json.dumps(old_design))
    new_design.update({
        "decision": "PASS",
        "design_version": "ASTRA_EVENT_DESIGN_V2",
        "sample_definition": f"Primary adjusted sample N={adjusted_n}: truthful-fulltext annual-filing issuer, exact SPY200 pre-event model, nonmissing baseline controls, NVDA excluded for known Sep3 direct confound; no outcome-dependent exclusions.",
        "treatment": f"{chosen} = {chosen_label}; selected by a pre-outcome rule based only on prevalence and empirical pre-event placebo power.",
        "dataset_hashes": {
            "analysis_ready_data_v3_20260907/firm_pre_event_controls_complete.csv": SOURCE["firm_pre_event_controls_complete.csv"][1],
            "analysis_ready_data_v3_20260907/firm_pre_event_price_metrics.csv": SOURCE["firm_pre_event_price_metrics.csv"][1],
            "analysis_ready_data_v3_20260907/us_equity_daily_prices_20250101_20260904.parquet": SOURCE["us_equity_daily_prices_20250101_20260904.parquet"][1],
            f"{DESIGN_V2}/astra_pre_event_ai_exposure_v2.csv": hashes["astra_pre_event_ai_exposure_v2.csv"],
            f"{DESIGN_V2}/event_day_sec_current_report_flags_v2.csv": hashes["event_day_sec_current_report_flags_v2.csv"],
            f"{DESIGN_V2}/event_design_v2.json": hashes["event_design_v2.json"],
            f"{DESIGN_V2}/primary_adjusted_sample_v2.csv": hashes["primary_adjusted_sample_v2.csv"],
            f"{DESIGN_V2}/placebo_power_v2.json": hashes["placebo_power_v2.json"],
        },
        "power": {
            "status": "PASS",
            "method": power["method"],
            "plausible_effect": "Pre-outcome design threshold: empirical 80% MDE <=0.50 percentage points per one-SD selected filing exposure.",
            "mde": f"Empirical placebo MDE80={mde['mde80_percentage_points']:.4f}pp; MDE90={mde['mde90_percentage_points']:.4f}pp per one-SD selected exposure.",
            "evidence": f"{power['placebo_windows']} non-overlapping common pre-event two-day placebo windows; balanced N={power['balanced_sample_n']}; Astra outcomes not accessed.",
        },
    })
    # Update only wording that depended on V1 artifacts; preserve timing, estimand and multiplicity.
    new_design["models"] = [
        str(x).replace("frozen Sep3/Sep4 SEC current-report flags", "corrected CIK-normalized Sep3/Sep4 SEC current-report flags")
        for x in new_design.get("models", [])
    ]
    director.records["design_closure"] = {"updated_at": now(), **new_design}
    director.records["design_repair_v2"] = {
        "updated_at": now(),
        "status": "PASS",
        "design_folder_id": folder_id,
        "chosen_primary_exposure": chosen,
        "frontier_nonzero_share": exposure_qa["frontier_nonzero_share"],
        "corrected_event_day_flagged_firms": flags_qa["flagged_unique_firms"],
        "empirical_placebo_windows": power["placebo_windows"],
        "empirical_mde80_percentage_points": mde["mde80_percentage_points"],
        "outcome_inspected": "NO",
    }
    paper.active_paper.primary_exposure = new_design["treatment"]
    paper.active_paper.intended_design = str(new_design["design"])
    paper.active_paper.primary_outcome = str(new_design["outcome"])
    paper.save(); director.save()
    next_action = ensure_action(paper, director, None)
    if not next_action or next_action.kind is not ActionKind.PRE_FREEZE_AUDIT:
        raise RuntimeError(f"expected fresh PRE_FREEZE_AUDIT after V2 repair, got {next_action}")
    paper.save(); director.save()
    _patch_json(client, paper_meta["id"], paper_path); _patch_json(client, director_meta["id"], director_path)
    return {
        "stage": paper.stage.value,
        "design_folder_id": folder_id,
        "pending_action_id": next_action.id,
        "pending_action_kind": next_action.kind.value,
        "chosen_primary_exposure": chosen,
        "outcome_inspected": "NO",
    }


def main() -> int:
    if WORK.exists():
        shutil.rmtree(WORK)
    INP.mkdir(parents=True); OUT.mkdir(parents=True)
    client = DriveClient(DriveCredentials.from_env())
    download_sources(client)
    exposure_v1 = pd.read_csv(INP / "astra_pre_event_ai_exposure_v1.csv", dtype=str, keep_default_na=False)
    metrics = pd.read_csv(INP / "firm_pre_event_price_metrics.csv", dtype=str, keep_default_na=False)
    controls = pd.read_csv(INP / "firm_pre_event_controls_complete.csv", dtype=str, keep_default_na=False)
    event_v1 = json.loads((INP / "event_design_v1.json").read_text(encoding="utf-8"))

    exposure_v2, exposure_qa = build_exposure_v2(exposure_v1)
    flags, flags_qa = corrected_current_report_flags(exposure_v2)
    sample = adjusted_sample(exposure_v2, metrics, controls)
    power, balanced, coeff = empirical_placebo_power(sample)
    chosen = choose_primary_exposure(
        frontier_nonzero_share=exposure_qa["frontier_nonzero_share"],
        frontier_mde80=power["frontier"]["mde80_return_units"],
        broad_mde80=power["broad"]["mde80_return_units"],
    )
    if chosen == "POWER_REPAIR_REQUIRED":
        raise RuntimeError(f"empirical placebo power fails closed for both exposure definitions: {power}")
    exposure_v2["chosen_primary_exposure"] = chosen
    exposure_v2["outcome_inspected"] = "NO"
    exposure_v2.sort_values("ticker").to_csv(OUT / "astra_pre_event_ai_exposure_v2.csv", index=False)

    chosen_col = "frontier" if chosen == "frontier_ai_exposure_z" else "broad"
    primary = sample[["ticker", chosen_col, "log_market_cap", "roa", "residvol", "sic2"]].copy()
    primary.rename(columns={chosen_col: chosen, "roa": "profitability_roa", "residvol": "residual_vol_200"}, inplace=True)
    primary["outcome_inspected"] = "NO"
    primary.sort_values("ticker").to_csv(OUT / "primary_adjusted_sample_v2.csv", index=False)
    event_v2 = build_event_design_v2(event_v1, chosen, flags_qa, power)

    repair = {
        "status": "PASS",
        "paper_id": PAPER_ID,
        "generated_at_utc": now(),
        "git_commit_sha": git_sha(),
        "parent_design": "design_inputs_v1_20260907",
        "parent_design_folder_id": DESIGN_V1_FOLDER_ID,
        "repairs": [
            "CIK_NORMALIZED_EVENT_DAY_SEC_CURRENT_REPORT_FLAGS",
            "EMPIRICAL_COMMON_DATE_PRE_EVENT_PLACEBO_POWER",
            "ASTRA_SPECIFIC_FRONTIER_EXPOSURE_PRE_OUTCOME_PROMOTION_GATE",
        ],
        "chosen_primary_exposure": chosen,
        "frontier_nonzero_share": exposure_qa["frontier_nonzero_share"],
        "corrected_event_day_flagged_firms": flags_qa["flagged_unique_firms"],
        "placebo_windows": power["placebo_windows"],
        "outcome_inspected": "NO",
        "declaration": "Astra event-window firm returns, abnormal returns, CARs and outcome classifications were not read, calculated or summarized during pre-freeze V2 repair.",
    }
    (OUT / "pre_freeze_repair_v2.json").write_text(json.dumps(repair, indent=2, sort_keys=True), encoding="utf-8")

    files = [
        "astra_pre_event_ai_exposure_v2.csv",
        "event_day_sec_current_report_flags_v2.csv",
        "event_day_sec_current_report_flags_v2_qa.json",
        "placebo_power_v2.json",
        "placebo_coefficients_v2.csv",
        "primary_adjusted_sample_v2.csv",
        "event_design_v2.json",
        "pre_freeze_repair_v2.json",
    ]
    manifest = {
        "paper_id": PAPER_ID,
        "design_inputs": DESIGN_V2,
        "generated_at_utc": now(),
        "git_commit_sha": git_sha(),
        "outcome_inspected": "NO",
        "source_hashes": {name: sha for name, (_, sha) in SOURCE.items()},
        "files": [{"name": n, "bytes": (OUT / n).stat().st_size, "sha256": sha256_file(OUT / n)} for n in files],
    }
    (OUT / "design_input_manifest_v2.json").write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
    files.append("design_input_manifest_v2.json")
    folder_id, hashes = publish(client, files)
    state = update_design_state(client, folder_id, hashes, chosen, power, exposure_qa, flags_qa, len(sample))
    print(json.dumps({
        "status": "PASS",
        "design_inputs": DESIGN_V2,
        "manifest": manifest,
        "exposure_qa": exposure_qa,
        "event_flags_qa": flags_qa,
        "power": power,
        "state": state,
        "outcome_inspected": "NO",
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
