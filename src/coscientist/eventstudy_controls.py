"""Pre-event numerical controls materialization, XBRL extraction, and provenance engine.

Strict information boundary:
- All source facts must be publicly filed on or before CUTOFF_DATE (2026-08-06).
- Zero outcome data (returns, abnormal returns, CARs) accessed or computed.
- Exact accession binding preferred; period-consistent fallback validated.
"""
from __future__ import annotations

import csv
import json
import logging
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

CUTOFF_DATE = "2026-08-06"

NET_INCOME_TAGS = [
    "NetIncomeLoss",
    "ProfitLoss",
    "NetIncomeLossAvailableToCommonStockholdersBasic",
    "IncomeLossFromContinuingOperations"
]

ASSETS_TAGS = [
    "Assets"
]

LIABILITIES_TAGS = [
    "Liabilities",
    "LiabilitiesCurrent"
]

LONG_TERM_DEBT_TAGS = [
    "LongTermDebtNoncurrent",
    "LongTermDebt",
    "NoncurrentBorrowings"
]

SHORT_TERM_DEBT_TAGS = [
    "DebtCurrent",
    "ShortTermBorrowings",
    "CurrentBorrowings",
    "LinesOfCreditCurrent"
]

TOTAL_DEBT_TAGS = [
    "DebtInstrumentCarryingAmount",
    "Borrowings"
]

INTANGIBLE_ASSETS_EX_GOODWILL_TAGS = [
    "IntangibleAssetsNetExcludingGoodwill",
    "IntangibleAssetsOtherThanGoodwill"
]

GOODWILL_TAGS = [
    "Goodwill"
]

COMBINED_GOODWILL_AND_INTANGIBLE_TAGS = [
    "GoodwillAndIntangibleAssetsNet"
]

RD_EXPENSE_TAGS = [
    "ResearchAndDevelopmentExpense",
    "ResearchAndDevelopmentExpenseSoftwareExcludingAcquiredInProcessCost"
]

REVENUE_TAGS = [
    "Revenues",
    "RevenueFromContractWithCustomerExcludingAssessedTax",
    "SalesRevenueNet",
    "Revenue",
    "RevenuesNetOfInterestExpense"
]

POINT_IN_TIME_SHARES_TAGS = [
    "EntityCommonStockSharesOutstanding",
    "CommonStockSharesOutstanding"
]


def extract_best_fact(
    facts_map: Dict[str, Dict[str, Any]],
    tags: List[str],
    target_accn: Optional[str] = None,
    require_fy: bool = True,
    units_filter: Optional[Tuple[str, ...]] = None,
    cutoff: str = CUTOFF_DATE
) -> Optional[Dict[str, Any]]:
    taxonomies = ["us-gaap", "ifrs-full", "dei"]
    for tag in tags:
        for tax in taxonomies:
            tax_dict = facts_map.get(tax, {})
            if tag not in tax_dict:
                continue
            units = tax_dict[tag].get("units", {})
            for unit_name, entries in units.items():
                if units_filter and unit_name.lower() not in [u.lower() for u in units_filter]:
                    continue
                valid = []
                for e in entries:
                    f_date = e.get("filed", "")
                    end_date = e.get("end", "")
                    form = e.get("form", "")
                    fp = e.get("fp", "")
                    
                    if not f_date or f_date > cutoff:
                        continue
                    if end_date and end_date > cutoff:
                        continue
                    if require_fy and fp != "FY" and form not in ("10-K", "20-F", "40-F", "10-K/A"):
                        continue
                    if e.get("val") is not None:
                        valid.append(e)
                        
                if not valid:
                    continue
                    
                if target_accn:
                    for e in valid:
                        if e.get("accn") == target_accn:
                            return {
                                "val": float(e["val"]),
                                "tag": tag,
                                "taxonomy": tax,
                                "accn": e.get("accn", ""),
                                "filed": e.get("filed", ""),
                                "fy": str(e.get("fy", "")),
                                "fp": e.get("fp", ""),
                                "start": e.get("start", ""),
                                "end": e.get("end", ""),
                                "form": e.get("form", "")
                            }
                            
                valid.sort(key=lambda x: (x.get("filed", ""), x.get("end", "")), reverse=True)
                top = valid[0]
                return {
                    "val": float(top["val"]),
                    "tag": tag,
                    "taxonomy": tax,
                    "accn": top.get("accn", ""),
                    "filed": top.get("filed", ""),
                    "fy": str(top.get("fy", "")),
                    "fp": top.get("fp", ""),
                    "start": top.get("start", ""),
                    "end": top.get("end", ""),
                    "form": top.get("form", "")
                }
                
    return None


def extract_shares_outstanding(
    facts_map: Dict[str, Dict[str, Any]],
    target_accn: Optional[str] = None,
    cutoff: str = CUTOFF_DATE
) -> Tuple[Optional[float], Optional[str], Optional[str], Optional[str], Optional[str], Optional[str]]:
    taxonomies = ["dei", "us-gaap", "ifrs-full"]
    for tag in POINT_IN_TIME_SHARES_TAGS:
        for tax in taxonomies:
            tax_dict = facts_map.get(tax, {})
            if tag not in tax_dict:
                continue
            units = tax_dict[tag].get("units", {})
            for u in ("shares", "Shares"):
                if u in units:
                    entries = units[u]
                    valid = []
                    for e in entries:
                        f_date = e.get("filed", "")
                        end_date = e.get("end", "")
                        val = e.get("val")
                        if not f_date or f_date > cutoff:
                            continue
                        if end_date and end_date > cutoff:
                            continue
                        if val is not None and float(val) > 0:
                            valid.append(e)
                            
                    if not valid:
                        continue
                        
                    if target_accn:
                        for e in valid:
                            if e.get("accn") == target_accn:
                                as_of = e.get("end") or e.get("filed")
                                return float(e["val"]), tag, tax, e.get("accn"), as_of, e.get("filed")
                                
                    valid.sort(key=lambda x: (x.get("filed", ""), x.get("end", "")), reverse=True)
                    top = valid[0]
                    as_of = top.get("end") or top.get("filed")
                    return float(top["val"]), tag, tax, top.get("accn"), as_of, top.get("filed")
                    
    return None, None, None, None, None, None


def materialize_pre_event_controls(
    staging_dir: Path,
    cache_dir: Path,
    cutoff: str = CUTOFF_DATE
) -> Tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, Dict[str, Any]]:
    cf_path = cache_dir / "companyfacts.zip"
    sub_path = cache_dir / "submissions.zip"
    
    cf_zip = zipfile.ZipFile(cf_path)
    cf_names = set(cf_zip.namelist())
    
    sub_zip = zipfile.ZipFile(sub_path)
    sub_names = set(sub_zip.namelist())
    
    candidates = []
    with open(staging_dir / "candidate_firm_universe.csv", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            if r.get("included") == "Y":
                candidates.append(r)
                
    filing_index = {}
    with open(staging_dir / "pre_event_filing_index.csv", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            filing_index[r["ticker"]] = r
            
    price_metrics = {}
    if (staging_dir / "firm_pre_event_price_metrics.csv").exists():
        with open(staging_dir / "firm_pre_event_price_metrics.csv", encoding="utf-8") as f:
            for r in csv.DictReader(f):
                price_metrics[r["ticker"]] = r

    control_rows = []
    provenance_rows = []
    shares_audit_rows = []
    
    max_dates_observed = {
        "maximum_shares_source_filing_date": "0000-00-00",
        "maximum_shares_as_of_date": "0000-00-00",
        "maximum_accounting_source_filing_date": "0000-00-00",
        "maximum_accounting_period_end": "0000-00-00",
        "market_cap_price_date": "0000-00-00",
        "beta_estimation_date": "0000-00-00",
        "canonical_filing_selection_date": "0000-00-00"
    }

    for u in candidates:
        tk = u["ticker"]
        cik = u["cik"]
        f_info = filing_index.get(tk, {})
        p_info = price_metrics.get(tk, {})
        
        target_accn = f_info.get("accession_number")
        f_date = f_info.get("filing_date", "")
        if f_date and f_date <= cutoff and f_date > max_dates_observed["canonical_filing_selection_date"]:
            max_dates_observed["canonical_filing_selection_date"] = f_date
            
        entity_name = u["security_name"]
        sic = ""
        sic_desc = ""
        sub_json_name = f"CIK{cik}.json"
        if sub_json_name in sub_names:
            try:
                sub_d = json.loads(sub_zip.read(sub_json_name).decode("utf-8"))
                sic = str(sub_d.get("sic", "")).strip()
                sic_desc = str(sub_d.get("sicDescription", "")).strip()
                if sub_d.get("name"):
                    entity_name = str(sub_d.get("name")).strip()
            except Exception:
                pass
        sic2 = sic[:2] if len(sic) >= 2 else ""

        cf_json_name = f"CIK{cik}.json"
        facts_by_tax = {}
        if cf_json_name in cf_names:
            try:
                cf_d = json.loads(cf_zip.read(cf_json_name).decode("utf-8"))
                raw_facts = cf_d.get("facts", {})
                facts_by_tax = {
                    "dei": raw_facts.get("dei", {}),
                    "us-gaap": raw_facts.get("us-gaap", {}),
                    "ifrs-full": raw_facts.get("ifrs-full", {})
                }
            except Exception:
                pass

        shares_val, shares_tag, shares_tax, shares_accn, shares_as_of, shares_filed = extract_shares_outstanding(
            facts_by_tax, target_accn=target_accn, cutoff=cutoff
        )
        if shares_filed and shares_filed > max_dates_observed["maximum_shares_source_filing_date"]:
            max_dates_observed["maximum_shares_source_filing_date"] = shares_filed
        if shares_as_of and shares_as_of > max_dates_observed["maximum_shares_as_of_date"]:
            max_dates_observed["maximum_shares_as_of_date"] = shares_as_of
            
        price_close = float(p_info["pre_event_price_close"]) if p_info.get("pre_event_price_close") else None
        price_date = p_info.get("pre_event_last_price_date", "")
        if price_date and price_date <= cutoff and price_date > max_dates_observed["market_cap_price_date"]:
            max_dates_observed["market_cap_price_date"] = price_date
            
        if shares_val is not None and shares_val > 0 and price_close is not None and price_close > 0:
            market_cap = float(shares_val * price_close)
            market_cap_method = "POINT_IN_TIME_SHARES_X_PRE_EVENT_CLOSE"
            market_cap_missing = None
        else:
            market_cap = None
            market_cap_method = None
            if shares_val is None:
                market_cap_missing = "NO_POINT_IN_TIME_SHARES_FILED_ON_OR_BEFORE_CUTOFF"
            elif price_close is None:
                market_cap_missing = "NO_VALID_PRE_EVENT_PRICE"
            else:
                market_cap_missing = "NON_POSITIVE_INPUT"

        audit_pass = "PASS" if (not shares_filed or shares_filed <= cutoff) and (not shares_as_of or shares_as_of <= cutoff) else "FAIL"
        shares_audit_rows.append({
            "ticker": tk,
            "cik": cik,
            "shares_source_accession": shares_accn or "",
            "shares_source_filing_date": shares_filed or "",
            "shares_source_period_end": shares_as_of or "",
            "shares_source_tag": shares_tag or "",
            "shares_source_taxonomy": shares_tax or "",
            "pre_event_price_date": price_date or "",
            "market_cap": market_cap if market_cap is not None else "",
            "audit_status": audit_pass
        })

        f_assets = extract_best_fact(facts_by_tax, ASSETS_TAGS, target_accn=target_accn, require_fy=True, cutoff=cutoff)
        f_ni = extract_best_fact(facts_by_tax, NET_INCOME_TAGS, target_accn=target_accn, require_fy=True, cutoff=cutoff)
        f_st_debt = extract_best_fact(facts_by_tax, SHORT_TERM_DEBT_TAGS, target_accn=target_accn, require_fy=True, cutoff=cutoff)
        f_lt_debt = extract_best_fact(facts_by_tax, LONG_TERM_DEBT_TAGS, target_accn=target_accn, require_fy=True, cutoff=cutoff)
        f_tot_debt = extract_best_fact(facts_by_tax, TOTAL_DEBT_TAGS, target_accn=target_accn, require_fy=True, cutoff=cutoff)
        f_liab = extract_best_fact(facts_by_tax, LIABILITIES_TAGS, target_accn=target_accn, require_fy=True, cutoff=cutoff)
        f_intang_ex_gw = extract_best_fact(facts_by_tax, INTANGIBLE_ASSETS_EX_GOODWILL_TAGS, target_accn=target_accn, require_fy=True, cutoff=cutoff)
        f_gw = extract_best_fact(facts_by_tax, GOODWILL_TAGS, target_accn=target_accn, require_fy=True, cutoff=cutoff)
        f_combined_gw_intang = extract_best_fact(facts_by_tax, COMBINED_GOODWILL_AND_INTANGIBLE_TAGS, target_accn=target_accn, require_fy=True, cutoff=cutoff)
        f_rd = extract_best_fact(facts_by_tax, RD_EXPENSE_TAGS, target_accn=target_accn, require_fy=True, cutoff=cutoff)
        f_rev = extract_best_fact(facts_by_tax, REVENUE_TAGS, target_accn=target_accn, require_fy=True, cutoff=cutoff)

        for f_obj in (f_assets, f_ni, f_st_debt, f_lt_debt, f_tot_debt, f_liab, f_intang_ex_gw, f_gw, f_combined_gw_intang, f_rd, f_rev):
            if f_obj:
                f_dt = f_obj.get("filed", "")
                e_dt = f_obj.get("end", "")
                if f_dt and f_dt > max_dates_observed["maximum_accounting_source_filing_date"]:
                    max_dates_observed["maximum_accounting_source_filing_date"] = f_dt
                if e_dt and e_dt > max_dates_observed["maximum_accounting_period_end"]:
                    max_dates_observed["maximum_accounting_period_end"] = e_dt

        assets_val = f_assets["val"] if f_assets else None
        assets_end = f_assets["end"] if f_assets else ""
        assets_accn = f_assets["accn"] if f_assets else ""

        used_accessions = set()
        if target_accn:
            for f_obj in (f_assets, f_ni, f_st_debt, f_lt_debt, f_tot_debt, f_liab, f_intang_ex_gw, f_gw, f_combined_gw_intang, f_rd, f_rev):
                if f_obj and f_obj.get("accn"):
                    used_accessions.add(f_obj["accn"])
        cross_accession_fallback = any(accn != target_accn for accn in used_accessions) if (target_accn and used_accessions) else False

        roa = None
        roa_period_consistent = False
        roa_missing = None
        if f_ni is not None and f_assets is not None and assets_val and assets_val > 0:
            if f_ni["end"] == assets_end:
                roa = float(f_ni["val"] / assets_val)
                roa_period_consistent = True
            else:
                roa_missing = "PERIOD_MISMATCH_BETWEEN_NET_INCOME_AND_ASSETS"
        else:
            if f_ni is None and f_assets is None:
                roa_missing = "ACCOUNTING_DATA_NOT_REPORTED"
            elif f_ni is None:
                roa_missing = "NET_INCOME_NOT_REPORTED"
            elif f_assets is None or assets_val <= 0:
                roa_missing = "ASSETS_ZERO_OR_NOT_REPORTED"

        total_debt_val = None
        total_debt_method = None
        total_debt_accn = ""
        total_debt_tag = ""
        total_debt_tax = ""
        total_debt_filed = ""
        total_debt_end = ""
        leverage_debt_assets = None
        leverage_period_consistent = False
        leverage_missing = None

        if f_tot_debt is not None and f_tot_debt["end"] == assets_end:
            total_debt_val = f_tot_debt["val"]
            total_debt_method = "DIRECT_REPORTED_TOTAL_DEBT"
            total_debt_accn = f_tot_debt["accn"]
            total_debt_tag = f_tot_debt["tag"]
            total_debt_tax = f_tot_debt["taxonomy"]
            total_debt_filed = f_tot_debt["filed"]
            total_debt_end = f_tot_debt["end"]
        elif (f_st_debt is not None and f_lt_debt is not None and 
              f_st_debt["end"] == assets_end and f_lt_debt["end"] == assets_end):
            total_debt_val = float(f_st_debt["val"] + f_lt_debt["val"])
            total_debt_method = "SHORT_PLUS_LONG_TERM_DEBT"
            total_debt_accn = f_st_debt["accn"] if f_st_debt["accn"] == f_lt_debt["accn"] else f"{f_st_debt['accn']}+{f_lt_debt['accn']}"
            total_debt_tag = f"{f_st_debt['tag']}+{f_lt_debt['tag']}"
            total_debt_tax = f_st_debt["taxonomy"] if f_st_debt["taxonomy"] == f_lt_debt["taxonomy"] else f"{f_st_debt['taxonomy']}+{f_lt_debt['taxonomy']}"
            total_debt_filed = max(f_st_debt["filed"], f_lt_debt["filed"])
            total_debt_end = assets_end

        if total_debt_val is not None and assets_val and assets_val > 0:
            leverage_debt_assets = float(total_debt_val / assets_val)
            leverage_period_consistent = True
        else:
            if total_debt_val is None:
                leverage_missing = "TOTAL_DEBT_NOT_REPORTED_OR_INCOMPLETE_COMPONENTS"
            else:
                leverage_missing = "ASSETS_ZERO_OR_NOT_REPORTED"

        liabilities_to_assets = None
        if f_liab is not None and assets_val and assets_val > 0 and f_liab["end"] == assets_end:
            liabilities_to_assets = float(f_liab["val"] / assets_val)

        combined_intangibles_val = None
        combined_intangibles_method = None
        combined_intangibles_tag = ""
        combined_intangibles_tax = ""
        combined_intangibles_accn = ""
        combined_intangibles_filed = ""
        combined_intangibles_end = ""
        goodwill_double_count_prevented = False

        if f_combined_gw_intang is not None and f_combined_gw_intang["end"] == assets_end:
            combined_intangibles_val = f_combined_gw_intang["val"]
            combined_intangibles_method = "REPORTED_GOODWILL_AND_INTANGIBLES_NET"
            combined_intangibles_tag = f_combined_gw_intang["tag"]
            combined_intangibles_tax = f_combined_gw_intang["taxonomy"]
            combined_intangibles_accn = f_combined_gw_intang["accn"]
            combined_intangibles_filed = f_combined_gw_intang["filed"]
            combined_intangibles_end = f_combined_gw_intang["end"]
            goodwill_double_count_prevented = True
        elif (f_intang_ex_gw is not None and f_gw is not None and 
              f_intang_ex_gw["end"] == assets_end and f_gw["end"] == assets_end):
            combined_intangibles_val = float(f_intang_ex_gw["val"] + f_gw["val"])
            combined_intangibles_method = "CONSTRUCTED_INTANGIBLES_EX_GOODWILL_PLUS_GOODWILL"
            combined_intangibles_tag = f"{f_intang_ex_gw['tag']}+{f_gw['tag']}"
            combined_intangibles_tax = f_intang_ex_gw["taxonomy"] if f_intang_ex_gw["taxonomy"] == f_gw["taxonomy"] else f"{f_intang_ex_gw['taxonomy']}+{f_gw['taxonomy']}"
            combined_intangibles_accn = f_intang_ex_gw["accn"] if f_intang_ex_gw["accn"] == f_gw["accn"] else f"{f_intang_ex_gw['accn']}+{f_gw['accn']}"
            combined_intangibles_filed = max(f_intang_ex_gw["filed"], f_gw["filed"])
            combined_intangibles_end = assets_end
            goodwill_double_count_prevented = True
        elif f_intang_ex_gw is not None and f_intang_ex_gw["end"] == assets_end:
            combined_intangibles_val = float(f_intang_ex_gw["val"])
            combined_intangibles_method = "INTANGIBLES_EX_GOODWILL_ONLY"
            combined_intangibles_tag = f_intang_ex_gw["tag"]
            combined_intangibles_tax = f_intang_ex_gw["taxonomy"]
            combined_intangibles_accn = f_intang_ex_gw["accn"]
            combined_intangibles_filed = f_intang_ex_gw["filed"]
            combined_intangibles_end = f_intang_ex_gw["end"]
        elif f_gw is not None and f_gw["end"] == assets_end:
            combined_intangibles_val = float(f_gw["val"])
            combined_intangibles_method = "GOODWILL_ONLY"
            combined_intangibles_tag = f_gw["tag"]
            combined_intangibles_tax = f_gw["taxonomy"]
            combined_intangibles_accn = f_gw["accn"]
            combined_intangibles_filed = f_gw["filed"]
            combined_intangibles_end = f_gw["end"]

        intangibles_period_consistent = combined_intangibles_val is not None
        combined_intangibles_to_assets = float(combined_intangibles_val / assets_val) if (combined_intangibles_val is not None and assets_val and assets_val > 0) else None

        rd_to_revenue = None
        rd_period_consistent = False
        rd_missing = None
        if f_rd is not None and f_rev is not None:
            if f_rd["start"] == f_rev["start"] and f_rd["end"] == f_rev["end"]:
                if f_rev["val"] > 0:
                    rd_to_revenue = float(f_rd["val"] / f_rev["val"])
                    rd_period_consistent = True
                else:
                    rd_missing = "REVENUE_ZERO_OR_NEGATIVE"
            else:
                rd_missing = "PERIOD_MISMATCH_BETWEEN_RD_AND_REVENUE"
        else:
            if f_rd is None:
                rd_missing = "RD_EXPENSE_NOT_REPORTED"
            else:
                rd_missing = "REVENUE_NOT_REPORTED"

        beta_120 = float(p_info["market_beta_120"]) if p_info.get("market_beta_120") not in (None, "") else None
        beta_200 = float(p_info["market_beta_200"]) if p_info.get("market_beta_200") not in (None, "") else None
        beta_250 = float(p_info["market_beta_250"]) if p_info.get("market_beta_250") not in (None, "") else None
        
        last_price_dt = p_info.get("pre_event_last_price_date", "")
        if last_price_dt and last_price_dt <= cutoff and last_price_dt > max_dates_observed["beta_estimation_date"]:
            max_dates_observed["beta_estimation_date"] = last_price_dt

        control_rows.append({
            "ticker": tk,
            "cik": cik,
            "entity_name": entity_name,
            "exchange": u["exchange"],
            "sic": sic,
            "sic_description": sic_desc,
            "sic2": sic2,
            "has_sic": bool(sic),
            "has_sic2": bool(sic2),
            "pre_event_form": f_info.get("form", ""),
            "pre_event_filing_date": f_date,
            "period_of_report": f_info.get("period_of_report", ""),
            "pre_event_cutoff_date": cutoff,
            "shares_outstanding": shares_val,
            "shares_as_of_date": shares_as_of,
            "shares_source_filing_date": shares_filed,
            "shares_source_period_end": shares_as_of,
            "shares_source_tag": shares_tag,
            "shares_source_taxonomy": shares_tax,
            "shares_source_accn": shares_accn,
            "pre_event_price_used": price_close,
            "pre_event_price_date": price_date,
            "market_cap": market_cap,
            "market_cap_method": market_cap_method,
            "market_cap_missing_reason": market_cap_missing,
            "has_market_cap": market_cap is not None,
            "net_income": f_ni["val"] if f_ni else None,
            "net_income_tag": f_ni["tag"] if f_ni else None,
            "net_income_taxonomy": f_ni["taxonomy"] if f_ni else None,
            "net_income_accn": f_ni["accn"] if f_ni else None,
            "net_income_filed": f_ni["filed"] if f_ni else None,
            "net_income_start": f_ni["start"] if f_ni else None,
            "net_income_end": f_ni["end"] if f_ni else None,
            "total_assets": assets_val,
            "total_assets_tag": f_assets["tag"] if f_assets else None,
            "total_assets_taxonomy": f_assets["taxonomy"] if f_assets else None,
            "total_assets_accn": assets_accn,
            "total_assets_filed": f_assets["filed"] if f_assets else None,
            "total_assets_end": assets_end,
            "profitability_roa": roa,
            "roa_period_consistent": roa_period_consistent,
            "roa_missing_reason": roa_missing,
            "has_profitability": roa is not None and roa_period_consistent,
            "short_term_debt": f_st_debt["val"] if f_st_debt else None,
            "long_term_debt": f_lt_debt["val"] if f_lt_debt else None,
            "reported_total_debt": f_tot_debt["val"] if f_tot_debt else None,
            "total_debt": total_debt_val,
            "total_debt_method": total_debt_method,
            "total_debt_tag": total_debt_tag,
            "total_debt_taxonomy": total_debt_tax,
            "total_debt_accn": total_debt_accn,
            "total_debt_filed": total_debt_filed,
            "total_debt_end": total_debt_end,
            "total_liabilities": f_liab["val"] if f_liab else None,
            "leverage_debt_assets": leverage_debt_assets,
            "leverage_period_consistent": leverage_period_consistent,
            "leverage_missing_reason": leverage_missing,
            "has_leverage": leverage_debt_assets is not None and leverage_period_consistent,
            "liabilities_to_assets": liabilities_to_assets,
            "intangibles_ex_goodwill": f_intang_ex_gw["val"] if f_intang_ex_gw else None,
            "goodwill": f_gw["val"] if f_gw else None,
            "reported_goodwill_and_intangibles": f_combined_gw_intang["val"] if f_combined_gw_intang else None,
            "combined_intangibles": combined_intangibles_val,
            "combined_intangibles_method": combined_intangibles_method,
            "combined_intangibles_tag": combined_intangibles_tag,
            "combined_intangibles_taxonomy": combined_intangibles_tax,
            "combined_intangibles_accn": combined_intangibles_accn,
            "combined_intangibles_filed": combined_intangibles_filed,
            "combined_intangibles_end": combined_intangibles_end,
            "goodwill_double_count_prevented": goodwill_double_count_prevented,
            "intangibles_plus_goodwill_to_assets": combined_intangibles_to_assets,
            "intangibles_period_consistent": intangibles_period_consistent,
            "has_intangible_assets": combined_intangibles_to_assets is not None and intangibles_period_consistent,
            "research_and_development_expense": f_rd["val"] if f_rd else None,
            "rd_expense_tag": f_rd["tag"] if f_rd else None,
            "rd_expense_taxonomy": f_rd["taxonomy"] if f_rd else None,
            "rd_expense_accn": f_rd["accn"] if f_rd else None,
            "rd_expense_filed": f_rd["filed"] if f_rd else None,
            "rd_expense_start": f_rd["start"] if f_rd else None,
            "rd_expense_end": f_rd["end"] if f_rd else None,
            "revenue": f_rev["val"] if f_rev else None,
            "revenue_tag": f_rev["tag"] if f_rev else None,
            "revenue_taxonomy": f_rev["taxonomy"] if f_rev else None,
            "revenue_accn": f_rev["accn"] if f_rev else None,
            "revenue_filed": f_rev["filed"] if f_rev else None,
            "revenue_start": f_rev["start"] if f_rev else None,
            "revenue_end": f_rev["end"] if f_rev else None,
            "rd_to_revenue": rd_to_revenue,
            "rd_period_consistent": rd_period_consistent,
            "rd_missing_reason": rd_missing,
            "has_rd_proxy": f_rd is not None,
            "has_rd_to_revenue": rd_to_revenue is not None and rd_period_consistent,
            "cross_accession_fallback": cross_accession_fallback,
            "pre_event_price_obs": int(p_info.get("pre_event_price_obs", 0)) if p_info.get("pre_event_price_obs") else 0,
            "has_120_pre_event_obs": p_info.get("has_120_pre_event_obs") in ("True", True),
            "has_200_pre_event_obs": p_info.get("has_200_pre_event_obs") in ("True", True),
            "has_250_pre_event_obs": p_info.get("has_250_pre_event_obs") in ("True", True),
            "has_full_requested_history": p_info.get("has_full_requested_history") in ("True", True),
            "market_beta_120": beta_120,
            "market_beta_200": beta_200,
            "market_beta_250": beta_250,
            "has_market_beta_120": beta_120 is not None,
            "has_market_beta_200": beta_200 is not None,
            "has_market_beta_250": beta_250 is not None
        })

        primitive_specs = [
            ("SHARES_OUTSTANDING", shares_val, shares_tag, shares_tax, shares_accn, shares_filed, "", shares_as_of, "", "", "Point-in-time common shares"),
            ("PRE_EVENT_PRICE", price_close, "Close", "YahooFinance", "DAILY_PRICE_PANEL", price_date, "", price_date, "", "", "Pre-event closing price as of 2026-08-06"),
            ("MARKET_CAP", market_cap, f"{shares_tag}*Close", shares_tax, shares_accn, shares_filed, "", price_date, "", "", "Shares outstanding * pre-event close"),
            ("NET_INCOME", f_ni["val"] if f_ni else None, f_ni["tag"] if f_ni else "", f_ni["taxonomy"] if f_ni else "", f_ni["accn"] if f_ni else "", f_ni["filed"] if f_ni else "", f_ni["start"] if f_ni else "", f_ni["end"] if f_ni else "", f_ni["fy"] if f_ni else "", f_ni["fp"] if f_ni else "", "Annual net income"),
            ("TOTAL_ASSETS", assets_val, f_assets["tag"] if f_assets else "", f_assets["taxonomy"] if f_assets else "", assets_accn, f_assets["filed"] if f_assets else "", "", assets_end, f_assets["fy"] if f_assets else "", f_assets["fp"] if f_assets else "", "Balance sheet total assets"),
            ("PROFITABILITY_ROA", roa, f"{f_ni['tag'] if f_ni else 'None'}/{f_assets['tag'] if f_assets else 'None'}", f_ni["taxonomy"] if f_ni else "", f_ni["accn"] if f_ni else "", f_ni["filed"] if f_ni else "", f_ni["start"] if f_ni else "", assets_end, f_ni["fy"] if f_ni else "", f_ni["fp"] if f_ni else "", "Net income / Total assets (period matched)"),
            ("SHORT_TERM_DEBT", f_st_debt["val"] if f_st_debt else None, f_st_debt["tag"] if f_st_debt else "", f_st_debt["taxonomy"] if f_st_debt else "", f_st_debt["accn"] if f_st_debt else "", f_st_debt["filed"] if f_st_debt else "", "", f_st_debt["end"] if f_st_debt else "", f_st_debt["fy"] if f_st_debt else "", f_st_debt["fp"] if f_st_debt else "", "Short-term borrowings/debt"),
            ("LONG_TERM_DEBT", f_lt_debt["val"] if f_lt_debt else None, f_lt_debt["tag"] if f_lt_debt else "", f_lt_debt["taxonomy"] if f_lt_debt else "", f_lt_debt["accn"] if f_lt_debt else "", f_lt_debt["filed"] if f_lt_debt else "", "", f_lt_debt["end"] if f_lt_debt else "", f_lt_debt["fy"] if f_lt_debt else "", f_lt_debt["fp"] if f_lt_debt else "", "Long-term debt"),
            ("REPORTED_TOTAL_DEBT", f_tot_debt["val"] if f_tot_debt else None, f_tot_debt["tag"] if f_tot_debt else "", f_tot_debt["taxonomy"] if f_tot_debt else "", f_tot_debt["accn"] if f_tot_debt else "", f_tot_debt["filed"] if f_tot_debt else "", "", f_tot_debt["end"] if f_tot_debt else "", f_tot_debt["fy"] if f_tot_debt else "", f_tot_debt["fp"] if f_tot_debt else "", "Direct reported total debt"),
            ("TOTAL_DEBT", total_debt_val, total_debt_tag, total_debt_tax, total_debt_accn, total_debt_filed, "", total_debt_end, "", "", f"Composite/direct total debt ({total_debt_method})"),
            ("LEVERAGE_DEBT_ASSETS", leverage_debt_assets, f"{total_debt_tag}/{f_assets['tag'] if f_assets else 'None'}", total_debt_tax, total_debt_accn, total_debt_filed, "", assets_end, "", "", "Total debt / Total assets (period matched)"),
            ("TOTAL_LIABILITIES", f_liab["val"] if f_liab else None, f_liab["tag"] if f_liab else "", f_liab["taxonomy"] if f_liab else "", f_liab["accn"] if f_liab else "", f_liab["filed"] if f_liab else "", "", f_liab["end"] if f_liab else "", f_liab["fy"] if f_liab else "", f_liab["fp"] if f_liab else "", "Total balance sheet liabilities"),
            ("INTANGIBLES_EX_GOODWILL", f_intang_ex_gw["val"] if f_intang_ex_gw else None, f_intang_ex_gw["tag"] if f_intang_ex_gw else "", f_intang_ex_gw["taxonomy"] if f_intang_ex_gw else "", f_intang_ex_gw["accn"] if f_intang_ex_gw else "", f_intang_ex_gw["filed"] if f_intang_ex_gw else "", "", f_intang_ex_gw["end"] if f_intang_ex_gw else "", f_intang_ex_gw["fy"] if f_intang_ex_gw else "", f_intang_ex_gw["fp"] if f_intang_ex_gw else "", "Intangible assets excluding goodwill"),
            ("GOODWILL", f_gw["val"] if f_gw else None, f_gw["tag"] if f_gw else "", f_gw["taxonomy"] if f_gw else "", f_gw["accn"] if f_gw else "", f_gw["filed"] if f_gw else "", "", f_gw["end"] if f_gw else "", f_gw["fy"] if f_gw else "", f_gw["fp"] if f_gw else "", "Goodwill"),
            ("COMBINED_GOODWILL_INTANGIBLES_REPORTED", f_combined_gw_intang["val"] if f_combined_gw_intang else None, f_combined_gw_intang["tag"] if f_combined_gw_intang else "", f_combined_gw_intang["taxonomy"] if f_combined_gw_intang else "", f_combined_gw_intang["accn"] if f_combined_gw_intang else "", f_combined_gw_intang["filed"] if f_combined_gw_intang else "", "", f_combined_gw_intang["end"] if f_combined_gw_intang else "", f_combined_gw_intang["fy"] if f_combined_gw_intang else "", f_combined_gw_intang["fp"] if f_combined_gw_intang else "", "Direct reported goodwill and intangibles"),
            ("COMBINED_INTANGIBLES_TOTAL", combined_intangibles_val, combined_intangibles_tag, combined_intangibles_tax, combined_intangibles_accn, combined_intangibles_filed, "", combined_intangibles_end, "", "", f"Combined intangibles ({combined_intangibles_method})"),
            ("INTANGIBLES_TO_ASSETS", combined_intangibles_to_assets, f"{combined_intangibles_tag}/{f_assets['tag'] if f_assets else 'None'}", combined_intangibles_tax, combined_intangibles_accn, combined_intangibles_filed, "", assets_end, "", "", "Combined intangibles / Total assets"),
            ("RESEARCH_AND_DEVELOPMENT_EXPENSE", f_rd["val"] if f_rd else None, f_rd["tag"] if f_rd else "", f_rd["taxonomy"] if f_rd else "", f_rd["accn"] if f_rd else "", f_rd["filed"] if f_rd else "", f_rd["start"] if f_rd else "", f_rd["end"] if f_rd else "", f_rd["fy"] if f_rd else "", f_rd["fp"] if f_rd else "", "Research and development expense"),
            ("REVENUE", f_rev["val"] if f_rev else None, f_rev["tag"] if f_rev else "", f_rev["taxonomy"] if f_rev else "", f_rev["accn"] if f_rev else "", f_rev["filed"] if f_rev else "", f_rev["start"] if f_rev else "", f_rev["end"] if f_rev else "", f_rev["fy"] if f_rev else "", f_rev["fp"] if f_rev else "", "Revenues / Sales"),
            ("RD_TO_REVENUE", rd_to_revenue, f"{f_rd['tag'] if f_rd else 'None'}/{f_rev['tag'] if f_rev else 'None'}", f_rd["taxonomy"] if f_rd else "", f_rd["accn"] if f_rd else "", f_rd["filed"] if f_rd else "", f_rd["start"] if f_rd else "", f_rd["end"] if f_rd else "", f_rd["fy"] if f_rd else "", f_rd["fp"] if f_rd else "", "R&D / Revenue (period matched)"),
            ("INDUSTRY_SIC", sic, "sic", "SEC_SUBMISSIONS", sub_json_name, f_date or cutoff, "", f_date or cutoff, "", "", "Primary Standard Industrial Classification code")
        ]

        for c_name, val, tag_used, tax_used, accn_used, filed_dt, p_start, p_end, fy_val, fp_val, desc in primitive_specs:
            provenance_rows.append({
                "ticker": tk,
                "cik": cik,
                "construct": c_name,
                "value": val if val is not None else "",
                "available": "Y" if val is not None else "N",
                "xbrl_tag": tag_used or "",
                "source_taxonomy": tax_used or "",
                "source_accession": accn_used or "",
                "source_filing_date": filed_dt or "",
                "period_start": p_start or "",
                "period_end": p_end or "",
                "fiscal_year": fy_val or "",
                "fiscal_period": fp_val or "",
                "cross_accession_fallback": "Y" if (accn_used and target_accn and accn_used != target_accn) else "N",
                "pre_event_cutoff": cutoff,
                "description": desc
            })

    df_controls = pd.DataFrame(control_rows)
    df_prov = pd.DataFrame(provenance_rows)
    df_shares_audit = pd.DataFrame(shares_audit_rows)
    
    all_cutoff_pass = (
        max_dates_observed["maximum_shares_source_filing_date"] <= cutoff and
        max_dates_observed["maximum_shares_as_of_date"] <= cutoff and
        max_dates_observed["maximum_accounting_source_filing_date"] <= cutoff and
        max_dates_observed["maximum_accounting_period_end"] <= cutoff and
        max_dates_observed["market_cap_price_date"] <= cutoff and
        max_dates_observed["beta_estimation_date"] <= cutoff and
        max_dates_observed["canonical_filing_selection_date"] <= cutoff
    )
    max_dates_observed["pre_event_cutoff_date"] = cutoff
    max_dates_observed["audit_status"] = "PASS" if all_cutoff_pass else "FAIL"
    
    return df_controls, df_prov, df_shares_audit, max_dates_observed
