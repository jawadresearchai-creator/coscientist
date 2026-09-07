"""
CLI script to materialize pre-event controls, control provenance,
shares cutoff audit, cutoff audit JSON, and data availability matrix for MS-ASTRA-REVALUE-2026.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Ensure src is on path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from coscientist.eventstudy_controls import materialize_pre_event_controls, CUTOFF_DATE


def build_availability_matrix(df_controls: Any, staging_path: Path) -> None:
    matrix_rows = []
    for _, r in df_controls.iterrows():
        tk = r["ticker"]
        matrix_rows.append({
            "ticker": tk,
            "cik": r["cik"],
            "has_pre_event_filing": bool(r.get("pre_event_form")),
            "pre_event_form": r.get("pre_event_form", ""),
            "has_pre_event_price": bool(r.get("pre_event_price_used")),
            "has_120_pre_event_obs": bool(r.get("has_120_pre_event_obs")),
            "has_200_pre_event_obs": bool(r.get("has_200_pre_event_obs")),
            "has_250_pre_event_obs": bool(r.get("has_250_pre_event_obs")),
            "has_market_cap": bool(r.get("has_market_cap")),
            "has_profitability": bool(r.get("has_profitability")),
            "roa_period_consistent": bool(r.get("roa_period_consistent")),
            "has_leverage": bool(r.get("has_leverage")),
            "leverage_period_consistent": bool(r.get("leverage_period_consistent")),
            "has_intangible_assets": bool(r.get("has_intangible_assets")),
            "intangibles_period_consistent": bool(r.get("intangibles_period_consistent")),
            "has_rd_proxy": bool(r.get("has_rd_proxy")),
            "has_rd_to_revenue": bool(r.get("has_rd_to_revenue")),
            "rd_period_consistent": bool(r.get("rd_period_consistent")),
            "has_sic": bool(r.get("has_sic")),
            "has_market_beta_120": bool(r.get("has_market_beta_120")),
            "has_market_beta_200": bool(r.get("has_market_beta_200")),
            "has_market_beta_250": bool(r.get("has_market_beta_250")),
            "cross_accession_fallback": bool(r.get("cross_accession_fallback")),
            "analysis_ready_baseline": bool(
                r.get("has_pre_event_filing") and 
                r.get("has_120_pre_event_obs") and 
                r.get("has_market_cap") and 
                r.get("has_sic")
            )
        })
    import pandas as pd
    df_matrix = pd.DataFrame(matrix_rows)
    matrix_csv = staging_path / "analysis_data_availability_matrix.csv"
    df_matrix.to_csv(matrix_csv, index=False)
    print(f"Wrote analysis availability matrix ({len(df_matrix)} rows) to {matrix_csv}")


def main():
    parser = argparse.ArgumentParser(description="Materialize Pre-Event Numerical Controls")
    parser.add_argument("--staging-dir", type=str, required=True, help="Path to snapshot staging directory")
    parser.add_argument("--cache-dir", type=str, required=True, help="Path to SEC bulk facts cache directory")
    parser.add_argument("--cutoff", type=str, default=CUTOFF_DATE, help="Pre-event information cutoff (YYYY-MM-DD)")
    args = parser.parse_args()

    staging_path = Path(args.staging_dir)
    cache_path = Path(args.cache_dir)

    print(f"Materializing pre-event controls with cutoff <= {args.cutoff}...")
    df_controls, df_prov, df_shares_audit, cutoff_summary = materialize_pre_event_controls(
        staging_path, cache_path, cutoff=args.cutoff
    )

    controls_csv = staging_path / "firm_pre_event_controls_complete.csv"
    controls_pq = staging_path / "firm_pre_event_controls_complete.parquet"
    df_controls.to_csv(controls_csv, index=False)
    df_controls.to_parquet(controls_pq, index=False)

    prov_csv = staging_path / "firm_control_provenance.csv"
    df_prov.to_csv(prov_csv, index=False)

    audit_csv = staging_path / "shares_cutoff_audit.csv"
    df_shares_audit.to_csv(audit_csv, index=False)

    audit_json = staging_path / "pre_event_cutoff_audit.json"
    with open(audit_json, "w", encoding="utf-8") as fh:
        json.dump(cutoff_summary, fh, indent=2)

    build_availability_matrix(df_controls, staging_path)

    print(f"Successfully materialized {len(df_controls)} control rows, {len(df_prov)} provenance records, and {len(df_shares_audit)} shares audit records.")
    print(f"Cutoff audit status: {cutoff_summary['audit_status']}")


if __name__ == "__main__":
    main()
