import csv
import json
import logging
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

CUTOFF_DATE = "2026-08-06"

# Taxonomies and tag fallback hierarchies
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

INTANGIBLE_ASSETS_TAGS = [
    "IntangibleAssetsNetExcludingGoodwill",
    "IntangibleAssetsOtherThanGoodwill",
    "GoodwillAndIntangibleAssetsNet"
]

GOODWILL_TAGS = [
    "Goodwill"
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

SHARES_TAGS = [
    "CommonStockSharesOutstanding",
    "EntityCommonStockSharesOutstanding",
    "CommonStockSharesIssued",
    "WeightedAverageNumberOfSharesOutstandingBasic"
]


def extract_best_fact(
    facts_map: Dict[str, Any],
    tags: List[str],
    target_accn: Optional[str] = None,
    require_fy: bool = True,
    units_filter: Optional[Tuple[str, ...]] = None,
    cutoff: str = CUTOFF_DATE
) -> Tuple[Optional[float], Optional[str], Optional[str], Optional[str], Optional[str], Optional[str]]:
    """
    Extracts the best XBRL fact matching the tag hierarchy and cutoff date.
    Returns: (val, tag, accn, filed_date, fy, period_end_date)
    """
    for tag in tags:
        if tag not in facts_map:
            continue
        units = facts_map[tag].get("units", {})
        for unit_name, entries in units.items():
            if units_filter and unit_name.lower() not in [u.lower() for u in units_filter]:
                continue
            valid = []
            for e in entries:
                f_date = e.get("filed", "")
                form = e.get("form", "")
                fp = e.get("fp", "")
                
                # Enforce strict cutoff
                if f_date > cutoff:
                    continue
                if require_fy and fp != "FY" and form not in ("10-K", "20-F", "40-F", "10-K/A"):
                    continue
                valid.append(e)
                
            if not valid:
                continue
                
            # If target accession is provided, try exact accession match first
            if target_accn:
                for e in valid:
                    if e.get("accn") == target_accn and e.get("val") is not None:
                        return float(e["val"]), tag, e.get("accn"), e.get("filed"), str(e.get("fy", "")), e.get("end")
                        
            # Fallback to latest valid pre-event observation by filed date, then end date
            valid.sort(key=lambda x: (x.get("filed", ""), x.get("end", "")), reverse=True)
            for e in valid:
                if e.get("val") is not None:
                    return float(e["val"]), tag, e.get("accn"), e.get("filed"), str(e.get("fy", "")), e.get("end")
                    
    return None, None, None, None, None, None


def extract_shares_outstanding(
    facts_map: Dict[str, Any],
    target_accn: Optional[str] = None,
    cutoff: str = CUTOFF_DATE
) -> Tuple[Optional[float], Optional[str], Optional[str], Optional[str], Optional[str]]:
    """
    Extracts pre-event shares outstanding dated <= cutoff.
    Returns: (shares, tag, accn, as_of_date, filed_date)
    """
    for tag in SHARES_TAGS:
        if tag not in facts_map:
            continue
        units = facts_map[tag].get("units", {})
        for u in ("shares", "Shares"):
            if u in units:
                entries = units[u]
                valid = [
                    e for e in entries
                    if (e.get("filed", "") <= cutoff or e.get("end", "") <= cutoff) and e.get("val") is not None
                ]
                if not valid:
                    continue
                if target_accn:
                    for e in valid:
                        if e.get("accn") == target_accn:
                            as_of = e.get("end") or e.get("filed")
                            return float(e["val"]), tag, e.get("accn"), as_of, e.get("filed")
                valid.sort(key=lambda x: (x.get("filed", ""), x.get("end", "")), reverse=True)
                top = valid[0]
                as_of = top.get("end") or top.get("filed")
                return float(top["val"]), tag, top.get("accn"), as_of, top.get("filed")
    return None, None, None, None, None


def materialize_pre_event_controls(
    staging_dir: Path,
    cache_dir: Path,
    cutoff: str = CUTOFF_DATE
) -> Tuple[pd.DataFrame, pd.DataFrame, Dict[str, Any]]:
    """
    Materializes pre-event numerical controls, provenance, and cutoff audit.
    """
    cf_path = cache_dir / "companyfacts.zip"
    sub_path = cache_dir / "submissions.zip"
    
    cf_zip = zipfile.ZipFile(cf_path)
    cf_names = set(cf_zip.namelist())
    
    sub_zip = zipfile.ZipFile(sub_path)
    sub_names = set(sub_zip.namelist())
    
    # Load candidate universe
    candidates = []
    with open(staging_dir / "candidate_firm_universe.csv", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            if r.get("included") == "Y":
                candidates.append(r)
                
    # Load filing index
    filing_index = {}
    with open(staging_dir / "pre_event_filing_index.csv", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            filing_index[r["ticker"]] = r
            
    # Load price metrics
    price_metrics = {}
    if (staging_dir / "firm_pre_event_price_metrics.csv").exists():
        with open(staging_dir / "firm_pre_event_price_metrics.csv", encoding="utf-8") as f:
            for r in csv.DictReader(f):
                price_metrics[r["ticker"]] = r

    control_rows = []
    provenance_rows = []
    
    max_dates_observed = {
        "filing_selection": "0000-00-00",
        "shares_outstanding": "0000-00-00",
        "market_cap_price": "0000-00-00",
        "accounting_controls": "0000-00-00",
        "beta_estimation": "0000-00-00"
    }

    for u in candidates:
        tk = u["ticker"]
        cik = u["cik"]
        f_info = filing_index.get(tk, {})
        p_info = price_metrics.get(tk, {})
        
        target_accn = f_info.get("accession_number")
        f_date = f_info.get("filing_date", "")
        if f_date and f_date <= cutoff and f_date > max_dates_observed["filing_selection"]:
            max_dates_observed["filing_selection"] = f_date
            
        # 1. Entity & Industry metadata from submissions
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

        # 2. Extract facts from companyfacts
        cf_json_name = f"CIK{cik}.json"
        facts = {}
        if cf_json_name in cf_names:
            try:
                cf_d = json.loads(cf_zip.read(cf_json_name).decode("utf-8"))
                facts = cf_d.get("facts", {})
            except Exception:
                pass

        gaap = facts.get("us-gaap", {})
        ifrs = facts.get("ifrs-full", {})
        dei = facts.get("dei", {})
        all_financial_facts = {**ifrs, **gaap}
        all_facts = {**dei, **ifrs, **gaap}

        # 3. Shares & Market Cap
        shares_val, shares_tag, shares_accn, shares_as_of, shares_filed = extract_shares_outstanding(
            all_facts, target_accn=target_accn, cutoff=cutoff
        )
        if shares_as_of and shares_as_of <= cutoff and shares_as_of > max_dates_observed["shares_outstanding"]:
            max_dates_observed["shares_outstanding"] = shares_as_of
            
        price_close = float(p_info["pre_event_price_close"]) if p_info.get("pre_event_price_close") else None
        price_date = p_info.get("pre_event_last_price_date", "")
        if price_date and price_date <= cutoff and price_date > max_dates_observed["market_cap_price"]:
            max_dates_observed["market_cap_price"] = price_date
            
        if shares_val is not None and shares_val > 0 and price_close is not None and price_close > 0:
            market_cap = float(shares_val * price_close)
            market_cap_method = "SHARES_OUTSTANDING_X_PRE_EVENT_CLOSE"
            market_cap_missing = None
        else:
            market_cap = None
            market_cap_method = None
            if shares_val is None:
                market_cap_missing = "NO_VALID_SHARES_OUTSTANDING"
            elif price_close is None:
                market_cap_missing = "NO_VALID_PRE_EVENT_PRICE"
            else:
                market_cap_missing = "NON_POSITIVE_INPUT"

        # 4. Profitability (ROA)
        ni_val, ni_tag, ni_accn, ni_filed, ni_fy, ni_end = extract_best_fact(
            all_financial_facts, NET_INCOME_TAGS, target_accn=target_accn, require_fy=True, cutoff=cutoff
        )
        assets_val, assets_tag, assets_accn, assets_filed, assets_fy, assets_end = extract_best_fact(
            all_financial_facts, ASSETS_TAGS, target_accn=target_accn, require_fy=True, cutoff=cutoff
        )
        
        for dt_val in (ni_filed, assets_filed):
            if dt_val and dt_val <= cutoff and dt_val > max_dates_observed["accounting_controls"]:
                max_dates_observed["accounting_controls"] = dt_val
                
        if ni_val is not None and assets_val is not None and assets_val > 0:
            roa = float(ni_val / assets_val)
            roa_missing = None
        else:
            roa = None
            if ni_val is None and assets_val is None:
                roa_missing = "NO_ELIGIBLE_PRE_EVENT_FILING" if not target_accn else "TAG_NOT_REPORTED"
            elif ni_val is None:
                roa_missing = "NET_INCOME_NOT_REPORTED"
            elif assets_val is None or assets_val <= 0:
                roa_missing = "ASSETS_ZERO_OR_NOT_REPORTED"
            else:
                roa_missing = "COMPUTATION_ERROR"

        # 5. Leverage
        st_debt_val, st_debt_tag, _, _, _, _ = extract_best_fact(
            all_financial_facts, SHORT_TERM_DEBT_TAGS, target_accn=target_accn, require_fy=True, cutoff=cutoff
        )
        lt_debt_val, lt_debt_tag, _, _, _, _ = extract_best_fact(
            all_financial_facts, LONG_TERM_DEBT_TAGS, target_accn=target_accn, require_fy=True, cutoff=cutoff
        )
        tot_debt_val, tot_debt_tag, _, _, _, _ = extract_best_fact(
            all_financial_facts, TOTAL_DEBT_TAGS, target_accn=target_accn, require_fy=True, cutoff=cutoff
        )
        liab_val, liab_tag, _, _, _, _ = extract_best_fact(
            all_financial_facts, LIABILITIES_TAGS, target_accn=target_accn, require_fy=True, cutoff=cutoff
        )

        # Composite total debt calculation
        total_debt = None
        if lt_debt_val is not None and st_debt_val is not None:
            total_debt = float(lt_debt_val + st_debt_val)
        elif lt_debt_val is not None:
            total_debt = float(lt_debt_val)
        elif tot_debt_val is not None:
            total_debt = float(tot_debt_val)
            
        if total_debt is not None and assets_val is not None and assets_val > 0:
            leverage_debt_assets = float(total_debt / assets_val)
            leverage_missing = None
        else:
            leverage_debt_assets = None
            if total_debt is None:
                leverage_missing = "DEBT_NOT_REPORTED"
            else:
                leverage_missing = "ASSETS_ZERO_OR_NOT_REPORTED"

        liabilities_to_assets = float(liab_val / assets_val) if (liab_val is not None and assets_val and assets_val > 0) else None

        # 6. Intangibles & R&D
        intang_val, intang_tag, _, _, _, _ = extract_best_fact(
            all_financial_facts, INTANGIBLE_ASSETS_TAGS, target_accn=target_accn, require_fy=True, cutoff=cutoff
        )
        gw_val, gw_tag, _, _, _, _ = extract_best_fact(
            all_financial_facts, GOODWILL_TAGS, target_accn=target_accn, require_fy=True, cutoff=cutoff
        )
        rd_val, rd_tag, _, _, _, _ = extract_best_fact(
            all_financial_facts, RD_EXPENSE_TAGS, target_accn=target_accn, require_fy=True, cutoff=cutoff
        )
        rev_val, rev_tag, _, _, _, _ = extract_best_fact(
            all_financial_facts, REVENUE_TAGS, target_accn=target_accn, require_fy=True, cutoff=cutoff
        )

        intangibles_to_assets = float(intang_val / assets_val) if (intang_val is not None and assets_val and assets_val > 0) else None
        goodwill_to_assets = float(gw_val / assets_val) if (gw_val is not None and assets_val and assets_val > 0) else None
        
        # Combined intangibles + goodwill
        combined_intang = None
        if intang_val is not None and gw_val is not None:
            combined_intang = float(intang_val + gw_val)
        elif intang_val is not None:
            combined_intang = float(intang_val)
        elif gw_val is not None:
            combined_intang = float(gw_val)
        combined_intang_to_assets = float(combined_intang / assets_val) if (combined_intang is not None and assets_val and assets_val > 0) else None

        rd_to_rev = float(rd_val / rev_val) if (rd_val is not None and rev_val and rev_val > 0) else None
        has_intangible = intang_val is not None or gw_val is not None
        has_rd = rd_val is not None

        if has_intangible and has_rd:
            proxy_avail = "INTANGIBLES_AND_RD"
            intang_missing = None
        elif has_intangible:
            proxy_avail = "INTANGIBLES_ONLY"
            intang_missing = None
        elif has_rd:
            proxy_avail = "RD_ONLY"
            intang_missing = "INTANGIBLE_ASSETS_NOT_SEPARATELY_DISCLOSED"
        else:
            proxy_avail = "NONE"
            intang_missing = "TAG_NOT_REPORTED"

        # 7. Betas & price stats
        beta_120 = float(p_info["market_beta_120"]) if p_info.get("market_beta_120") not in (None, "") else None
        beta_200 = float(p_info["market_beta_200"]) if p_info.get("market_beta_200") not in (None, "") else None
        beta_250 = float(p_info["market_beta_250"]) if p_info.get("market_beta_250") not in (None, "") else None
        
        last_price_dt = p_info.get("pre_event_last_price_date", "")
        if last_price_dt and last_price_dt <= cutoff and last_price_dt > max_dates_observed["beta_estimation"]:
            max_dates_observed["beta_estimation"] = last_price_dt

        # Control Row
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
            "shares_source_tag": shares_tag,
            "shares_source_accn": shares_accn,
            "pre_event_price_used": price_close,
            "pre_event_price_date": price_date,
            "market_cap": market_cap,
            "market_cap_method": market_cap_method,
            "market_cap_missing_reason": market_cap_missing,
            "has_market_cap": market_cap is not None,
            "net_income": ni_val,
            "net_income_tag": ni_tag,
            "total_assets": assets_val,
            "total_assets_tag": assets_tag,
            "profitability_roa": roa,
            "roa_missing_reason": roa_missing,
            "has_profitability": roa is not None,
            "short_term_debt": st_debt_val,
            "long_term_debt": lt_debt_val,
            "total_debt": total_debt,
            "total_liabilities": liab_val,
            "leverage_debt_assets": leverage_debt_assets,
            "liabilities_to_assets": liabilities_to_assets,
            "leverage_missing_reason": leverage_missing,
            "has_leverage": leverage_debt_assets is not None,
            "intangible_assets": intang_val,
            "intangible_assets_tag": intang_tag,
            "goodwill": gw_val,
            "goodwill_tag": gw_tag,
            "intangible_assets_to_assets": intangibles_to_assets,
            "goodwill_to_assets": goodwill_to_assets,
            "intangibles_plus_goodwill_to_assets": combined_intang_to_assets,
            "research_and_development_expense": rd_val,
            "rd_expense_tag": rd_tag,
            "revenue": rev_val,
            "revenue_tag": rev_tag,
            "rd_to_revenue": rd_to_rev,
            "has_intangible_assets": has_intangible,
            "has_rd_proxy": has_rd,
            "intangible_proxy_availability": proxy_avail,
            "intangible_primary_missing_reason": intang_missing,
            "accounting_source_accn": ni_accn or assets_accn or target_accn,
            "fiscal_period": "FY",
            "fiscal_year": ni_fy or assets_fy or "",
            "accounting_filing_date": ni_filed or assets_filed or "",
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

        # Provenance rows for every primary construct
        constructs = [
            ("MARKET_CAP", market_cap, "shares_outstanding * price_used", shares_tag, shares_accn, shares_as_of, market_cap_missing),
            ("PROFITABILITY_ROA", roa, "net_income / total_assets", f"{ni_tag} / {assets_tag}", ni_accn or assets_accn, ni_filed or assets_filed, roa_missing),
            ("LEVERAGE_DEBT_ASSETS", leverage_debt_assets, "total_debt / total_assets", f"debt / {assets_tag}", assets_accn, assets_filed, leverage_missing),
            ("INTANGIBLE_INTENSITY", combined_intang_to_assets, "(intangibles + goodwill) / total_assets", f"{intang_tag}+{gw_tag}", assets_accn, assets_filed, intang_missing),
            ("RD_INTENSITY", rd_to_rev, "rd_expense / revenue", f"{rd_tag} / {rev_tag}", assets_accn, assets_filed, "RD_OR_REV_NOT_REPORTED" if rd_to_rev is None else None),
            ("INDUSTRY_SIC", sic, "direct submission attribute", "sic", sub_json_name, f_date or cutoff, "SIC_NOT_REPORTED" if not sic else None)
        ]
        for c_name, val, formula, tag_used, accn_used, dt_used, miss_reason in constructs:
            provenance_rows.append({
                "ticker": tk,
                "cik": cik,
                "construct": c_name,
                "value": val,
                "available": "Y" if val is not None else "N",
                "transformation_formula": formula,
                "source_tag": tag_used or "",
                "source_accession": accn_used or "",
                "reporting_date": dt_used or "",
                "pre_event_cutoff": cutoff,
                "missing_reason_code": miss_reason or "NONE"
            })

    df_controls = pd.DataFrame(control_rows)
    df_prov = pd.DataFrame(provenance_rows)
    
    return df_controls, df_prov, max_dates_observed
