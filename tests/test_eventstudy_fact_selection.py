from coscientist.eventstudy_fact_selection import (
    extract_best_fact_latest,
    extract_shares_outstanding_latest,
)


def _concept(entries):
    return {"units": {"USD": entries}}


def test_target_accession_prefers_current_comparative_period():
    facts = {
        "us-gaap": {
            "NetIncomeLoss": _concept([
                {"val": 100, "accn": "A", "filed": "2026-02-20", "start": "2023-01-01", "end": "2023-12-31", "form": "10-K", "fp": "FY"},
                {"val": 300, "accn": "A", "filed": "2026-02-20", "start": "2025-01-01", "end": "2025-12-31", "form": "10-K", "fp": "FY"},
            ])
        }
    }
    got = extract_best_fact_latest(facts, ["NetIncomeLoss"], target_accn="A")
    assert got["val"] == 300
    assert got["end"] == "2025-12-31"


def test_newer_ifrs_fact_beats_stale_us_gaap_fallback():
    facts = {
        "us-gaap": {
            "NetIncomeLoss": _concept([
                {"val": 10, "accn": "OLD", "filed": "2011-04-05", "start": "2010-01-01", "end": "2010-12-31", "form": "20-F", "fp": "FY"}
            ])
        },
        "ifrs-full": {
            "ProfitLoss": _concept([
                {"val": 50, "accn": "NEW", "filed": "2026-02-27", "start": "2025-01-01", "end": "2025-12-31", "form": "40-F", "fp": "FY"}
            ])
        },
    }
    got = extract_best_fact_latest(facts, ["NetIncomeLoss", "ProfitLoss"], target_accn="MISSING")
    assert got["val"] == 50
    assert got["taxonomy"] == "ifrs-full"
    assert got["accn"] == "NEW"


def test_same_end_prefers_longer_annual_duration():
    facts = {
        "us-gaap": {
            "Revenues": _concept([
                {"val": 25, "accn": "A", "filed": "2026-02-20", "start": "2025-10-01", "end": "2025-12-31", "form": "10-K", "fp": "FY"},
                {"val": 100, "accn": "A", "filed": "2026-02-20", "start": "2025-01-01", "end": "2025-12-31", "form": "10-K", "fp": "FY"},
            ])
        }
    }
    got = extract_best_fact_latest(facts, ["Revenues"], target_accn="A")
    assert got["val"] == 100
    assert got["start"] == "2025-01-01"


def test_shares_target_accession_prefers_latest_as_of_date():
    facts = {
        "dei": {
            "EntityCommonStockSharesOutstanding": {
                "units": {"shares": [
                    {"val": 100, "accn": "A", "filed": "2026-02-20", "end": "2024-12-31"},
                    {"val": 150, "accn": "A", "filed": "2026-02-20", "end": "2025-12-31"},
                ]}
            }
        }
    }
    val, tag, tax, accn, as_of, filed = extract_shares_outstanding_latest(facts, target_accn="A")
    assert val == 150
    assert as_of == "2025-12-31"
    assert accn == "A"
