"""Strict outcome-blind pre-event price metrics for event studies."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

import numpy as np
import pandas as pd

CUTOFF_DATE = "2026-08-06"
BENCHMARK = "SPY"
WINDOWS = (120, 200, 250)


@dataclass(frozen=True)
class BetaEstimate:
    n: int
    alpha: float
    beta: float
    residual_vol: float
    first_date: str
    last_date: str


def _ols(y: np.ndarray, x: np.ndarray) -> tuple[float, float, float]:
    X = np.column_stack([np.ones(len(x)), x])
    coef, *_ = np.linalg.lstsq(X, y, rcond=None)
    resid = y - X @ coef
    resid_vol = float(np.std(resid, ddof=2)) if len(resid) > 2 else float("nan")
    return float(coef[0]), float(coef[1]), resid_vol


def exact_window_beta(paired_returns: pd.DataFrame, window: int) -> BetaEstimate | None:
    """Estimate beta only when at least ``window`` paired return rows exist.

    The latest exactly ``window`` observations are used. No smaller sample is
    ever labelled as a 120/200/250-observation beta.
    """
    if len(paired_returns) < window:
        return None
    use = paired_returns.sort_values("Date").tail(window)
    if len(use) != window:
        raise AssertionError("exact beta window could not be constructed")
    alpha, beta, resid = _ols(
        use["firm_return"].to_numpy(dtype=float),
        use["benchmark_return"].to_numpy(dtype=float),
    )
    return BetaEstimate(
        n=window,
        alpha=alpha,
        beta=beta,
        residual_vol=resid,
        first_date=str(use["Date"].iloc[0].date()),
        last_date=str(use["Date"].iloc[-1].date()),
    )


def build_strict_pre_event_price_metrics(
    prices: pd.DataFrame,
    candidate_tickers: Iterable[str],
    *,
    cutoff: str = CUTOFF_DATE,
    benchmark: str = BENCHMARK,
) -> pd.DataFrame:
    required = {"Date", "Ticker", "Close"}
    missing = required - set(prices.columns)
    if missing:
        raise ValueError(f"price panel missing required columns: {sorted(missing)}")

    px = prices.copy()
    px["Date"] = pd.to_datetime(px["Date"], errors="coerce")
    px["Ticker"] = px["Ticker"].astype(str)
    px = px.loc[px["Date"].notna() & (px["Date"] <= pd.Timestamp(cutoff))].copy()
    if (px["Date"] > pd.Timestamp(cutoff)).any():
        raise AssertionError("post-cutoff price escaped filter")

    price_col = "Adj Close" if "Adj Close" in px.columns else "Close"
    px[price_col] = pd.to_numeric(px[price_col], errors="coerce")
    px["Close"] = pd.to_numeric(px["Close"], errors="coerce")
    px = px.sort_values(["Ticker", "Date"])

    bench = px.loc[px["Ticker"] == benchmark, ["Date", price_col]].dropna().copy()
    bench = bench.drop_duplicates("Date", keep="last").sort_values("Date")
    if bench.empty:
        raise ValueError(f"benchmark {benchmark} is absent from pre-event panel")
    bench["benchmark_return"] = bench[price_col].pct_change(fill_method=None)
    bench_returns = bench[["Date", "benchmark_return"]].dropna()

    rows: list[dict] = []
    for ticker in list(candidate_tickers):
        f = px.loc[px["Ticker"] == str(ticker)].copy()
        f = f.drop_duplicates("Date", keep="last").sort_values("Date")
        valid_price = f.loc[f[price_col].notna() & (f[price_col] > 0)].copy()
        n_prices = int(len(valid_price))
        if n_prices:
            valid_price["firm_return"] = valid_price[price_col].pct_change(fill_method=None)
            paired = valid_price[["Date", "firm_return"]].dropna().merge(
                bench_returns, on="Date", how="inner", validate="one_to_one"
            )
            paired = paired.replace([np.inf, -np.inf], np.nan).dropna()
            first_price = str(valid_price["Date"].iloc[0].date())
            last_price = str(valid_price["Date"].iloc[-1].date())
            close_rows = f.loc[f["Close"].notna()]
            pre_close = float(close_rows["Close"].iloc[-1]) if not close_rows.empty else np.nan
            adj_rows = f.loc[f[price_col].notna()]
            pre_adj = float(adj_rows[price_col].iloc[-1]) if not adj_rows.empty else np.nan
        else:
            paired = pd.DataFrame(columns=["Date", "firm_return", "benchmark_return"])
            first_price = last_price = ""
            pre_close = pre_adj = np.nan

        row = {
            "ticker": str(ticker),
            "pre_event_price_obs": n_prices,
            "pre_event_first_price_date": first_price,
            "pre_event_last_price_date": last_price,
            "pre_event_price_close": pre_close,
            "pre_event_price_adj_close": pre_adj,
            "valid_paired_pre_event_return_obs": int(len(paired)),
            "has_full_requested_history": bool(n_prices >= int(px[px["Ticker"] == benchmark]["Date"].nunique())),
            "beta_benchmark": benchmark,
            "cutoff_date": cutoff,
        }
        for w in WINDOWS:
            est = exact_window_beta(paired, w)
            row[f"has_{w}_pre_event_obs"] = bool(len(paired) >= w)
            row[f"market_beta_{w}"] = est.beta if est else np.nan
            row[f"alpha_{w}"] = est.alpha if est else np.nan
            row[f"residual_vol_{w}"] = est.residual_vol if est else np.nan
            row[f"beta_{w}_n"] = est.n if est else 0
            row[f"beta_{w}_first_return_date"] = est.first_date if est else ""
            row[f"beta_{w}_last_return_date"] = est.last_date if est else ""
            row[f"beta_{w}_missing_reason"] = "" if est else f"INSUFFICIENT_PAIRED_RETURNS_LT_{w}"
        rows.append(row)

    out = pd.DataFrame(rows)
    for w in WINDOWS:
        bad = out[out[f"market_beta_{w}"].notna() & (out[f"beta_{w}_n"] != w)]
        if not bad.empty:
            raise AssertionError(f"{len(bad)} beta_{w} rows violate exact-N invariant")
    return out
