"""Drive-streamed SEC 10-K section extraction for the AI-washing event study.

Scientific boundary:
- frozen corpus identity = exactly 7,361 raw filenames from the pre-event manifest
- extract Item 1, Item 1A, Item 7 only
- frozen AI dictionary preserved from the original corpus builder
- Item 1 + Item 7 are candidate sources for Primary Talk; Item 1A is risk-only falsification
- no outcome/return access anywhere in this module
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import tempfile
from dataclasses import asdict, dataclass
from html.parser import HTMLParser
from pathlib import Path
from typing import Iterable, Sequence

from .drive import DriveClient, DriveCredentials, DriveError

EXPECTED_CORPUS_COUNT = 7361
EXPECTED_MANIFEST_SHA256 = "cc2067a4689536f47afdf6fafc464e87385a658e6010cca41880715fcd6d2d8f"
EXPECTED_FILENAME_SET_SHA256 = "dfff64049e9d5758c0f2a854bfe6f9ede7b96853c5da80cf136d4b3c9ac8614d"
PARSER_VERSION = "eventstudy-sec-v2.0.0"
QA_SEED = "EVENTSTUDY-SEC-QA-20260905-v1"

# Frozen verbatim from sec_edgar_corpus_builder.py used for the original package.
AI_PATTERNS = [
    r"\bartificial intelligence\b",
    r"\bgenerative ai\b",
    r"\bgen ai\b",
    r"\bmachine learning\b",
    r"\bdeep learning\b",
    r"\bneural networks?\b",
    r"\blarge language models?\b",
    r"\bllms?\b",
    r"\bnatural language processing\b",
    r"\bcomputer vision\b",
    r"\breinforcement learning\b",
    r"\bai-enabled\b",
    r"\bai-powered\b",
    r"\bai based\b",
    r"\bai-based\b",
    r"\bai systems?\b",
    r"\bai models?\b",
    r"\bai tools?\b",
]
AI_RE = re.compile("|".join(AI_PATTERNS), re.I)

RAW_NAME_RE = re.compile(r"^edgar_data_(\d+)_([0-9]{10}-[0-9]{2}-[0-9]{6})\.txt$")
BLOCK_TAGS = {
    "address", "article", "aside", "blockquote", "br", "caption", "dd", "div", "dl", "dt",
    "figcaption", "footer", "h1", "h2", "h3", "h4", "h5", "h6", "header", "hr", "li",
    "main", "nav", "ol", "p", "pre", "section", "table", "tbody", "td", "tfoot", "th",
    "thead", "tr", "ul",
}
SKIP_TAGS = {"script", "style", "noscript", "head", "ix:hidden"}

# Exact item-token separation is the central v2 repair: Item 1 MUST NOT match Item 1A.
ITEM_LINE_RE = re.compile(
    r"^\s*item\s+(1a|1b|1c|1|2|7a|7|8)(?![a-z0-9])"
    r"(?:\s*[\.\:\-\u2013\u2014])?(?:\s+.*)?\s*$",
    re.I,
)
TARGET_ENDS = {
    "Item1": {"Item1A", "Item1B", "Item1C", "Item2"},
    "Item1A": {"Item1B", "Item1C", "Item2"},
    "Item7": {"Item7A", "Item8"},
}
TOKEN_CANON = {
    "1": "Item1", "1a": "Item1A", "1b": "Item1B", "1c": "Item1C",
    "2": "Item2", "7": "Item7", "7a": "Item7A", "8": "Item8",
}


class ExtractionError(RuntimeError):
    pass


class _VisibleTextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.skip_depth = 0

    def handle_starttag(self, tag: str, attrs) -> None:
        tag = tag.lower()
        if self.skip_depth:
            self.skip_depth += 1
            return
        if tag in SKIP_TAGS:
            self.skip_depth = 1
            return
        if tag in BLOCK_TAGS:
            self.parts.append("\n")

    def handle_startendtag(self, tag: str, attrs) -> None:
        if not self.skip_depth and tag.lower() in BLOCK_TAGS:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if self.skip_depth:
            self.skip_depth -= 1
            return
        if tag in BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self.skip_depth:
            self.parts.append(data)


@dataclass(frozen=True)
class Heading:
    key: str
    start: int
    end: int
    text: str


@dataclass
class SectionResult:
    section: str
    status: str
    text: str = ""
    start: int | None = None
    end: int | None = None
    start_heading: str = ""
    end_heading: str = ""
    chars: int = 0
    word_count: int = 0
    sha256: str = ""
    candidate_count: int = 0


@dataclass
class FilingMeta:
    filename: str
    cik: str
    accession: str
    company: str
    filing_date: str


def filename_set_sha256(names: Iterable[str]) -> str:
    payload = "\n".join(sorted(names)) + "\n"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def validate_corpus_listing(files: Sequence[dict]) -> dict:
    txt = [f for f in files if str(f.get("name", "")).endswith(".txt")]
    names = [str(f["name"]) for f in txt]
    bad = sorted(n for n in names if not RAW_NAME_RE.match(n))
    counts: dict[str, int] = {}
    for name in names:
        counts[name] = counts.get(name, 0) + 1
    duplicates = sorted(n for n, count in counts.items() if count > 1)
    digest = filename_set_sha256(names)
    ok = (
        len(names) == EXPECTED_CORPUS_COUNT
        and not bad
        and not duplicates
        and digest == EXPECTED_FILENAME_SET_SHA256
    )
    return {
        "status": "PASS" if ok else "FAIL",
        "expected_count": EXPECTED_CORPUS_COUNT,
        "observed_count": len(names),
        "expected_filename_set_sha256": EXPECTED_FILENAME_SET_SHA256,
        "observed_filename_set_sha256": digest,
        "invalid_names": bad,
        "duplicate_names": duplicates,
        "outcome_inspected": "NO",
    }


def extract_primary_10k(raw: bytes) -> str:
    text = raw.decode("utf-8", errors="replace")
    blocks = re.findall(r"(?is)<DOCUMENT>(.*?)</DOCUMENT>", text)
    if not blocks:
        raise ExtractionError("SEC submission contains no <DOCUMENT> blocks")
    for block in blocks:
        m = re.search(r"(?im)^\s*<TYPE>\s*([^\r\n<]+)", block)
        if not m or m.group(1).strip().upper() != "10-K":
            continue
        tm = re.search(r"(?is)<TEXT>(.*?)</TEXT>", block)
        return tm.group(1) if tm else block
    raise ExtractionError("SEC submission contains no primary TYPE 10-K document")


def html_to_visible_text(source: str) -> str:
    parser = _VisibleTextParser()
    parser.feed(source)
    parser.close()
    text = "".join(parser.parts).replace("\xa0", " ")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    lines: list[str] = []
    for line in text.split("\n"):
        line = re.sub(r"[ \t\f\v]+", " ", line).strip()
        if line:
            lines.append(line)
        elif lines and lines[-1] != "":
            lines.append("")
    return "\n".join(lines).strip()


def find_headings(text: str) -> list[Heading]:
    out: list[Heading] = []
    pos = 0
    for line in text.splitlines(keepends=True):
        clean = line.strip()
        if clean and len(clean) <= 260:
            m = ITEM_LINE_RE.match(clean)
            if m:
                out.append(Heading(TOKEN_CANON[m.group(1).lower()], pos, pos + len(line), clean))
        pos += len(line)
    return out


def _word_count(text: str) -> int:
    return len(re.findall(r"\b[\w'-]+\b", text))


def _section_candidates(text: str, headings: Sequence[Heading], target: str) -> list[tuple[int, Heading, Heading, str]]:
    starts = [h for h in headings if h.key == target]
    end_keys = TARGET_ENDS[target]
    ends = [h for h in headings if h.key in end_keys]
    candidates: list[tuple[int, Heading, Heading, str]] = []
    for start in starts:
        later = [end for end in ends if end.start > start.start]
        if not later:
            continue
        end = min(later, key=lambda h: h.start)
        segment = text[start.start:end.start].strip()
        if len(segment) >= 200:
            candidates.append((len(segment), start, end, segment))
    return candidates


def extract_section(text: str, headings: Sequence[Heading], target: str) -> SectionResult:
    candidates = _section_candidates(text, headings, target)
    if not candidates:
        return SectionResult(section=target, status="NOT_FOUND")
    # Preserve the original body-vs-TOC rule: the body occurrence is normally the longest.
    candidates.sort(key=lambda x: (x[0], x[1].start), reverse=True)
    length, start, end, segment = candidates[0]
    return SectionResult(
        section=target,
        status="OK",
        text=segment,
        start=start.start,
        end=end.start,
        start_heading=start.text,
        end_heading=end.text,
        chars=length,
        word_count=_word_count(segment),
        sha256=hashlib.sha256(segment.encode("utf-8")).hexdigest(),
        candidate_count=len(candidates),
    )


def extract_sections(text: str) -> dict[str, SectionResult]:
    headings = find_headings(text)
    results = {key: extract_section(text, headings, key) for key in ("Item1", "Item1A", "Item7")}
    seen: dict[str, str] = {}
    for key, result in results.items():
        if result.status == "OK":
            if result.sha256 in seen:
                raise ExtractionError(f"section collision: {key} has identical text hash to {seen[result.sha256]}")
            seen[result.sha256] = key
    return results


def parse_meta(raw: bytes, filename: str) -> FilingMeta:
    rm = RAW_NAME_RE.match(filename)
    if not rm:
        raise ExtractionError(f"unexpected raw filename: {filename}")
    expected_cik = str(int(rm.group(1)))
    expected_accession = rm.group(2)
    header = raw[:1_500_000].decode("utf-8", errors="replace")

    def one(pattern: str, label: str, default: str | None = None) -> str:
        m = re.search(pattern, header, re.I | re.M)
        if not m:
            if default is not None:
                return default
            raise ExtractionError(f"SEC header missing {label}")
        return m.group(1).strip()

    accession = one(r"^ACCESSION NUMBER:\s*([^\r\n]+)", "accession")
    cik = one(r"^\s*CENTRAL INDEX KEY:\s*([^\r\n]+)", "CIK")
    company = one(r"^\s*COMPANY CONFORMED NAME:\s*([^\r\n]+)", "company", "")
    filed = one(r"^FILED AS OF DATE:\s*(\d{8})", "filed date")
    form = one(r"^CONFORMED SUBMISSION TYPE:\s*([^\r\n]+)", "form")
    if form.upper() != "10-K":
        raise ExtractionError(f"header form is {form!r}, expected 10-K")
    if str(int(cik)) != expected_cik or accession != expected_accession:
        raise ExtractionError(
            f"filename/header identity mismatch: filename CIK/accession={expected_cik}/{expected_accession}, "
            f"header={str(int(cik))}/{accession}"
        )
    return FilingMeta(filename, expected_cik, accession, company, f"{filed[:4]}-{filed[4:6]}-{filed[6:8]}")


def split_sentences(text: str) -> list[str]:
    # Frozen deterministic splitter from the original builder.
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return []
    sentences = re.split(r"(?<=[.!?])\s+(?=[A-Z0-9(])", text)
    return [s.strip() for s in sentences if len(s.strip()) >= 20]


def ai_candidates(meta: FilingMeta, section: str, text: str) -> list[dict]:
    sentences = split_sentences(text)
    rows: list[dict] = []
    for index, sentence in enumerate(sentences):
        if not AI_RE.search(sentence):
            continue
        sid_raw = f"{meta.cik}|{meta.accession}|{section}|{index}|{sentence}"
        rows.append({
            "sentence_id": hashlib.sha256(sid_raw.encode("utf-8")).hexdigest()[:20],
            "cik": meta.cik,
            "company": meta.company,
            "filing_date": meta.filing_date,
            "filing_accession_path": meta.accession,
            "section": section,
            "sentence_index": index,
            "prev_sentence": sentences[index - 1] if index else "",
            "focal_sentence": sentence,
            "next_sentence": sentences[index + 1] if index + 1 < len(sentences) else "",
            "dictionary_hit": ";".join(sorted({m.group(0).lower() for m in AI_RE.finditer(sentence)})),
            "parser_version": PARSER_VERSION,
        })
    return rows


def process_raw(raw: bytes, filename: str) -> tuple[dict, list[dict]]:
    meta = parse_meta(raw, filename)
    primary = extract_primary_10k(raw)
    visible = html_to_visible_text(primary)
    sections = extract_sections(visible)
    summary = {**asdict(meta), "parser_version": PARSER_VERSION, "outcome_inspected": "NO"}
    candidates: list[dict] = []
    for key, result in sections.items():
        prefix = key.lower()
        summary.update({
            f"{prefix}_status": result.status,
            f"{prefix}_start": result.start,
            f"{prefix}_end": result.end,
            f"{prefix}_start_heading": result.start_heading,
            f"{prefix}_end_heading": result.end_heading,
            f"{prefix}_chars": result.chars,
            f"{prefix}_word_count": result.word_count,
            f"{prefix}_sha256": result.sha256,
            f"{prefix}_candidate_count": result.candidate_count,
        })
        if result.status == "OK":
            candidates.extend(ai_candidates(meta, key, result.text))
    summary["item1_item7_word_count"] = sections["Item1"].word_count + sections["Item7"].word_count
    summary["ai_candidate_count"] = len(candidates)
    return summary, candidates


def deterministic_rank(name: str, seed: str = QA_SEED) -> str:
    return hashlib.sha256(f"{seed}|{name}".encode()).hexdigest()


def select_smoke(files: Sequence[dict]) -> list[dict]:
    ordered = sorted(files, key=lambda f: int(f.get("size") or 0))
    if len(ordered) < 3:
        return ordered
    return [ordered[0], ordered[len(ordered) // 2], ordered[-1]]


def select_qa(files: Sequence[dict], n: int = 100) -> list[dict]:
    """Deterministic size x accession-year stratified QA sample."""
    rows = sorted(files, key=lambda f: int(f.get("size") or 0))
    if len(rows) <= n:
        return rows
    strata: dict[tuple[int, str], list[dict]] = {}
    for index, file in enumerate(rows):
        quintile = min(4, (index * 5) // len(rows))
        match = RAW_NAME_RE.match(str(file["name"]))
        accession_year = match.group(2)[11:13] if match else "xx"
        strata.setdefault((quintile, accession_year), []).append(file)
    selected: list[dict] = []
    keys = sorted(strata)
    base = n // len(keys)
    for key in keys:
        ranked = sorted(strata[key], key=lambda f: deterministic_rank(str(f["name"])))
        selected.extend(ranked[:base])
    remaining = n - len(selected)
    if remaining:
        used = {f["name"] for f in selected}
        pool = sorted(
            (f for f in rows if f["name"] not in used),
            key=lambda f: deterministic_rank(str(f["name"]), QA_SEED + "|remainder"),
        )
        selected.extend(pool[:remaining])
    return sorted(selected, key=lambda f: str(f["name"]))


def select_shard(files: Sequence[dict], shard_index: int, shard_count: int) -> list[dict]:
    if shard_count < 1 or not (0 <= shard_index < shard_count):
        raise ValueError("invalid shard index/count")
    return [
        f for f in sorted(files, key=lambda x: str(x["name"]))
        if int(hashlib.sha256(str(f["name"]).encode()).hexdigest(), 16) % shard_count == shard_index
    ]


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


def run_drive_batch(raw_folder_id: str, out_dir: str, mode: str,
                    shard_index: int = 0, shard_count: int = 1) -> dict:
    client = DriveClient(DriveCredentials.from_env())
    files = [f for f in client.list_folder(raw_folder_id) if str(f.get("name", "")).endswith(".txt")]
    audit = validate_corpus_listing(files)
    if audit["status"] != "PASS":
        raise DriveError(f"raw corpus identity failed: {json.dumps(audit, sort_keys=True)}")
    if mode == "smoke":
        selected = select_smoke(files)
    elif mode == "qa":
        selected = select_qa(files, 100)
    elif mode == "full":
        selected = select_shard(files, shard_index, shard_count)
    else:
        raise ValueError(f"unknown mode: {mode}")

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    summaries: list[dict] = []
    candidates: list[dict] = []
    failures: list[dict] = []
    with tempfile.TemporaryDirectory(prefix="eventstudy-sec-") as tmpdir:
        for index, file in enumerate(selected, 1):
            name = str(file["name"])
            local = os.path.join(tmpdir, name)
            try:
                client.download(str(file["id"]), local)
                raw = Path(local).read_bytes()
                summary, rows = process_raw(raw, name)
                summary["drive_file_id"] = file["id"]
                summary["drive_size"] = file.get("size", "")
                summaries.append(summary)
                candidates.extend(rows)
                print(
                    f"[{index}/{len(selected)}] OK {name} sections={summary['item1_status']}/"
                    f"{summary['item1a_status']}/{summary['item7_status']} ai={len(rows)}",
                    flush=True,
                )
            except Exception as exc:
                failures.append({
                    "filename": name,
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                    "parser_version": PARSER_VERSION,
                    "outcome_inspected": "NO",
                })
                print(f"[{index}/{len(selected)}] FAIL {name}: {exc}", flush=True)
            finally:
                if os.path.exists(local):
                    os.remove(local)

    _write_csv(out / "section_index.csv", summaries)
    _write_csv(out / "ai_sentence_candidates.csv", candidates)
    _write_csv(out / "extraction_failures.csv", failures)
    report = {
        **audit,
        "mode": mode,
        "parser_version": PARSER_VERSION,
        "selected": len(selected),
        "processed_ok": len(summaries),
        "failed": len(failures),
        "ai_candidate_rows": len(candidates),
        "section_collision_failures": sum("collision" in f["error"].lower() for f in failures),
        "outcome_inspected": "NO",
    }
    (out / "run_summary.json").write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    return report


def merge_outputs(input_root: str, out_dir: str, expected_rows: int = EXPECTED_CORPUS_COUNT) -> dict:
    root = Path(input_root)
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    summaries: list[dict] = []
    candidates: list[dict] = []
    failures: list[dict] = []
    for path in root.rglob("section_index.csv"):
        if path.stat().st_size:
            with path.open(encoding="utf-8", newline="") as handle:
                summaries.extend(csv.DictReader(handle))
    for path in root.rglob("ai_sentence_candidates.csv"):
        if path.stat().st_size:
            with path.open(encoding="utf-8", newline="") as handle:
                candidates.extend(csv.DictReader(handle))
    for path in root.rglob("extraction_failures.csv"):
        if path.stat().st_size:
            with path.open(encoding="utf-8", newline="") as handle:
                failures.extend(csv.DictReader(handle))

    name_counts: dict[str, int] = {}
    for row in summaries:
        name_counts[row.get("filename", "")] = name_counts.get(row.get("filename", ""), 0) + 1
    duplicates = sorted(name for name, count in name_counts.items() if count > 1)
    id_counts: dict[str, int] = {}
    for row in candidates:
        sid = row.get("sentence_id", "")
        if sid:
            id_counts[sid] = id_counts.get(sid, 0) + 1
    duplicate_candidate_ids = sorted(sid for sid, count in id_counts.items() if count > 1)
    collision_rows = [
        row for row in summaries
        if row.get("item1_sha256") and (
            row.get("item1_sha256") == row.get("item1a_sha256")
            or row.get("item1_sha256") == row.get("item7_sha256")
            or (row.get("item1a_sha256") and row.get("item1a_sha256") == row.get("item7_sha256"))
        )
    ]
    ok = (
        len(summaries) == expected_rows
        and not failures
        and not duplicates
        and not duplicate_candidate_ids
        and not collision_rows
    )
    _write_csv(out / "section_index_v2.csv", summaries)
    _write_csv(out / "ai_sentence_candidates_v2.csv", candidates)
    _write_csv(out / "extraction_failures_v2.csv", failures)
    report = {
        "status": "PASS" if ok else "FAIL",
        "expected_filing_rows": expected_rows,
        "observed_filing_rows": len(summaries),
        "failure_rows": len(failures),
        "duplicate_filenames": duplicates,
        "duplicate_sentence_ids": duplicate_candidate_ids,
        "section_hash_collision_rows": len(collision_rows),
        "ai_candidate_rows": len(candidates),
        "parser_version": PARSER_VERSION,
        "outcome_inspected": "NO",
    }
    (out / "PHASE2_EXTRACTION_AUDIT_v2.json").write_text(
        json.dumps(report, indent=2, sort_keys=True), encoding="utf-8"
    )
    if not ok:
        raise ExtractionError(f"merged extraction audit failed: {json.dumps(report, sort_keys=True)}")
    return report


def publish_outputs(eventstudy_root_id: str, input_dir: str, run_id: str) -> list[dict]:
    client = DriveClient(DriveCredentials.from_env())
    targets = {
        "section_index_v2.csv": "03_Extracted_Sections",
        "ai_sentence_candidates_v2.csv": "04_AI_Sentence_Corpus",
        "extraction_failures_v2.csv": "03_Extracted_Sections",
        "PHASE2_EXTRACTION_AUDIT_v2.json": "00_Protocol",
    }
    uploaded: list[dict] = []
    for basename, child in targets.items():
        path = Path(input_dir) / basename
        if not path.exists():
            continue
        folder = client.find_child(eventstudy_root_id, child, folder=True)
        remote_name = f"{Path(basename).stem}__run-{run_id}{Path(basename).suffix}"
        file_id = client.upload(str(path), folder["id"], remote_name)
        uploaded.append({
            "local": str(path),
            "drive_folder": child,
            "remote_name": remote_name,
            "file_id": file_id,
        })
    return uploaded


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd", required=True)
    run = sub.add_parser("run")
    run.add_argument("--raw-folder-id", required=True)
    run.add_argument("--out-dir", required=True)
    run.add_argument("--mode", choices=["smoke", "qa", "full"], required=True)
    run.add_argument("--shard-index", type=int, default=0)
    run.add_argument("--shard-count", type=int, default=1)
    merge = sub.add_parser("merge")
    merge.add_argument("--input-root", required=True)
    merge.add_argument("--out-dir", required=True)
    publish = sub.add_parser("publish")
    publish.add_argument("--eventstudy-root-id", required=True)
    publish.add_argument("--input-dir", required=True)
    publish.add_argument("--run-id", required=True)
    args = parser.parse_args(argv)
    if args.cmd == "run":
        result = run_drive_batch(args.raw_folder_id, args.out_dir, args.mode, args.shard_index, args.shard_count)
    elif args.cmd == "merge":
        result = merge_outputs(args.input_root, args.out_dir)
    else:
        result = publish_outputs(args.eventstudy_root_id, args.input_dir, args.run_id)
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
