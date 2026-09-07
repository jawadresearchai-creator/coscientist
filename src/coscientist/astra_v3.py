"""Outcome-blind repair rules for the Astra analysis-ready V3 snapshot.

These functions are deliberately pure: they transform already-acquired pre-event
inputs and never access event-window outcomes.
"""
from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

CUTOFF_DATE = "2026-08-06"
FULL_TEXT_PREVIEW_CEILING = 5000
BETA_WINDOWS = (120, 200, 250)


def needs_full_text_repair(record: dict[str, Any]) -> bool:
    """Return True only for acquired filings whose supposed full text is preview-sized."""
    if str(record.get("source_acquisition_status", "")).upper() != "SUCCESS":
        return False
    try:
        n = int(record.get("normalized_text_length") or len(record.get("normalized_text", "") or ""))
    except (TypeError, ValueError):
        n = len(str(record.get("normalized_text", "") or ""))
    return n <= FULL_TEXT_PREVIEW_CEILING


def truthful_full_text_status(normalized_text: str, extraction_status: str) -> tuple[str, str]:
    """Prevent a 5,000-character preview from being labelled full text."""
    n = len(normalized_text or "")
    if extraction_status == "SUCCESS_FULL_TEXT" and n > FULL_TEXT_PREVIEW_CEILING:
        return "Y", extraction_status
    if n > 0:
        return "N", "SUCCESS_PARTIAL_TEXT"
    return "N", extraction_status if extraction_status.startswith("FAILED") else "FAILED_TEXT_EXTRACTION"


def repair_strict_leverage(
    controls: pd.DataFrame,
    provenance: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, int]]:
    """Enforce debt/assets = (current debt + noncurrent debt) / total assets.

    A direct `DebtInstrumentCarryingAmount`/`Borrowings` fact is retained only as
    reported-source metadata. It is never used as total debt because those tags
    can describe one instrument rather than issuer-wide debt. Both debt
    components must be available and period-matched to total assets.
    """
    c = controls.copy()
    p = provenance.copy()
    required = {"ticker", "short_term_debt", "long_term_debt", "total_assets", "total_assets_end"}
    missing = required - set(c.columns)
    if missing:
        raise ValueError(f"controls missing strict-leverage inputs: {sorted(missing)}")
    preq = {"ticker", "construct", "available", "period_end"}
    pmissing = preq - set(p.columns)
    if pmissing:
        raise ValueError(f"provenance missing strict-leverage inputs: {sorted(pmissing)}")

    idx: dict[tuple[str, str], dict[str, Any]] = {}
    for row in p.to_dict("records"):
        idx[(str(row.get("ticker", "")), str(row.get("construct", "")))] = row

    recomputed = 0
    invalidated = 0
    for i, row in c.iterrows():
        ticker = str(row["ticker"])
        assets = pd.to_numeric(pd.Series([row.get("total_assets")]), errors="coerce").iloc[0]
        st = pd.to_numeric(pd.Series([row.get("short_term_debt")]), errors="coerce").iloc[0]
        lt = pd.to_numeric(pd.Series([row.get("long_term_debt")]), errors="coerce").iloc[0]
        assets_end = str(row.get("total_assets_end") or "")
        stp = idx.get((ticker, "SHORT_TERM_DEBT"), {})
        ltp = idx.get((ticker, "LONG_TERM_DEBT"), {})
        st_end = str(stp.get("period_end") or "")
        lt_end = str(ltp.get("period_end") or "")
        st_ok = pd.notna(st) and str(stp.get("available", "")).upper() == "Y"
        lt_ok = pd.notna(lt) and str(ltp.get("available", "")).upper() == "Y"
        period_ok = bool(assets_end and st_end == assets_end and lt_end == assets_end)
        assets_ok = pd.notna(assets) and float(assets) > 0

        if st_ok and lt_ok and period_ok and assets_ok:
            total = float(st) + float(lt)
            lev = total / float(assets)
            c.at[i, "total_debt"] = total
            c.at[i, "total_debt_method"] = "STRICT_SHORT_PLUS_LONG_TERM_DEBT"
            c.at[i, "total_debt_tag"] = f"{stp.get('xbrl_tag','')}+{ltp.get('xbrl_tag','')}"
            c.at[i, "total_debt_taxonomy"] = (
                str(stp.get("source_taxonomy", ""))
                if stp.get("source_taxonomy") == ltp.get("source_taxonomy")
                else f"{stp.get('source_taxonomy','')}+{ltp.get('source_taxonomy','')}"
            )
            c.at[i, "total_debt_accn"] = (
                str(stp.get("source_accession", ""))
                if stp.get("source_accession") == ltp.get("source_accession")
                else f"{stp.get('source_accession','')}+{ltp.get('source_accession','')}"
            )
            c.at[i, "total_debt_filed"] = max(str(stp.get("source_filing_date", "")), str(ltp.get("source_filing_date", "")))
            c.at[i, "total_debt_end"] = assets_end
            c.at[i, "leverage_debt_assets"] = lev
            c.at[i, "leverage_period_consistent"] = True
            c.at[i, "leverage_missing_reason"] = ""
            c.at[i, "has_leverage"] = True
            recomputed += 1
        else:
            had = bool(row.get("has_leverage")) or pd.notna(pd.to_numeric(pd.Series([row.get("leverage_debt_assets")]), errors="coerce").iloc[0])
            for col in ("total_debt", "leverage_debt_assets"):
                c.at[i, col] = np.nan
            for col in ("total_debt_method", "total_debt_tag", "total_debt_taxonomy", "total_debt_accn", "total_debt_filed", "total_debt_end"):
                c.at[i, col] = ""
            c.at[i, "leverage_period_consistent"] = False
            c.at[i, "has_leverage"] = False
            if not st_ok or not lt_ok:
                reason = "STRICT_DEBT_COMPONENTS_INCOMPLETE"
            elif not period_ok:
                reason = "STRICT_DEBT_COMPONENT_PERIOD_MISMATCH"
            else:
                reason = "ASSETS_ZERO_OR_NOT_REPORTED"
            c.at[i, "leverage_missing_reason"] = reason
            if had:
                invalidated += 1

        c.at[i, "leverage_definition"] = "(SHORT_TERM_DEBT+LONG_TERM_DEBT)/TOTAL_ASSETS"
        c.at[i, "reported_total_debt_used_for_leverage"] = False

    # Replace derived provenance rows deterministically; retain REPORTED_TOTAL_DEBT as audit-only metadata.
    if "description" in p.columns:
        mask_reported = p["construct"].astype(str).eq("REPORTED_TOTAL_DEBT")
        p.loc[mask_reported, "description"] = "Direct reported debt-like fact retained for audit only; not used in strict leverage"
    for construct in ("TOTAL_DEBT", "LEVERAGE_DEBT_ASSETS"):
        p = p.loc[~p["construct"].astype(str).eq(construct)].copy()

    new_rows: list[dict[str, Any]] = []
    base_cols = list(p.columns)
    for row in c.to_dict("records"):
        ticker = str(row["ticker"])
        has = bool(row.get("has_leverage"))
        stp = idx.get((ticker, "SHORT_TERM_DEBT"), {})
        ltp = idx.get((ticker, "LONG_TERM_DEBT"), {})
        common = {
            "ticker": ticker,
            "cik": row.get("cik", ""),
            "available": "Y" if has else "N",
            "source_taxonomy": row.get("total_debt_taxonomy", "") if has else "",
            "source_accession": row.get("total_debt_accn", "") if has else "",
            "source_filing_date": row.get("total_debt_filed", "") if has else "",
            "period_start": "",
            "period_end": row.get("total_assets_end", "") if has else "",
            "fiscal_year": "",
            "fiscal_period": "",
            "cross_accession_fallback": "N",
            "pre_event_cutoff": CUTOFF_DATE,
        }
        debt = {**common, "construct": "TOTAL_DEBT", "value": row.get("total_debt") if has else np.nan,
                "xbrl_tag": row.get("total_debt_tag", "") if has else "",
                "description": "Strict short-term debt + long-term debt; both components period-matched to assets"}
        lev = {**common, "construct": "LEVERAGE_DEBT_ASSETS", "value": row.get("leverage_debt_assets") if has else np.nan,
               "xbrl_tag": f"{row.get('total_debt_tag','')}/Assets" if has else "",
               "description": "Strict (short-term debt + long-term debt) / total assets; no direct debt-tag substitution"}
        new_rows.extend([debt, lev])
    add = pd.DataFrame(new_rows)
    for col in base_cols:
        if col not in add.columns:
            add[col] = ""
    p = pd.concat([p, add[base_cols]], ignore_index=True)
    return c, p, {"strict_leverage_available": recomputed, "previous_values_invalidated": invalidated}


def merge_strict_beta_metrics(controls: pd.DataFrame, metrics: pd.DataFrame) -> pd.DataFrame:
    """Replace V2 beta/eligibility columns with exact paired-return-window values."""
    c = controls.copy().set_index("ticker", drop=False)
    m = metrics.copy().set_index("ticker", drop=False)
    if not c.index.is_unique or not m.index.is_unique:
        raise ValueError("ticker must be unique in controls and price metrics")
    for ticker in c.index:
        if ticker not in m.index:
            for w in BETA_WINDOWS:
                c.at[ticker, f"has_{w}_pre_event_obs"] = False
                c.at[ticker, f"market_beta_{w}"] = np.nan
                c.at[ticker, f"has_market_beta_{w}"] = False
            c.at[ticker, "valid_paired_pre_event_return_obs"] = 0
            continue
        mr = m.loc[ticker]
        c.at[ticker, "pre_event_price_obs"] = int(mr["pre_event_price_obs"])
        c.at[ticker, "has_full_requested_history"] = bool(mr["has_full_requested_history"])
        c.at[ticker, "valid_paired_pre_event_return_obs"] = int(mr["valid_paired_pre_event_return_obs"])
        for w in BETA_WINDOWS:
            c.at[ticker, f"has_{w}_pre_event_obs"] = bool(mr[f"has_{w}_pre_event_obs"])
            c.at[ticker, f"market_beta_{w}"] = mr[f"market_beta_{w}"]
            c.at[ticker, f"has_market_beta_{w}"] = bool(pd.notna(mr[f"market_beta_{w}"]))
            c.at[ticker, f"beta_{w}_n"] = int(mr[f"beta_{w}_n"])
            c.at[ticker, f"beta_{w}_first_return_date"] = mr[f"beta_{w}_first_return_date"]
            c.at[ticker, f"beta_{w}_last_return_date"] = mr[f"beta_{w}_last_return_date"]
            c.at[ticker, f"beta_{w}_missing_reason"] = mr[f"beta_{w}_missing_reason"]
    return c.reset_index(drop=True)
