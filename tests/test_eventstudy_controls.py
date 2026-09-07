import pytest
from datetime import datetime

from coscientist.eventstudy_controls import (
    CUTOFF_DATE,
    extract_best_fact,
    extract_shares_outstanding
)


def test_cutoff_enforcement_strictly_rejects_post_cutoff():
    facts_map = {
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
    val, tag, accn, filed, fy, end = extract_best_fact(
        facts_map, ["NetIncomeLoss"], cutoff="2026-08-06"
    )
    assert val == 100_000_000
    assert filed == "2026-08-05"
    assert accn == "0001-26-000001"
    assert val != 999_999_999


def test_shares_outstanding_cutoff_and_exact_accn():
    facts_map = {
        "EntityCommonStockSharesOutstanding": {
            "units": {
                "shares": [
                    {
                        "filed": "2026-03-01",
                        "end": "2026-02-28",
                        "val": 50_000_000,
                        "accn": "0001-26-000010"
                    },
                    {
                        "filed": "2026-08-20",  # POST-CUTOFF
                        "end": "2026-08-15",
                        "val": 75_000_000,
                        "accn": "0001-26-000020"
                    }
                ]
            }
        }
    }
    val, tag, accn, as_of, filed = extract_shares_outstanding(
        facts_map, cutoff="2026-08-06"
    )
    assert val == 50_000_000
    assert filed == "2026-03-01"
    assert accn == "0001-26-000010"


def test_tag_fallback_hierarchy_prefers_primary_tag():
    facts_map = {
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
    val, tag, accn, filed, fy, end = extract_best_fact(
        facts_map, ["NetIncomeLoss", "ProfitLoss"], cutoff="2026-08-06"
    )
    assert tag == "NetIncomeLoss"
    assert val == 50_000_000


def test_missing_reason_is_machine_readable():
    facts_map = {}
    val, tag, accn, filed, fy, end = extract_best_fact(
        facts_map, ["NetIncomeLoss"], cutoff="2026-08-06"
    )
    assert val is None
    assert tag is None
