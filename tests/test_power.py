"""G4 must kill hopeless designs, refuse leaked data, and defer on close calls."""
import numpy as np
import pandas as pd
import pytest

from coscientist.power import power_preflight, PowerError

N_PRE = 12   # periods 0..11 are pre-treatment; treatment starts at 12


def panel(n_units, n_periods=N_PRE, unit_sd=1.0, noise_sd=1.0, seed=11):
    rng = np.random.default_rng(seed)
    rows = []
    for u in range(n_units):
        ue = rng.normal(0, unit_sd)
        for t in range(n_periods):
            rows.append((u, t, ue + rng.normal(0, noise_sd)))
    return pd.DataFrame(rows, columns=["unit", "t", "y"])


def run(df, **kw):
    kw.setdefault("pre_period_end", N_PRE)
    return power_preflight(df, unit="unit", time="t", outcome="y", **kw)


def test_c716_shaped_design_is_killed():
    """60 clusters, ~11pp residual noise, against a 2.1bp mechanical shock."""
    res = run(panel(60, unit_sd=3.5, noise_sd=10.5), plausible_effect=0.021)
    assert res.powered is False
    assert res.ratio > 10, "should fail by orders of magnitude, not marginally"


def test_large_panel_with_a_real_effect_passes():
    res = run(panel(4000, n_periods=8, unit_sd=0.4, noise_sd=0.9), plausible_effect=0.25)
    assert res.powered is True
    assert res.ratio < 1


def test_post_treatment_rows_are_refused():
    """The regression test for the leakage hole.

    The docstring used to promise pre-period-only and check nothing, so the
    engine's most important anti-leakage claim rested on caller discipline.
    """
    contaminated = panel(50, n_periods=20)          # periods 0..19
    with pytest.raises(PowerError, match="OUTCOME LEAKAGE"):
        power_preflight(contaminated, unit="unit", time="t", outcome="y",
                        pre_period_end=N_PRE)


def test_pre_period_end_is_not_optional():
    with pytest.raises(TypeError):
        power_preflight(panel(50), unit="unit", time="t", outcome="y")


def test_boundary_period_counts_as_post():
    """time == pre_period_end is treatment period zero, not the last pre period."""
    with pytest.raises(PowerError, match="OUTCOME LEAKAGE"):
        power_preflight(panel(50, n_periods=13), unit="unit", time="t",
                        outcome="y", pre_period_end=N_PRE)


def test_conservative_bound_is_never_more_optimistic():
    res = run(panel(200, n_periods=10, unit_sd=1.0, noise_sd=2.0), plausible_effect=1.0)
    assert res.mde_conservative >= res.mde


def test_small_cluster_count_is_refused():
    with pytest.raises(PowerError, match="clusters"):
        run(panel(2, n_periods=11))


def test_missing_column_is_refused():
    with pytest.raises(PowerError, match="missing required column"):
        power_preflight(panel(50), unit="unit", time="t", outcome="absent",
                        pre_period_end=N_PRE)


def test_degenerate_treated_share_is_refused():
    with pytest.raises(PowerError, match="treated_share"):
        run(panel(50), treated_share=0.0)


def test_more_clusters_lowers_the_detectable_effect():
    small = run(panel(40, n_periods=10, unit_sd=1.0, noise_sd=2.0))
    large = run(panel(400, n_periods=10, unit_sd=1.0, noise_sd=2.0))
    assert large.mde < small.mde
