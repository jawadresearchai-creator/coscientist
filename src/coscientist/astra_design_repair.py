"""Outcome-blind primitives for Astra pre-freeze design repair.

These helpers operate only on pre-event metadata, exposure and return residuals.
They never inspect Astra event-window outcomes.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Iterable

import numpy as np
import pandas as pd


_CIK_DECIMAL = re.compile(r"^\s*(\d+)(?:\.0+)?\s*$")
_SEC_COMPACT_DATE = re.compile(r"^(\d{4})(\d{2})(\d{2})$")


def normalize_cik(value: object) -> str:
    """Return SEC CIK as an unpadded digit string."""
    s = str(value or "").strip()
    if not s:
        return ""
    m = _CIK_DECIMAL.match(s)
    if not m:
        digits = re.sub(r"\D", "", s)
        if not digits:
            return ""
    else:
        digits = m.group(1)
    digits = digits.lstrip("0")
    return digits or "0"


def normalize_sec_filed_date(value: object) -> str:
    """Normalize EDGAR master-index Date Filed values to ISO YYYY-MM-DD."""
    s = str(value or "").strip()
    if not s:
        return ""
    m = _SEC_COMPACT_DATE.fullmatch(s)
    if m:
        return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
    return s


def parse_sec_master_current_reports(text: str, forms: Iterable[str]) -> pd.DataFrame:
    wanted = {str(x).upper() for x in forms}
    rows: list[dict[str, str]] = []
    for line in str(text).splitlines():
        parts = line.split("|")
        if len(parts) != 5:
            continue
        cik, company, form, filed, filename = [p.strip() for p in parts]
        norm = normalize_cik(cik)
        if not norm or form.upper() not in wanted:
            continue
        rows.append({
            "cik_norm": norm,
            "cik": cik,
            "company": company,
            "form": form.upper(),
            "filed": normalize_sec_filed_date(filed),
            "filename": filename,
        })
    return pd.DataFrame(rows)


def residualized_exposure(x: np.ndarray, controls: np.ndarray) -> tuple[np.ndarray, float]:
    """Residualize one exposure on a fixed pre-event control matrix."""
    x = np.asarray(x, dtype=float)
    X = np.asarray(controls, dtype=float)
    if x.ndim != 1 or X.ndim != 2 or len(x) != len(X):
        raise ValueError("x and controls have incompatible shapes")
    coef, *_ = np.linalg.lstsq(X, x, rcond=None)
    resid = x - X @ coef
    sxx = float(np.dot(resid, resid))
    if not np.isfinite(sxx) or sxx <= 0:
        raise ValueError("exposure has no residual variation after controls")
    return resid, sxx


def weighted_residualized_exposure(
    x: np.ndarray, controls: np.ndarray, weights: np.ndarray
) -> tuple[np.ndarray, float, np.ndarray]:
    """Residualize exposure for WLS and return whitened residuals/weights."""
    x = np.asarray(x, dtype=float)
    X = np.asarray(controls, dtype=float)
    w = np.asarray(weights, dtype=float)
    if x.ndim != 1 or X.ndim != 2 or w.ndim != 1 or len(x) != len(X) or len(x) != len(w):
        raise ValueError("x, controls and weights have incompatible shapes")
    if not np.isfinite(w).all() or np.any(w <= 0):
        raise ValueError("weights must be finite and strictly positive")
    sqrt_w = np.sqrt(w)
    xw = x * sqrt_w
    Xw = X * sqrt_w[:, None]
    coef, *_ = np.linalg.lstsq(Xw, xw, rcond=None)
    resid_w = xw - Xw @ coef
    sxx = float(np.dot(resid_w, resid_w))
    if not np.isfinite(sxx) or sxx <= 0:
        raise ValueError("weighted exposure has no residual variation after controls")
    return resid_w, sxx, sqrt_w


def conservative_inverse_variance_weights(sigma: np.ndarray) -> tuple[np.ndarray, dict[str, float]]:
    """Build bounded pre-event precision weights without rewarding invalid sigma.

    Strictly positive finite residual sigmas are clipped to their p10/p90 range.
    Zero/non-finite sigma values are assigned the p90 sigma before inversion, so
    stale/degenerate histories receive the *lowest* allowed precision weight,
    never an infinite or dominant weight.
    """
    s = np.asarray(sigma, dtype=float)
    positive = s[np.isfinite(s) & (s > 0)]
    if len(positive) < 2:
        raise ValueError("insufficient positive residual sigmas for precision weighting")
    lo, hi = float(np.quantile(positive, 0.10)), float(np.quantile(positive, 0.90))
    if not (np.isfinite(lo) and np.isfinite(hi) and lo > 0 and hi >= lo):
        raise ValueError("invalid residual-sigma clipping bounds")
    bad = ~np.isfinite(s) | (s <= 0)
    safe = np.where(bad, hi, s)
    clipped = np.clip(safe, lo, hi)
    w = 1.0 / np.square(clipped)
    w = w / float(np.mean(w))
    ess = float(np.square(w.sum()) / np.square(w).sum())
    return w, {
        "sigma_clip_p10": lo,
        "sigma_clip_p90": hi,
        "invalid_or_zero_sigma_downweighted": float(bad.sum()),
        "weight_effective_n": ess,
    }


def placebo_coefficients(residual_x: np.ndarray, sxx: float, placebo_y: np.ndarray) -> np.ndarray:
    """FWL exposure coefficients for many placebo outcomes with fixed controls."""
    rx = np.asarray(residual_x, dtype=float)
    Y = np.asarray(placebo_y, dtype=float)
    if Y.ndim != 2 or Y.shape[0] != len(rx):
        raise ValueError("placebo_y must be n_firms x n_windows")
    if not np.isfinite(Y).all():
        raise ValueError("placebo outcomes must be finite on the balanced sample")
    if sxx <= 0:
        raise ValueError("sxx must be positive")
    return (rx @ Y) / float(sxx)


@dataclass(frozen=True)
class EmpiricalMDE:
    windows: int
    coefficient_sd: float
    mde80: float
    mde90: float
    placebo_abs_p95: float


def empirical_mde(coefficients: Iterable[float]) -> EmpiricalMDE:
    b = np.asarray(list(coefficients), dtype=float)
    b = b[np.isfinite(b)]
    if len(b) < 20:
        raise ValueError("empirical MDE requires at least 20 placebo windows")
    sd = float(np.std(b, ddof=1))
    if not np.isfinite(sd) or sd <= 0:
        raise ValueError("placebo coefficient dispersion is zero or invalid")
    z975, z80, z90 = 1.959963984540054, 0.8416212335729143, 1.2815515655446004
    return EmpiricalMDE(
        windows=int(len(b)),
        coefficient_sd=sd,
        mde80=float((z975 + z80) * sd),
        mde90=float((z975 + z90) * sd),
        placebo_abs_p95=float(np.quantile(np.abs(b), 0.95)),
    )


def choose_primary_exposure(*, frontier_nonzero_share: float, frontier_mde80: float,
                            broad_mde80: float, min_frontier_share: float = 0.25,
                            max_mde80: float = 0.005) -> str:
    """Pre-outcome deterministic promotion rule for Astra-specific exposure."""
    if frontier_nonzero_share >= min_frontier_share and frontier_mde80 <= max_mde80:
        return "frontier_ai_exposure_z"
    if broad_mde80 <= max_mde80:
        return "primary_ai_exposure_z"
    return "POWER_REPAIR_REQUIRED"


def select_calibration_candidate(
    candidates: list[dict], *, threshold: float = 0.005
) -> dict | None:
    """Select the simplest predeclared candidate that passes calibration."""
    for candidate in candidates:
        mde = float(candidate.get("calibration_mde80", float("inf")))
        if np.isfinite(mde) and mde <= threshold:
            return candidate
    return None
