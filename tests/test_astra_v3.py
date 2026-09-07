import numpy as np
import pandas as pd

from coscientist.astra_v3 import (
    merge_strict_beta_metrics,
    needs_full_text_repair,
    repair_strict_leverage,
    truthful_full_text_status,
)
from coscientist.eventstudy_price_metrics import build_strict_pre_event_price_metrics


def test_preview_sized_success_requires_full_text_repair():
    assert needs_full_text_repair({"source_acquisition_status": "SUCCESS", "normalized_text_length": 5000})
    assert needs_full_text_repair({"source_acquisition_status": "SUCCESS", "normalized_text": "x" * 4999})
    assert not needs_full_text_repair({"source_acquisition_status": "SUCCESS", "normalized_text_length": 5001})
    assert not needs_full_text_repair({"source_acquisition_status": "NOT_ACQUIRED", "normalized_text_length": 0})


def test_truthful_full_text_status_rejects_exact_5000_preview():
    assert truthful_full_text_status("x" * 5000, "SUCCESS_FULL_TEXT") == ("N", "SUCCESS_PARTIAL_TEXT")
    assert truthful_full_text_status("x" * 5001, "SUCCESS_FULL_TEXT") == ("Y", "SUCCESS_FULL_TEXT")


def test_strict_leverage_uses_only_period_matched_components():
    controls = pd.DataFrame([
        {
            "ticker": "GOOD", "cik": "1", "short_term_debt": 20.0, "long_term_debt": 30.0,
            "total_assets": 100.0, "total_assets_end": "2025-12-31", "reported_total_debt": 999.0,
            "total_debt": 999.0, "total_debt_method": "DIRECT_REPORTED_TOTAL_DEBT", "total_debt_tag": "Borrowings",
            "total_debt_taxonomy": "us-gaap", "total_debt_accn": "a", "total_debt_filed": "2026-02-01",
            "total_debt_end": "2025-12-31", "leverage_debt_assets": 9.99, "leverage_period_consistent": True,
            "leverage_missing_reason": "", "has_leverage": True,
        },
        {
            "ticker": "BAD", "cik": "2", "short_term_debt": 20.0, "long_term_debt": 30.0,
            "total_assets": 100.0, "total_assets_end": "2025-12-31", "reported_total_debt": 80.0,
            "total_debt": 80.0, "total_debt_method": "DIRECT_REPORTED_TOTAL_DEBT", "total_debt_tag": "DebtInstrumentCarryingAmount",
            "total_debt_taxonomy": "us-gaap", "total_debt_accn": "b", "total_debt_filed": "2026-02-01",
            "total_debt_end": "2025-12-31", "leverage_debt_assets": 0.8, "leverage_period_consistent": True,
            "leverage_missing_reason": "", "has_leverage": True,
        },
    ])
    provenance = pd.DataFrame([
        {"ticker":"GOOD","cik":"1","construct":"SHORT_TERM_DEBT","value":20.0,"available":"Y","xbrl_tag":"DebtCurrent","source_taxonomy":"us-gaap","source_accession":"a","source_filing_date":"2026-02-01","period_start":"","period_end":"2025-12-31","fiscal_year":"2025","fiscal_period":"FY","cross_accession_fallback":"N","pre_event_cutoff":"2026-08-06","description":""},
        {"ticker":"GOOD","cik":"1","construct":"LONG_TERM_DEBT","value":30.0,"available":"Y","xbrl_tag":"LongTermDebtNoncurrent","source_taxonomy":"us-gaap","source_accession":"a","source_filing_date":"2026-02-01","period_start":"","period_end":"2025-12-31","fiscal_year":"2025","fiscal_period":"FY","cross_accession_fallback":"N","pre_event_cutoff":"2026-08-06","description":""},
        {"ticker":"GOOD","cik":"1","construct":"REPORTED_TOTAL_DEBT","value":999.0,"available":"Y","xbrl_tag":"Borrowings","source_taxonomy":"us-gaap","source_accession":"a","source_filing_date":"2026-02-01","period_start":"","period_end":"2025-12-31","fiscal_year":"2025","fiscal_period":"FY","cross_accession_fallback":"N","pre_event_cutoff":"2026-08-06","description":""},
        {"ticker":"BAD","cik":"2","construct":"SHORT_TERM_DEBT","value":20.0,"available":"Y","xbrl_tag":"DebtCurrent","source_taxonomy":"us-gaap","source_accession":"b","source_filing_date":"2026-02-01","period_start":"","period_end":"2024-12-31","fiscal_year":"2024","fiscal_period":"FY","cross_accession_fallback":"N","pre_event_cutoff":"2026-08-06","description":""},
        {"ticker":"BAD","cik":"2","construct":"LONG_TERM_DEBT","value":30.0,"available":"Y","xbrl_tag":"LongTermDebtNoncurrent","source_taxonomy":"us-gaap","source_accession":"b","source_filing_date":"2026-02-01","period_start":"","period_end":"2025-12-31","fiscal_year":"2025","fiscal_period":"FY","cross_accession_fallback":"N","pre_event_cutoff":"2026-08-06","description":""},
    ])
    out, prov, stats = repair_strict_leverage(controls, provenance)
    good = out[out.ticker == "GOOD"].iloc[0]
    bad = out[out.ticker == "BAD"].iloc[0]
    assert good.total_debt == 50.0
    assert good.leverage_debt_assets == 0.5
    assert good.reported_total_debt_used_for_leverage is False or good.reported_total_debt_used_for_leverage == False
    assert bool(good.has_leverage)
    assert pd.isna(bad.leverage_debt_assets)
    assert not bool(bad.has_leverage)
    assert bad.leverage_missing_reason == "STRICT_DEBT_COMPONENT_PERIOD_MISMATCH"
    assert stats["strict_leverage_available"] == 1
    assert stats["previous_values_invalidated"] == 1
    assert "Direct reported debt-like fact retained for audit only" in prov.loc[prov.construct == "REPORTED_TOTAL_DEBT", "description"].iloc[0]


def test_exact_beta_windows_do_not_label_short_samples_as_120():
    dates = pd.bdate_range("2026-01-01", periods=131)
    market = np.linspace(100, 130, len(dates))
    firm = np.linspace(50, 72, len(dates))
    px = pd.concat([
        pd.DataFrame({"Date": dates, "Ticker": "SPY", "Close": market}),
        pd.DataFrame({"Date": dates, "Ticker": "AAA", "Close": firm}),
        pd.DataFrame({"Date": dates[-101:], "Ticker": "BBB", "Close": np.linspace(20, 25, 101)}),
    ], ignore_index=True)
    out = build_strict_pre_event_price_metrics(px, ["AAA", "BBB"], cutoff=str(dates.max().date()))
    aaa = out[out.ticker == "AAA"].iloc[0]
    bbb = out[out.ticker == "BBB"].iloc[0]
    assert aaa.beta_120_n == 120
    assert pd.notna(aaa.market_beta_120)
    assert bbb.beta_120_n == 0
    assert pd.isna(bbb.market_beta_120)
    assert bbb.beta_120_missing_reason == "INSUFFICIENT_PAIRED_RETURNS_LT_120"


def test_merge_strict_beta_replaces_v2_flags():
    controls = pd.DataFrame([{"ticker":"AAA","pre_event_price_obs":999,"has_120_pre_event_obs":True,"market_beta_120":9.0,"has_market_beta_120":True,
                              "has_200_pre_event_obs":True,"market_beta_200":9.0,"has_market_beta_200":True,
                              "has_250_pre_event_obs":True,"market_beta_250":9.0,"has_market_beta_250":True,
                              "has_full_requested_history":True}])
    metrics = pd.DataFrame([{"ticker":"AAA","pre_event_price_obs":110,"valid_paired_pre_event_return_obs":109,"has_full_requested_history":False,
                             "has_120_pre_event_obs":False,"market_beta_120":np.nan,"beta_120_n":0,"beta_120_first_return_date":"","beta_120_last_return_date":"","beta_120_missing_reason":"INSUFFICIENT_PAIRED_RETURNS_LT_120",
                             "has_200_pre_event_obs":False,"market_beta_200":np.nan,"beta_200_n":0,"beta_200_first_return_date":"","beta_200_last_return_date":"","beta_200_missing_reason":"INSUFFICIENT_PAIRED_RETURNS_LT_200",
                             "has_250_pre_event_obs":False,"market_beta_250":np.nan,"beta_250_n":0,"beta_250_first_return_date":"","beta_250_last_return_date":"","beta_250_missing_reason":"INSUFFICIENT_PAIRED_RETURNS_LT_250"}])
    out = merge_strict_beta_metrics(controls, metrics).iloc[0]
    assert out.pre_event_price_obs == 110
    assert out.valid_paired_pre_event_return_obs == 109
    assert not bool(out.has_market_beta_120)
    assert pd.isna(out.market_beta_120)
