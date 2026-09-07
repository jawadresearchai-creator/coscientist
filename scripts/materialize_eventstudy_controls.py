"""
CLI script to materialize pre-event controls, control provenance,
cutoff audit, and data availability matrix for MS-ASTRA-REVALUE-2026.
"""
import argparse
import sys
from pathlib import Path

# Ensure src is on path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from coscientist.eventstudy_controls import materialize_pre_event_controls, CUTOFF_DATE


def main():
    parser = argparse.ArgumentParser(description="Materialize Pre-Event Numerical Controls")
    parser.add_argument("--staging-dir", type=str, required=True, help="Path to snapshot staging directory")
    parser.add_argument("--cache-dir", type=str, required=True, help="Path to SEC bulk facts cache directory")
    parser.add_argument("--cutoff", type=str, default=CUTOFF_DATE, help="Pre-event information cutoff (YYYY-MM-DD)")
    args = parser.parse_args()

    staging_path = Path(args.staging_dir)
    cache_path = Path(args.cache_dir)

    print(f"Materializing pre-event controls with cutoff <= {args.cutoff}...")
    df_controls, df_prov, max_dates = materialize_pre_event_controls(staging_path, cache_path, cutoff=args.cutoff)

    controls_csv = staging_path / "firm_pre_event_controls_complete.csv"
    controls_pq = staging_path / "firm_pre_event_controls_complete.parquet"
    df_controls.to_csv(controls_csv, index=False)
    df_controls.to_parquet(controls_pq, index=False)

    prov_csv = staging_path / "firm_control_provenance.csv"
    df_prov.to_csv(prov_csv, index=False)

    print(f"Successfully materialized {len(df_controls)} control rows and {len(df_prov)} provenance records.")


if __name__ == "__main__":
    main()
