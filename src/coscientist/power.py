"""G4: the power preflight.

This is the gate that would have killed C716 in an hour instead of after a
design freeze, an independent revalidation and a 28-quarter panel build.

Using ONLY pre-period data -- never a post-treatment outcome, never a
treated-vs-control contrast -- estimate how small an effect the design could
detect, and compare it to the effect the mechanism could plausibly produce.
If the smallest detectable effect exceeds the largest plausible one, no
amount of careful execution rescues the study.

A note on the design effect, because it is easy to get wrong. If residuals
are demeaned by unit AND the inference clusters on unit, the unit variance
component has already been absorbed and the measured ICC collapses to zero --
flattering the design. So two bounds are computed:

  optimistic   residualised on unit and time FE       (lower MDE)
  conservative residualised on time FE only           (higher MDE)

The kill verdict uses the OPTIMISTIC bound. A gate that ends candidates should
only fire when even the most favourable assumption says the study is hopeless;
false kills were an expensive failure mode in earlier versions.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any

import numpy as np
import pandas as pd
from scipy import stats


class PowerError(ValueError):
    pass


@dataclass
class PowerResult:
    n_obs: int
    n_clusters: int
    mean_cluster_size: float
    residual_sd: float
    icc: float                     # optimistic (unit+time FE residuals)
    icc_conservative: float        # time FE only
    design_effect: float
    design_effect_conservative: float
    effective_n: float
    treated_share: float
    standard_error: float
    mde: float                     # optimistic; the verdict uses this
    mde_conservative: float
    plausible_effect: float | None
    ratio: float | None            # mde / plausible_effect; < 1 is good
    powered: bool | None
    alpha: float
    power: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def summary(self) -> str:
        base = (
            f"MDE={self.mde:.4g} (conservative {self.mde_conservative:.4g}) "
            f"[sd={self.residual_sd:.4g}, icc={self.icc:.3f}, "
            f"G={self.n_clusters}, DEFF={self.design_effect:.2f}]"
        )
        if self.plausible_effect is None:
            return base + " | no plausible effect supplied"
        return base + (
            f" | plausible={self.plausible_effect:.4g}"
            f" | ratio={self.ratio:.3g}"
            f" | {'POWERED' if self.powered else 'UNDERPOWERED'}"
        )


def _residualise(df, unit, time, outcome, absorb_unit: bool):
    y = df[outcome].astype(float)
    grand = y.mean()
    time_mean = df.groupby(time)[outcome].transform("mean")
    if not absorb_unit:
        return y - time_mean + grand
    unit_mean = df.groupby(unit)[outcome].transform("mean")
    return y - unit_mean - time_mean + grand


def _moulton_icc(resid: pd.Series, cluster: pd.Series) -> tuple[float, float]:
    """Average pairwise within-cluster residual correlation.

    The estimator that actually drives clustered-SE inflation:
        rho = mean within-cluster pairwise covariance / total variance
    Computed without materialising pairs -- for a cluster with residual sum S
    and sum of squares Q, the pairwise product total is (S^2 - Q) / 2.
    """
    frame = pd.DataFrame({"r": np.asarray(resid, dtype=float), "g": np.asarray(cluster)}).dropna()
    if frame.empty:
        raise PowerError("no non-missing residuals to compute ICC")

    grouped = frame.groupby("g")["r"]
    sizes = grouped.size().to_numpy(dtype=float)
    mean_size = float(sizes.mean())

    total_var = float(frame["r"].var(ddof=1))
    if not np.isfinite(total_var) or total_var <= 0:
        return 0.0, mean_size

    sums = grouped.sum().to_numpy(dtype=float)
    sumsq = grouped.apply(lambda s: float(np.sum(np.square(s)))).to_numpy(dtype=float)
    pair_cov_total = float(np.sum((sums**2 - sumsq) / 2.0))
    n_pairs = float(np.sum(sizes * (sizes - 1.0) / 2.0))
    if n_pairs <= 0:
        return 0.0, mean_size

    rho = (pair_cov_total / n_pairs) / total_var
    if not np.isfinite(rho):
        return 0.0, mean_size
    return float(min(max(rho, 0.0), 0.999)), mean_size


def power_preflight(
    panel: pd.DataFrame,
    *,
    unit: str,
    time: str,
    outcome: str,
    pre_period_end: Any,
    cluster: str | None = None,
    treated_share: float = 0.5,
    plausible_effect: float | None = None,
    alpha: float = 0.05,
    power: float = 0.80,
) -> PowerResult:
    """Minimum detectable effect from pre-period data alone.

    `pre_period_end` is REQUIRED and enforced, not merely documented. Every
    row must satisfy `time < pre_period_end`; for staggered adoption, pass the
    EARLIEST treatment time in the sample.

    That argument exists because this docstring used to assert "pre-period
    only" and then check nothing. The most important anti-leakage claim in the
    engine rested entirely on caller discipline, which is to say on nothing at
    all: an assertion a machine does not enforce is a comment, and comments do
    not hold a lock shut.
    """
    for col in (unit, time, outcome):
        if col not in panel.columns:
            raise PowerError(f"panel missing required column {col!r}")
    if not 0.0 < treated_share < 1.0:
        raise PowerError("treated_share must lie strictly between 0 and 1")
    if pre_period_end is None:
        raise PowerError(
            "pre_period_end is required: the gate must be able to prove that no "
            "post-treatment observation ever reached it"
        )

    contaminated = panel[panel[time] >= pre_period_end]
    if len(contaminated) > 0:
        offenders = sorted(pd.unique(contaminated[time]))[:5]
        raise PowerError(
            f"OUTCOME LEAKAGE: {len(contaminated)} of {len(panel)} rows have "
            f"{time} >= pre_period_end ({pre_period_end!r}); first offending periods "
            f"{offenders}. The power gate may only ever see pre-treatment data."
        )

    keep = [c for c in {unit, time, outcome, cluster} if c]
    df = panel[keep].dropna()
    if len(df) < 10:
        raise PowerError(f"only {len(df)} usable rows; too few for a power preflight")

    cluster_col = cluster or unit
    n_clusters = int(df[cluster_col].nunique())
    if n_clusters < 3:
        raise PowerError(f"only {n_clusters} clusters; clustered inference is not credible")

    resid_opt = _residualise(df, unit, time, outcome, absorb_unit=True)
    resid_con = _residualise(df, unit, time, outcome, absorb_unit=False)

    sd = float(resid_opt.std(ddof=1))
    if not np.isfinite(sd) or sd <= 0:
        raise PowerError("residual standard deviation is zero or undefined")
    sd_con = float(resid_con.std(ddof=1))

    icc_opt, mean_size = _moulton_icc(resid_opt, df[cluster_col])
    icc_con, _ = _moulton_icc(resid_con, df[cluster_col])

    n_obs = int(len(df))
    deff_opt = 1.0 + (mean_size - 1.0) * icc_opt
    deff_con = 1.0 + (mean_size - 1.0) * icc_con

    dfree = n_clusters - 1
    mult = float(stats.t.ppf(1.0 - alpha / 2.0, dfree) + stats.t.ppf(power, dfree))
    var_share = treated_share * (1.0 - treated_share)

    eff_n = n_obs / deff_opt if deff_opt > 0 else float(n_obs)
    se = sd / np.sqrt(eff_n * var_share)
    mde = mult * se

    eff_n_con = n_obs / deff_con if deff_con > 0 else float(n_obs)
    mde_con = mult * (sd_con / np.sqrt(eff_n_con * var_share))

    ratio = powered = None
    if plausible_effect is not None:
        if plausible_effect <= 0:
            raise PowerError("plausible_effect must be positive")
        ratio = mde / plausible_effect
        powered = bool(mde <= plausible_effect)

    return PowerResult(
        n_obs=n_obs,
        n_clusters=n_clusters,
        mean_cluster_size=mean_size,
        residual_sd=sd,
        icc=icc_opt,
        icc_conservative=icc_con,
        design_effect=float(deff_opt),
        design_effect_conservative=float(deff_con),
        effective_n=float(eff_n),
        treated_share=float(treated_share),
        standard_error=float(se),
        mde=float(mde),
        mde_conservative=float(mde_con),
        plausible_effect=plausible_effect,
        ratio=None if ratio is None else float(ratio),
        powered=powered,
        alpha=alpha,
        power=power,
    )
