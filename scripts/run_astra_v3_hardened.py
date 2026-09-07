"""Hardened launcher for the Astra analysis-ready V3 builder.

This wrapper deliberately leaves the audited V3 builder small and stable while
hardening two execution boundaries discovered during the first live run:
1. SEC primary-document failures/short parses fall back to the complete EDGAR
   submission text for the same accession.
2. The price parquet is predicate-filtered to <= 2026-08-06 before rows are
   materialized into pandas, preserving a strict outcome-blind access boundary.
"""
from __future__ import annotations

import gzip
import importlib.util
import re
import time
import urllib.request
from datetime import date
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

REPO_ROOT = Path(__file__).resolve().parents[1]
BUILDER_PATH = REPO_ROOT / "scripts" / "build_astra_analysis_ready_v3.py"
PRICE_FILE = "us_equity_daily_prices_20250101_20260904.parquet"
CUTOFF = "2026-08-06"

spec = importlib.util.spec_from_file_location("astra_v3_builder", BUILDER_PATH)
if spec is None or spec.loader is None:
    raise RuntimeError(f"cannot load builder at {BUILDER_PATH}")
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)

_ORIGINAL_READ_PARQUET = pd.read_parquet


def _submission_fallback_url(primary_url: str) -> str | None:
    """Convert an EDGAR primary-document URL to the complete submission .txt URL."""
    m = re.match(
        r"^https://www\.sec\.gov/Archives/edgar/data/(\d+)/(\d{18})/[^/?#]+",
        primary_url,
        re.I,
    )
    if not m:
        return None
    cik, compact = m.groups()
    accession = f"{compact[:10]}-{compact[10:12]}-{compact[12:]}"
    return f"https://www.sec.gov/Archives/edgar/data/{cik}/{compact}/{accession}.txt"


def _http_fetch(url: str, limiter, attempts: int = 10) -> bytes:
    last: Exception | None = None
    for attempt in range(1, attempts + 1):
        limiter.wait()
        try:
            req = urllib.request.Request(url, headers=builder.SEC_HEADERS)
            with urllib.request.urlopen(req, timeout=75) as resp:
                raw = resp.read()
                if resp.headers.get("Content-Encoding") == "gzip" or raw[:2] == b"\x1f\x8b":
                    raw = gzip.decompress(raw)
            if len(raw) < 500:
                raise RuntimeError(f"SEC payload too short: {len(raw)} bytes")
            return raw
        except Exception as exc:
            last = exc
            if attempt < attempts:
                time.sleep(min(20.0, 1.8 * attempt))
    raise RuntimeError(f"SEC fetch failed after {attempts} attempts for {url}: {last}")


def _hardened_fetch_sec(url: str, limiter) -> bytes:
    """Fetch primary filing; use complete submission when primary is unavailable or preview-sized."""
    primary_raw: bytes | None = None
    primary_error: Exception | None = None
    try:
        primary_raw = _http_fetch(url, limiter)
        norm, _, _ = builder.html_to_normalized_text(primary_raw)
        if len(norm) > 5000:
            return primary_raw
        print(
            f"SEC primary document parsed to only {len(norm)} chars; trying complete submission fallback: {url}",
            flush=True,
        )
    except Exception as exc:
        primary_error = exc
        print(f"SEC primary fetch failed; trying complete submission fallback: {url} :: {exc}", flush=True)

    fallback = _submission_fallback_url(url)
    if fallback:
        try:
            fallback_raw = _http_fetch(fallback, limiter)
            norm, _, _ = builder.html_to_normalized_text(fallback_raw)
            if len(norm) > 5000:
                print(f"SEC fallback succeeded ({len(norm)} chars): {fallback}", flush=True)
                return fallback_raw
            print(f"SEC fallback remained short ({len(norm)} chars): {fallback}", flush=True)
            if primary_raw is not None:
                return primary_raw
            return fallback_raw
        except Exception as fallback_exc:
            if primary_raw is not None:
                print(f"SEC fallback failed; retaining fetched primary payload: {fallback_exc}", flush=True)
                return primary_raw
            raise RuntimeError(
                f"primary and complete-submission SEC fetch both failed; primary={primary_error}; fallback={fallback_exc}"
            ) from fallback_exc

    if primary_raw is not None:
        return primary_raw
    raise RuntimeError(f"SEC primary fetch failed and no complete-submission fallback could be derived: {primary_error}")


def _predicate_filtered_read_parquet(path, *args, **kwargs):
    """Read the price parquet with a storage-level date predicate before pandas materialization."""
    p = Path(path)
    if p.name != PRICE_FILE:
        return _ORIGINAL_READ_PARQUET(path, *args, **kwargs)

    schema = pq.read_schema(p)
    if "Date" not in schema.names:
        raise RuntimeError(f"price parquet lacks Date column; schema={schema}")
    typ = schema.field("Date").type
    if pa.types.is_timestamp(typ):
        cutoff_value = pd.Timestamp(CUTOFF).to_pydatetime()
    elif pa.types.is_date32(typ) or pa.types.is_date64(typ):
        cutoff_value = date.fromisoformat(CUTOFF)
    elif pa.types.is_string(typ) or pa.types.is_large_string(typ):
        cutoff_value = CUTOFF
    else:
        raise RuntimeError(f"unsupported Date parquet type for predicate filtering: {typ}")

    filters = list(kwargs.pop("filters", []))
    filters.append(("Date", "<=", cutoff_value))
    df = _ORIGINAL_READ_PARQUET(path, *args, filters=filters, **kwargs)
    parsed = pd.to_datetime(df["Date"], errors="coerce")
    if parsed.notna().any() and parsed.max() > pd.Timestamp(CUTOFF):
        raise RuntimeError(f"predicate-filter boundary failed; max materialized date={parsed.max()}")
    print(
        f"PRICE_PREDICATE_FILTER_PASS cutoff={CUTOFF} rows={len(df)} max_date={parsed.max() if parsed.notna().any() else 'NA'}",
        flush=True,
    )
    return df


# Monkeypatch only the two audited execution boundaries; all scientific gates,
# manifests, hashes, Drive promotion and lifecycle semantics stay in the canonical builder.
builder._fetch_sec = _hardened_fetch_sec
builder.pd.read_parquet = _predicate_filtered_read_parquet


if __name__ == "__main__":
    raise SystemExit(builder.main())
