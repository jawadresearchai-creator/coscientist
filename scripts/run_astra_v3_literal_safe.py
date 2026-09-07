"""Literal-ticker-safe final launcher for Astra V3.

This wrapper sits on top of ``run_astra_v3_hardened.py`` and changes only CSV
parsing at stages that carry security identifiers. It prevents valid ticker
symbols such as ``NA`` from being interpreted as pandas missing-value tokens.
All scientific selection, SEC, beta, leverage, cutoff, Drive verification and
immutability gates remain those of the hardened V3 launcher.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Callable, TypeVar

from coscientist.eventstudy_io import read_csv_preserve_literals

REPO_ROOT = Path(__file__).resolve().parents[1]
HARDENED_PATH = REPO_ROOT / "scripts" / "run_astra_v3_hardened.py"

spec = importlib.util.spec_from_file_location("astra_v3_hardened", HARDENED_PATH)
if spec is None or spec.loader is None:
    raise RuntimeError(f"cannot load hardened V3 launcher at {HARDENED_PATH}")
hardened = importlib.util.module_from_spec(spec)
spec.loader.exec_module(hardened)
builder = hardened.builder

T = TypeVar("T")


def _with_literal_csv_reader(fn: Callable[..., T]) -> Callable[..., T]:
    """Run one V3 stage with pandas default NA-token parsing disabled."""
    def wrapped(*args, **kwargs):
        original = builder.pd.read_csv
        builder.pd.read_csv = read_csv_preserve_literals
        try:
            return fn(*args, **kwargs)
        finally:
            builder.pd.read_csv = original
    return wrapped


# These are the only V3 stages that read ticker-bearing CSVs with pandas.
# Wrapping SEC repair also protects `_apply_index_corrections`, which otherwise
# would turn ticker `NA` into an empty ticker when rewriting the filing index.
builder.repair_sec_corpus = _with_literal_csv_reader(builder.repair_sec_corpus)
builder.rebuild_sec_manifest = _with_literal_csv_reader(builder.rebuild_sec_manifest)
builder.repair_prices_and_controls = _with_literal_csv_reader(builder.repair_prices_and_controls)


if __name__ == "__main__":
    raise SystemExit(builder.main())
