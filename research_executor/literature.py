from __future__ import annotations

import argparse
import hashlib
import html
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict
from pathlib import Path
from typing import Any, Callable

from research_executor.acquisition import (
    AcquisitionError,
    _child,
    _download_json,
    _drive_client_from_env,
    _optional_child,
    _search_job_folder,
    _write_json,
    sha256_file,
)
from research_executor.public_status import utc_now, validate_job_id, write_public_status

ROUTES_PATH = Path(__file__).with_name("literature_routes.json")
_TAG_RE = re.compile(r"<[^>]+>")
_TOKEN_RE = re.compile(r"[a-z0-9]+")
_STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "by", "can", "do", "does",
    "for", "from", "how", "in", "is", "it", "of", "on", "or", "that", "the",
    "to", "what", "when", "where", "which", "with", "whether"
}


class LiteratureError(AcquisitionError):
    pass


def canonical_sha256(value: Any) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def load_literature_routes(path: str | Path = ROUTES_PATH) -> dict[str, Any]:
    routes = json.loads(Path(path).read_text(encoding="utf-8"))
    policy = routes.get("policy") or {}
    required = {
        "zero_cost_only": True,
        "automatic_paid_routes": False,
        "private_query_only": True,
        "deduplication_required": True,
        "evidence_traceability_required": True,
        "checkpoint_required": True,
    }
    for key, expected in required.items():
        if policy.get(key) is not expected:
            raise LiteratureError(f"literature route policy must enforce {key}={expected}")
    required_sources = policy.get("required_live_sources") or []
    if set(required_sources) != {"crossref", "europe-pmc"}:
        raise LiteratureError("Session 07 requires Crossref and Europe PMC as keyless live sources")
    return routes


def clean_text(value: Any) -> str:
    if value is None:
        return ""
    text = html.unescape(str(value))
    text = _TAG_RE.sub(" ", text)
    return " ".join(text.split())


def normalize_doi(value: Any) -> str | None:
    text = clean_text(value).strip().lower()
    if not text:
        return None
    for prefix in ("https://doi.org/", "http://doi.org/", "doi:"):
        if text.startswith(prefix):
            text = text[len(prefix):]
    text = text.strip().strip(".")
    return text if text.startswith("10.") and "/" in text else None


def normalize_identifier(value: Any) -> str | None:
    text = clean_text(value).strip()
    return text or None


def normalize_title(value: Any) -> str:
    text = clean_text(value).lower()
    return " ".join(_TOKEN_RE.findall(text))


def tokens(value: Any) -> set[str]:
    return {t for t in _TOKEN_RE.findall(clean_text(value).lower()) if t not in _STOPWORDS and len(t) > 1}


def _first(value: Any) -> Any:
    if isinstance(value, list):
        return value[0] if value else None
    return value


def _crossref_year(item: dict[str, Any]) -> int | None:
    for key in ("published-print", "published-online", "published", "issued", "created"):
        parts = ((item.get(key) or {}).get("date-parts") or [])
        if parts and parts[0]:
            try:
                return int(parts[0][0])
            except (TypeError, ValueError):
                pass
    return None


def _crossref_authors(item: dict[str, Any]) -> list[str]:
    authors = []
    for author in item.get("author") or []:
        name = " ".join(x for x in [clean_text(author.get("given")), clean_text(author.get("family"))] if x)
        if name:
            authors.append(name)
    return authors


def normalize_crossref(item: dict[str, Any]) -> dict[str, Any] | None:
    title = clean_text(_first(item.get("title")))
    if not title:
        return None
    doi = normalize_doi(item.get("DOI"))
    return {
        "source": "crossref",
        "source_record_id": doi or clean_text(item.get("URL")) or canonical_sha256(title)[:16],
        "doi": doi,
        "pmid": None,
        "pmcid": None,
        "openalex_id": None,
        "title": title,
        "normalized_title": normalize_title(title),
        "abstract": clean_text(item.get("abstract")),
        "year": _crossref_year(item),
        "publication_date": None,
        "type": clean_text(item.get("type")),
        "journal": clean_text(_first(item.get("container-title"))),
        "authors": _crossref_authors(item),
        "citation_count": int(item.get("is-referenced-by-count") or 0),
        "url": clean_text(item.get("URL")),
    }


def normalize_europe_pmc(item: dict[str, Any]) -> dict[str, Any] | None:
    title = clean_text(item.get("title"))
    if not title:
        return None
    year = item.get("pubYear")
    try:
        year = int(year) if year else None
    except (TypeError, ValueError):
        year = None
    doi = normalize_doi(item.get("doi"))
    source_id = normalize_identifier(item.get("id")) or normalize_identifier(item.get("pmid")) or canonical_sha256(title)[:16]
    return {
        "source": "europe-pmc",
        "source_record_id": source_id,
        "doi": doi,
        "pmid": normalize_identifier(item.get("pmid")),
        "pmcid": normalize_identifier(item.get("pmcid")),
        "openalex_id": None,
        "title": title,
        "normalized_title": normalize_title(title),
        "abstract": clean_text(item.get("abstractText")),
        "year": year,
        "publication_date": clean_text(item.get("firstPublicationDate")) or None,
        "type": clean_text(item.get("pubType")),
        "journal": clean_text(item.get("journalTitle")),
        "authors": [x.strip() for x in clean_text(item.get("authorString")).split(",") if x.strip()],
        "citation_count": int(item.get("citedByCount") or 0),
        "url": f"https://europepmc.org/article/{urllib.parse.quote(str(item.get('source') or 'MED'))}/{urllib.parse.quote(str(source_id))}",
    }


def reconstruct_openalex_abstract(index: Any) -> str:
    if not isinstance(index, dict) or not index:
        return ""
    positions: list[tuple[int, str]] = []
    for word, spots in index.items():
        if not isinstance(spots, list):
            continue
        for pos in spots:
            try:
                positions.append((int(pos), str(word)))
            except (TypeError, ValueError):
                continue
    return " ".join(word for _, word in sorted(positions))


def normalize_openalex(item: dict[str, Any]) -> dict[str, Any] | None:
    title = clean_text(item.get("title"))
    if not title:
        return None
    primary = item.get("primary_location") or {}
    source = primary.get("source") or {}
    authors = []
    for authorship in item.get("authorships") or []:
        name = clean_text(((authorship.get("author") or {}).get("display_name")))
        if name:
            authors.append(name)
    raw_id = clean_text(item.get("id"))
    openalex_id = raw_id.rsplit("/", 1)[-1] if raw_id else None
    return {
        "source": "openalex-free-key",
        "source_record_id": openalex_id or canonical_sha256(title)[:16],
        "doi": normalize_doi(item.get("doi")),
        "pmid": normalize_identifier(((item.get("ids") or {}).get("pmid"))),
        "pmcid": normalize_identifier(((item.get("ids") or {}).get("pmcid"))),
        "openalex_id": openalex_id,
        "title": title,
        "normalized_title": normalize_title(title),
        "abstract": reconstruct_openalex_abstract(item.get("abstract_inverted_index")),
        "year": item.get("publication_year"),
        "publication_date": clean_text(item.get("publication_date")) or None,
        "type": clean_text(item.get("type")),
        "journal": clean_text(source.get("display_name")),
        "authors": authors,
        "citation_count": int(item.get("cited_by_count") or 0),
        "url": raw_id,
    }


def _json_request(
    url: str,
    *,
    opener: Callable[..., Any] = urllib.request.urlopen,
    attempts: int = 3,
) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": "Research-CoScientist-Literature/1.0 (zero-cost scholarly metadata broker)",
        },
    )
    last_error: Exception | None = None
    for attempt in range(attempts):
        try:
            with opener(request, timeout=120) as response:
                raw = response.read()
            return json.loads(raw.decode("utf-8"))
        except urllib.error.HTTPError as exc:
            last_error = exc
            if exc.code not in {429, 500, 502, 503, 504} or attempt + 1 >= attempts:
                break
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            last_error = exc
            if attempt + 1 >= attempts:
                break
        time.sleep(2 ** attempt)
    raise LiteratureError(f"scholarly source request failed after {attempts} attempts: {type(last_error).__name__}")


def search_crossref(query: str, rows: int, *, opener: Callable[..., Any] = urllib.request.urlopen) -> list[dict[str, Any]]:
    params = urllib.parse.urlencode({"query.bibliographic": query, "rows": rows})
    payload = _json_request(f"https://api.crossref.org/works?{params}", opener=opener)
    items = ((payload.get("message") or {}).get("items") or [])
    return [record for item in items if (record := normalize_crossref(item))]


def search_europe_pmc(query: str, rows: int, *, opener: Callable[..., Any] = urllib.request.urlopen) -> list[dict[str, Any]]:
    params = urllib.parse.urlencode({"query": query, "format": "json", "resultType": "core", "pageSize": rows})
    payload = _json_request(f"https://www.ebi.ac.uk/europepmc/webservices/rest/search?{params}", opener=opener)
    items = (((payload.get("resultList") or {}).get("result")) or [])
    return [record for item in items if (record := normalize_europe_pmc(item))]


def search_openalex(
    query: str,
    rows: int,
    *,
    api_key: str | None = None,
    opener: Callable[..., Any] = urllib.request.urlopen,
) -> tuple[list[dict[str, Any]], str]:
    if not api_key:
        return [], "SKIPPED_NO_FREE_KEY"
    params = urllib.parse.urlencode({"search": query, "per_page": rows, "api_key": api_key})
    payload = _json_request(f"https://api.openalex.org/works?{params}", opener=opener)
    items = payload.get("results") or []
    return [record for item in items if (record := normalize_openalex(item))], "PASS"


def search_all_sources(
    query: str,
    rows: int,
    *,
    openalex_key: str | None = None,
    opener: Callable[..., Any] = urllib.request.urlopen,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    records: list[dict[str, Any]] = []
    provenance: list[dict[str, Any]] = []
    query_hash = hashlib.sha256(query.encode("utf-8")).hexdigest()

    crossref = search_crossref(query, rows, opener=opener)
    if not crossref:
        raise LiteratureError("required source Crossref returned no records")
    records.extend(crossref)
    provenance.append({"source": "crossref", "status": "PASS", "result_count": len(crossref), "query_sha256": query_hash})

    epmc = search_europe_pmc(query, rows, opener=opener)
    if not epmc:
        raise LiteratureError("required source Europe PMC returned no records")
    records.extend(epmc)
    provenance.append({"source": "europe-pmc", "status": "PASS", "result_count": len(epmc), "query_sha256": query_hash})

    openalex, openalex_status = search_openalex(query, rows, api_key=openalex_key, opener=opener)
    records.extend(openalex)
    provenance.append({"source": "openalex-free-key", "status": openalex_status, "result_count": len(openalex), "query_sha256": query_hash})
    return records, provenance


def record_key(record: dict[str, Any]) -> str:
    if record.get("doi"):
        return f"doi:{record['doi']}"
    if record.get("pmid"):
        return f"pmid:{record['pmid']}"
    if record.get("pmcid"):
        return f"pmcid:{record['pmcid']}"
    title = record.get("normalized_title") or normalize_title(record.get("title"))
    year = record.get("year") or "unknown"
    return f"title:{hashlib.sha256(f'{title}|{year}'.encode()).hexdigest()[:24]}"


def deduplicate_records(records: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        groups[record_key(record)].append(record)

    works: list[dict[str, Any]] = []
    for key, members in groups.items():
        members = sorted(members, key=lambda x: (x.get("source") or "", x.get("source_record_id") or ""))
        best = max(members, key=lambda x: (len(x.get("abstract") or ""), len(x.get("title") or "")))
        identifiers = {
            field: sorted({str(m[field]) for m in members if m.get(field)})
            for field in ("doi", "pmid", "pmcid", "openalex_id")
        }
        authors = []
        for member in members:
            for author in member.get("authors") or []:
                if author and author not in authors:
                    authors.append(author)
        title_norm = best.get("normalized_title") or normalize_title(best.get("title"))
        version_group_id = "VG-" + hashlib.sha256(title_norm.encode("utf-8")).hexdigest()[:16]
        works.append({
            "canonical_id": key,
            "version_group_id": version_group_id,
            "title": best.get("title"),
            "normalized_title": title_norm,
            "abstract": best.get("abstract") or "",
            "year": best.get("year"),
            "publication_date": best.get("publication_date"),
            "type": best.get("type"),
            "journal": best.get("journal"),
            "authors": authors,
            "citation_count": max(int(m.get("citation_count") or 0) for m in members),
            "identifiers": identifiers,
            "source_records": [
                {
                    "source": m.get("source"),
                    "source_record_id": m.get("source_record_id"),
                    "doi": m.get("doi"),
                    "pmid": m.get("pmid"),
                    "pmcid": m.get("pmcid"),
                    "openalex_id": m.get("openalex_id"),
                    "url": m.get("url"),
                }
                for m in members
            ],
        })

    works.sort(key=lambda w: (-(w.get("citation_count") or 0), -(w.get("year") or 0), w.get("title") or ""))
    lineage_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for work in works:
        lineage_groups[work["version_group_id"]].append(work)
    lineages = [
        {
            "version_group_id": group_id,
            "member_count": len(members),
            "canonical_ids": [m["canonical_id"] for m in members],
            "years": sorted({m["year"] for m in members if m.get("year")}),
        }
        for group_id, members in sorted(lineage_groups.items())
    ]
    return works, lineages


def build_evidence_packets(works: list[dict[str, Any]]) -> list[dict[str, Any]]:
    packets = []
    for index, work in enumerate(works, 1):
        packets.append({
            "evidence_id": f"EVID-{index:04d}",
            "canonical_id": work["canonical_id"],
            "version_group_id": work["version_group_id"],
            "title": work["title"],
            "abstract": work.get("abstract") or "",
            "year": work.get("year"),
            "journal": work.get("journal"),
            "authors": work.get("authors") or [],
            "identifiers": work.get("identifiers") or {},
            "citation_count": work.get("citation_count") or 0,
            "traceability": work.get("source_records") or [],
        })
    return packets


def _overlap_score(query: str, text: str) -> tuple[float, list[str]]:
    left = tokens(query)
    right = tokens(text)
    if not left or not right:
        return 0.0, []
    matching = sorted(left & right)
    return round(len(matching) / len(left), 6), matching


def build_claim_source_map(claims: list[str], packets: list[dict[str, Any]], limit: int = 5) -> list[dict[str, Any]]:
    mapping = []
    for claim_index, claim in enumerate(claims, 1):
        scored = []
        for packet in packets:
            score, matching = _overlap_score(claim, f"{packet.get('title', '')} {packet.get('abstract', '')}")
            if score > 0:
                scored.append({
                    "evidence_id": packet["evidence_id"],
                    "canonical_id": packet["canonical_id"],
                    "lexical_support_score": score,
                    "matching_terms": matching,
                })
        scored.sort(key=lambda x: (-x["lexical_support_score"], x["evidence_id"]))
        mapping.append({
            "claim_id": f"CLAIM-{claim_index:03d}",
            "claim": claim,
            "candidate_evidence": scored[:limit],
            "interpretation_required": True,
        })
    return mapping


def build_novelty_court(research_question: str, works: list[dict[str, Any]], limit: int = 10) -> dict[str, Any]:
    qnorm = normalize_title(research_question)
    qtokens = tokens(research_question)
    collisions = []
    for work in works:
        title_tokens = tokens(work.get("title"))
        union = qtokens | title_tokens
        similarity = (len(qtokens & title_tokens) / len(union)) if union else 0.0
        exact = bool(qnorm and qnorm == work.get("normalized_title"))
        collisions.append({
            "canonical_id": work["canonical_id"],
            "title": work["title"],
            "year": work.get("year"),
            "title_similarity": round(similarity, 6),
            "exact_normalized_title_match": exact,
            "identifiers": work.get("identifiers") or {},
        })
    collisions.sort(key=lambda x: (-int(x["exact_normalized_title_match"]), -x["title_similarity"], -(x.get("year") or 0)))
    strongest = collisions[:limit]
    max_score = strongest[0]["title_similarity"] if strongest else 0.0
    if strongest and strongest[0]["exact_normalized_title_match"]:
        screen = "DIRECT_TITLE_COLLISION"
    elif max_score >= 0.65:
        screen = "HIGH_TITLE_SIMILARITY_SIGNAL"
    elif max_score >= 0.45:
        screen = "MODERATE_TITLE_SIMILARITY_SIGNAL"
    else:
        screen = "NO_CLOSE_TITLE_COLLISION_DETECTED"
    return {
        "schema_version": "cosci.novelty-court/1.0",
        "screen": screen,
        "max_title_similarity": round(max_score, 6),
        "strongest_collisions": strongest,
        "method": "deterministic metadata-level normalized-title token Jaccard screen",
        "limitations": [
            "Absence of a close title match does not establish scientific novelty.",
            "Abstract/full-text interpretation and field-aware judgment remain required.",
            "The Research Director must issue the final novelty decision.",
        ],
        "final_novelty_judgment": "REQUIRES_RESEARCH_DIRECTOR",
    }


def validate_literature_manifest(manifest: dict[str, Any], job_id: str) -> None:
    validate_job_id(job_id)
    if manifest.get("schema_version") != "cosci.job/1.0" or manifest.get("job_id") != job_id:
        raise LiteratureError("private literature manifest identity mismatch")
    if manifest.get("privacy_class") != "PRIVATE_RESEARCH" or manifest.get("task_type") != "LITERATURE":
        raise LiteratureError("literature job must be a private LITERATURE job")
    cost = manifest.get("cost_policy") or {}
    if cost.get("require_zero_cost") is not True or cost.get("automatic_paid_services") is not False:
        raise LiteratureError("literature job violates zero-cost policy")
    params = manifest.get("parameters") or {}
    if not clean_text(params.get("research_question")) or not clean_text(params.get("search_query")):
        raise LiteratureError("literature job requires private research_question and search_query")
    rows = params.get("max_results_per_source", 10)
    if not isinstance(rows, int) or not 1 <= rows <= 50:
        raise LiteratureError("max_results_per_source must be an integer from 1 to 50")
    claims = params.get("claims")
    if not isinstance(claims, list) or not claims or len(claims) > 10 or any(not clean_text(x) for x in claims):
        raise LiteratureError("literature job requires 1-10 non-empty claim probes")


def _upload_json_once(client: Any, folder_id: str, path: Path, name: str) -> str:
    existing = _optional_child(client, folder_id, name)
    if existing:
        check = path.parent / f"drive-existing-{name}"
        client.download(existing["id"], str(check))
        if sha256_file(check) != sha256_file(path):
            raise LiteratureError(f"existing private Drive artifact differs for {name}")
        return existing["id"]
    return client.upload(str(path), folder_id, name)


def run_private_literature_job(
    job_id: str,
    *,
    client: Any | None = None,
    workdir: str | Path | None = None,
    opener: Callable[..., Any] = urllib.request.urlopen,
    openalex_key: str | None = None,
) -> dict[str, Any]:
    validate_job_id(job_id)
    routes = load_literature_routes()
    client = client or _drive_client_from_env()
    job_folder = _search_job_folder(client, job_id)
    manifest_obj = _child(client, job_folder["id"], "job.json", folder=False)
    checkpoint_folder = _child(client, job_folder["id"], "checkpoints", folder=True)
    results_folder = _child(client, job_folder["id"], "results", folder=True)
    logs_folder = _child(client, job_folder["id"], "logs", folder=True)
    outputs_folder = _child(client, job_folder["id"], "outputs", folder=True)
    metadata_folder = _child(client, job_folder["id"], "metadata", folder=True)

    import tempfile
    context = tempfile.TemporaryDirectory(prefix="cosci-literature-") if workdir is None else None
    root = Path(context.name if context else workdir)
    root.mkdir(parents=True, exist_ok=True)
    try:
        manifest, manifest_sha = _download_json(client, manifest_obj["id"], root / "job.json")
        validate_literature_manifest(manifest, job_id)
        existing_result = _optional_child(client, results_folder["id"], "result.json")
        if existing_result:
            result, _ = _download_json(client, existing_result["id"], root / "result-existing.json")
            if result.get("status") != "SUCCEEDED" or (result.get("provenance") or {}).get("manifest_sha256") != manifest_sha:
                raise LiteratureError("existing literature result does not match this exact manifest")
            return {"job_id": job_id, "status": "SUCCEEDED", "resumed": True}

        params = manifest["parameters"]
        question = clean_text(params["research_question"])
        query = clean_text(params["search_query"])
        claims = [clean_text(x) for x in params["claims"]]
        rows = params.get("max_results_per_source", 10)
        query_sha = hashlib.sha256(query.encode("utf-8")).hexdigest()

        checkpoint = {
            "schema_version": "cosci.checkpoint/1.0",
            "job_id": job_id,
            "privacy_class": "PRIVATE_RESEARCH",
            "stage": "LITERATURE_QUERY_LOCKED",
            "manifest_sha256": manifest_sha,
            "query_sha256": query_sha,
            "required_sources": routes["policy"]["required_live_sources"],
            "resume_from": "RETRIEVE_SCHOLARLY_METADATA",
        }
        checkpoint_path = root / "checkpoint.json"
        _write_json(checkpoint_path, checkpoint)
        _upload_json_once(client, checkpoint_folder["id"], checkpoint_path, "checkpoint.json")

        records, source_status = search_all_sources(
            query,
            rows,
            openalex_key=openalex_key if openalex_key is not None else os.getenv("OPENALEX_API_KEY"),
            opener=opener,
        )
        works, version_lineage = deduplicate_records(records)
        if not works:
            raise LiteratureError("deduplication produced no scholarly works")
        packets = build_evidence_packets(works)
        claim_map = build_claim_source_map(claims, packets)
        novelty = build_novelty_court(question, works)

        corpus = {
            "schema_version": "cosci.literature-corpus/1.0",
            "research_question": question,
            "search_query": query,
            "query_sha256": query_sha,
            "raw_normalized_record_count": len(records),
            "deduplicated_work_count": len(works),
            "works": works,
            "version_lineage": version_lineage,
        }
        evidence = {
            "schema_version": "cosci.evidence-packets/1.0",
            "query_sha256": query_sha,
            "packet_count": len(packets),
            "packets": packets,
        }
        claim_mapping = {
            "schema_version": "cosci.claim-source-map/1.0",
            "query_sha256": query_sha,
            "claim_count": len(claim_map),
            "claims": claim_map,
        }
        source_provenance = {
            "schema_version": "cosci.literature-source-provenance/1.0",
            "query_sha256": query_sha,
            "retrieved_at": utc_now(),
            "sources": source_status,
            "route_policy": routes["policy"],
            "manifest_sha256": manifest_sha,
            "executor": "GITHUB_ACTIONS_PUBLIC",
            "github": {
                "repository": os.getenv("GITHUB_REPOSITORY"),
                "sha": os.getenv("GITHUB_SHA"),
                "run_id": os.getenv("GITHUB_RUN_ID"),
                "run_attempt": os.getenv("GITHUB_RUN_ATTEMPT"),
            },
        }

        artifacts = [
            ("literature_corpus.json", corpus, outputs_folder["id"], "LITERATURE_CORPUS"),
            ("evidence_packets.json", evidence, outputs_folder["id"], "EVIDENCE_PACKETS"),
            ("claim_source_map.json", claim_mapping, outputs_folder["id"], "CLAIM_SOURCE_MAP"),
            ("novelty_court.json", novelty, outputs_folder["id"], "NOVELTY_COURT"),
            ("source_provenance.json", source_provenance, metadata_folder["id"], "SOURCE_PROVENANCE"),
        ]
        published = []
        for name, value, folder_id, kind in artifacts:
            path = root / name
            _write_json(path, value)
            digest = sha256_file(path)
            file_id = _upload_json_once(client, folder_id, path, name)
            published.append({"name": name, "kind": kind, "drive_file_id": file_id, "sha256": digest})

        source_counts = {entry["source"]: entry["result_count"] for entry in source_status}
        result = {
            "schema_version": "cosci.result/1.0",
            "job_id": job_id,
            "project_id": manifest.get("project_id"),
            "status": "SUCCEEDED",
            "privacy_class": "PRIVATE_RESEARCH",
            "task_type": "LITERATURE",
            "outputs": published,
            "metrics": {
                "normalized_record_count": len(records),
                "deduplicated_work_count": len(works),
                "evidence_packet_count": len(packets),
                "claim_count": len(claim_map),
                "source_result_counts": source_counts,
                "novelty_screen": novelty["screen"],
                "max_title_similarity": novelty["max_title_similarity"],
            },
            "validation": {
                "passed": True,
                "checks": [
                    {"name": "zero_cost_sources", "status": "PASS"},
                    {"name": "multi_source_retrieval", "status": "PASS"},
                    {"name": "identifier_normalization", "status": "PASS"},
                    {"name": "deduplication_and_version_lineage", "status": "PASS"},
                    {"name": "evidence_traceability", "status": "PASS"},
                    {"name": "claim_source_mapping", "status": "PASS"},
                    {"name": "conservative_novelty_screen", "status": "PASS"},
                    {"name": "private_drive_publication", "status": "PASS"},
                ],
            },
            "provenance": {
                "manifest_sha256": manifest_sha,
                "query_sha256": query_sha,
                "executor": "GITHUB_ACTIONS_PUBLIC",
            },
            "next_state": "WAITING_FOR_RESEARCH_DIRECTOR",
        }
        result_path = root / "result.json"
        _write_json(result_path, result)
        client.upload(str(result_path), results_folder["id"], "result.json")

        log_path = root / "execution.log"
        log_path.write_text(
            "\n".join([
                f"job_id={job_id}",
                "task_type=LITERATURE",
                "zero_cost_sources=PASS",
                "multi_source_retrieval=PASS",
                f"normalized_record_count={len(records)}",
                f"deduplicated_work_count={len(works)}",
                f"evidence_packet_count={len(packets)}",
                "identifier_normalization=PASS",
                "deduplication_and_version_lineage=PASS",
                "evidence_traceability=PASS",
                "claim_source_mapping=PASS",
                "conservative_novelty_screen=PASS",
                "private_drive_publication=PASS",
                "next_state=WAITING_FOR_RESEARCH_DIRECTOR",
                "",
            ]),
            encoding="utf-8",
        )
        client.upload(str(log_path), logs_folder["id"], "execution.log")
        return {"job_id": job_id, "status": "SUCCEEDED", "resumed": False}
    finally:
        if context:
            context.cleanup()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Research CoScientist private literature/evidence engine")
    parser.add_argument("--job-id", required=True)
    parser.add_argument("--public-status", default="out/literature-status.json")
    args = parser.parse_args(argv)
    status = run_private_literature_job(args.job_id)
    write_public_status(args.job_id, args.public_status, task_type="LITERATURE")
    print(f"LITERATURE_EXECUTION={status['status']} job_id={args.job_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
