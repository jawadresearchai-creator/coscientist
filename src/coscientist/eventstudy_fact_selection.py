"""Current-period, outcome-blind SEC XBRL fact selection for event-study controls.

This module fixes two failure modes in the legacy selector:
1. tag/taxonomy iteration order must never outrank recency; and
2. when one annual filing contains comparative periods, the current reporting
   period must be selected rather than the first matching accession entry.
"""
from __future__ import annotations

from datetime import date
from typing import Any, Dict, List, Optional, Tuple

CUTOFF_DATE = "2026-08-06"
TAXONOMY_PRIORITY = {"us-gaap": 0, "ifrs-full": 1, "dei": 2}
POINT_IN_TIME_SHARES_TAGS = ["EntityCommonStockSharesOutstanding", "CommonStockSharesOutstanding"]


def _duration_days(start: str, end: str) -> int:
    if not start or not end:
        return 0
    try:
        return max(0, (date.fromisoformat(end) - date.fromisoformat(start)).days)
    except Exception:
        return 0


def _candidate_key(c: Dict[str, Any], tag_rank: int) -> tuple:
    # Current reporting end first. For equal end dates prefer the longest
    # duration (annual rather than an interim/comparative context), then most
    # recent public filing date. Tag/taxonomy priority only breaks true ties.
    return (
        str(c.get("end", "")),
        _duration_days(str(c.get("start", "")), str(c.get("end", ""))),
        str(c.get("filed", "")),
        -tag_rank,
        -TAXONOMY_PRIORITY.get(str(c.get("taxonomy", "")), 99),
    )


def extract_best_fact_latest(
    facts_map: Dict[str, Dict[str, Any]],
    tags: List[str],
    target_accn: Optional[str] = None,
    require_fy: bool = True,
    units_filter: Optional[Tuple[str, ...]] = None,
    cutoff: str = CUTOFF_DATE,
) -> Optional[Dict[str, Any]]:
    """Select one current-period fact across all candidate tags/taxonomies.

    Exact target-accession facts are preferred. Within the target accession,
    comparative contexts are resolved by latest period end and longest duration.
    If the target accession lacks the construct, the globally latest public
    pre-cutoff fact is selected across all allowed tags and taxonomies.
    """
    all_candidates: list[tuple[Dict[str, Any], int]] = []
    unit_filter = {u.lower() for u in units_filter} if units_filter else None

    for tag_rank, tag in enumerate(tags):
        for taxonomy in ("us-gaap", "ifrs-full", "dei"):
            concept = facts_map.get(taxonomy, {}).get(tag)
            if not concept:
                continue
            for unit_name, entries in concept.get("units", {}).items():
                if unit_filter and unit_name.lower() not in unit_filter:
                    continue
                for e in entries:
                    filed = str(e.get("filed", ""))
                    end = str(e.get("end", ""))
                    form = str(e.get("form", ""))
                    fp = str(e.get("fp", ""))
                    if not filed or filed > cutoff:
                        continue
                    if end and end > cutoff:
                        continue
                    if require_fy and fp != "FY" and form not in ("10-K", "20-F", "40-F", "10-K/A", "20-F/A", "40-F/A"):
                        continue
                    if e.get("val") is None:
                        continue
                    try:
                        val = float(e["val"])
                    except Exception:
                        continue
                    c = {
                        "val": val,
                        "tag": tag,
                        "taxonomy": taxonomy,
                        "accn": str(e.get("accn", "")),
                        "filed": filed,
                        "fy": str(e.get("fy", "")),
                        "fp": fp,
                        "start": str(e.get("start", "")),
                        "end": end,
                        "form": form,
                    }
                    all_candidates.append((c, tag_rank))

    if not all_candidates:
        return None

    if target_accn:
        exact = [(c, rank) for c, rank in all_candidates if c.get("accn") == target_accn]
        if exact:
            return max(exact, key=lambda cr: _candidate_key(cr[0], cr[1]))[0]

    return max(all_candidates, key=lambda cr: _candidate_key(cr[0], cr[1]))[0]


def extract_shares_outstanding_latest(
    facts_map: Dict[str, Dict[str, Any]],
    target_accn: Optional[str] = None,
    cutoff: str = CUTOFF_DATE,
):
    """Select the latest legitimate point-in-time common-shares fact."""
    candidates: list[tuple[Dict[str, Any], int]] = []
    for tag_rank, tag in enumerate(POINT_IN_TIME_SHARES_TAGS):
        for taxonomy in ("dei", "us-gaap", "ifrs-full"):
            concept = facts_map.get(taxonomy, {}).get(tag)
            if not concept:
                continue
            for unit_name, entries in concept.get("units", {}).items():
                if unit_name.lower() != "shares":
                    continue
                for e in entries:
                    filed = str(e.get("filed", ""))
                    end = str(e.get("end", ""))
                    if not filed or filed > cutoff or (end and end > cutoff):
                        continue
                    try:
                        val = float(e.get("val"))
                    except Exception:
                        continue
                    if val <= 0:
                        continue
                    candidates.append(({
                        "val": val,
                        "tag": tag,
                        "taxonomy": taxonomy,
                        "accn": str(e.get("accn", "")),
                        "filed": filed,
                        "end": end,
                        "start": "",
                    }, tag_rank))
    if not candidates:
        return None, None, None, None, None, None
    use = candidates
    if target_accn:
        exact = [x for x in candidates if x[0].get("accn") == target_accn]
        if exact:
            use = exact
    c, _ = max(use, key=lambda cr: _candidate_key(cr[0], cr[1]))
    as_of = c.get("end") or c.get("filed")
    return c["val"], c["tag"], c["taxonomy"], c["accn"], as_of, c["filed"]
