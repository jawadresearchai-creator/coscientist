"""Second-stage outcome-blind Astra power rescue on untouched pre-event placebos.

The frozen specification lives at ops/astra_stage2_power_rescue_spec.json. This
script never reads post-cutoff firm returns. It uses the 100 eligible pre-event
trading dates immediately preceding the first rescue block, selects on the first
25 two-day pseudo-events, validates exactly once on the later 25, and publishes
only when the unchanged validation MDE80 <= 0.50 percentage points.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

import repair_astra_pre_freeze_v2 as base
import rescue_astra_pre_freeze_power_v2 as r1
import run_astra_pre_freeze_repair_v2_hardened as hardened
from coscientist.astra_design_repair import (
    conservative_inverse_variance_weights,
    empirical_mde,
    select_calibration_candidate,
)
from coscientist.director import ActionKind, DirectorState, apply_answer, ensure_action
from coscientist.single_paper import PaperStage, SinglePaperState

SPEC_PATH = Path("ops/astra_stage2_power_rescue_spec.json")
SPEC = json.loads(SPEC_PATH.read_text(encoding="utf-8"))
CAL_THRESHOLD = float(SPEC["calibration_mde80_max_return_units"])
VAL_THRESHOLD = float(SPEC["validation_mde80_max_return_units"])
MIN_N = int(SPEC["minimum_pseudo_event_sample_n"])

# Reuse the hardened SEC transport and conservative WLS rule.
base.fetch_sec_master = hardened.fetch_sec_master_identity
r1.base.fetch_sec_master = hardened.fetch_sec_master_identity
r1._weights_from_sigma = conservative_inverse_variance_weights
r1.MIN_WINDOW_N = MIN_N
r1.ESTIMATOR_ORDER = [
    {"model": "SPY", "cross": "OLS", "screen_q": None},
    {"model": "MULTI3", "cross": "OLS", "screen_q": None},
    {"model": "SPY", "cross": "WLS", "screen_q": None},
    {"model": "MULTI3", "cross": "WLS", "screen_q": None},
    {"model": "SPY", "cross": "WLS", "screen_q": 0.90},
    {"model": "MULTI3", "cross": "WLS", "screen_q": 0.90},
    {"model": "SPY", "cross": "WLS", "screen_q": 0.80},
    {"model": "MULTI3", "cross": "WLS", "screen_q": 0.80},
]


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


def rich_control_matrix(df: pd.DataFrame) -> np.ndarray:
    x = df.copy()
    x["industry_cell"] = hierarchical_industry_cell(x)
    cats = pd.concat(
        [
            pd.get_dummies(x["industry_cell"].astype(str), prefix="ind", drop_first=True, dtype=float),
            pd.get_dummies(x["exchange"].astype(str), prefix="ex", drop_first=True, dtype=float),
            pd.get_dummies(x["pre_event_form"].astype(str), prefix="form", drop_first=True, dtype=float),
        ],
        axis=1,
    )
    continuous = np.column_stack(
        [
            np.ones(len(x)),
            x["log_market_cap"].to_numpy(float),
            x["roa"].to_numpy(float),
            x["residvol"].to_numpy(float),
            x["beta"].to_numpy(float),
            x["alpha"].to_numpy(float),
        ]
    )
    return np.column_stack([continuous, cats.to_numpy(float)])


# r1._coef_for_candidate resolves its control matrix through base._control_matrix.
r1.base._control_matrix = rich_control_matrix


def enrich_sample(sample: pd.DataFrame, controls: pd.DataFrame) -> pd.DataFrame:
    meta = controls[["ticker", "sic", "exchange", "pre_event_form"]].copy()
    out = sample.merge(meta, on="ticker", how="left", validate="one_to_one")
    needed = ["sic", "exchange", "pre_event_form", "beta", "alpha"]
    for col in needed:
        if col not in out.columns:
            raise RuntimeError(f"stage2 enriched sample missing {col}")
    if out[needed].isna().any().any() or out[["sic", "exchange", "pre_event_form"]].astype(str).eq("").any().any():
        raise RuntimeError("stage2 enriched sample has missing rich-control metadata")
    return out


def stage2_tournament(sample: pd.DataFrame, frontier_share: float) -> tuple[dict[str, Any], dict[str, Any]]:
    rets, factors = r1._factor_panel(sample)
    common = factors.dropna().index
    eligible = [d for d in common if len(common[common < d]) >= 200]
    if len(eligible) < 200:
        raise RuntimeError(f"need >=200 eligible pre-event dates for independent stage2 block, got {len(eligible)}")
    stage1_dates = eligible[-100:]
    selected_dates = eligible[-200:-100]
    if set(stage1_dates) & set(selected_dates):
        raise RuntimeError("stage2 placebo block overlaps stage1 placebo block")
    windows = list(zip(selected_dates[0::2], selected_dates[1::2]))
    if len(windows) != 50:
        raise RuntimeError(f"expected 50 stage2 placebo windows, got {len(windows)}")

    exposure_order = ["frontier"] if frontier_share >= 0.25 else []
    exposure_order.append("broad")
    candidate_specs: list[dict[str, Any]] = []
    for cfg in r1.ESTIMATOR_ORDER:
        for exposure in exposure_order:
            candidate_specs.append({**cfg, "exposure": exposure, "name": r1._candidate_name(cfg, exposure) + "__RICH"})

    coeffs = {c["name"]: [] for c in candidate_specs}
    ns = {c["name"]: [] for c in candidate_specs}
    rows: list[dict[str, Any]] = []
    for wi, (d1, d2) in enumerate(windows, 1):
        payloads: dict[str, tuple[pd.DataFrame, np.ndarray, np.ndarray]] = {}
        for model in ["SPY", "MULTI3"]:
            payload = r1._pseudo_event_for_model(sample, rets, factors, d1, d2, model)
            if payload is None:
                raise RuntimeError(f"could not build {model} stage2 pseudo-event {d1.date()}/{d2.date()}")
            payloads[model] = payload
        row: dict[str, Any] = {"window": wi, "date_1": str(d1.date()), "date_2": str(d2.date()), "outcome_inspected": "NO"}
        for cand in candidate_specs:
            frame, car, sigma = payloads[cand["model"]]
            exposure_col = "frontier" if cand["exposure"] == "frontier" else "broad"
            b, n, _ = r1._coef_for_candidate(frame, car, sigma, cand, exposure_col)
            coeffs[cand["name"]].append(b)
            ns[cand["name"]].append(n)
            row[cand["name"]] = b
            row[cand["name"] + "__n"] = n
        rows.append(row)

    summaries: list[dict[str, Any]] = []
    for cand in candidate_specs:
        vals = np.asarray(coeffs[cand["name"]], dtype=float)
        cal = empirical_mde(vals[:25])
        val = empirical_mde(vals[25:])
        summaries.append(
            {
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
            }
        )

    selected = select_calibration_candidate(summaries, threshold=CAL_THRESHOLD)
    result: dict[str, Any] = {
        "status": "REPAIR" if selected is None else "CALIBRATION_PASS",
        "spec_version": SPEC["spec_version"],
        "spec_sha256": base.sha256_file(SPEC_PATH),
        "method": "Independent stage2 rolling exact-200 pseudo-event tournament with rich pre-event controls and hierarchical industry fixed effects; selection uses only 25 earlier stage2 windows and validation uses 25 later untouched stage2 windows.",
        "calibration_threshold_mde80_return_units": CAL_THRESHOLD,
        "validation_threshold_mde80_return_units": VAL_THRESHOLD,
        "placebo_windows": 50,
        "calibration_windows": 25,
        "validation_windows": 25,
        "stage1_first_date": str(stage1_dates[0].date()),
        "stage1_last_date": str(stage1_dates[-1].date()),
        "stage2_first_date": str(selected_dates[0].date()),
        "stage2_last_date": str(selected_dates[-1].date()),
        "stage1_stage2_overlap_dates": 0,
        "frontier_nonzero_share": frontier_share,
        "candidate_order": [c["name"] for c in candidate_specs],
        "candidates": summaries,
        "outcome_inspected": "NO",
    }
    pd.DataFrame(rows).to_csv(base.OUT / "power_rescue_stage2_placebo_coefficients_v2.csv", index=False)
    if selected is None:
        (base.OUT / "power_rescue_stage2_v2.json").write_text(json.dumps(result, indent=2, sort_keys=True), encoding="utf-8")
        raise RuntimeError("no frozen stage2 candidate passed the <=0.45pp calibration MDE80 gate")
    result["selected_from_calibration"] = selected
    if float(selected["validation_mde80"]) > VAL_THRESHOLD:
        result["status"] = "VALIDATION_FAIL"
        (base.OUT / "power_rescue_stage2_v2.json").write_text(json.dumps(result, indent=2, sort_keys=True), encoding="utf-8")
        raise RuntimeError(
            f"stage2 calibration-selected candidate failed untouched validation: {selected['name']} validation MDE80={selected['validation_mde80_pp']:.4f}pp"
        )
    result["status"] = "PASS"
    result["selected"] = selected
    (base.OUT / "power_rescue_stage2_v2.json").write_text(json.dumps(result, indent=2, sort_keys=True), encoding="utf-8")
    return result, selected


def fit_actual(sample: pd.DataFrame, selected: dict[str, Any]) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    coef, primary, meta = r1.fit_actual_pre_event_model(sample, selected)
    extra = sample[["ticker", "sic", "exchange", "pre_event_form", "beta", "alpha"]].copy()
    primary = primary.merge(extra, on="ticker", how="left", validate="one_to_one")
    primary["hierarchical_industry_cell"] = hierarchical_industry_cell(primary)
    primary.rename(columns={"beta": "pre_event_market_beta_200", "alpha": "pre_event_alpha_200"}, inplace=True)
    meta["control_system"] = "log market cap + ROA + selected-model residual sigma + beta200 + alpha200 + hierarchical SIC4/SIC3/SIC2 FE + exchange FE + pre-event form FE"
    meta["stage2_spec_sha256"] = base.sha256_file(SPEC_PATH)
    return coef, primary, meta


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
    paper = SinglePaperState.load(str(paper_path)); director = DirectorState.load(str(director_path))
    action = ensure_action(paper, director, None)
    if paper.stage is not PaperStage.DESIGN_READY or not action or action.kind is not ActionKind.PRE_FREEZE_AUDIT:
        raise RuntimeError(f"expected DESIGN_READY/PRE_FREEZE_AUDIT, got {paper.stage}/{action}")
    answer = {
        "action_id": action.id,
        "decision": "REPAIR",
        "findings": [
            "The first empirical rescue candidate passed calibration but failed its untouched validation at 0.6577pp MDE80, above the unchanged 0.50pp threshold.",
            "Stage2 therefore uses an entirely earlier, nonoverlapping pre-event placebo block and richer pre-event precision controls; the failed validation block is not reused for selection.",
            "Astra event-window outcomes remain locked and uninspected.",
        ],
        "repair_summary": "Adopt immutable stage2 V2 design only after a frozen candidate passes <=0.45pp calibration MDE80 and the exact candidate passes <=0.50pp on untouched stage2 validation.",
        "novelty_closure": "REPAIR",
        "measurement": "REPAIR",
        "identification": "REPAIR",
        "power": "REPAIR",
        "access_licence_ethics": "PASS",
    }
    if apply_answer(paper, director, answer, None) != "REPAIR":
        raise RuntimeError("stage2 PRE_FREEZE_AUDIT repair answer did not apply")
    old = json.loads(json.dumps(director.records.get("design_closure", {})))
    if old.get("decision") != "PASS":
        raise RuntimeError("stage2 rescue requires preserved PASS pre-audit design closure")
    director.records["design_closure_v1_pre_audit_repair"] = {"updated_at": base.now(), **old}

    chosen_col = "frontier_ai_exposure_z" if selected["exposure"] == "frontier" else "primary_ai_exposure_z"
    expected = "SPY exact-200" if selected["model"] == "SPY" else "exact-200 SPY + (QQQ-SPY) + (IWM-SPY)"
    cross = "OLS" if selected["cross"] == "OLS" else "bounded inverse-residual-variance WLS"
    screen = "none" if selected["screen_q"] is None else f"selected-model residual volatility <=p{int(float(selected['screen_q'])*100)}"
    new = json.loads(json.dumps(old))
    new.update(
        {
            "decision": "PASS",
            "design_version": "ASTRA_EVENT_DESIGN_V2_STAGE2_RESCUED",
            "sample_definition": f"Primary N={actual_meta['actual_primary_n']}; truthful-fulltext issuer with exact selected expected-return model, complete rich pre-event controls, volatility screen={screen}, and NVDA excluded for known direct Sep3 confound; no outcome-dependent exclusions.",
            "treatment": f"{chosen_col}; exposure choice and estimator fixed before Astra outcome access.",
            "controls": [
                "log market capitalization",
                "profitability ROA",
                "selected expected-return residual sigma",
                "pre-event market beta200",
                "pre-event alpha200",
                "hierarchical SIC4/SIC3/SIC2 fixed effects with >=20-firm support rule",
                "exchange fixed effects",
                "pre-event filing-form fixed effects",
            ],
            "models": [
                f"PRIMARY: {expected} expected-return model and cross-sectional {cross}; CAR[0,+1] on {chosen_col} plus frozen rich controls; volatility screen={screen}.",
                "Inference sensitivity: SIC2-clustered standard errors where support is adequate.",
                "Expected-return sensitivities: SPY exact-120/exact-250 and QQQ/IWM benchmark alternatives, never promoted by significance.",
                "Timing sensitivity: AR[0] and AR[+1] separately.",
                "Exposure sensitivity: broad and frontier/generative filing intensities, any-core-AI indicator, and section-specific intensities where available.",
                "Confound sensitivity: include-all and corrected clean-news samples excluding Sep3/Sep4 SEC current-report flags.",
            ],
            "dataset_hashes": {
                "analysis_ready_data_v3_20260907/firm_pre_event_controls_complete.csv": base.SOURCE["firm_pre_event_controls_complete.csv"][1],
                "analysis_ready_data_v3_20260907/firm_pre_event_price_metrics.csv": base.SOURCE["firm_pre_event_price_metrics.csv"][1],
                "analysis_ready_data_v3_20260907/us_equity_daily_prices_20250101_20260904.parquet": base.SOURCE["us_equity_daily_prices_20250101_20260904.parquet"][1],
                f"{base.DESIGN_V2}/astra_pre_event_ai_exposure_v2.csv": hashes["astra_pre_event_ai_exposure_v2.csv"],
                f"{base.DESIGN_V2}/event_day_sec_current_report_flags_v2.csv": hashes["event_day_sec_current_report_flags_v2.csv"],
                f"{base.DESIGN_V2}/expected_return_coefficients_v2.csv": hashes["expected_return_coefficients_v2.csv"],
                f"{base.DESIGN_V2}/primary_adjusted_sample_v2.csv": hashes["primary_adjusted_sample_v2.csv"],
                f"{base.DESIGN_V2}/power_rescue_stage2_v2.json": hashes["power_rescue_stage2_v2.json"],
                "ops/astra_stage2_power_rescue_spec.json": base.sha256_file(SPEC_PATH),
            },
            "power": {
                "status": "PASS",
                "method": rescue["method"],
                "plausible_effect": "Stage2 calibration required MDE80 <=0.45pp; untouched validation retained the original <=0.50pp requirement.",
                "mde": f"Calibration MDE80={selected['calibration_mde80_pp']:.4f}pp; untouched validation MDE80={selected['validation_mde80_pp']:.4f}pp per one-SD exposure.",
                "evidence": f"25 calibration + 25 later validation pseudo-events from an earlier 100-date block with zero overlap with first-rescue dates; minimum N={selected['min_window_n']}; Astra outcomes not accessed.",
            },
        }
    )
    director.records["design_closure"] = {"updated_at": base.now(), **new}
    director.records["design_repair_v2_stage2"] = {
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
        "stage2_spec_sha256": base.sha256_file(SPEC_PATH),
        "outcome_inspected": "NO",
    }
    paper.active_paper.primary_exposure = new["treatment"]
    paper.active_paper.intended_design = str(new["design"])
    paper.active_paper.primary_outcome = str(new["outcome"])
    paper.save(); director.save()
    next_action = ensure_action(paper, director, None)
    if not next_action or next_action.kind is not ActionKind.PRE_FREEZE_AUDIT:
        raise RuntimeError(f"expected fresh PRE_FREEZE_AUDIT after stage2 repair, got {next_action}")
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
    sample = enrich_sample(base.adjusted_sample(exposure_v2, metrics, controls), controls)
    rescue, selected = stage2_tournament(sample, exposure_qa["frontier_nonzero_share"])
    coef_df, primary, actual_meta = fit_actual(sample, selected)

    chosen_col = "frontier_ai_exposure_z" if selected["exposure"] == "frontier" else "primary_ai_exposure_z"
    exposure_v2["chosen_primary_exposure"] = chosen_col
    exposure_v2["outcome_inspected"] = "NO"
    exposure_v2.sort_values("ticker").to_csv(base.OUT / "astra_pre_event_ai_exposure_v2.csv", index=False)
    coef_df.sort_values("ticker").to_csv(base.OUT / "expected_return_coefficients_v2.csv", index=False)
    primary.sort_values("ticker").to_csv(base.OUT / "primary_adjusted_sample_v2.csv", index=False)

    event_v2 = json.loads(json.dumps(event_v1))
    event_v2["version"] = "ASTRA_EVENT_DESIGN_V2_STAGE2_RESCUED"
    event_v2["generated_at_utc"] = base.now()
    event_v2["primary_exposure"] = {"column": chosen_col, "selection": "Frozen stage2 pre-outcome calibration/validation rule."}
    event_v2["expected_return"]["primary"] = "SPY exact-200" if selected["model"] == "SPY" else "Exact-200 SPY + (QQQ-SPY) + (IWM-SPY)"
    event_v2["primary_cross_sectional_estimator"] = {
        "estimator": selected["cross"],
        "volatility_screen_quantile": selected["screen_q"],
        "rich_controls": SPEC["cross_sectional_controls"],
        "actual_primary_n": actual_meta["actual_primary_n"],
    }
    event_v2["known_confound_policy"]["event_day_current_report_rule"] = "Corrected CIK/date-normalized Sep3/Sep4 EDGAR 8-K/6-K flags are clean-news robustness only; no automatic primary exclusion."
    event_v2["power_rescue_stage2"] = rescue
    event_v2["outcome_inspected"] = "NO"
    (base.OUT / "event_design_v2.json").write_text(json.dumps(event_v2, indent=2, sort_keys=True), encoding="utf-8")

    repair = {
        "status": "PASS",
        "paper_id": base.PAPER_ID,
        "generated_at_utc": base.now(),
        "git_commit_sha": base.git_sha(),
        "spec_sha256": base.sha256_file(SPEC_PATH),
        "selected_candidate": selected["name"],
        "frontier_nonzero_share": exposure_qa["frontier_nonzero_share"],
        "corrected_event_day_flagged_firms": flags_qa["flagged_unique_firms"],
        "calibration_mde80_percentage_points": selected["calibration_mde80_pp"],
        "validation_mde80_percentage_points": selected["validation_mde80_pp"],
        "actual_primary_n": actual_meta["actual_primary_n"],
        "outcome_inspected": "NO",
        "declaration": "Astra event-window firm returns, abnormal returns, CARs and outcome classifications were not read, calculated or summarized during stage2 pre-freeze rescue."
    }
    (base.OUT / "pre_freeze_repair_v2.json").write_text(json.dumps(repair, indent=2, sort_keys=True), encoding="utf-8")

    files = [
        "astra_pre_event_ai_exposure_v2.csv",
        "event_day_sec_current_report_flags_v2.csv",
        "event_day_sec_current_report_flags_v2_qa.json",
        "expected_return_coefficients_v2.csv",
        "power_rescue_stage2_v2.json",
        "power_rescue_stage2_placebo_coefficients_v2.csv",
        "primary_adjusted_sample_v2.csv",
        "event_design_v2.json",
        "pre_freeze_repair_v2.json",
    ]
    manifest = {
        "paper_id": base.PAPER_ID,
        "design_inputs": base.DESIGN_V2,
        "generated_at_utc": base.now(),
        "git_commit_sha": base.git_sha(),
        "stage2_spec_sha256": base.sha256_file(SPEC_PATH),
        "outcome_inspected": "NO",
        "source_hashes": {name: sha for name, (_, sha) in base.SOURCE.items()},
        "files": [{"name": n, "bytes": (base.OUT / n).stat().st_size, "sha256": base.sha256_file(base.OUT / n)} for n in files],
    }
    (base.OUT / "design_input_manifest_v2.json").write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
    files.append("design_input_manifest_v2.json")
    folder_id, hashes = base.publish(client, files)
    state = update_state(client, folder_id, hashes, selected, rescue, exposure_qa, flags_qa, actual_meta)
    print(json.dumps({"status": "PASS", "design_inputs": base.DESIGN_V2, "selected": selected, "actual": actual_meta, "event_flags_qa": flags_qa, "state": state, "outcome_inspected": "NO"}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
