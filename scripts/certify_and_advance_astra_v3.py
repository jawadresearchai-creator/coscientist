"""Certify immutable Astra V3 and advance the paper through the Director.

This script is outcome-blind. It reads only the immutable V3 pre-event snapshot,
re-checks deterministic completeness/integrity invariants, writes a separate
certification receipt in the paper root (never mutating V3), then uses the
Research Director's own REPAIR and DATA_FEASIBILITY actions to advance the paper
from DEVELOPING/REPAIR to DATA_FEASIBLE. It leaves DESIGN_CLOSURE pending.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from coscientist.director import ActionKind, DirectorState, apply_answer, ensure_action
from coscientist.drive import UPLOAD_API, DriveClient, DriveCredentials, FOLDER_MIME
from coscientist.single_paper import PaperStage, SinglePaperState

PAPER_ID = "MS-ASTRA-REVALUE-2026"
STUDY_FOLDER_ID = "1yx34Mj-k7SsGSuPli8UVuR_E639eeLcb"
SNAPSHOT = "analysis_ready_data_v3_20260907"
GENERATION_SHA = "971d24ac296b1a7cf2e794d678d7158c6a0b0c5b"
CUTOFF = "2026-08-06"
EXPECTED_CANDIDATES = 5547
EXPECTED_ELIGIBLE = 5234
EXPECTED_NO_FILING = 313
EXPECTED_BETAS = {120: 5278, 200: 5149, 250: 5062}
EXPECTED_STRICT_LEVERAGE = 665
EXPECTED_RD_EXPENSE = 2544
EXPECTED_RD_RATIO = 1815
EXPECTED_INTANGIBLES_FULL = 1829
EXPECTED_INTANGIBLES_PARTIAL = 1460
CERT_NAME = "v3_independent_certification_20260907.json"
WORK = Path(".astra_v3_certify")

AUDIT_FILES = [
    "acquisition_manifest.json",
    "v3_repair_audit.json",
    "price_metrics_v3_qa.json",
    "accounting_fact_selection_v3_qa.json",
    "pre_event_cutoff_audit.json",
    "drive_roundtrip_verification.json",
    "data_completion_summary.json",
    "firm_pre_event_price_metrics.csv",
    "firm_pre_event_controls_complete.csv",
    "sec_corpus_manifest_full.csv",
    "pre_event_filing_index.csv",
    "shares_cutoff_audit.csv",
    "firm_control_provenance.csv",
]


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        while chunk := fh.read(1024 * 1024):
            h.update(chunk)
    return h.hexdigest()


def _num(s: pd.Series) -> pd.Series:
    return pd.to_numeric(s.replace("", np.nan), errors="coerce")


def _bool(s: pd.Series) -> pd.Series:
    return s.astype(str).str.upper().isin(["TRUE", "Y", "1", "YES"])


def _one_named(client: DriveClient, folder_id: str, name: str) -> dict:
    hits = [f for f in client.list_folder(folder_id) if f.get("name") == name]
    if len(hits) != 1:
        raise RuntimeError(f"expected exactly one {name!r} in {folder_id}, found {len(hits)}")
    return hits[0]


def _patch_small_json(client: DriveClient, file_id: str, path: Path) -> None:
    payload = path.read_bytes()
    req = urllib.request.Request(
        f"{UPLOAD_API}/files/{file_id}?uploadType=media&fields=id",
        data=payload,
        method="PATCH",
        headers={
            "Authorization": f"Bearer {client.token()}",
            "Content-Type": "application/json",
            "Content-Length": str(len(payload)),
        },
    )
    with client.opener(req, timeout=120) as resp:
        resp.read()


def _upsert_json(client: DriveClient, folder_id: str, name: str, path: Path) -> str:
    hits = [f for f in client.list_folder(folder_id) if f.get("name") == name]
    if len(hits) > 1:
        raise RuntimeError(f"ambiguous certification receipt {name}: {len(hits)} files")
    if hits:
        _patch_small_json(client, hits[0]["id"], path)
        return hits[0]["id"]
    return client.upload(str(path), folder_id, name)


def certify(client: DriveClient) -> tuple[dict, dict[str, str]]:
    root = client.list_folder(STUDY_FOLDER_ID)
    finals = [f for f in root if f.get("name") == SNAPSHOT and f.get("mimeType") == FOLDER_MIME]
    building = [f for f in root if str(f.get("name", "")).startswith(SNAPSHOT + "__BUILDING_")]
    if len(finals) != 1:
        raise RuntimeError(f"expected one immutable V3 final folder, found {len(finals)}")
    if building:
        raise RuntimeError(f"stale V3 BUILDING folders remain: {[x.get('name') for x in building]}")
    folder_id = finals[0]["id"]
    remote = {f["name"]: f for f in client.list_folder(folder_id)}
    missing = [x for x in AUDIT_FILES if x not in remote]
    if missing:
        raise RuntimeError(f"V3 certification missing required payloads: {missing}")

    if WORK.exists():
        shutil.rmtree(WORK)
    WORK.mkdir(parents=True)
    for name in AUDIT_FILES:
        client.download(remote[name]["id"], str(WORK / name))

    manifest = json.loads((WORK / "acquisition_manifest.json").read_text(encoding="utf-8"))
    repair = json.loads((WORK / "v3_repair_audit.json").read_text(encoding="utf-8"))
    price_qa = json.loads((WORK / "price_metrics_v3_qa.json").read_text(encoding="utf-8"))
    acct_qa = json.loads((WORK / "accounting_fact_selection_v3_qa.json").read_text(encoding="utf-8"))
    cutoff_qa = json.loads((WORK / "pre_event_cutoff_audit.json").read_text(encoding="utf-8"))
    roundtrip = json.loads((WORK / "drive_roundtrip_verification.json").read_text(encoding="utf-8"))

    if manifest.get("paper_id") != PAPER_ID or manifest.get("snapshot_id") != SNAPSHOT:
        raise RuntimeError("V3 manifest identity mismatch")
    if manifest.get("git_commit_sha") != GENERATION_SHA:
        raise RuntimeError(f"generation SHA mismatch: {manifest.get('git_commit_sha')}")
    if manifest.get("outcome_inspected") != "NO":
        raise RuntimeError("V3 manifest is not outcome-blind")
    if cutoff_qa.get("audit_status") != "PASS" or cutoff_qa.get("pre_event_cutoff_date") != CUTOFF:
        raise RuntimeError(f"pre-event cutoff audit failed: {cutoff_qa}")
    if roundtrip.get("status") != "PASS" or any(x.get("status") != "PASS" for x in roundtrip.get("checks", [])):
        raise RuntimeError("Drive round-trip verification is not fully PASS")

    file_hashes = {x["name"]: x["sha256"] for x in manifest.get("files", [])}
    for name in AUDIT_FILES:
        if name == "acquisition_manifest.json":
            continue  # metadata file is intentionally outside its own payload manifest
        expected = file_hashes.get(name)
        if expected and sha256_file(WORK / name) != expected:
            raise RuntimeError(f"SHA-256 mismatch for downloaded V3 file {name}")

    metrics = pd.read_csv(WORK / "firm_pre_event_price_metrics.csv", dtype=str, keep_default_na=False)
    controls = pd.read_csv(WORK / "firm_pre_event_controls_complete.csv", dtype=str, keep_default_na=False)
    sec = pd.read_csv(WORK / "sec_corpus_manifest_full.csv", dtype=str, keep_default_na=False)
    filing = pd.read_csv(WORK / "pre_event_filing_index.csv", dtype=str, keep_default_na=False)
    shares = pd.read_csv(WORK / "shares_cutoff_audit.csv", dtype=str, keep_default_na=False)
    prov = pd.read_csv(WORK / "firm_control_provenance.csv", dtype=str, keep_default_na=False)

    dfs = {"metrics": metrics, "controls": controls, "sec": sec, "filing": filing, "shares": shares}
    base = set(controls["ticker"])
    for name, df in dfs.items():
        if len(df) != EXPECTED_CANDIDATES or df["ticker"].nunique() != EXPECTED_CANDIDATES:
            raise RuntimeError(f"{name}: expected {EXPECTED_CANDIDATES} unique firms, got {len(df)}/{df['ticker'].nunique()}")
        if set(df["ticker"]) != base:
            raise RuntimeError(f"{name}: ticker universe differs from controls")
        if int((df["ticker"] == "NA").sum()) != 1:
            raise RuntimeError(f"{name}: literal ticker NA was not preserved exactly once")

    acquired = sec["source_acquisition_status"].str.upper().eq("SUCCESS")
    full = sec["full_text_available"].str.upper().eq("Y")
    lengths = _num(sec["normalized_text_length"])
    if int(acquired.sum()) != EXPECTED_ELIGIBLE or int((~acquired).sum()) != EXPECTED_NO_FILING:
        raise RuntimeError("SEC eligible/no-filing counts are wrong")
    if int((acquired & full).sum()) != EXPECTED_ELIGIBLE:
        raise RuntimeError("not every eligible SEC filing is truthfully full-text")
    if int((acquired & full & (lengths <= 5000)).sum()) != 0:
        raise RuntimeError("preview-sized filing is still labelled full text")
    if int(lengths[acquired & full].min()) < 10022:
        raise RuntimeError("minimum V3 full-text length regressed")
    if sec.loc[acquired, "filing_date"].max() > CUTOFF:
        raise RuntimeError("post-cutoff SEC filing entered V3")

    for w, expected_count in EXPECTED_BETAS.items():
        beta = _num(metrics[f"market_beta_{w}"])
        n = _num(metrics[f"beta_{w}_n"])
        populated = beta.notna()
        if int(populated.sum()) != expected_count:
            raise RuntimeError(f"beta {w} count mismatch: {int(populated.sum())}")
        if int((populated & (n != w)).sum()) != 0:
            raise RuntimeError(f"beta {w} populated below/above exact-N threshold")
        if int((populated & (metrics[f"beta_{w}_last_return_date"] > CUTOFF)).sum()) != 0:
            raise RuntimeError(f"beta {w} contains post-cutoff return date")
    if price_qa.get("storage_level_cutoff_filter") is not True:
        raise RuntimeError("storage-level price cutoff filter not certified")

    has_lev = _bool(controls["has_leverage"])
    st = _num(controls["short_term_debt"])
    lt = _num(controls["long_term_debt"])
    assets = _num(controls["total_assets"])
    debt = _num(controls["total_debt"])
    leverage = _num(controls["leverage_debt_assets"])
    calc_debt = st + lt
    calc_lev = calc_debt / assets
    if int(has_lev.sum()) != EXPECTED_STRICT_LEVERAGE:
        raise RuntimeError(f"strict leverage count mismatch: {int(has_lev.sum())}")
    if int((has_lev & ~controls["total_debt_method"].eq("STRICT_SHORT_PLUS_LONG_TERM_DEBT")).sum()) != 0:
        raise RuntimeError("non-strict debt method used in available leverage")
    if int(_bool(controls["reported_total_debt_used_for_leverage"]).sum()) != 0:
        raise RuntimeError("reported debt-like fact was used for strict leverage")
    if int((has_lev & ((debt - calc_debt).abs() > 1e-9 * (1 + calc_debt.abs()))).sum()) != 0:
        raise RuntimeError("strict total-debt arithmetic failed")
    if int((has_lev & ((leverage - calc_lev).abs() > 1e-9 * (1 + calc_lev.abs()))).sum()) != 0:
        raise RuntimeError("strict leverage arithmetic failed")

    reported = prov[prov["construct"].eq("REPORTED_TOTAL_DEBT")]
    expected_desc = "Direct reported debt-like fact retained for audit only; not used in strict leverage"
    if int((reported["description"] != expected_desc).sum()) != 0:
        raise RuntimeError("reported total debt provenance is not audit-only")
    if prov["source_filing_date"].replace("", np.nan).dropna().max() > CUTOFF:
        raise RuntimeError("post-cutoff accounting source filing entered provenance")

    if int(shares["audit_status"].str.upper().eq("PASS").sum()) != EXPECTED_CANDIDATES:
        raise RuntimeError("shares cutoff audit is not all PASS")
    if shares["shares_source_filing_date"].max() > CUTOFF or shares["shares_source_period_end"].max() > CUTOFF:
        raise RuntimeError("post-cutoff shares fact entered V3")
    akts = shares.loc[shares["ticker"].eq("AKTS")]
    if len(akts) != 1 or akts.iloc[0]["shares_source_accession"] != "0001193125-26-216749":
        raise RuntimeError("AKTS shares correction regressed")

    has_mc = _bool(controls["has_market_cap"])
    mc = _num(controls["market_cap"])
    calc_mc = _num(controls["shares_outstanding"]) * _num(controls["pre_event_price_used"])
    if int((has_mc & ((mc - calc_mc).abs() > 1e-6 * (1 + calc_mc.abs()))).sum()) != 0:
        raise RuntimeError("market-cap arithmetic failed")

    rd = _num(controls["research_and_development_expense"])
    rd_ratio = _num(controls["rd_to_revenue"])
    if int(rd.notna().sum()) != EXPECTED_RD_EXPENSE or int(rd_ratio.notna().sum()) != EXPECTED_RD_RATIO:
        raise RuntimeError("R&D expense/ratio availability counts changed")

    full_int = controls["combined_intangibles_method"].eq("CONSTRUCTED_INTANGIBLES_EX_GOODWILL_PLUS_GOODWILL")
    partial_int = _bool(controls["has_intangible_assets"]) & ~full_int
    if int(full_int.sum()) != EXPECTED_INTANGIBLES_FULL or int(partial_int.sum()) != EXPECTED_INTANGIBLES_PARTIAL:
        raise RuntimeError("intangibles full/partial proxy counts changed")

    barrick = controls.loc[controls["ticker"].eq("B")]
    if len(barrick) != 1:
        raise RuntimeError("Barrick row missing or duplicated")
    b = barrick.iloc[0]
    if b["pre_event_form"] != "40-F" or b["pre_event_filing_date"] != "2026-02-27" or b["period_of_report"] != "2025-12-31":
        raise RuntimeError("Barrick canonical filing correction regressed")
    for c in ("net_income_filed", "total_assets_filed", "revenue_filed"):
        if b[c] != "2026-02-27":
            raise RuntimeError(f"Barrick stale accounting source returned: {c}={b[c]}")

    if repair["repairs"]["sec_truthfulness"]["truthful_full_text_filings"] != EXPECTED_ELIGIBLE:
        raise RuntimeError("repair audit SEC truthfulness count mismatch")
    if acct_qa.get("selector") != "CURRENT_PERIOD_GLOBAL_ACROSS_TAXONOMIES_V3":
        raise RuntimeError("accounting selector is not V3 current-period global selector")

    certification = {
        "status": "PASS",
        "paper_id": PAPER_ID,
        "snapshot": SNAPSHOT,
        "drive_folder_id": folder_id,
        "generation_git_sha": GENERATION_SHA,
        "certified_at_utc": datetime.now(timezone.utc).isoformat(),
        "outcome_inspected": "NO",
        "checks": {
            "candidate_firms": EXPECTED_CANDIDATES,
            "sec_truthful_full_text": EXPECTED_ELIGIBLE,
            "sec_no_eligible_filing": EXPECTED_NO_FILING,
            "beta_120_exact_n": EXPECTED_BETAS[120],
            "beta_200_exact_n": EXPECTED_BETAS[200],
            "beta_250_exact_n": EXPECTED_BETAS[250],
            "strict_leverage_available": EXPECTED_STRICT_LEVERAGE,
            "rd_expense_available": EXPECTED_RD_EXPENSE,
            "rd_ratio_available": EXPECTED_RD_RATIO,
            "intangibles_full_both_components": EXPECTED_INTANGIBLES_FULL,
            "intangibles_partial_proxy": EXPECTED_INTANGIBLES_PARTIAL,
            "ticker_NA_preserved": True,
            "Barrick_canonical_40F_corrected": True,
            "drive_roundtrip": "PASS",
            "pre_event_cutoff": CUTOFF,
        },
        "usage_guards": [
            "Treat 1,460 GOODWILL_ONLY or INTANGIBLES_EX_GOODWILL_ONLY observations as partial proxies; do not label all 3,289 as complete combined intangibles.",
            "Report R&D expense availability (2,544) separately from R&D/revenue availability (1,815).",
            "For 40-F corpus provenance, source_provenance=SEC_EDGAR_COMPLETE_SUBMISSION_40F plus CIK/accession identifies the fetched complete submission even when source_url retains the canonical primary-document URL.",
            "No event-window outcome, abnormal return, CAR, or winner/loser classification may be accessed until design freeze and AnalysisLock.",
        ],
    }
    return certification, file_hashes


def advance_director(client: DriveClient, certification: dict, file_hashes: dict[str, str]) -> dict:
    paper_meta = _one_named(client, STUDY_FOLDER_ID, "paper_state.json")
    director_meta = _one_named(client, STUDY_FOLDER_ID, "director.json")
    paper_path = WORK / "paper_state.json"
    director_path = WORK / "director.json"
    client.download(paper_meta["id"], str(paper_path))
    client.download(director_meta["id"], str(director_path))
    paper = SinglePaperState.load(str(paper_path))
    director = DirectorState.load(str(director_path))

    if not paper.active_paper or paper.active_paper.paper_id != PAPER_ID:
        raise RuntimeError("paper state is not bound to Astra")
    if paper.stage not in {PaperStage.DEVELOPING, PaperStage.DATA_FEASIBLE}:
        raise RuntimeError(f"unexpected Astra stage for V3 certification: {paper.stage}")

    if paper.stage is PaperStage.DEVELOPING:
        if paper.current_problem:
            if paper.current_problem.get("code") != "DATA_COMPLETENESS_V3_REPAIR":
                raise RuntimeError(f"unexpected current problem: {paper.current_problem}")
            repair_action = ensure_action(paper, director, None)
            if not repair_action or repair_action.kind is not ActionKind.REPAIR:
                raise RuntimeError("Director did not produce expected REPAIR action")
            result = apply_answer(
                paper,
                director,
                {
                    "action_id": repair_action.id,
                    "decision": "REPAIRED",
                    "repair_summary": (
                        f"Immutable {SNAPSHOT} independently certified PASS: 5,234 truthful SEC full texts; "
                        "exact 120/200/250 betas; strict component-defined leverage; Barrick and literal ticker NA corrected; "
                        "Drive round-trip PASS; outcome inspected NO."
                    ),
                },
                None,
            )
            if result != "REPAIRED":
                raise RuntimeError(f"repair Director result was {result}")

        feasibility_action = ensure_action(paper, director, None)
        if not feasibility_action or feasibility_action.kind is not ActionKind.DATA_FEASIBILITY:
            raise RuntimeError("Director did not produce expected DATA_FEASIBILITY action")

        requirements = [
            {"name": "pre-event equity prices", "concepts": ["equity prices", "pre-event returns"], "necessity": "ESSENTIAL"},
            {"name": "market beta", "concepts": ["market beta", "paired returns"], "necessity": "ESSENTIAL"},
            {"name": "firm size", "concepts": ["market capitalization", "shares outstanding"], "necessity": "ESSENTIAL"},
            {"name": "profitability", "concepts": ["net income", "total assets", "ROA"], "necessity": "ESSENTIAL"},
            {"name": "leverage", "concepts": ["short term debt", "long term debt", "total assets"], "necessity": "ESSENTIAL"},
            {"name": "intangibles/R&D", "concepts": ["intangibles", "goodwill", "research and development"], "necessity": "ESSENTIAL"},
            {"name": "industry", "concepts": ["SIC", "industry"], "necessity": "ESSENTIAL"},
            {"name": "pre-event SEC textual exposure", "concepts": ["SEC annual filing", "10-K", "20-F", "40-F", "AI exposure"], "necessity": "ESSENTIAL"},
        ]
        source_by_req = {
            "pre-event equity prices": "firm_pre_event_price_metrics.csv",
            "market beta": "firm_pre_event_price_metrics.csv",
            "firm size": "firm_pre_event_controls_complete.csv",
            "profitability": "firm_pre_event_controls_complete.csv",
            "leverage": "firm_pre_event_controls_complete.csv",
            "intangibles/R&D": "firm_pre_event_controls_complete.csv",
            "industry": "firm_pre_event_controls_complete.csv",
            "pre-event SEC textual exposure": "sec_pre_event_filings_full.zip",
        }
        external = []
        for req in requirements:
            name = req["name"]
            fname = source_by_req[name]
            external.append({
                "requirement": name,
                "source_id": f"ASTRA_V3:{fname}",
                "dataset_identity": f"{SNAPSHOT}/{fname}",
                "url": f"https://drive.google.com/drive/folders/{certification['drive_folder_id']}",
                "verified": True,
                "free": True,
                "official_or_authoritative": True,
                "access_class": "HELD",
                "licence": "Held immutable research artifact derived from free public SEC/market sources; source-specific public terms preserved.",
                "sha256": file_hashes[fname],
            })
        result = apply_answer(
            paper,
            director,
            {"action_id": feasibility_action.id, "requirements": requirements, "external_sources": external},
            None,
        )
        if result != "DATA_FEASIBLE" or paper.stage is not PaperStage.DATA_FEASIBLE:
            raise RuntimeError(f"Director failed to advance to DATA_FEASIBLE: {result}/{paper.stage}")

    # Ensure the next paper-local action is created, but do not answer it here.
    next_action = ensure_action(paper, director, None)
    if not next_action or next_action.kind is not ActionKind.DESIGN_CLOSURE:
        raise RuntimeError(f"expected DESIGN_CLOSURE after data feasibility, got {next_action}")

    paper.save()
    director.save()
    _patch_small_json(client, paper_meta["id"], paper_path)
    _patch_small_json(client, director_meta["id"], director_path)
    return {
        "stage": paper.stage.value,
        "current_problem": paper.current_problem,
        "director_bound_stage": director.bound_stage,
        "pending_action_id": next_action.id,
        "pending_action_kind": next_action.kind.value,
    }


def main() -> int:
    client = DriveClient(DriveCredentials.from_env())
    certification, file_hashes = certify(client)
    WORK.mkdir(parents=True, exist_ok=True)
    cert_path = WORK / CERT_NAME
    cert_path.write_text(json.dumps(certification, indent=2, sort_keys=True), encoding="utf-8")
    cert_id = _upsert_json(client, STUDY_FOLDER_ID, CERT_NAME, cert_path)
    state = advance_director(client, certification, file_hashes)
    print(json.dumps({"certification": certification, "certification_drive_file_id": cert_id, "state": state}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
