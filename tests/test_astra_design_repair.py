import numpy as np
import pandas as pd
import pytest

from coscientist.astra_design_repair import (
    choose_primary_exposure,
    empirical_mde,
    normalize_cik,
    normalize_sec_filed_date,
    parse_sec_master_current_reports,
    placebo_coefficients,
    residualized_exposure,
    select_calibration_candidate,
    weighted_residualized_exposure,
)


def test_normalize_cik_handles_decimal_padding_and_noise():
    assert normalize_cik("0001397187") == "1397187"
    assert normalize_cik("1397187.0") == "1397187"
    assert normalize_cik(" 000802481.000 ") == "802481"
    assert normalize_cik("CIK0000012345") == "12345"
    assert normalize_cik("") == ""


def test_parse_sec_master_current_reports_keeps_only_requested_forms():
    text = "\n".join([
        "1397187|LULULEMON ATHLETICA INC.|8-K|20260903|edgar/data/1397187/a.htm",
        "802481|PILGRIMS PRIDE CORP|8-K|20260904|edgar/data/802481/b.htm",
        "123|OTHER CO|10-K|20260903|edgar/data/123/c.htm",
        "not|a|valid|master|row|extra",
    ])
    got = parse_sec_master_current_reports(text, {"8-K", "8-K/A", "6-K", "6-K/A"})
    assert list(got["cik_norm"]) == ["1397187", "802481"]
    assert set(got["form"]) == {"8-K"}
    assert set(got["filed"]) == {"2026-09-03", "2026-09-04"}
    assert normalize_sec_filed_date("2026-09-03") == "2026-09-03"


def test_residualized_exposure_is_orthogonal_to_controls():
    x = np.array([0.2, 1.1, -0.3, 2.0, 0.5, 1.7], dtype=float)
    z = np.array([-2, -1, 0, 1, 2, 3], dtype=float)
    controls = np.column_stack([np.ones(len(x)), z])
    resid, sxx = residualized_exposure(x, controls)
    assert sxx > 0
    assert np.max(np.abs(controls.T @ resid)) < 1e-10

    weights = np.array([1.0, 2.0, 0.8, 1.5, 0.7, 2.5], dtype=float)
    resid_w, sxx_w, sqrt_w = weighted_residualized_exposure(x, controls, weights)
    assert sxx_w > 0
    assert np.max(np.abs((controls * sqrt_w[:, None]).T @ resid_w)) < 1e-10
    y = np.array([0.01, -0.02, 0.015, 0.00, 0.025, -0.01])
    beta_fwl = placebo_coefficients(resid_w, sxx_w, (y * sqrt_w)[:, None])[0]
    X_full = np.column_stack([controls, x])
    sw = sqrt_w[:, None]
    beta_direct = np.linalg.lstsq(X_full * sw, y * sqrt_w, rcond=None)[0][-1]
    assert beta_fwl == pytest.approx(beta_direct)


def test_empirical_mde_uses_placebo_coefficient_dispersion():
    rx = np.array([-1.5, -0.5, 0.5, 1.5], dtype=float)
    sxx = float(rx @ rx)
    # 25 common pseudo-event windows with cross-firm patterns retained by column.
    scale = np.linspace(-0.02, 0.02, 25)
    Y = np.outer(rx, scale)
    b = placebo_coefficients(rx, sxx, Y)
    assert np.allclose(b, scale)
    result = empirical_mde(b)
    expected_sd = float(np.std(scale, ddof=1))
    assert result.windows == 25
    assert result.coefficient_sd == pytest.approx(expected_sd)
    assert result.mde80 == pytest.approx((1.959963984540054 + 0.8416212335729143) * expected_sd)
    with pytest.raises(ValueError):
        empirical_mde(scale[:19])


def test_primary_exposure_promotion_rule_is_outcome_blind_and_fail_closed():
    assert choose_primary_exposure(frontier_nonzero_share=.40, frontier_mde80=.004, broad_mde80=.003) == "frontier_ai_exposure_z"
    assert choose_primary_exposure(frontier_nonzero_share=.10, frontier_mde80=.004, broad_mde80=.003) == "primary_ai_exposure_z"
    assert choose_primary_exposure(frontier_nonzero_share=.40, frontier_mde80=.006, broad_mde80=.004) == "primary_ai_exposure_z"
    assert choose_primary_exposure(frontier_nonzero_share=.10, frontier_mde80=.006, broad_mde80=.006) == "POWER_REPAIR_REQUIRED"

    candidates = [
        {"name": "baseline", "calibration_mde80": .007},
        {"name": "wls", "calibration_mde80": .0049},
        {"name": "screen10", "calibration_mde80": .0040},
    ]
    assert select_calibration_candidate(candidates)["name"] == "wls"
    assert select_calibration_candidate(candidates, threshold=.003) is None
