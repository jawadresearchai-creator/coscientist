"""The resource ledger. Added because a scope feature shipped with no tests at all."""
import json

import pytest

from coscientist.budget import DEFAULT_BUDGETS, BudgetExceeded, BudgetLedger


def ledger(tmp_path, cycle="cycle-001", study="C705"):
    return BudgetLedger.load_or_new(str(tmp_path / "b.json"), cycle, DEFAULT_BUDGETS, study)


def test_spend_accumulates_and_check_refuses_an_overdraft(tmp_path):
    b = ledger(tmp_path)
    b.spend("search_calls", 400)
    assert b.lines["search_calls"].remaining == 100
    with pytest.raises(BudgetExceeded, match="search_calls"):
        b.check("search_calls", 200)


def test_spend_survives_the_process_that_spends_it(tmp_path):
    p = str(tmp_path / "b.json")
    a = BudgetLedger.load_or_new(p, "cycle-001", DEFAULT_BUDGETS, "C705")
    a.spend("bigquery_bytes", 900e9)
    a.save(p)
    b = BudgetLedger.load_or_new(p, "cycle-001", DEFAULT_BUDGETS, "C705")
    assert b.lines["bigquery_bytes"].used == pytest.approx(900e9)
    with pytest.raises(BudgetExceeded):
        b.check("bigquery_bytes", 200e9)


def test_cycle_scope_resets_on_a_new_cycle(tmp_path):
    p = str(tmp_path / "b.json")
    a = BudgetLedger.load_or_new(p, "cycle-001", DEFAULT_BUDGETS, "C705")
    a.spend("search_calls", 300)
    a.save(p)
    b = BudgetLedger.load_or_new(p, "cycle-002", DEFAULT_BUDGETS, "C705")
    assert b.lines["search_calls"].used == 0


def test_study_scope_does_not_reset_on_a_new_cycle(tmp_path):
    """A study is not a cycle.

    Both mapped to the cycle id, so drive_bytes -- the 25 GB per-PAPER
    acquisition ceiling -- reset every cycle. A governance limit on the wrong
    clock is not a limit.
    """
    p = str(tmp_path / "b.json")
    a = BudgetLedger.load_or_new(p, "cycle-001", DEFAULT_BUDGETS, "C705")
    a.spend("drive_bytes", 20e9)
    a.save(p)
    b = BudgetLedger.load_or_new(p, "cycle-002", DEFAULT_BUDGETS, "C705")
    assert b.lines["drive_bytes"].used == pytest.approx(20e9), "same study, same ceiling"
    c = BudgetLedger.load_or_new(p, "cycle-002", DEFAULT_BUDGETS, "C716")
    assert c.lines["drive_bytes"].used == 0, "new study, fresh ceiling"


def test_month_scope_survives_a_cycle_change(tmp_path):
    p = str(tmp_path / "b.json")
    a = BudgetLedger.load_or_new(p, "cycle-001", DEFAULT_BUDGETS, "C705")
    a.spend("bigquery_bytes", 500e9)
    a.save(p)
    b = BudgetLedger.load_or_new(p, "cycle-002", DEFAULT_BUDGETS, "C705")
    assert b.lines["bigquery_bytes"].used == pytest.approx(500e9)


def test_a_fresh_ledger_stamps_its_periods(tmp_path):
    """Otherwise the first roll after creation could wipe real spend."""
    b = ledger(tmp_path)
    assert all(line.period_key for line in b.lines.values())


def test_save_is_atomic(tmp_path):
    import inspect
    assert "os.replace" in inspect.getsource(BudgetLedger.save)


def test_warning_fires_before_exhaustion(tmp_path):
    b = ledger(tmp_path)
    b.spend("search_calls", 410)
    assert b.lines["search_calls"].warn
    assert "search_calls" in "; ".join(b.warnings())


def test_a_study_scoped_budget_demands_a_study_id(tmp_path):
    """A fixed abstraction with an unfixed caller is an unfixed system.

    BudgetLine already distinguished study from cycle. The CLI then called it
    without a study id, `study or cycle` fell back, and the 25 GB per-paper
    acquisition ceiling reset every cycle again -- the exact bug the scope
    field was added to fix.
    """
    import pytest

    from coscientist.budget import BudgetConfigurationError

    with pytest.raises(BudgetConfigurationError, match="no study id"):
        BudgetLedger.load_or_new(str(tmp_path / "b.json"), "cycle-001",
                                 {"drive_bytes": (25e9, "B", "study")})


def test_the_budget_cli_passes_the_study_through(tmp_path):
    """The caller, tested. Not just the abstraction it calls."""
    import argparse

    from coscientist.cli import cmd_budget

    args = argparse.Namespace(state=str(tmp_path / "engine.json"),
                              cycle="cycle-001", study="C705")
    assert cmd_budget(args) == 0
    ledger = BudgetLedger.load_or_new(str(tmp_path / "budget.json"), "cycle-002",
                                      DEFAULT_BUDGETS, "C705")
    assert ledger.lines["drive_bytes"].period_key == "C705"
