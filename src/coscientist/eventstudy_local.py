"""Resumable local executor for Event Study SEC extraction.

This is the heavy-compute fallback when GitHub-hosted Actions cannot allocate a
runner.  Google Drive Desktop supplies the raw corpus as an ordinary filesystem
path; Git remains code/provenance only.  The executor never opens outcome data.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import tempfile
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
from typing import Sequence

from .eventstudy_sec import (
    EXPECTED_CORPUS_COUNT,
    PARSER_VERSION,
    ExtractionError,
    process_raw,
    select_qa,
    select_smoke,
    validate_corpus_listing,
)


def _file_record(path: Path) -> dict:
    return {"name": path.name, "size": str(path.stat().st_size), "path": str(path)}


def inventory_local(raw_dir: str) -> tuple[list[dict], dict]:
    root = Path(raw_dir)
    if not root.is_dir():
        raise FileNotFoundError(f"raw directory not found: {root}")
    files = [_file_record(p) for p in root.iterdir() if p.is_file() and p.suffix.lower() == ".txt"]
    audit = validate_corpus_listing(files)
    audit.update({
        "raw_dir": str(root.resolve()),
        "total_bytes": sum(int(f["size"]) for f in files),
        "parser_version": PARSER_VERSION,
        "execution_plane": "LOCAL_ANTIGRAVITY",
        "outcome_inspected": "NO",
    })
    return files, audit


def _checkpoint_key(name: str) -> str:
    return hashlib.sha256(name.encode("utf-8")).hexdigest()[:24]


def _checkpoint_path(checkpoint_dir: Path, name: str) -> Path:
    return checkpoint_dir / f"{_checkpoint_key(name)}.json"


def _process_one(path_str: str) -> dict:
    path = Path(path_str)
    raw = path.read_bytes()
    summary, candidates = process_raw(raw, path.name)
    summary["local_size"] = path.stat().st_size
    return {"summary": summary, "candidates": candidates}


def _atomic_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=path.name + ".", suffix=".part", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, sort_keys=True)
        os.replace(tmp_name, path)
    except BaseException:
        try:
            os.remove(tmp_name)
        except OSError:
            pass
        raise


def _read_checkpoint(path: Path) -> dict | None:
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if payload.get("parser_version") != PARSER_VERSION:
        return None
    return payload


def _write_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fields: list[str] = []
    for row in rows:
        for key in row:
            if key not in fields:
                fields.append(key)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def _selected(files: list[dict], mode: str) -> list[dict]:
    if mode == "smoke":
        return select_smoke(files)
    if mode == "qa":
        return select_qa(files, 100)
    if mode == "full":
        return sorted(files, key=lambda f: f["name"])
    raise ValueError(mode)


def run_local(raw_dir: str, out_dir: str, mode: str = "full", workers: int = 4) -> dict:
    files, corpus_audit = inventory_local(raw_dir)
    if corpus_audit["status"] != "PASS":
        raise ExtractionError(f"local raw corpus identity failed: {json.dumps(corpus_audit, sort_keys=True)}")

    selected = _selected(files, mode)
    out = Path(out_dir)
    checkpoint_dir = out / "checkpoints"
    checkpoint_dir.mkdir(parents=True, exist_ok=True)
    workers = max(1, min(int(workers), 8))

    completed: dict[str, dict] = {}
    pending: list[dict] = []
    for f in selected:
        checkpoint = _read_checkpoint(_checkpoint_path(checkpoint_dir, f["name"]))
        if checkpoint and checkpoint.get("status") == "OK" and checkpoint.get("filename") == f["name"]:
            completed[f["name"]] = checkpoint
        else:
            pending.append(f)

    start_report = {
        **corpus_audit,
        "mode": mode,
        "selected": len(selected),
        "already_checkpointed": len(completed),
        "pending": len(pending),
        "workers": workers,
        "out_dir": str(out.resolve()),
        "outcome_inspected": "NO",
    }
    (out / "LOCAL_RUN_START.json").write_text(json.dumps(start_report, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(start_report, indent=2), flush=True)

    if pending:
        with ProcessPoolExecutor(max_workers=workers) as pool:
            future_map = {pool.submit(_process_one, f["path"]): f for f in pending}
            done_count = len(completed)
            for future in as_completed(future_map):
                f = future_map[future]
                name = f["name"]
                try:
                    result = future.result()
                    payload = {
                        "status": "OK",
                        "filename": name,
                        "parser_version": PARSER_VERSION,
                        "summary": result["summary"],
                        "candidates": result["candidates"],
                        "outcome_inspected": "NO",
                    }
                    _atomic_json(_checkpoint_path(checkpoint_dir, name), payload)
                    completed[name] = payload
                    done_count += 1
                    print(
                        f"[{done_count}/{len(selected)}] OK {name} "
                        f"sections={result['summary']['item1_status']}/"
                        f"{result['summary']['item1a_status']}/"
                        f"{result['summary']['item7_status']} "
                        f"ai={len(result['candidates'])}",
                        flush=True,
                    )
                except Exception as exc:
                    payload = {
                        "status": "FAIL",
                        "filename": name,
                        "parser_version": PARSER_VERSION,
                        "error_type": type(exc).__name__,
                        "error": str(exc),
                        "outcome_inspected": "NO",
                    }
                    _atomic_json(_checkpoint_path(checkpoint_dir, name), payload)
                    completed[name] = payload
                    done_count += 1
                    print(f"[{done_count}/{len(selected)}] FAIL {name}: {exc}", flush=True)

    summaries: list[dict] = []
    candidates: list[dict] = []
    failures: list[dict] = []
    for f in selected:
        payload = _read_checkpoint(_checkpoint_path(checkpoint_dir, f["name"]))
        if not payload:
            failures.append({"filename": f["name"], "error": "MISSING_CHECKPOINT"})
            continue
        if payload.get("status") == "OK":
            summaries.append(payload["summary"])
            candidates.extend(payload.get("candidates", []))
        else:
            failures.append({
                "filename": f["name"],
                "error_type": payload.get("error_type", ""),
                "error": payload.get("error", ""),
                "parser_version": PARSER_VERSION,
                "outcome_inspected": "NO",
            })

    _write_csv(out / "section_index.csv", summaries)
    _write_csv(out / "ai_sentence_candidates.csv", candidates)
    _write_csv(out / "extraction_failures.csv", failures)

    collisions = []
    for row in summaries:
        hashes = [row.get("item1_sha256", ""), row.get("item1a_sha256", ""), row.get("item7_sha256", "")]
        hashes = [h for h in hashes if h]
        if len(hashes) != len(set(hashes)):
            collisions.append(row.get("filename", ""))

    report = {
        **corpus_audit,
        "mode": mode,
        "selected": len(selected),
        "processed_ok": len(summaries),
        "failed": len(failures),
        "section_hash_collision_rows": len(collisions),
        "ai_candidate_rows": len(candidates),
        "complete_for_mode": len(summaries) == len(selected) and not failures and not collisions,
        "parser_version": PARSER_VERSION,
        "workers": workers,
        "outcome_inspected": "NO",
    }
    (out / "LOCAL_RUN_SUMMARY.json").write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(report, indent=2), flush=True)
    return report


def main(argv: Sequence[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Local resumable Event Study SEC extractor")
    p.add_argument("--raw-dir", required=True)
    p.add_argument("--out-dir", required=True)
    p.add_argument("--mode", choices=["smoke", "qa", "full"], default="full")
    p.add_argument("--workers", type=int, default=4)
    args = p.parse_args(argv)
    report = run_local(args.raw_dir, args.out_dir, args.mode, args.workers)
    return 0 if report.get("complete_for_mode") else 2


if __name__ == "__main__":
    raise SystemExit(main())
