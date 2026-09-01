"""Per-cycle resource ledger.

Scarcity is the defining constraint of this system, so it gets first-class
accounting rather than an assumption. Every gate draws against a declared
budget; a line hitting 80% reports rather than being discovered exhausted
halfway through a build.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field, asdict
from typing import Any

from .models import utcnow


class BudgetExceeded(RuntimeError):
    pass


class BudgetConfigurationError(RuntimeError):
    """A study-scoped budget with no study to scope it to."""


@dataclass
class BudgetLine:
    """One metered resource.

    `scope` and `period_key` exist because carrying `used` forward forever
    fixed one bug and created its mirror image: a monthly BigQuery cap that
    could no longer be spent once per cycle could instead be spent once, ever.
    Each line resets when its own period rolls over.
    """

    name: str
    limit: float
    used: float = 0.0
    unit: str = ""
    scope: str = "month"          # month | cycle | study | forever
    period_key: str = ""

    @property
    def remaining(self) -> float:
        return max(self.limit - self.used, 0.0)

    @property
    def fraction(self) -> float:
        return 0.0 if self.limit <= 0 else self.used / self.limit

    @property
    def warn(self) -> bool:
        return self.fraction >= 0.80

    def current_period(self, cycle: str, study: str | None = None) -> str:
        """A study is not a cycle.

        Both mapped to `cycle`, which meant `drive_bytes` -- the 25 GB
        per-PAPER acquisition ceiling carried since V3.2 -- reset every cycle.
        A governance limit that resets on the wrong clock is not a limit.
        """
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc)
        return {
            "month": now.strftime("%Y-%m"),
            "cycle": cycle,
            "study": study or cycle,
            "forever": "forever",
        }.get(self.scope, "forever")

    def roll(self, cycle: str, study: str | None = None) -> bool:
        """Reset if this line's accounting period has turned over."""
        key = self.current_period(cycle, study)
        if self.period_key == key:
            return False
        if self.period_key:            # a real rollover, not first initialisation
            self.used = 0.0
        self.period_key = key
        return True


@dataclass
class BudgetLedger:
    """Named BudgetLedger, not Ledger.

    `state.Ledger` is a different class entirely -- the append-only scientific
    record -- and cli.py was already importing this one under an alias to keep
    them apart. Two unrelated classes sharing a name in one package is a
    mis-import waiting to happen in a system whose whole point is provenance.
    """

    cycle: str
    study: str | None = None
    lines: dict[str, BudgetLine] = field(default_factory=dict)
    events: list[dict[str, Any]] = field(default_factory=list)

    @classmethod
    def new(cls, cycle: str, budgets: dict[str, tuple[float, str]],
            study: str | None = None) -> "BudgetLedger":
        return cls(
            cycle=cycle,
            study=study,
            lines={k: BudgetLine(name=k, limit=v[0], unit=v[1],
                                 scope=v[2] if len(v) > 2 else "month")
                   for k, v in budgets.items()},
        )

    @classmethod
    def load_or_new(
        cls, path: str, cycle: str, budgets: dict[str, tuple[float, str]],
        study: str | None = None,
    ) -> "BudgetLedger":
        """Persistent accounting across cycles.

        Creating a fresh ledger every run meant a nominal 1 TB monthly cap
        could be spent once per cycle rather than once per month. The budget
        must survive the process that spends it.
        """
        needs_study = any((spec[2] if len(spec) > 2 else "month") == "study"
                          for spec in budgets.values())
        if needs_study and not study:
            raise BudgetConfigurationError(
                "a study-scoped budget line exists but no study id was supplied. "
                "Silently substituting the cycle id is how the per-paper ceiling "
                "came to reset every cycle."
            )
        if not os.path.exists(path):
            fresh = cls.new(cycle, budgets, study)
            for line in fresh.lines.values():
                line.roll(cycle, study)     # stamp the period at creation
            return fresh
        with open(path, "r", encoding="utf-8") as fh:
            raw = json.load(fh)
        lines = {
            k: BudgetLine(name=v["name"], limit=v["limit"], used=v.get("used", 0.0),
                          unit=v.get("unit", ""), scope=v.get("scope", "month"),
                          period_key=v.get("period_key", ""))
            for k, v in raw.get("lines", {}).items()
        }
        for k, spec in budgets.items():
            if k not in lines:
                lines[k] = BudgetLine(name=k, limit=spec[0], unit=spec[1],
                                      scope=spec[2] if len(spec) > 2 else "month")
        ledger = cls(cycle=cycle, study=study, lines=lines, events=raw.get("events", []))
        for line in ledger.lines.values():
            line.roll(cycle, study)
        return ledger

    def check(self, name: str, amount: float) -> None:
        """Pre-flight. Refuse to start work that cannot finish."""
        line = self.lines.get(name)
        if line is None:
            raise BudgetExceeded(f"no budget line named {name!r}")
        if amount > line.remaining:
            raise BudgetExceeded(
                f"{name}: need {amount}{line.unit}, only {line.remaining}{line.unit} left "
                f"of {line.limit}{line.unit}"
            )

    def spend(self, name: str, amount: float, note: str = "") -> BudgetLine:
        self.check(name, amount)
        line = self.lines[name]
        line.used += amount
        self.events.append(
            {"at": utcnow(), "line": name, "amount": amount, "note": note, "used": line.used}
        )
        return line

    def warnings(self) -> list[str]:
        return [
            f"{l.name} at {l.fraction:.0%} ({l.used:g}/{l.limit:g}{l.unit})"
            for l in self.lines.values()
            if l.warn
        ]

    def save(self, path: str) -> None:
        # Atomic, like every other state write in the package. A budget file
        # truncated mid-write reads as zero spend, which is the worst possible
        # way for this particular file to fail.
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(
                {
                    "cycle": self.cycle,
                    "study": self.study,
                    "lines": {k: asdict(v) for k, v in self.lines.items()},
                    "events": self.events,
                },
                fh,
                indent=2,
            )
        os.replace(tmp, path)


# (limit, unit, accounting scope). Scope matters: these resources do not all
# reset on the same clock, and pretending they do is how a cap gets spent
# either far too often or exactly once.
DEFAULT_BUDGETS: dict[str, tuple] = {
    "bigquery_bytes": (1_000_000_000_000, "B", "month"),
    "actions_minutes": (2000, "min", "month"),
    "claude_tickets": (20, "", "cycle"),
    "codex_tickets": (20, "", "cycle"),
    "search_calls": (500, "", "cycle"),
    "drive_bytes": (25_000_000_000, "B", "study"),   # 25 GB per-paper ceiling
}
