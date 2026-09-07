"""CSV I/O safeguards for event-study datasets.

Security identifiers are data, not missing-value sentinels. In particular,
NASDAQ ticker ``NA`` (Nano Labs Ltd.) must remain the literal string ``NA``
rather than being interpreted by pandas as a default NA token.
"""
from __future__ import annotations

from typing import Any

import pandas as pd


def read_csv_preserve_literals(*args: Any, **kwargs: Any) -> pd.DataFrame:
    """Read CSV while preserving literal strings such as ticker ``NA``.

    Callers may still provide explicit ``na_values`` when a specific column
    requires them; the pandas default NA-token vocabulary is disabled unless
    the caller explicitly overrides ``keep_default_na``.
    """
    kwargs.setdefault("keep_default_na", False)
    return pd.read_csv(*args, **kwargs)
