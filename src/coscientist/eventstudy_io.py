"""CSV I/O safeguards for event-study datasets.

Security identifiers are data, not missing-value sentinels. In particular,
NASDAQ ticker ``NA`` (Nano Labs Ltd.) must remain the literal string ``NA``
rather than being interpreted by pandas as a default NA token.
"""
from __future__ import annotations

from typing import Any

import pandas as pd

# Capture pandas' real implementation at import time.  The V3 launcher
# temporarily monkey-patches ``pd.read_csv`` for ticker-bearing stages, so the
# safeguard must not dispatch through the patched attribute or it would recurse.
_PANDAS_READ_CSV = pd.read_csv


def read_csv_preserve_literals(*args: Any, **kwargs: Any) -> pd.DataFrame:
    """Read CSV while preserving literal strings such as ticker ``NA``.

    Callers may still provide explicit ``na_values`` when a specific column
    requires them; the pandas default NA-token vocabulary is disabled unless
    the caller explicitly overrides ``keep_default_na``.
    """
    kwargs.setdefault("keep_default_na", False)
    return _PANDAS_READ_CSV(*args, **kwargs)
