"""Outcome-blind Astra power rescue with calibration/validation placebo separation.

This script is a second-stage repair after the empirical common-date placebo gate
showed that the original SPY200 OLS design could not meet the predeclared 0.50pp
MDE80 threshold. It never reads post-cutoff firm returns. A fixed candidate family
is evaluated on early pre-event placebo windows; exactly one candidate may be
selected from calibration, and that exact candidate must pass untouched later
validation windows before any Drive publication or state mutation occurs.
"""
from __future__ import annotations

import json
import math
import shutil
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pyarrow.parquet as pq

import repair_astra_pre_freeze_v2 as base
from coscientist.astra_design_repair import (
    empirical_mde,
    placebo_coefficients,
    residualized_exposure,
    select_calibration_candidate,
    weighted_residualized_exposure,
)
from coscientist.director import ActionKind, apply_answer, ensure_action
from coscientist.single_paper import PaperStage, SinglePaperState

THRESHOLD = 0.005
MIN_WINDOW_N = 3000
BENCHMARKS = ["SPY", "QQQ", "IWM"]

# Ordered before any rescue results are inspected. Within each estimator family,
# Astra-specific frontier exposure is preferred only when its pre-event prevalence
# gate is satisfied; otherwise broad AI exposure is the only candidate exposure.
ESTIMATOR_ORDER = [
    {"model": "SPY", "cross": "OLS", "screen_q": None},
    {"model": "MULTI3", "cross": "OLS", "screen_q": None},
    {"model": "SPY", "cross": "WLS", "screen_q": None},
    {"model": "MULTI3", "cross": "WLS", "screen_q": None},
    {"model": "SPY", "cross": "OLS", "screen_q": 0.90},
    {"model": "MULTI3", "cross": "OLS", "screen_q": 0.90},
    {"model": "SPY", "cross": "WLS", "screen_q": 0.90},
    {"model": "MULTI3", "cross": "WLS", "screen_q": 0.90},
    {"model": "MULTI3", "cross": "WLS", "screen_q": 0.80},
]


def _factor_panel(sample: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    tickers = sorted(set(sample["ticker"].astype(str)))
    read_tickers = tickers + BENCHMARKS
    schema = pq.read_schema(base.INP / "us_equity_daily_prices_20250101_20260904.parquet")
    price_col = "Adj Close" if "Adj Close" in schema.names else "Close"
    table = pq.read_table(
        base.INP / "us_equity_daily_prices_20250101_20260904.parquet",
        columns=["Date", "Ticker", price_col],
        filters=[("Ticker", "in", read_tickers), ("Date", "<=", base.CUTOFF)],
    )
    px = table.to_pandas()
    px["Date"] = pd.to_datetime(px["Date"], errors="coerce")
    px[price_col] = pd.to_numeric(px[price_col], errors="coerce")
    px = px.dropna(subset=["Date", price_col]).drop_duplicates(["Ticker", "Date"], keep="last")
    wide = px.pivot(index="Date", columns="Ticker", values=price_col).sort_index()
    missing = [b for b in BENCHMARKS if b not in wide.columns]
    if missing:
        raise RuntimeError(f"rescue benchmark(s) absent from pre-event panel: {missing}")
    rets = wide.pct_change(fill_method=None).replace([np.inf, -np.inf], np.nan)
    factors = pd.DataFrame(index=rets.index)
    factors["MKT"] = rets["SPY"]
    factors["TECH"] = rets["QQQ"] - rets["SPY"]
    factors["SIZE"] = rets["IWM"] - rets["SPY"]
    return rets, factors


def _factor_columns(model: str) -> list[str]:
    if model == "SPY":
        return ["MKT"]
    if model == "MULTI3":
        return ["MKT", "TECH", "SIZE"]
    raise ValueError(model)


def _candidate_name(cfg: dict[str, Any], exposure: str) -> str:
    screen = "FULL" if cfg["screen_q"] is None else f"VOL_LE_{int(float(cfg['screen_q']) * 100)}PCT"
    return f"{cfg['model']}__{cfg['cross']}__{screen}__{exposure}"


def _weights_from_sigma(sigma: np.ndarray) -> tuple[np.ndarray, dict[str, float]]:
    s = np.asarray(sigma, dtype=float)
    if np.any(~np.isfinite(s)) or np.any(s <= 0):
        raise ValueError("invalid residual sigma for precision weighting")
    lo, hi = float(np.quantile(s, 0.10)), float(np.quantile(s, 0.90))
    clipped = np.clip(s, lo, hi)
    w = 1.0 / np.square(clipped)
    w = w / float(np.mean(w))
    ess = float(np.square(w.sum()) / np.square(w).sum())
    return w, {"sigma_clip_p10": lo, "sigma_clip_p90": hi, "weight_effective_n": ess}


def _coef_for_candidate(
    frame: pd.DataFrame,
    car: np.ndarray,
    sigma: np.ndarray,
    cfg: dict[str, Any],
    exposure: str,
) -> tuple[float, int, dict[str, float]]:
    df = frame.copy()
    df["car"] = np.asarray(car, dtype=float)
    df["model_sigma"] = np.asarray(sigma, dtype=float)
    if cfg["screen_q"] is not None:
        cutoff = float(df["model_sigma"].quantile(float(cfg["screen_q"])))
        df = df.loc[df["model_sigma"] <= cutoff].copy()
    else:
        cutoff = float("nan")
    if len(df) < MIN_WINDOW_N:
        return float("nan"), len(df), {"screen_sigma_cutoff": cutoff}
    df["residvol"] = df["model_sigma"]
    X = base._control_matrix(df)
    x = df[exposure].to_numpy(float)
    y = df["car"].to_numpy(float)
    meta: dict[str, float] = {"screen_sigma_cutoff": cutoff}
    if cfg["cross"] == "OLS":
        rx, sxx = residualized_exposure(x, X)
        b = float(placebo_coefficients(rx, sxx, y[:, None])[0])
    elif cfg["cross"] == "WLS":
        w, wmeta = _weights_from_sigma(df["model_sigma"].to_numpy(float))
        rx, sxx, sqrt_w = weighted_residualized_exposure(x, X, w)
        b = float(placebo_coefficients(rx, sxx, (y * sqrt_w)[:, None])[0])
        meta.update(wmeta)
    else:
        raise ValueError(cfg["cross"])
    return b, len(df), meta


def _pseudo_event_for_model(
    sample: pd.DataFrame,
    rets: pd.DataFrame,
    factors: pd.DataFrame,
    d1: pd.Timestamp,
    d2: pd.Timestamp,
    model: str,
) -> tuple[pd.DataFrame, np.ndarray, np.ndarray] | None:
    cols = _factor_columns(model)
    factor_ok = factors[cols].dropna().index
    est_dates = factor_ok[factor_ok < d1][-200:]
    if len(est_dates) != 200 or d1 not in factors.index or d2 not in factors.index:
        return None
    if factors.loc[[d1, d2], cols].isna().any().any():
        return None
    tickers = [t for t in sample["ticker"].astype(str) if t in rets.columns]
    firm = rets[tickers]
    good = firm.loc[est_dates].notna().all(axis=0) & firm.loc[[d1, d2]].notna().all(axis=0)
    good_tickers = list(good.index[good])
    if len(good_tickers) < MIN_WINDOW_N:
        return None
    frame = sample.set_index("ticker").loc[good_tickers].copy()
    X = np.column_stack([np.ones(len(est_dates)), factors.loc[est_dates, cols].to_numpy(float)])
    Y = firm.loc[est_dates, good_tickers].to_numpy(float)
    coef = np.linalg.lstsq(X, Y, rcond=None)[0]
    resid = Y - X @ coef
    dof = len(est_dates) - X.shape[1]
    sigma = np.sqrt(np.sum(np.square(resid), axis=0) / dof)
    Xe = np.column_stack([np.ones(2), factors.loc[[d1, d2], cols].to_numpy(float)])
    predicted = Xe @ coef
    actual = firm.loc[[d1, d2], good_tickers].to_numpy(float)
    car = np.sum(actual - predicted, axis=0)
    frame["model_sigma"] = sigma
    return frame.reset_index(), car, sigma


def rescue_tournament(sample: pd.DataFrame, frontier_share: float) -> tuple[dict[str, Any], dict[str, Any]]:
    rets, factors = _factor_panel(sample)
    common = factors.dropna().index
    eligible_event_dates = [d for d in common if len(common[common < d]) >= 200]
    selected_dates = eligible_event_dates[-100:]
    if len(selected_dates) != 100:
        raise RuntimeError(f"expected 100 rescue placebo dates with 200-day histories, got {len(selected_dates)}")
    windows = list(zip(selected_dates[0::2], selected_dates[1::2]))
    if len(windows) != 50:
        raise RuntimeError("rescue requires exactly 50 non-overlapping two-day placebo windows")

    exposure_order = ["frontier"] if frontier_share >= 0.25 else []
    exposure_order.append("broad")
    candidate_specs: list[dict[str, Any]] = []
    for cfg in ESTIMATOR_ORDER:
        for exposure in exposure_order:
            candidate_specs.append({**cfg, "exposure": exposure, "name": _candidate_name(cfg, exposure)})

    coeffs = {c["name"]: [] for c in candidate_specs}
    ns = {c["name"]: [] for c in candidate_specs}
    window_rows: list[dict[str, Any]] = []
    for wi, (d1, d2) in enumerate(windows, 1):
        model_payload: dict[str, tuple[pd.DataFrame, np.ndarray, np.ndarray]] = {}
        for model in ["SPY", "MULTI3"]:
            payload = _pseudo_event_for_model(sample, rets, factors, d1, d2, model)
            if payload is None:
                raise RuntimeError(f"could not construct {model} pseudo-event window {d1.date()}/{d2.date()}")
            model_payload[model] = payload
        row: dict[str, Any] = {"window": wi, "date_1": str(d1.date()), "date_2": str(d2.date()), "outcome_inspected": "NO"}
        for cand in candidate_specs:
            frame, car, sigma = model_payload[cand["model"]]
            exposure_col = "frontier" if cand["exposure"] == "frontier" else "broad"
            b, n, _ = _coef_for_candidate(frame, car, sigma, cand, exposure_col)
            coeffs[cand["name"]].append(b)
            ns[cand["name"]].append(n)
            row[cand["name"]] = b
            row[cand["name"] + "__n"] = n
        window_rows.append(row)

    calibration_idx = slice(0, 25)
    validation_idx = slice(25, 50)
    summaries: list[dict[str, Any]] = []
    for cand in candidate_specs:
        vals = np.asarray(coeffs[cand["name"]], dtype=float)
        cal = empirical_mde(vals[calibration_idx])
        val = empirical_mde(vals[validation_idx])
        summaries.append({
            **cand,
            "calibration_windows": 25,
            "validation_windows": 25,
            "calibration_mde80": cal.mde80,
            "calibration_mde80_pp": cal.mde80 * 100,
            "validation_mde80": val.mde80,
            "validation_mde80_pp": val.mde80 * 100,
            "calibration_sd": cal.coefficient_sd,
            "validation_sd": val.coefficient_sd,
            "min_window_n": int(min(ns[cand["name"]])),
            "median_window_n": float(np.median(ns[cand["name"]])),
        })

    selected = select_calibration_candidate(summaries, threshold=THRESHOLD)
    result = {
        "status": "REPAIR" if selected is None else "CALIBRATION_PASS",
        "method": "Rolling pseudo-event power tournament: each pre-event two-day placebo window uses only the preceding exact 200 benchmark-factor return dates; candidate selection uses the first 25 windows and the exact selected candidate is tested once on the later 25 validation windows.",
        "threshold_mde80_return_units": THRESHOLD,
        "threshold_mde80_percentage_points": THRESHOLD * 100,
        "placebo_windows": 50,
        "calibration_windows": 25,
        "validation_windows": 25,
        "first_placebo_date": str(windows[0][0].date()),
        "last_placebo_date": str(windows[-1][1].date()),
        "frontier_nonzero_share": frontier_share,
        "candidate_order": [x["name"] for x in candidate_specs],
        "candidates": summaries,
        "outcome_inspected": "NO",
    }
    if selected is None:
        (base.OUT / "power_rescue_v2.json").write_text(json.dumps(result, indent=2, sort_keys=True), encoding="utf-8")
        pd.DataFrame(window_rows).to_csv(base.OUT / "power_rescue_placebo_coefficients_v2.csv", index=False)
        raise RuntimeError("no predeclared rescue candidate met the unchanged 0.50pp MDE80 threshold on calibration windows")
    if float(selected["validation_mde80"]) > THRESHOLD:
        result["status"] = "VALIDATION_FAIL"
        result["selected_from_calibration"] = selected
        (base.OUT / "power_rescue_v2.json").write_text(json.dumps(result, indent=2, sort_keys=True), encoding="utf-8")
        pd.DataFrame(window_rows).to_csv(base.OUT / "power_rescue_placebo_coefficients_v2.csv", index=False)
        raise RuntimeError(
            f"calibration-selected rescue candidate failed untouched validation: {selected['name']} validation MDE80={selected['validation_mde80_pp']:.4f}pp"
        )
    result["status"] = "PASS"
    result["selected"] = selected
    (base.OUT / "power_rescue_v2.json").write_text(json.dumps(result, indent=2, sort_keys=True), encoding="utf-8")
    pd.DataFrame(window_rows).to_csv(base.OUT / "power_rescue_placebo_coefficients_v2.csv", index=False)
    return result, selected


def fit_actual_pre_event_model(sample: pd.DataFrame, selected: dict[str, Any]) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    rets, factors = _factor_panel(sample)
    cols = _factor_columns(selected["model"])
    factor_ok = factors[cols].dropna().index
    est_dates = factor_ok[factor_ok <= pd.Timestamp(base.CUTOFF)][-200:]
    if len(est_dates) != 200:
        raise RuntimeError("actual selected expected-return model lacks exact 200 pre-event factor dates")
    tickers = [t for t in sample["ticker"].astype(str) if t in rets.columns]
    firm = rets[tickers]
    good = firm.loc[est_dates].notna().all(axis=0)
    good_tickers = list(good.index[good])
    frame = sample.set_index("ticker").loc[good_tickers].copy()
    X = np.column_stack([np.ones(len(est_dates)), factors.loc[est_dates, cols].to_numpy(float)])
    Y = firm.loc[est_dates, good_tickers].to_numpy(float)
    coef = np.linalg.lstsq(X, Y, rcond=None)[0]
    resid = Y - X @ coef
    dof = len(est_dates) - X.shape[1]
    sigma = np.sqrt(np.sum(np.square(resid), axis=0) / dof)
    frame["model_sigma"] = sigma
    screen_cutoff = None
    if selected["screen_q"] is not None:
        screen_cutoff = float(frame["model_sigma"].quantile(float(selected["screen_q"])))
        frame = frame.loc[frame["model_sigma"] <= screen_cutoff].copy()
    if len(frame) < MIN_WINDOW_N:
        raise RuntimeError(f"selected rescue design leaves only {len(frame)} actual pre-event firms")
    if selected["cross"] == "WLS":
        weights, wmeta = _weights_from_sigma(frame["model_sigma"].to_numpy(float))
    else:
        weights = np.ones(len(frame), dtype=float)
        wmeta = {"weight_effective_n": float(len(frame))}
    frame["primary_weight"] = weights
    frame["residvol"] = frame["model_sigma"]

    coef_df = pd.DataFrame({"ticker": good_tickers, "alpha": coef[0, :], "model_sigma": sigma})
    for i, name in enumerate(cols, 1):
        coef_df[f"beta_{name.lower()}"] = coef[i, :]
    coef_df["expected_return_model"] = selected["model"]
    coef_df["estimation_n"] = 200
    coef_df["estimation_first_date"] = str(est_dates[0].date())
    coef_df["estimation_last_date"] = str(est_dates[-1].date())
    coef_df["outcome_inspected"] = "NO"
    coef_df["included_primary"] = coef_df["ticker"].isin(set(frame.index))
    if selected["cross"] == "WLS":
        coef_df = coef_df.merge(frame[["primary_weight"]], left_on="ticker", right_index=True, how="left")
    else:
        coef_df["primary_weight"] = np.where(coef_df["included_primary"], 1.0, np.nan)

    exposure_col = "frontier" if selected["exposure"] == "frontier" else "broad"
    primary = frame.reset_index()[["ticker", exposure_col, "log_market_cap", "roa", "residvol", "sic2", "primary_weight"]].copy()
    chosen_col = "frontier_ai_exposure_z" if selected["exposure"] == "frontier" else "primary_ai_exposure_z"
    primary.rename(columns={exposure_col: chosen_col, "roa": "profitability_roa", "residvol": "selected_model_residual_vol_200"}, inplace=True)
    primary["outcome_inspected"] = "NO"
    meta = {
        "selected_candidate": selected["name"],
        "actual_primary_n": int(len(primary)),
        "model": selected["model"],
        "cross_sectional_estimator": selected["cross"],
        "screen_q": selected["screen_q"],
        "screen_sigma_cutoff": screen_cutoff,
        "weight_effective_n": wmeta["weight_effective_n"],
        "estimation_n": 200,
        "estimation_first_date": str(est_dates[0].date()),
        "estimation_last_date": str(est_dates[-1].date()),
        "outcome_inspected": "NO",
    }
    return coef_df, primary, meta


def update_state(
    client: base.DriveClient,
    folder_id: str,
    hashes: dict[str, str],
    selected: dict[str, Any],
    rescue: dict[str, Any],
    exposure_qa: dict[str, Any],
    flags_qa: dict[str, Any],
    actual_meta: dict[str, Any],
) -> dict[str, Any]:
    paper_meta = base._one_named(client, base.STUDY_FOLDER_ID, "paper_state.json")
    director_meta = base._one_named(client, base.STUDY_FOLDER_ID, "director.json")
    paper_path, director_path = base.WORK / "paper_state.json", base.WORK / "director.json"
    client.download(paper_meta["id"], str(paper_path)); client.download(director_meta["id"], str(director_path))
    paper = SinglePaperState.load(str(paper_path)); director = base.DirectorState.load(str(director_path))
    action = ensure_action(paper, director, None)
    if paper.stage is not PaperStage.DESIGN_READY or not action or action.kind is not ActionKind.PRE_FREEZE_AUDIT:
        raise RuntimeError(f"expected DESIGN_READY/PRE_FREEZE_AUDIT, got {paper.stage}/{action}")

    audit_answer = {
        "action_id": action.id,
        "decision": "REPAIR",
        "findings": [
            "V1 event-day SEC current-report robustness was false-zero; corrected parser/join now uses normalized CIK and SEC Date Filed formats.",
            "The original analytic power calculation materially understated common-date coefficient dispersion; empirical rolling placebo MDE exceeded the fixed 0.50pp threshold.",
            "A fixed, outcome-blind rescue family with chronological calibration/validation separation was required before freeze.",
        ],
        "repair_summary": "Replace the unvalidated V1 power/design artifacts with immutable V2 artifacts only if one predeclared rescue estimator passes the unchanged 0.50pp MDE80 threshold on calibration and then untouched validation; keep Astra outcomes locked.",
        "novelty_closure": "REPAIR",
        "measurement": "REPAIR",
        "identification": "REPAIR",
        "power": "REPAIR",
        "access_licence_ethics": "PASS",
    }
    result = apply_answer(paper, director, audit_answer, None)
    if result != "REPAIR":
        raise RuntimeError(f"pre-freeze audit repair did not apply: {result}")

    old_design = json.loads(json.dumps(director.records.get("design_closure", {})))
    if old_design.get("decision") != "PASS":
        raise RuntimeError("cannot rescue V2 without a PASS V1 design closure record")
    director.records["design_closure_v1_pre_audit_repair"] = {"updated_at": base.now(), **old_design}

    chosen_col = "frontier_ai_exposure_z" if selected["exposure"] == "frontier" else "primary_ai_exposure_z"
    exposure_label = "frontier/generative AI filing intensity" if selected["exposure"] == "frontier" else "broad AI filing intensity"
    expected_model = (
        "SPY market model" if selected["model"] == "SPY"
        else "three-factor traded-benchmark model: SPY market return + (QQQ-SPY) technology tilt + (IWM-SPY) size tilt"
    )
    screen_text = "no volatility screen" if selected["screen_q"] is None else f"pre-event selected-model residual volatility at or below the {int(float(selected['screen_q']) * 100)}th percentile"
    cross_text = (
        "OLS with HC3 standard errors" if selected["cross"] == "OLS"
        else "pre-event inverse-residual-variance WLS with residual sigma clipped at its 10th/90th percentiles and HC3 standard errors"
    )
    new_design = json.loads(json.dumps(old_design))
    new_design.update({
        "decision": "PASS",
        "design_version": "ASTRA_EVENT_DESIGN_V2_RESCUED",
        "sample_definition": f"Primary sample N={actual_meta['actual_primary_n']}: V3 truthful-fulltext annual-filing issuer satisfying the selected exact-200 expected-return model, baseline controls and {screen_text}; NVDA excluded for known Sep3 direct confound; no outcome-dependent exclusions.",
        "treatment": f"{chosen_col} = {exposure_label}; selected before outcome access under the frozen rescue tournament.",
        "controls": ["log market capitalization", "profitability ROA", "SIC2 fixed effects", "selected-model pre-event residual volatility"],
        "models": [
            f"PRIMARY: {expected_model}, exact 200 pre-event returns through {base.CUTOFF}; cross-sectional {cross_text} of CAR[0,+1] on {chosen_col} + log market cap + ROA + selected-model residual volatility + SIC2 FE; {screen_text}.",
            "Inference sensitivity: SIC2-clustered standard errors where cluster count/support is adequate.",
            "Expected-return sensitivity: SPY exact-120/exact-250 and QQQ/IWM benchmark sensitivities retained as secondary robustness, never promoted by significance.",
            "Timing sensitivity: AR[0] and AR[+1] separately.",
            "Exposure sensitivity: broad and frontier/generative filing intensities, any-core-AI indicator, and section-specific intensities where available.",
            "Confound sensitivity: include-all sample and corrected clean-news sample excluding firms with Sep3/Sep4 SEC current-report flags.",
            "Nested firm-characteristic heterogeneity: strict leverage, R&D/revenue, and intangibles without imputation; each reports its own N.",
            "Sequence sensitivities: Aug7, Aug26, Sep1 and Sep3 disclosures using the same frozen expected-return specification; Sep1 excludes NVDA and CRWD for direct concurrent collaboration news.",
        ],
        "dataset_hashes": {
            "analysis_ready_data_v3_20260907/firm_pre_event_controls_complete.csv": base.SOURCE["firm_pre_event_controls_complete.csv"][1],
            "analysis_ready_data_v3_20260907/firm_pre_event_price_metrics.csv": base.SOURCE["firm_pre_event_price_metrics.csv"][1],
            "analysis_ready_data_v3_20260907/us_equity_daily_prices_20250101_20260904.parquet": base.SOURCE["us_equity_daily_prices_20250101_20260904.parquet"][1],
            f"{base.DESIGN_V2}/astra_pre_event_ai_exposure_v2.csv": hashes["astra_pre_event_ai_exposure_v2.csv"],
            f"{base.DESIGN_V2}/event_day_sec_current_report_flags_v2.csv": hashes["event_day_sec_current_report_flags_v2.csv"],
            f"{base.DESIGN_V2}/event_design_v2.json": hashes["event_design_v2.json"],
            f"{base.DESIGN_V2}/expected_return_coefficients_v2.csv": hashes["expected_return_coefficients_v2.csv"],
            f"{base.DESIGN_V2}/primary_adjusted_sample_v2.csv": hashes["primary_adjusted_sample_v2.csv"],
            f"{base.DESIGN_V2}/power_rescue_v2.json": hashes["power_rescue_v2.json"],
        },
        "power": {
            "status": "PASS",
            "method": rescue["method"],
            "plausible_effect": "Predeclared outcome-blind design threshold retained unchanged: validation MDE80 <=0.50 percentage points per one-SD selected filing exposure.",
            "mde": f"Calibration MDE80={selected['calibration_mde80_pp']:.4f}pp; untouched validation MDE80={selected['validation_mde80_pp']:.4f}pp per one-SD selected exposure.",
            "evidence": f"25 chronological calibration and 25 later validation two-day pseudo-event windows, each with its own preceding exact-200 expected-return estimation window; minimum pseudo-event N={selected['min_window_n']}; Astra outcomes not accessed.",
        },
    })
    director.records["design_closure"] = {"updated_at": base.now(), **new_design}
    director.records["design_repair_v2"] = {
        "updated_at": base.now(),
        "status": "PASS",
        "design_folder_id": folder_id,
        "selected_candidate": selected["name"],
        "chosen_primary_exposure": chosen_col,
        "frontier_nonzero_share": exposure_qa["frontier_nonzero_share"],
        "corrected_event_day_flagged_firms": flags_qa["flagged_unique_firms"],
        "calibration_mde80_percentage_points": selected["calibration_mde80_pp"],
        "validation_mde80_percentage_points": selected["validation_mde80_pp"],
        "actual_primary_n": actual_meta["actual_primary_n"],
        "outcome_inspected": "NO",
    }
    paper.active_paper.primary_exposure = new_design["treatment"]
    paper.active_paper.intended_design = str(new_design["design"])
    paper.active_paper.primary_outcome = str(new_design["outcome"])
    paper.save(); director.save()
    next_action = ensure_action(paper, director, None)
    if not next_action or next_action.kind is not ActionKind.PRE_FREEZE_AUDIT:
        raise RuntimeError(f"expected fresh PRE_FREEZE_AUDIT after rescued V2, got {next_action}")
    paper.save(); director.save()
    base._patch_json(client, paper_meta["id"], paper_path); base._patch_json(client, director_meta["id"], director_path)
    return {
        "stage": paper.stage.value,
        "design_folder_id": folder_id,
        "pending_action_id": next_action.id,
        "pending_action_kind": next_action.kind.value,
        "selected_candidate": selected["name"],
        "outcome_inspected": "NO",
    }


def main() -> int:
    if base.WORK.exists():
        shutil.rmtree(base.WORK)
    base.INP.mkdir(parents=True); base.OUT.mkdir(parents=True)
    client = base.DriveClient(base.DriveCredentials.from_env())
    base.download_sources(client)
    exposure_v1 = pd.read_csv(base.INP / "astra_pre_event_ai_exposure_v1.csv", dtype=str, keep_default_na=False)
    metrics = pd.read_csv(base.INP / "firm_pre_event_price_metrics.csv", dtype=str, keep_default_na=False)
    controls = pd.read_csv(base.INP / "firm_pre_event_controls_complete.csv", dtype=str, keep_default_na=False)
    event_v1 = json.loads((base.INP / "event_design_v1.json").read_text(encoding="utf-8"))

    exposure_v2, exposure_qa = base.build_exposure_v2(exposure_v1)
    _, flags_qa = base.corrected_current_report_flags(exposure_v2)
    sample = base.adjusted_sample(exposure_v2, metrics, controls)
    rescue, selected = rescue_tournament(sample, exposure_qa["frontier_nonzero_share"])
    coef_df, primary, actual_meta = fit_actual_pre_event_model(sample, selected)

    chosen_col = "frontier_ai_exposure_z" if selected["exposure"] == "frontier" else "primary_ai_exposure_z"
    exposure_v2["chosen_primary_exposure"] = chosen_col
    exposure_v2["outcome_inspected"] = "NO"
    exposure_v2.sort_values("ticker").to_csv(base.OUT / "astra_pre_event_ai_exposure_v2.csv", index=False)
    coef_df.sort_values("ticker").to_csv(base.OUT / "expected_return_coefficients_v2.csv", index=False)
    primary.sort_values("ticker").to_csv(base.OUT / "primary_adjusted_sample_v2.csv", index=False)

    event_v2 = json.loads(json.dumps(event_v1))
    event_v2["version"] = "ASTRA_EVENT_DESIGN_V2_RESCUED"
    event_v2["generated_at_utc"] = base.now()
    event_v2["primary_exposure"] = {
        "column": chosen_col,
        "selection": "Selected before outcome access by fixed rescue candidate order using 25 early calibration placebo windows; exact candidate then passed 25 untouched later validation windows at unchanged MDE80<=0.50pp threshold.",
    }
    event_v2["expected_return"]["primary"] = (
        "SPY exact-200 rolling/final market model" if selected["model"] == "SPY"
        else "Exact-200 three-factor traded-benchmark model: SPY + (QQQ-SPY) + (IWM-SPY)"
    )
    event_v2["primary_cross_sectional_estimator"] = {
        "estimator": selected["cross"],
        "volatility_screen_quantile": selected["screen_q"],
        "weight_rule": "inverse selected-model residual variance with sigma clipped p10/p90" if selected["cross"] == "WLS" else "equal weight",
        "actual_primary_n": actual_meta["actual_primary_n"],
    }
    event_v2["known_confound_policy"]["event_day_current_report_rule"] = "Corrected CIK/date-normalized Sep3/Sep4 EDGAR 8-K/6-K flags are prespecified clean-news robustness only; no automatic primary exclusion."
    event_v2["power_rescue"] = rescue
    event_v2["outcome_inspected"] = "NO"
    (base.OUT / "event_design_v2.json").write_text(json.dumps(event_v2, indent=2, sort_keys=True), encoding="utf-8")

    repair = {
        "status": "PASS",
        "paper_id": base.PAPER_ID,
        "generated_at_utc": base.now(),
        "git_commit_sha": base.git_sha(),
        "parent_design": "design_inputs_v1_20260907",
        "parent_design_folder_id": base.DESIGN_V1_FOLDER_ID,
        "repairs": [
            "CIK_AND_SEC_DATE_NORMALIZED_EVENT_DAY_CURRENT_REPORT_FLAGS",
            "ROLLING_EXACT200_COMMON_DATE_PLACEBO_POWER",
            "CHRONOLOGICAL_CALIBRATION_VALIDATION_RESCUE_TOURNAMENT",
            "PRE_OUTCOME_EXPECTED_RETURN_AND_PRECISION_ESTIMATOR_SELECTION",
        ],
        "selected_candidate": selected["name"],
        "frontier_nonzero_share": exposure_qa["frontier_nonzero_share"],
        "corrected_event_day_flagged_firms": flags_qa["flagged_unique_firms"],
        "calibration_mde80_percentage_points": selected["calibration_mde80_pp"],
        "validation_mde80_percentage_points": selected["validation_mde80_pp"],
        "actual_primary_n": actual_meta["actual_primary_n"],
        "outcome_inspected": "NO",
        "declaration": "Astra event-window firm returns, abnormal returns, CARs and outcome classifications were not read, calculated or summarized during the pre-freeze rescue.",
    }
    (base.OUT / "pre_freeze_repair_v2.json").write_text(json.dumps(repair, indent=2, sort_keys=True), encoding="utf-8")

    files = [
        "astra_pre_event_ai_exposure_v2.csv",
        "event_day_sec_current_report_flags_v2.csv",
        "event_day_sec_current_report_flags_v2_qa.json",
        "expected_return_coefficients_v2.csv",
        "power_rescue_v2.json",
        "power_rescue_placebo_coefficients_v2.csv",
        "primary_adjusted_sample_v2.csv",
        "event_design_v2.json",
        "pre_freeze_repair_v2.json",
    ]
    manifest = {
        "paper_id": base.PAPER_ID,
        "design_inputs": base.DESIGN_V2,
        "generated_at_utc": base.now(),
        "git_commit_sha": base.git_sha(),
        "outcome_inspected": "NO",
        "source_hashes": {name: sha for name, (_, sha) in base.SOURCE.items()},
        "files": [{"name": n, "bytes": (base.OUT / n).stat().st_size, "sha256": base.sha256_file(base.OUT / n)} for n in files],
    }
    (base.OUT / "design_input_manifest_v2.json").write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
    files.append("design_input_manifest_v2.json")
    folder_id, hashes = base.publish(client, files)
    state = update_state(client, folder_id, hashes, selected, rescue, exposure_qa, flags_qa, actual_meta)
    print(json.dumps({
        "status": "PASS",
        "design_inputs": base.DESIGN_V2,
        "selected": selected,
        "actual": actual_meta,
        "event_flags_qa": flags_qa,
        "state": state,
        "outcome_inspected": "NO",
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
