"""CLI for deterministic v4.6 integrity receipts."""
from __future__ import annotations

import argparse
import json

import pandas as pd

from .gms_lake import GMSCatalog
from .integrity import (
    ReceiptError,
    build_analysis_receipt,
    build_dataset_receipt,
    build_final_audit_evidence,
    build_power_receipt,
)
from .power import PowerError, power_preflight


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m coscientist.integrity_cli")
    sub = p.add_subparsers(dest="command", required=True)

    ds = sub.add_parser("dataset")
    ds.add_argument("--paper", required=True)
    ds.add_argument("--catalog", required=True)
    ds.add_argument("--remote-path", action="append", required=True)
    ds.add_argument("--out", required=True)

    pw = sub.add_parser("power")
    pw.add_argument("--paper", required=True)
    pw.add_argument("--input", required=True)
    pw.add_argument("--unit", required=True)
    pw.add_argument("--time", required=True)
    pw.add_argument("--outcome", required=True)
    pw.add_argument("--pre-period-end", required=True)
    pw.add_argument("--cluster")
    pw.add_argument("--treated-share", type=float, default=0.5)
    pw.add_argument("--plausible-effect", type=float, required=True)
    pw.add_argument("--alpha", type=float, default=0.05)
    pw.add_argument("--power", type=float, default=0.80)
    pw.add_argument("--out", required=True)

    ar = sub.add_parser("analysis")
    ar.add_argument("--paper", required=True)
    ar.add_argument("--freeze", required=True)
    ar.add_argument("--analysis-lock", required=True)
    ar.add_argument("--results", required=True)
    ar.add_argument("--git-sha", required=True)
    ar.add_argument("--workflow-run-id", required=True)
    ar.add_argument("--provenance-status", required=True)
    ar.add_argument("--publication-status", required=True)
    ar.add_argument("--out", required=True)

    fa = sub.add_parser("final-audit-evidence")
    fa.add_argument("--paper", required=True)
    fa.add_argument("--freeze", required=True)
    fa.add_argument("--analysis-receipt", required=True)
    fa.add_argument("--manuscript", required=True)
    fa.add_argument("--numeric-provenance-status", required=True)
    fa.add_argument("--reproducibility-status", required=True)
    fa.add_argument("--out", required=True)
    return p


def _coerce_boundary(value: str):
    # Preserve ISO dates as strings; use an integer when the entire column is
    # naturally integer-like and the caller supplies one.  Power preflight then
    # compares values in the same representation used by the input frame.
    try:
        return int(value)
    except ValueError:
        return value


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "dataset":
            receipt = build_dataset_receipt(
                GMSCatalog.load(args.catalog), paper_id=args.paper,
                remote_paths=args.remote_path,
            )
            print(receipt.save(args.out))
            return 0

        if args.command == "power":
            df = pd.read_csv(args.input)
            pre_end = _coerce_boundary(args.pre_period_end)
            result = power_preflight(
                df,
                unit=args.unit,
                time=args.time,
                outcome=args.outcome,
                pre_period_end=pre_end,
                cluster=args.cluster,
                treated_share=args.treated_share,
                plausible_effect=args.plausible_effect,
                alpha=args.alpha,
                power=args.power,
            )
            parameters = {
                "unit": args.unit,
                "time": args.time,
                "outcome": args.outcome,
                "cluster": args.cluster,
                "treated_share": args.treated_share,
                "plausible_effect": args.plausible_effect,
                "alpha": args.alpha,
                "power": args.power,
            }
            receipt = build_power_receipt(
                paper_id=args.paper,
                input_path=args.input,
                pre_period_end=pre_end,
                parameters=parameters,
                result=result.to_dict(),
            )
            print(receipt.save(args.out))
            return 0

        if args.command == "analysis":
            receipt = build_analysis_receipt(
                paper_id=args.paper,
                freeze_path=args.freeze,
                analysis_lock_path=args.analysis_lock,
                results_manifest_path=args.results,
                git_sha=args.git_sha,
                workflow_run_id=args.workflow_run_id,
                provenance_status=args.provenance_status,
                publication_status=args.publication_status,
            )
            print(receipt.save(args.out))
            return 0

        if args.command == "final-audit-evidence":
            receipt = build_final_audit_evidence(
                paper_id=args.paper,
                freeze_path=args.freeze,
                analysis_receipt_path=args.analysis_receipt,
                manuscript_path=args.manuscript,
                numeric_provenance_status=args.numeric_provenance_status,
                reproducibility_status=args.reproducibility_status,
            )
            print(receipt.save(args.out))
            return 0
    except (ReceiptError, PowerError, FileNotFoundError, ValueError, json.JSONDecodeError) as exc:
        print(f"INTEGRITY ERROR\n{exc}")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
