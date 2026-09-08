"""Availability-corrected launcher for the frozen Astra stage2 power rescue.

The first stage2 attempt stopped before candidate evaluation because the fixed
pre-event panel has 198 eligible dates. This launcher preserves the frozen
candidate family and thresholds, uses the 98 untouched dates preceding the
stage1 block, and splits them into 24 calibration + 25 validation windows.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

SCRIPTS = Path(__file__).resolve().parent
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import rescue_astra_pre_freeze_power_stage2 as s2
from coscientist.astra_design_repair import empirical_mde, select_calibration_candidate

SPEC = json.loads(Path("ops/astra_stage2_power_rescue_spec.json").read_text(encoding="utf-8"))
CAL_THRESHOLD = float(SPEC["calibration_mde80_max_return_units"])
VAL_THRESHOLD = float(SPEC["validation_mde80_max_return_units"])


def corrected_stage2_tournament(sample: pd.DataFrame, frontier_share: float) -> tuple[dict[str, Any], dict[str, Any]]:
    rets, factors = s2.r1._factor_panel(sample)
    common = factors.dropna().index
    eligible = [d for d in common if len(common[common < d]) >= 200]
    if len(eligible) != 198:
        raise RuntimeError(f"availability-corrected stage2 spec requires exactly 198 eligible dates, got {len(eligible)}")
    stage1_dates = eligible[-100:]
    selected_dates = eligible[:-100]
    if len(selected_dates) != 98:
        raise RuntimeError(f"expected exactly 98 untouched stage2 dates, got {len(selected_dates)}")
    if set(stage1_dates) & set(selected_dates):
        raise RuntimeError("stage2 placebo block overlaps stage1 placebo block")
    windows = list(zip(selected_dates[0::2], selected_dates[1::2]))
    if len(windows) != 49:
        raise RuntimeError(f"expected 49 stage2 placebo windows, got {len(windows)}")

    exposure_order = ["frontier"] if frontier_share >= 0.25 else []
    exposure_order.append("broad")
    candidate_specs: list[dict[str, Any]] = []
    for cfg in s2.r1.ESTIMATOR_ORDER:
        for exposure in exposure_order:
            candidate_specs.append({**cfg, "exposure": exposure, "name": s2.r1._candidate_name(cfg, exposure) + "__RICH"})

    coeffs = {c["name"]: [] for c in candidate_specs}
    ns = {c["name"]: [] for c in candidate_specs}
    rows: list[dict[str, Any]] = []
    for wi, (d1, d2) in enumerate(windows, 1):
        payloads: dict[str, tuple[pd.DataFrame, np.ndarray, np.ndarray]] = {}
        for model in ["SPY", "MULTI3"]:
            payload = s2.r1._pseudo_event_for_model(sample, rets, factors, d1, d2, model)
            if payload is None:
                raise RuntimeError(f"could not build {model} stage2 pseudo-event {d1.date()}/{d2.date()}")
            payloads[model] = payload
        row: dict[str, Any] = {"window": wi, "date_1": str(d1.date()), "date_2": str(d2.date()), "outcome_inspected": "NO"}
        for cand in candidate_specs:
            frame, car, sigma = payloads[cand["model"]]
            exposure_col = "frontier" if cand["exposure"] == "frontier" else "broad"
            b, n, _ = s2.r1._coef_for_candidate(frame, car, sigma, cand, exposure_col)
            coeffs[cand["name"]].append(b)
            ns[cand["name"]].append(n)
            row[cand["name"]] = b
            row[cand["name"] + "__n"] = n
        rows.append(row)

    summaries: list[dict[str, Any]] = []
    for cand in candidate_specs:
        vals = np.asarray(coeffs[cand["name"]], dtype=float)
        cal = empirical_mde(vals[:24])
        val = empirical_mde(vals[24:])
        summaries.append(
            {
                **cand,
                "calibration_windows": 24,
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
        "spec_sha256": s2.base.sha256_file(Path("ops/astra_stage2_power_rescue_spec.json")),
        "method": "Independent stage2 rolling exact-200 pseudo-event tournament on all 98 untouched dates preceding the stage1 block, with rich pre-event controls and hierarchical industry fixed effects; 24 calibration windows select one frozen candidate and 25 later windows validate it once.",
        "calibration_threshold_mde80_return_units": CAL_THRESHOLD,
        "validation_threshold_mde80_return_units": VAL_THRESHOLD,
        "placebo_windows": 49,
        "calibration_windows": 24,
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
    pd.DataFrame(rows).to_csv(s2.base.OUT / "power_rescue_stage2_placebo_coefficients_v2.csv", index=False)
    if selected is None:
        (s2.base.OUT / "power_rescue_stage2_v2.json").write_text(json.dumps(result, indent=2, sort_keys=True), encoding="utf-8")
        raise RuntimeError("no frozen stage2 candidate passed the <=0.45pp calibration MDE80 gate")
    result["selected_from_calibration"] = selected
    if float(selected["validation_mde80"]) > VAL_THRESHOLD:
        result["status"] = "VALIDATION_FAIL"
        (s2.base.OUT / "power_rescue_stage2_v2.json").write_text(json.dumps(result, indent=2, sort_keys=True), encoding="utf-8")
        raise RuntimeError(
            f"stage2 calibration-selected candidate failed untouched validation: {selected['name']} validation MDE80={selected['validation_mde80_pp']:.4f}pp"
        )
    result["status"] = "PASS"
    result["selected"] = selected
    (s2.base.OUT / "power_rescue_stage2_v2.json").write_text(json.dumps(result, indent=2, sort_keys=True), encoding="utf-8")
    return result, selected


s2.stage2_tournament = corrected_stage2_tournament

if __name__ == "__main__":
    raise SystemExit(s2.main())
