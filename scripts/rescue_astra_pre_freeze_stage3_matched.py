"""Stage3 outcome-blind Astra matched-contrast power rescue.

Scientific boundary:
- acquires only 2023-01-01..2024-12-31 historical prices;
- never reads Astra event-window outcomes;
- uses 2024H1 only for candidate calibration;
- selects one candidate in a frozen order;
- validates that exact candidate once on untouched 2024H2;
- publishes Design V2 / patches paper-local state only after validation passes.

The frozen specification is ops/astra_stage3_estimand_rescue_spec.json.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import shutil
import subprocess
import time
import uuid
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import pandas as pd
import pyarrow.parquet as pq
import yfinance as yf

import repair_astra_pre_freeze_v2 as base
import run_astra_pre_freeze_repair_v2_hardened as hardened
from coscientist.astra_design_repair import empirical_mde
from coscientist.director import ActionKind, DirectorState, apply_answer, ensure_action
from coscientist.single_paper import PaperStage, SinglePaperState

SPEC_PATH = Path("ops/astra_stage3_estimand_rescue_spec.json")
SPEC = json.loads(SPEC_PATH.read_text(encoding="utf-8"))
WORK = Path(".astra_stage3_matched")
INP = WORK / "inputs"
OUT = WORK / "outputs"
PRICE = WORK / "price_extension"
PRICE_FOLDER_NAME = "stage3_pre_event_price_extension_20230101_20241231"
PRICE_START = "2023-01-01"
PRICE_END_EXCLUSIVE = "2025-01-01"
PRICE_MAX_ALLOWED = pd.Timestamp("2024-12-31")
BENCHMARKS = ["SPY", "QQQ", "IWM"]
CAL_THRESHOLD = float(SPEC["calibration_equivalent_mde80_max_return_units"])
VAL_THRESHOLD = float(SPEC["validation_equivalent_mde80_max_return_units"])
MIN_FIRMS = int(SPEC["minimum_candidate_firms"])
MIN_WINDOWS = int(SPEC["minimum_valid_placebo_windows_each_half"])

# Reuse audited SEC transport for corrected current-report robustness.
base.fetch_sec_master = hardened.fetch_sec_master_identity


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        while chunk := fh.read(8 * 1024 * 1024):
            h.update(chunk)
    return h.hexdigest()


def _one_named(client: base.DriveClient, folder_id: str, name: str) -> dict[str, Any]:
    hits = [f for f in client.list_folder(folder_id) if f.get("name") == name]
    if len(hits) != 1:
        raise RuntimeError(f"expected exactly one {name!r} under {folder_id}, found {len(hits)}")
    return hits[0]


def download_inputs(client: base.DriveClient) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    INP.mkdir(parents=True, exist_ok=True)
    sources = {
        "astra_pre_event_ai_exposure_v1.csv": base.SOURCE["astra_pre_event_ai_exposure_v1.csv"],
        "firm_pre_event_controls_complete.csv": base.SOURCE["firm_pre_event_controls_complete.csv"],
        "event_design_v1.json": base.SOURCE["event_design_v1.json"],
    }
    for name, (fid, expected) in sources.items():
        print(f"download {name}", flush=True)
        got = client.download(fid, str(INP / name), expected_sha256=expected)
        if got != expected:
            raise RuntimeError(f"source hash mismatch for {name}: {got} != {expected}")
    exposure = pd.read_csv(INP / "astra_pre_event_ai_exposure_v1.csv", dtype=str, keep_default_na=False)
    controls = pd.read_csv(INP / "firm_pre_event_controls_complete.csv", dtype=str, keep_default_na=False)
    event_v1 = json.loads((INP / "event_design_v1.json").read_text(encoding="utf-8"))
    return exposure, controls, event_v1


def build_stage3_base(exposure_v1: pd.DataFrame, controls: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    exposure_v2, exposure_qa = base.build_exposure_v2(exposure_v1)
    need_controls = ["ticker", "market_cap", "profitability_roa", "sic", "exchange", "pre_event_form"]
    missing = [c for c in need_controls if c not in controls.columns]
    if missing:
        raise RuntimeError(f"V3 controls missing stage3 metadata: {missing}")
    df = exposure_v2.merge(controls[need_controls], on="ticker", how="left", validate="one_to_one")
    df["log_market_cap"] = np.log(pd.to_numeric(df["market_cap"].replace("", np.nan), errors="coerce"))
    df["roa"] = pd.to_numeric(df["profitability_roa"].replace("", np.nan), errors="coerce")
    df["broad"] = pd.to_numeric(df["primary_ai_exposure_z"].replace("", np.nan), errors="coerce")
    df["frontier"] = pd.to_numeric(df["frontier_ai_exposure_z"].replace("", np.nan), errors="coerce")
    base_mask = (
        base._bool(df["primary_beta200_eligible"])
        & ~df["ticker"].eq("NVDA")
        & df[["log_market_cap", "roa", "broad", "frontier"]].notna().all(axis=1)
        & df["sic"].astype(str).ne("")
        & df["exchange"].astype(str).ne("")
        & df["pre_event_form"].astype(str).ne("")
    )
    df = df.loc[base_mask].copy()
    if len(df) < 4000:
        raise RuntimeError(f"stage3 pre-event base unexpectedly small: {len(df)}")
    df["size_quintile"] = pd.qcut(df["log_market_cap"], 5, labels=["Q1", "Q2", "Q3", "Q4", "Q5"], duplicates="drop").astype(str)
    df["industry_cell"] = hierarchical_industry_cell(df)
    df["match_block"] = df["industry_cell"].astype(str) + "__" + df["size_quintile"].astype(str)
    return exposure_v2, df, exposure_qa


def _sic_strings(s: pd.Series) -> pd.Series:
    x = s.astype(str).str.replace(r"\.0$", "", regex=True).str.replace(r"\D", "", regex=True)
    return x.str.zfill(4).str[-4:]


def hierarchical_industry_cell(df: pd.DataFrame) -> pd.Series:
    sic4 = _sic_strings(df["sic"])
    sic3 = sic4.str[:3]
    sic2 = sic4.str[:2]
    c4 = sic4.map(sic4.value_counts())
    c3 = sic3.map(sic3.value_counts())
    return pd.Series(
        np.where(c4 >= 20, "S4_" + sic4, np.where(c3 >= 20, "S3_" + sic3, "S2_" + sic2)),
        index=df.index,
        dtype=str,
    )


def yahoo_symbol(ticker: str) -> str:
    return str(ticker).strip().replace(".", "-")


def _extract_symbol_frame(batch: pd.DataFrame, symbol: str) -> pd.DataFrame | None:
    if batch is None or batch.empty:
        return None
    if isinstance(batch.columns, pd.MultiIndex):
        l0 = {str(x) for x in batch.columns.get_level_values(0)}
        l1 = {str(x) for x in batch.columns.get_level_values(1)} if batch.columns.nlevels > 1 else set()
        if symbol in l0:
            sub = batch[symbol].copy()
        elif symbol in l1:
            sub = batch.xs(symbol, axis=1, level=1).copy()
        else:
            return None
    else:
        sub = batch.copy()
    if sub.empty:
        return None
    sub = sub.reset_index()
    if "Date" not in sub.columns:
        date_col = sub.columns[0]
        sub.rename(columns={date_col: "Date"}, inplace=True)
    return sub


def _download_batch(symbols: list[str]) -> pd.DataFrame:
    return yf.download(
        tickers=symbols,
        start=PRICE_START,
        end=PRICE_END_EXCLUSIVE,
        interval="1d",
        group_by="ticker",
        auto_adjust=False,
        actions=False,
        threads=True,
        progress=False,
        timeout=30,
    )


def acquire_price_extension(tickers: list[str]) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    PRICE.mkdir(parents=True, exist_ok=True)
    originals = sorted(set(tickers) | set(BENCHMARKS))
    mapping: dict[str, str] = {t: yahoo_symbol(t) for t in originals}
    reverse: dict[str, list[str]] = {}
    for original, ys in mapping.items():
        reverse.setdefault(ys, []).append(original)
    collisions = {ys: vals for ys, vals in reverse.items() if len(vals) > 1}
    if collisions:
        raise RuntimeError(f"Yahoo symbol normalization collisions: {collisions}")

    rows: list[pd.DataFrame] = []
    status: dict[str, dict[str, Any]] = {
        t: {"ticker": t, "yahoo_symbol": mapping[t], "status": "PENDING", "attempts": 0, "observations": 0, "price_field": ""}
        for t in originals
    }
    yahoo_symbols = [mapping[t] for t in originals]
    chunk_size = 80
    for ci in range(0, len(yahoo_symbols), chunk_size):
        chunk = yahoo_symbols[ci : ci + chunk_size]
        data = None
        err = ""
        for attempt in range(1, 4):
            try:
                data = _download_batch(chunk)
                err = ""
                break
            except Exception as exc:
                err = f"{type(exc).__name__}: {exc}"
                time.sleep(2 * attempt)
        for ys in chunk:
            original = reverse[ys][0]
            status[original]["attempts"] += 1
            sub = _extract_symbol_frame(data, ys) if data is not None else None
            if sub is None or sub.empty:
                status[original]["status"] = "BATCH_MISSING"
                status[original]["error"] = err or "symbol absent from batch response"
                continue
            field = "Adj Close" if "Adj Close" in sub.columns else ("Close" if "Close" in sub.columns else "")
            if not field:
                status[original]["status"] = "NO_PRICE_FIELD"
                continue
            keep = [c for c in ["Date", "Open", "High", "Low", "Close", "Adj Close", "Volume"] if c in sub.columns]
            sub = sub[keep].copy()
            sub["Date"] = pd.to_datetime(sub["Date"], errors="coerce", utc=True).dt.tz_convert(None).dt.normalize()
            sub = sub.loc[sub["Date"].notna() & (sub["Date"] <= PRICE_MAX_ALLOWED)].copy()
            sub[field] = pd.to_numeric(sub[field], errors="coerce")
            sub = sub.loc[sub[field].notna()].drop_duplicates("Date", keep="last")
            if sub.empty:
                status[original]["status"] = "NO_VALID_ROWS"
                continue
            sub["Ticker"] = original
            sub["price_field_used"] = field
            rows.append(sub)
            status[original].update(
                {
                    "status": "SUCCESS",
                    "observations": int(len(sub)),
                    "first_date": str(sub["Date"].min().date()),
                    "last_date": str(sub["Date"].max().date()),
                    "price_field": field,
                }
            )
        done = min(ci + chunk_size, len(yahoo_symbols))
        print(f"stage3 prices batch progress {done}/{len(yahoo_symbols)}", flush=True)

    # One conservative individual retry for batch-missing names.
    missing = [t for t, s in status.items() if s["status"] != "SUCCESS"]
    for idx, original in enumerate(missing, 1):
        ys = mapping[original]
        status[original]["attempts"] += 1
        try:
            data = _download_batch([ys])
            sub = _extract_symbol_frame(data, ys)
            if sub is None or sub.empty:
                continue
            field = "Adj Close" if "Adj Close" in sub.columns else ("Close" if "Close" in sub.columns else "")
            if not field:
                continue
            keep = [c for c in ["Date", "Open", "High", "Low", "Close", "Adj Close", "Volume"] if c in sub.columns]
            sub = sub[keep].copy()
            sub["Date"] = pd.to_datetime(sub["Date"], errors="coerce", utc=True).dt.tz_convert(None).dt.normalize()
            sub = sub.loc[sub["Date"].notna() & (sub["Date"] <= PRICE_MAX_ALLOWED)].copy()
            sub[field] = pd.to_numeric(sub[field], errors="coerce")
            sub = sub.loc[sub[field].notna()].drop_duplicates("Date", keep="last")
            if sub.empty:
                continue
            sub["Ticker"] = original
            sub["price_field_used"] = field
            rows.append(sub)
            status[original].update(
                {
                    "status": "SUCCESS",
                    "observations": int(len(sub)),
                    "first_date": str(sub["Date"].min().date()),
                    "last_date": str(sub["Date"].max().date()),
                    "price_field": field,
                    "error": "",
                }
            )
        except Exception as exc:
            status[original]["error"] = f"{type(exc).__name__}: {exc}"
        if idx % 100 == 0:
            print(f"stage3 individual retries {idx}/{len(missing)}", flush=True)
            time.sleep(1)

    if not rows:
        raise RuntimeError("stage3 Yahoo acquisition returned zero usable rows")
    panel = pd.concat(rows, ignore_index=True, sort=False)
    panel = panel.drop_duplicates(["Ticker", "Date"], keep="last").sort_values(["Ticker", "Date"])
    status_df = pd.DataFrame(status.values()).sort_values("ticker")
    counts = panel.groupby("Ticker").size()
    benchmark_counts = {b: int(counts.get(b, 0)) for b in BENCHMARKS}
    firm_counts = counts.drop(labels=[b for b in BENCHMARKS if b in counts.index], errors="ignore")
    qa = {
        "status": "PASS",
        "vendor": "Yahoo Finance via yfinance",
        "requested_tickers": int(len(originals)),
        "successful_tickers": int((status_df["status"] == "SUCCESS").sum()),
        "successful_firms_ge_450_obs": int((firm_counts >= 450).sum()),
        "benchmark_observations": benchmark_counts,
        "panel_rows": int(len(panel)),
        "first_date": str(panel["Date"].min().date()),
        "last_date": str(panel["Date"].max().date()),
        "duplicate_ticker_date_rows": int(panel.duplicated(["Ticker", "Date"]).sum()),
        "post_2024_rows": int((panel["Date"] > PRICE_MAX_ALLOWED).sum()),
        "close_fallback_tickers": int((status_df["price_field"] == "Close").sum()),
        "outcome_inspected": "NO",
    }
    gates = [
        qa["successful_firms_ge_450_obs"] >= 3000,
        all(benchmark_counts[b] >= 490 for b in BENCHMARKS),
        qa["duplicate_ticker_date_rows"] == 0,
        qa["post_2024_rows"] == 0,
        pd.Timestamp(qa["last_date"]) <= PRICE_MAX_ALLOWED,
    ]
    if not all(gates):
        qa["status"] = "FAIL"
        raise RuntimeError(f"stage3 price-extension QA failed: {qa}")
    return panel, status_df, qa


def publish_price_extension(client: base.DriveClient, panel: pd.DataFrame, status: pd.DataFrame, qa: dict[str, Any]) -> tuple[str, str]:
    PRICE.mkdir(parents=True, exist_ok=True)
    panel_path = PRICE / "us_equity_daily_prices_20230101_20241231.parquet"
    status_path = PRICE / "price_acquisition_status_stage3.csv"
    qa_path = PRICE / "price_extension_qa.json"
    panel.to_parquet(panel_path, index=False)
    status.to_csv(status_path, index=False)
    qa_path.write_text(json.dumps(qa, indent=2, sort_keys=True), encoding="utf-8")
    manifest = {
        "paper_id": base.PAPER_ID,
        "name": PRICE_FOLDER_NAME,
        "generated_at_utc": base.now(),
        "git_commit_sha": base.git_sha(),
        "spec_sha256": sha256_file(SPEC_PATH),
        "outcome_inspected": "NO",
        "files": {
            panel_path.name: {"bytes": panel_path.stat().st_size, "sha256": sha256_file(panel_path)},
            status_path.name: {"bytes": status_path.stat().st_size, "sha256": sha256_file(status_path)},
            qa_path.name: {"bytes": qa_path.stat().st_size, "sha256": sha256_file(qa_path)},
        },
    }
    manifest_path = PRICE / "price_extension_manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")

    finals = [f for f in client.list_folder(base.STUDY_FOLDER_ID) if f.get("name") == PRICE_FOLDER_NAME and f.get("mimeType") == base.FOLDER_MIME]
    if finals:
        raise RuntimeError(f"immutable {PRICE_FOLDER_NAME} already exists unexpectedly during first stage3 publication")
    folder_id = client.create_folder(f"{PRICE_FOLDER_NAME}__BUILDING_{uuid.uuid4().hex[:10]}", base.STUDY_FOLDER_ID)
    ids: dict[str, str] = {}
    for path in [panel_path, status_path, qa_path, manifest_path]:
        ids[path.name] = client.upload(str(path), folder_id, path.name)
    verify = WORK / "price_roundtrip"; verify.mkdir(parents=True, exist_ok=True)
    for path in [panel_path, status_path, qa_path, manifest_path]:
        expected = sha256_file(path)
        got = client.download(ids[path.name], str(verify / path.name), expected_sha256=expected)
        if got != expected:
            raise RuntimeError(f"stage3 price-extension Drive roundtrip mismatch: {path.name}")
    base._rename(client, folder_id, PRICE_FOLDER_NAME)
    return folder_id, manifest["files"][panel_path.name]["sha256"]


def load_or_acquire_price_extension(client: base.DriveClient, tickers: list[str]) -> tuple[pd.DataFrame, str, str]:
    finals = [f for f in client.list_folder(base.STUDY_FOLDER_ID) if f.get("name") == PRICE_FOLDER_NAME and f.get("mimeType") == base.FOLDER_MIME]
    if len(finals) > 1:
        raise RuntimeError(f"multiple immutable {PRICE_FOLDER_NAME} folders")
    if not finals:
        panel, status, qa = acquire_price_extension(tickers)
        folder_id, panel_sha = publish_price_extension(client, panel, status, qa)
        return panel, folder_id, panel_sha

    folder_id = finals[0]["id"]
    files = client.list_folder(folder_id)
    by_name = {f.get("name"): f for f in files}
    for req in ["us_equity_daily_prices_20230101_20241231.parquet", "price_extension_manifest.json", "price_extension_qa.json"]:
        if req not in by_name:
            raise RuntimeError(f"existing stage3 price extension missing {req}")
    manifest_path = PRICE / "price_extension_manifest.json"; PRICE.mkdir(parents=True, exist_ok=True)
    client.download(by_name["price_extension_manifest.json"]["id"], str(manifest_path))
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    panel_sha = manifest["files"]["us_equity_daily_prices_20230101_20241231.parquet"]["sha256"]
    panel_path = PRICE / "us_equity_daily_prices_20230101_20241231.parquet"
    client.download(by_name[panel_path.name]["id"], str(panel_path), expected_sha256=panel_sha)
    qa_path = PRICE / "price_extension_qa.json"; client.download(by_name[qa_path.name]["id"], str(qa_path))
    qa = json.loads(qa_path.read_text(encoding="utf-8"))
    if qa.get("status") != "PASS" or qa.get("outcome_inspected") != "NO" or pd.Timestamp(qa["last_date"]) > PRICE_MAX_ALLOWED:
        raise RuntimeError(f"existing stage3 price extension failed reuse audit: {qa}")
    panel = pd.read_parquet(panel_path)
    panel["Date"] = pd.to_datetime(panel["Date"], errors="coerce")
    return panel, folder_id, panel_sha


def candidate_definition(base_df: pd.DataFrame, name: str) -> dict[str, Any] | None:
    spec = next(x for x in SPEC["candidate_order"] if x["name"] == name)
    exposure_col = "frontier" if spec["exposure"] == "frontier_ai_exposure_z" else "broad"
    if exposure_col == "frontier" and float((base_df["frontier"] != 0).mean()) < 0.25:
        return None
    if "Q25_Q75" in name:
        qlow, qhigh = 0.25, 0.75
    elif "TERCILES" in name:
        qlow, qhigh = 1 / 3, 2 / 3
    else:
        raise ValueError(name)
    low_cut = float(base_df[exposure_col].quantile(qlow))
    high_cut = float(base_df[exposure_col].quantile(qhigh))
    c = base_df.loc[(base_df[exposure_col] <= low_cut) | (base_df[exposure_col] >= high_cut)].copy()
    c["high"] = (c[exposure_col] >= high_cut).astype(float)
    support = c.groupby("match_block")["high"].agg(["sum", "count"])
    valid_blocks = set(support.index[(support["sum"] >= 5) & ((support["count"] - support["sum"]) >= 5)])
    c = c.loc[c["match_block"].isin(valid_blocks)].copy()
    if len(c) < MIN_FIRMS:
        return None
    gap = blocked_difference(c[exposure_col].to_numpy(float), c["high"].to_numpy(float), c["match_block"].astype(str).to_numpy())
    if not np.isfinite(gap) or gap <= 0:
        return None
    return {
        "name": name,
        "exposure": spec["exposure"],
        "exposure_col": exposure_col,
        "low_cut": low_cut,
        "high_cut": high_cut,
        "candidate_n": int(len(c)),
        "blocks": int(c["match_block"].nunique()),
        "blocked_exposure_z_gap": float(gap),
        "frame": c,
    }


def blocked_difference(y: np.ndarray, high: np.ndarray, blocks: np.ndarray) -> float:
    df = pd.DataFrame({"y": np.asarray(y, float), "x": np.asarray(high, float), "b": blocks})
    means_x = df.groupby("b")["x"].transform("mean")
    means_y = df.groupby("b")["y"].transform("mean")
    rx = df["x"] - means_x
    ry = df["y"] - means_y
    den = float(np.dot(rx, rx))
    if den <= 0:
        return float("nan")
    return float(np.dot(rx, ry) / den)


def prepare_returns(panel: pd.DataFrame) -> pd.DataFrame:
    p = panel.copy()
    p["Date"] = pd.to_datetime(p["Date"], errors="coerce")
    p = p.loc[p["Date"].notna() & (p["Date"] <= PRICE_MAX_ALLOWED)].copy()
    p["px"] = np.where(
        pd.to_numeric(p.get("Adj Close"), errors="coerce").notna() if "Adj Close" in p.columns else False,
        pd.to_numeric(p.get("Adj Close"), errors="coerce") if "Adj Close" in p.columns else np.nan,
        pd.to_numeric(p.get("Close"), errors="coerce"),
    )
    p = p.loc[p["px"].notna()].drop_duplicates(["Ticker", "Date"], keep="last")
    wide = p.pivot(index="Date", columns="Ticker", values="px").sort_index()
    if "SPY" not in wide.columns:
        raise RuntimeError("SPY absent from stage3 price extension")
    return wide.pct_change(fill_method=None).replace([np.inf, -np.inf], np.nan)


def pseudo_event_car(rets: pd.DataFrame, tickers: list[str], d1: pd.Timestamp, d2: pd.Timestamp) -> pd.Series:
    spy = rets["SPY"]
    est_dates = spy.dropna().index[spy.dropna().index < d1][-200:]
    if len(est_dates) != 200 or d1 not in rets.index or d2 not in rets.index:
        return pd.Series(dtype=float)
    available = [t for t in tickers if t in rets.columns]
    if not available:
        return pd.Series(dtype=float)
    firm = rets[available]
    good = firm.loc[est_dates].notna().all(axis=0) & firm.loc[[d1, d2]].notna().all(axis=0)
    good_tickers = list(good.index[good])
    if len(good_tickers) < MIN_FIRMS:
        return pd.Series(dtype=float)
    m = spy.loc[est_dates].to_numpy(float)
    X = np.column_stack([np.ones(len(est_dates)), m])
    Y = firm.loc[est_dates, good_tickers].to_numpy(float)
    coef = np.linalg.lstsq(X, Y, rcond=None)[0]
    Xe = np.column_stack([np.ones(2), spy.loc[[d1, d2]].to_numpy(float)])
    actual = firm.loc[[d1, d2], good_tickers].to_numpy(float)
    car = np.sum(actual - Xe @ coef, axis=0)
    return pd.Series(car, index=good_tickers, dtype=float)


def contrast_for_window(candidate: dict[str, Any], car: pd.Series) -> tuple[float, int, int] | None:
    c = candidate["frame"].set_index("ticker").join(car.rename("car"), how="inner")
    if c.empty:
        return None
    support = c.groupby("match_block")["high"].agg(["sum", "count"])
    valid_blocks = set(support.index[(support["sum"] >= 5) & ((support["count"] - support["sum"]) >= 5)])
    c = c.loc[c["match_block"].isin(valid_blocks)].copy()
    if len(c) < MIN_FIRMS:
        return None
    coef = blocked_difference(c["car"].to_numpy(float), c["high"].to_numpy(float), c["match_block"].astype(str).to_numpy())
    if not np.isfinite(coef):
        return None
    return float(coef), int(len(c)), int(len(valid_blocks))


def half_windows(spy_dates: pd.DatetimeIndex, start: str, end: str) -> list[tuple[pd.Timestamp, pd.Timestamp]]:
    dates = [d for d in spy_dates if pd.Timestamp(start) <= d <= pd.Timestamp(end)]
    if len(dates) % 2:
        dates = dates[:-1]
    return list(zip(dates[0::2], dates[1::2]))


def calibrate_candidate(candidate: dict[str, Any], rets: pd.DataFrame, windows: list[tuple[pd.Timestamp, pd.Timestamp]]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    coeffs: list[float] = []
    rows: list[dict[str, Any]] = []
    tickers = candidate["frame"]["ticker"].astype(str).tolist()
    for i, (d1, d2) in enumerate(windows, 1):
        car = pseudo_event_car(rets, tickers, d1, d2)
        got = contrast_for_window(candidate, car)
        if got is None:
            continue
        coef, n, blocks = got
        coeffs.append(coef)
        rows.append({"window": i, "date_1": str(d1.date()), "date_2": str(d2.date()), "contrast": coef, "n": n, "blocks": blocks, "phase": "CALIBRATION", "outcome_inspected": "NO"})
    if len(coeffs) < MIN_WINDOWS:
        return {"status": "INSUFFICIENT_WINDOWS", "valid_windows": len(coeffs)}, rows
    mde = empirical_mde(coeffs)
    eq = float(mde.mde80 / candidate["blocked_exposure_z_gap"])
    return {
        "status": "PASS" if eq <= CAL_THRESHOLD else "FAIL",
        "valid_windows": len(coeffs),
        "contrast_mde80": mde.mde80,
        "contrast_mde80_pp": mde.mde80 * 100,
        "equivalent_per_sd_mde80": eq,
        "equivalent_per_sd_mde80_pp": eq * 100,
        "coefficient_sd": mde.coefficient_sd,
        "min_window_n": int(min(r["n"] for r in rows)),
        "median_window_n": float(np.median([r["n"] for r in rows])),
    }, rows


def validate_selected(candidate: dict[str, Any], rets: pd.DataFrame, windows: list[tuple[pd.Timestamp, pd.Timestamp]]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    coeffs: list[float] = []
    rows: list[dict[str, Any]] = []
    tickers = candidate["frame"]["ticker"].astype(str).tolist()
    for i, (d1, d2) in enumerate(windows, 1):
        car = pseudo_event_car(rets, tickers, d1, d2)
        got = contrast_for_window(candidate, car)
        if got is None:
            continue
        coef, n, blocks = got
        coeffs.append(coef)
        rows.append({"window": i, "date_1": str(d1.date()), "date_2": str(d2.date()), "contrast": coef, "n": n, "blocks": blocks, "phase": "VALIDATION", "outcome_inspected": "NO"})
    if len(coeffs) < MIN_WINDOWS:
        return {"status": "INSUFFICIENT_WINDOWS", "valid_windows": len(coeffs)}, rows
    mde = empirical_mde(coeffs)
    eq = float(mde.mde80 / candidate["blocked_exposure_z_gap"])
    return {
        "status": "PASS" if eq <= VAL_THRESHOLD else "FAIL",
        "valid_windows": len(coeffs),
        "contrast_mde80": mde.mde80,
        "contrast_mde80_pp": mde.mde80 * 100,
        "equivalent_per_sd_mde80": eq,
        "equivalent_per_sd_mde80_pp": eq * 100,
        "coefficient_sd": mde.coefficient_sd,
        "min_window_n": int(min(r["n"] for r in rows)),
        "median_window_n": float(np.median([r["n"] for r in rows])),
    }, rows


def run_stage3(base_df: pd.DataFrame, exposure_qa: dict[str, Any], panel: pd.DataFrame) -> tuple[dict[str, Any], dict[str, Any], pd.DataFrame]:
    rets = prepare_returns(panel)
    spy_dates = rets["SPY"].dropna().index
    calibration_windows = half_windows(spy_dates, "2024-01-01", "2024-06-30")
    validation_windows = half_windows(spy_dates, "2024-07-01", "2024-12-31")
    if len(calibration_windows) < MIN_WINDOWS or len(validation_windows) < MIN_WINDOWS:
        raise RuntimeError(f"fresh 2024 window counts insufficient: calibration={len(calibration_windows)}, validation={len(validation_windows)}")

    calibration: list[dict[str, Any]] = []
    calibration_rows: list[dict[str, Any]] = []
    selected: dict[str, Any] | None = None
    selected_cal: dict[str, Any] | None = None
    for spec in SPEC["candidate_order"]:
        cand = candidate_definition(base_df, spec["name"])
        if cand is None:
            calibration.append({"name": spec["name"], "status": "INADMISSIBLE"})
            continue
        result, rows = calibrate_candidate(cand, rets, calibration_windows)
        calibration_rows.extend([{**r, "candidate": cand["name"]} for r in rows])
        summary = {
            "name": cand["name"],
            "exposure": cand["exposure"],
            "candidate_n": cand["candidate_n"],
            "blocks": cand["blocks"],
            "blocked_exposure_z_gap": cand["blocked_exposure_z_gap"],
            **result,
        }
        calibration.append(summary)
        if result.get("status") == "PASS":
            selected = cand
            selected_cal = summary
            break

    stage3: dict[str, Any] = {
        "status": "REPAIR",
        "spec_version": SPEC["spec_version"],
        "spec_sha256": sha256_file(SPEC_PATH),
        "method": "Fresh-2024 matched high-versus-low exposure CAR contrast with hierarchical-industry x size-quintile blocking; 2024H1 calibrates frozen candidates and 2024H2 validates exactly one selected candidate.",
        "calibration_window_pairs": len(calibration_windows),
        "validation_window_pairs": len(validation_windows),
        "calibration_candidates": calibration,
        "calibration_threshold_equivalent_per_sd_mde80_pp": CAL_THRESHOLD * 100,
        "validation_threshold_equivalent_per_sd_mde80_pp": VAL_THRESHOLD * 100,
        "frontier_nonzero_share": exposure_qa["frontier_nonzero_share"],
        "outcome_inspected": "NO",
    }
    if selected is None or selected_cal is None:
        (OUT / "power_rescue_stage3.json").write_text(json.dumps(stage3, indent=2, sort_keys=True), encoding="utf-8")
        pd.DataFrame(calibration_rows).to_csv(OUT / "stage3_placebo_coefficients.csv", index=False)
        raise RuntimeError("stage3: no frozen matched-contrast candidate passed <=0.45pp equivalent-per-SD calibration MDE80")

    validation, validation_rows = validate_selected(selected, rets, validation_windows)
    stage3["selected_from_calibration"] = selected_cal
    stage3["validation"] = validation
    stage3["validation_candidate"] = selected["name"]
    all_rows = calibration_rows + [{**r, "candidate": selected["name"]} for r in validation_rows]
    pd.DataFrame(all_rows).to_csv(OUT / "stage3_placebo_coefficients.csv", index=False)
    if validation.get("status") != "PASS":
        stage3["status"] = "VALIDATION_FAIL"
        (OUT / "power_rescue_stage3.json").write_text(json.dumps(stage3, indent=2, sort_keys=True), encoding="utf-8")
        raise RuntimeError(
            f"stage3 calibration-selected candidate failed untouched 2024H2 validation: {selected['name']} equivalent MDE80={validation.get('equivalent_per_sd_mde80_pp', float('nan')):.4f}pp"
        )
    stage3["status"] = "PASS"
    (OUT / "power_rescue_stage3.json").write_text(json.dumps(stage3, indent=2, sort_keys=True), encoding="utf-8")
    return stage3, selected, selected["frame"].copy()


def build_design_outputs(
    exposure_v2: pd.DataFrame,
    event_v1: dict[str, Any],
    flags_qa: dict[str, Any],
    stage3: dict[str, Any],
    selected: dict[str, Any],
    primary: pd.DataFrame,
    price_folder_id: str,
    price_sha: str,
) -> list[str]:
    OUT.mkdir(parents=True, exist_ok=True)
    selected_col = "frontier_ai_exposure_z" if selected["exposure_col"] == "frontier" else "primary_ai_exposure_z"
    exposure_v2 = exposure_v2.copy()
    exposure_v2["chosen_primary_exposure"] = selected_col
    exposure_v2["outcome_inspected"] = "NO"
    exposure_v2.sort_values("ticker").to_csv(OUT / "astra_pre_event_ai_exposure_v2.csv", index=False)

    p = primary.copy()
    p["primary_group"] = np.where(p["high"].eq(1), "HIGH", "LOW")
    p["selected_exposure"] = p[selected["exposure_col"]]
    p["selected_exposure_column"] = selected_col
    p["outcome_inspected"] = "NO"
    p[["ticker", "selected_exposure", "selected_exposure_column", "primary_group", "match_block", "industry_cell", "size_quintile", "log_market_cap", "roa", "sic", "exchange", "pre_event_form", "outcome_inspected"]].sort_values("ticker").to_csv(OUT / "primary_matched_sample_v2.csv", index=False)

    event_v2 = json.loads(json.dumps(event_v1))
    event_v2["version"] = "ASTRA_EVENT_DESIGN_V2_STAGE3_MATCHED"
    event_v2["generated_at_utc"] = base.now()
    event_v2["primary_exposure"] = {
        "column": selected_col,
        "candidate": selected["name"],
        "estimand": "matched HIGH-versus-LOW two-day CAR contrast with hierarchical-industry x size-quintile block fixed effects",
        "blocked_exposure_z_gap": selected["blocked_exposure_z_gap"],
    }
    event_v2["expected_return"]["primary"] = "SPY exact-200 market model"
    event_v2["primary_cross_sectional_estimator"] = {
        "type": "blocked high-versus-low contrast",
        "block": "hierarchical SIC4/SIC3/SIC2 cell x pre-event market-cap quintile",
        "candidate_n": selected["candidate_n"],
        "blocks": selected["blocks"],
    }
    event_v2["known_confound_policy"]["event_day_current_report_rule"] = "Corrected CIK/date-normalized Sep3/Sep4 EDGAR 8-K/6-K flags are clean-news robustness only; no automatic primary exclusion."
    event_v2["power_rescue_stage3"] = stage3
    event_v2["price_extension"] = {"folder_id": price_folder_id, "sha256": price_sha, "date_max": "2024-12-31"}
    event_v2["outcome_inspected"] = "NO"
    (OUT / "event_design_v2.json").write_text(json.dumps(event_v2, indent=2, sort_keys=True), encoding="utf-8")

    repair = {
        "status": "PASS",
        "paper_id": base.PAPER_ID,
        "generated_at_utc": base.now(),
        "git_commit_sha": base.git_sha(),
        "spec_sha256": sha256_file(SPEC_PATH),
        "price_extension_folder_id": price_folder_id,
        "price_extension_sha256": price_sha,
        "selected_candidate": selected["name"],
        "validation_equivalent_per_sd_mde80_pp": stage3["validation"]["equivalent_per_sd_mde80_pp"],
        "outcome_inspected": "NO",
        "declaration": "Astra event-window firm returns, abnormal returns, CARs and outcome classifications were not read, calculated or summarized during stage3 pre-freeze rescue."
    }
    (OUT / "pre_freeze_repair_v2.json").write_text(json.dumps(repair, indent=2, sort_keys=True), encoding="utf-8")
    return [
        "astra_pre_event_ai_exposure_v2.csv",
        "event_day_sec_current_report_flags_v2.csv",
        "event_day_sec_current_report_flags_v2_qa.json",
        "primary_matched_sample_v2.csv",
        "stage3_placebo_coefficients.csv",
        "power_rescue_stage3.json",
        "event_design_v2.json",
        "pre_freeze_repair_v2.json",
    ]


def update_state(
    client: base.DriveClient,
    folder_id: str,
    hashes: dict[str, str],
    stage3: dict[str, Any],
    selected: dict[str, Any],
    price_folder_id: str,
    price_sha: str,
) -> dict[str, Any]:
    paper_meta = _one_named(client, base.STUDY_FOLDER_ID, "paper_state.json")
    director_meta = _one_named(client, base.STUDY_FOLDER_ID, "director.json")
    paper_path, director_path = WORK / "paper_state.json", WORK / "director.json"
    client.download(paper_meta["id"], str(paper_path)); client.download(director_meta["id"], str(director_path))
    paper = SinglePaperState.load(str(paper_path)); director = DirectorState.load(str(director_path))
    action = ensure_action(paper, director, None)
    if paper.stage is not PaperStage.DESIGN_READY or not action or action.kind is not ActionKind.PRE_FREEZE_AUDIT:
        raise RuntimeError(f"expected DESIGN_READY/PRE_FREEZE_AUDIT, got {paper.stage}/{action}")
    answer = {
        "action_id": action.id,
        "decision": "REPAIR",
        "findings": [
            "The original slope design and two independent slope-rescue validations failed the unchanged pre-event power standard.",
            "Stage3 therefore changes the estimand to a blocked high-versus-low exposure contrast and validates it only on fresh 2024 historical pseudo-events acquired before outcome access.",
            "The equivalent per-SD power standard remains unchanged at <=0.50 percentage points on untouched validation.",
        ],
        "repair_summary": "Adopt immutable stage3 matched-contrast V2 only after frozen 2024H1 calibration and one-shot 2024H2 validation pass; keep Astra outcomes locked and leave a fresh hostile PRE_FREEZE_AUDIT pending.",
        "novelty_closure": "REPAIR",
        "measurement": "REPAIR",
        "identification": "REPAIR",
        "power": "REPAIR",
        "access_licence_ethics": "PASS",
    }
    if apply_answer(paper, director, answer, None) != "REPAIR":
        raise RuntimeError("stage3 PRE_FREEZE_AUDIT repair answer did not apply")
    old = json.loads(json.dumps(director.records.get("design_closure", {})))
    if old.get("decision") != "PASS":
        raise RuntimeError("stage3 rescue requires preserved PASS pre-audit design closure")
    director.records["design_closure_v1_pre_audit_repair"] = {"updated_at": base.now(), **old}

    selected_col = "frontier_ai_exposure_z" if selected["exposure_col"] == "frontier" else "primary_ai_exposure_z"
    validation = stage3["validation"]
    new = json.loads(json.dumps(old))
    new.update({
        "decision": "PASS",
        "design_version": "ASTRA_EVENT_DESIGN_V2_STAGE3_MATCHED",
        "sample_definition": f"Primary matched HIGH/LOW sample N={selected['candidate_n']} across {selected['blocks']} hierarchical-industry x size-quintile blocks; NVDA excluded for known Sep3 direct confound; no outcome-dependent exclusions.",
        "treatment": f"{selected['name']} based on {selected_col}; blocked exposure-z gap={selected['blocked_exposure_z_gap']:.6f}.",
        "controls": ["hierarchical SIC4/SIC3/SIC2 x pre-event market-cap-quintile block fixed effects"],
        "models": [
            f"PRIMARY: exact-200 SPY market-model CAR[0,+1], blocked HIGH-versus-LOW contrast for {selected['name']}.",
            "Inference: heteroskedasticity-robust and industry-clustered sensitivity; no significance-based model promotion.",
            "Timing sensitivity: AR[0] and AR[+1] separately.",
            "Exposure sensitivity: broad versus frontier/generative matched contrasts as secondary specifications only.",
            "Confound sensitivity: include-all and corrected clean-news samples excluding firms with Sep3/Sep4 SEC current-report flags.",
        ],
        "dataset_hashes": {
            "analysis_ready_data_v3_20260907/firm_pre_event_controls_complete.csv": base.SOURCE["firm_pre_event_controls_complete.csv"][1],
            f"{base.DESIGN_V2}/astra_pre_event_ai_exposure_v2.csv": hashes["astra_pre_event_ai_exposure_v2.csv"],
            f"{base.DESIGN_V2}/primary_matched_sample_v2.csv": hashes["primary_matched_sample_v2.csv"],
            f"{base.DESIGN_V2}/power_rescue_stage3.json": hashes["power_rescue_stage3.json"],
            "stage3_price_extension_sha256": price_sha,
            "ops/astra_stage3_estimand_rescue_spec.json": sha256_file(SPEC_PATH),
        },
        "power": {
            "status": "PASS",
            "method": stage3["method"],
            "plausible_effect": "Unchanged equivalent per-SD validation MDE80 <=0.50 percentage points.",
            "mde": f"Calibration candidate={selected['name']}; untouched 2024H2 equivalent per-SD MDE80={validation['equivalent_per_sd_mde80_pp']:.4f}pp.",
            "evidence": f"Fresh 2024 calibration/validation periods using a separately materialized 2023-2024 Yahoo/yfinance pre-event price extension; validation windows={validation['valid_windows']}; Astra outcomes not accessed.",
        },
    })
    director.records["design_closure"] = {"updated_at": base.now(), **new}
    director.records["design_repair_v2_stage3"] = {
        "updated_at": base.now(),
        "status": "PASS",
        "design_folder_id": folder_id,
        "price_extension_folder_id": price_folder_id,
        "selected_candidate": selected["name"],
        "validation_equivalent_per_sd_mde80_pp": validation["equivalent_per_sd_mde80_pp"],
        "stage3_spec_sha256": sha256_file(SPEC_PATH),
        "outcome_inspected": "NO",
    }
    paper.active_paper.primary_exposure = new["treatment"]
    paper.active_paper.intended_design = str(new["design"])
    paper.active_paper.primary_outcome = str(new["outcome"])
    paper.save(); director.save()
    next_action = ensure_action(paper, director, None)
    if not next_action or next_action.kind is not ActionKind.PRE_FREEZE_AUDIT:
        raise RuntimeError(f"expected fresh PRE_FREEZE_AUDIT after stage3 repair, got {next_action}")
    paper.save(); director.save()
    base._patch_json(client, paper_meta["id"], paper_path); base._patch_json(client, director_meta["id"], director_path)
    return {"stage": paper.stage.value, "design_folder_id": folder_id, "pending_action_id": next_action.id, "pending_action_kind": next_action.kind.value, "selected_candidate": selected["name"], "outcome_inspected": "NO"}


def main() -> int:
    if WORK.exists():
        shutil.rmtree(WORK)
    INP.mkdir(parents=True); OUT.mkdir(parents=True); PRICE.mkdir(parents=True)
    client = base.DriveClient(base.DriveCredentials.from_env())
    exposure_v1, controls, event_v1 = download_inputs(client)
    exposure_v2, stage3_base, exposure_qa = build_stage3_base(exposure_v1, controls)

    panel, price_folder_id, price_sha = load_or_acquire_price_extension(client, stage3_base["ticker"].astype(str).tolist())
    # Corrected post-event filing flags are metadata-only robustness inputs; no return outcomes are touched.
    _, flags_qa = base.corrected_current_report_flags(exposure_v2)

    stage3, selected, primary = run_stage3(stage3_base, exposure_qa, panel)
    files = build_design_outputs(exposure_v2, event_v1, flags_qa, stage3, selected, primary, price_folder_id, price_sha)
    manifest = {
        "paper_id": base.PAPER_ID,
        "design_inputs": base.DESIGN_V2,
        "generated_at_utc": base.now(),
        "git_commit_sha": base.git_sha(),
        "stage3_spec_sha256": sha256_file(SPEC_PATH),
        "price_extension_folder_id": price_folder_id,
        "price_extension_sha256": price_sha,
        "outcome_inspected": "NO",
        "files": [{"name": n, "bytes": (OUT / n).stat().st_size, "sha256": sha256_file(OUT / n)} for n in files],
    }
    (OUT / "design_input_manifest_v2.json").write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
    files.append("design_input_manifest_v2.json")
    folder_id, hashes = base.publish(client, files)
    state = update_state(client, folder_id, hashes, stage3, selected, price_folder_id, price_sha)
    print(json.dumps({"status": "PASS", "design_inputs": base.DESIGN_V2, "selected": selected["name"], "validation": stage3["validation"], "price_extension_folder_id": price_folder_id, "state": state, "outcome_inspected": "NO"}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
