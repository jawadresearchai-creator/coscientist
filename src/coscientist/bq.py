"""BigQuery route with a mandatory dry-run byte ceiling.

BigQuery is route zero: it does not move files, it returns rows, which
sidesteps the entire class of transport failure that has defined this project.
But it bills by bytes scanned, and an autonomous engine issuing queries
unattended is exactly the shape of thing that scans a terabyte by accident.

So every query is estimated before it is run, and refused above a ceiling.
Set a project-level query cap in GCP as well: this guard is the seatbelt, the
project cap is the speed limiter.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .budget import BudgetLedger


class QueryTooLarge(RuntimeError):
    pass


@dataclass
class DryRunResult:
    bytes_processed: int
    gib: float
    allowed: bool
    ceiling_bytes: int

    def __str__(self) -> str:
        return (
            f"{self.gib:.2f} GiB estimated "
            f"({'within' if self.allowed else 'ABOVE'} "
            f"{self.ceiling_bytes / 2**30:.1f} GiB ceiling)"
        )


DEFAULT_CEILING_BYTES = 20 * 2**30   # 20 GiB per query


def estimate(client: Any, sql: str, ceiling_bytes: int = DEFAULT_CEILING_BYTES) -> DryRunResult:
    """Dry-run a query. Costs nothing and returns the byte estimate.

    `client` is any object exposing google-cloud-bigquery's `query` interface,
    so tests can pass a fake and CI needs no credentials.
    """
    from google.cloud import bigquery  # imported lazily; not needed for tests

    job = client.query(sql, job_config=bigquery.QueryJobConfig(dry_run=True, use_query_cache=False))
    processed = int(job.total_bytes_processed)
    return DryRunResult(
        bytes_processed=processed,
        gib=processed / 2**30,
        allowed=processed <= ceiling_bytes,
        ceiling_bytes=ceiling_bytes,
    )


def guarded_query(
    client: Any,
    sql: str,
    *,
    ledger: BudgetLedger | None = None,
    ceiling_bytes: int = DEFAULT_CEILING_BYTES,
    estimator=None,
):
    """Estimate, check the budget, then run. Never run blind."""
    est = (estimator or estimate)(client, sql, ceiling_bytes)
    if not est.allowed:
        raise QueryTooLarge(
            f"query would scan {est.gib:.2f} GiB, above the "
            f"{ceiling_bytes / 2**30:.1f} GiB ceiling. Narrow the scan "
            f"(partition filter, column pruning) or raise the ceiling deliberately."
        )
    if ledger is not None:
        ledger.spend("bigquery_bytes", float(est.bytes_processed), note="query")
    return client.query(sql)
