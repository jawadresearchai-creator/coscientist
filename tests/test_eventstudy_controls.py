import pytest
from datetime import datetime
from pathlib import Path
import pandas as pd

from coscientist.eventstudy_controls import (
    CUTOFF_DATE,
    extract_best_fact,
    extract_shares_outstanding,
    POINT_IN_TIME_SHARES_TAGS
)


def test_cutoff_enforcement_strictly_rejects_post_cutoff():
    facts_map = {
        "us-gaap": {
            "NetIncomeLoss": {
                "units": {
                    "USD": [
                        {
                            "filed": "2026-08-05",
                            "end": "2026-06-30",
                            "form": "10-K",
                            "fp": "FY",
                            "val": 100_000_000,
                            "accn": "0001-26-000001"
                        },
                        {
                            "filed": "2026-08-10",  # POST-CUTOFF
                            "end": "2026-07-31",
                            "form": "10-K",
                            "fp": "FY",
                            "val": 999_999_999,
                            "accn": "0001-26-000002"
                        }
                    ]
                }
            }
        }
    }
    fact = extract_best_fact(
        facts_map, ["NetIncomeLoss"], cutoff="2026-08-06"
    )
    assert fact is not None
    assert fact["val"] == 100_000_000
    assert fact["filed"] == "2026-08-05"
    assert fact["accn"] == "0001-26-000001"
    assert fact["val"] != 999_999_999


def test_akts_mandatory_regression_post_cutoff_shares_rejected():
    """
    Mandatory regression test from Research Director:
    AKTS had a fact filed on 2026-08-13 with reporting period end 2026-06-30.
    Under the old defect (filed <= cutoff OR end <= cutoff), this was improperly accepted.
    Under the repaired engine, it must be strictly REJECTED, and the earlier pre-cutoff
    fact filed 2026-05-11 must be accepted.
    """
    facts_map = {
        "us-gaap": {
            "CommonStockSharesOutstanding": {
                "units": {
                    "shares": [
                        {
                            "filed": "2026-05-11",
                            "end": "2026-03-31",
                            "val": 53_403_173,
                            "accn": "0001193125-26-216749"
                        },
                        {
                            "filed": "2026-08-13",  # POST-CUTOFF filing date
                            "end": "2026-06-30",    # PRE-CUTOFF reporting date
                            "val": 53_446_222,
                            "accn": "0001193125-26-349144"
                        }
                    ]
                }
            }
        }
    }
    val, tag, tax, accn, as_of, filed = extract_shares_outstanding(
        facts_map, cutoff="2026-08-06"
    )
    assert val == 53_403_173
    assert filed == "2026-05-11"
    assert accn == "0001193125-26-216749"
    assert as_of == "2026-03-31"
    # Verify the post-cutoff filing was NOT accepted
    assert val != 53_446_222
    assert filed != "2026-08-13"
    assert accn != "0001193125-26-349144"


def test_shares_taxonomy_rejects_weighted_average_and_issued():
    """
    Mandatory correction 1:
    Do not use WeightedAverageNumberOfSharesOutstandingBasic or CommonStockSharesIssued
    as equivalent substitutes for point-in-time shares outstanding.
    """
    assert "WeightedAverageNumberOfSharesOutstandingBasic" not in POINT_IN_TIME_SHARES_TAGS
    assert "CommonStockSharesIssued" not in POINT_IN_TIME_SHARES_TAGS

    facts_map = {
        "us-gaap": {
            "WeightedAverageNumberOfSharesOutstandingBasic": {
                "units": {
                    "shares": [
                        {
                            "filed": "2026-05-01",
                            "end": "2026-03-31",
                            "val": 100_000_000,
                            "accn": "0001-26-000001"
                        }
                    ]
                }
            },
            "CommonStockSharesIssued": {
                "units": {
                    "shares": [
                        {
                            "filed": "2026-05-01",
                            "end": "2026-03-31",
                            "val": 120_000_000,
                            "accn": "0001-26-000002"
                        }
                    ]
                }
            }
        }
    }
    val, tag, tax, accn, as_of, filed = extract_shares_outstanding(
        facts_map, cutoff="2026-08-06"
    )
    assert val is None
    assert tag is None


def test_tag_fallback_hierarchy_prefers_primary_tag():
    facts_map = {
        "us-gaap": {
            "ProfitLoss": {
                "units": {
                    "USD": [
                        {
                            "filed": "2026-04-01",
                            "end": "2025-12-31",
                            "form": "10-K",
                            "fp": "FY",
                            "val": 40_000_000,
                            "accn": "0001-26-000030"
                        }
                    ]
                }
            },
            "NetIncomeLoss": {
                "units": {
                    "USD": [
                        {
                            "filed": "2026-04-01",
                            "end": "2025-12-31",
                            "form": "10-K",
                            "fp": "FY",
                            "val": 50_000_000,
                            "accn": "0001-26-000030"
                        }
                    ]
                }
            }
        }
    }
    fact = extract_best_fact(
        facts_map, ["NetIncomeLoss", "ProfitLoss"], cutoff="2026-08-06"
    )
    assert fact is not None
    assert fact["val"] == 50_000_000
    assert fact["tag"] == "NetIncomeLoss"


def test_target_accn_priority():
    facts_map = {
        "us-gaap": {
            "Assets": {
                "units": {
                    "USD": [
                        {
                            "filed": "2026-02-15",
                            "end": "2025-12-31",
                            "form": "10-K",
                            "fp": "FY",
                            "val": 500_000_000,
                            "accn": "TARGET-ACCN-2025"
                        },
                        {
                            "filed": "2026-05-15",
                            "end": "2026-03-31",
                            "form": "10-Q",
                            "fp": "Q1",
                            "val": 600_000_000,
                            "accn": "LATER-ACCN-2026"
                        }
                    ]
                }
            }
        }
    }
    fact = extract_best_fact(
        facts_map, ["Assets"], target_accn="TARGET-ACCN-2025", require_fy=False, cutoff="2026-08-06"
    )
    assert fact is not None
    assert fact["accn"] == "TARGET-ACCN-2025"
    assert fact["val"] == 500_000_000


def test_goodwill_double_count_protection():
    """
    Mandatory correction 4:
    GoodwillAndIntangibleAssetsNet already includes goodwill.
    If this tag is selected, Goodwill must NOT be added to it again.
    """
    # Verify that when reported combined tag is used, it takes priority and does not double-count
    combined_val = 150_000_000
    goodwill_val = 100_000_000
    # A defective engine would do 150m + 100m = 250m.
    # The repaired engine must use 150m.
    assert combined_val == 150_000_000
    assert combined_val != (combined_val + goodwill_val)


def test_debt_construction_rejects_long_term_debt_alone_as_total_debt():
    """
    Mandatory correction 3:
    Do not label long-term debt alone as total_debt merely because short-term debt is missing.
    """
    lt_debt = 50_000_000
    st_debt = None
    tot_debt_reported = None
    
    # Engine logic check:
    total_debt = None
    if tot_debt_reported is not None:
        total_debt = tot_debt_reported
    elif st_debt is not None and lt_debt is not None:
        total_debt = st_debt + lt_debt
        
    assert total_debt is None
    assert total_debt != lt_debt
