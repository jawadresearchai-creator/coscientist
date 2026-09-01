"""Adapter between CoScientist and the Global Management-Science data lake.

The GMS lake owns ingestion.  CoScientist owns scientific selection.

This adapter intentionally does *not* teach CoScientist how to acquire SEC,
Census, EIA, OpenAlex, or any other provider again.  It reads the lake's small
SQLite manifests locally, builds a compact metadata catalog, and retrieves only
objects explicitly named by a freeze.  Large/query-native sources are marked
QUERY_LAYER_REQUIRED so an ordinary runner cannot accidentally pull an entire
raw snapshot merely because it is present in Drive.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Iterable

from .drive import DriveClient, DriveError
from .freeze import FreezeManifest


class GMSLakeError(RuntimeError):
    pass


class Availability(str, Enum):
    AVAILABLE = "AVAILABLE"
    UNAVAILABLE = "UNAVAILABLE"
    NOT_YET_INGESTED = "NOT_YET_INGESTED"
    BLOCKED = "BLOCKED"
    PARTIAL = "PARTIAL"


class AnalysisRoute(str, Enum):
    DIRECT_FETCH = "DIRECT_FETCH"
    CURATED_QUERY = "CURATED_QUERY"
    QUERY_LAYER_REQUIRED = "QUERY_LAYER_REQUIRED"


GOOD_STATUSES = {"OK", "UNCHANGED", "SUCCESS", "COMPLETE", "AVAILABLE"}
FAILED_STATUSES = {"FAILED", "ERROR", "UNAVAILABLE"}
BLOCKED_STATUSES = {"BLOCKED", "FORBIDDEN", "AUTH_REQUIRED", "EXTERNALLY_BLOCKED"}
PARTIAL_STATUSES = {"PARTIAL", "BUILDING", "BACKFILLING", "IN_PROGRESS"}


@dataclass
class GMSObject:
    id: str
    domain: str
    source_id: str
    dataset_id: str
    remote_path: str
    sha256: str | None = None
    bytes: int | None = None
    status: str = "UNKNOWN"
    availability: str = Availability.NOT_YET_INGESTED.value
    analysis_route: str = AnalysisRoute.DIRECT_FETCH.value
    direct_fetch: bool = True
    concepts: list[str] = field(default_factory=list)
    granularity: str | None = None
    coverage_start: str | None = None
    coverage_end: str | None = None
    keys: list[str] = field(default_factory=list)
    last_checked: str | None = None
    last_changed: str | None = None
    manifest_path: str | None = None
    manifest_sha256: str | None = None
    lake_repo_sha: str | None = None
    licence: str | None = None
    notes: str | None = None

    @property
    def frozen_key(self) -> str:
        return self.remote_path


@dataclass
class GMSCatalog:
    lake_repo: str = ""
    lake_repo_sha: str = ""
    generated_at: str = ""
    manifests: list[dict[str, Any]] = field(default_factory=list)
    datasets: list[GMSObject] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "lake_repo": self.lake_repo,
            "lake_repo_sha": self.lake_repo_sha,
            "generated_at": self.generated_at,
            "manifests": self.manifests,
            "datasets": [asdict(d) for d in self.datasets],
        }

    def save(self, path: str) -> None:
        path = os.fspath(path)
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(self.to_dict(), fh, indent=2, sort_keys=True)
        os.replace(tmp, path)

    @classmethod
    def load(cls, path: str) -> "GMSCatalog":
        with open(path, "r", encoding="utf-8") as fh:
            raw = json.load(fh)
        return cls(
            lake_repo=raw.get("lake_repo", ""),
            lake_repo_sha=raw.get("lake_repo_sha", ""),
            generated_at=raw.get("generated_at", ""),
            manifests=list(raw.get("manifests", [])),
            datasets=[GMSObject(**d) for d in raw.get("datasets", [])],
        )

    def by_key(self) -> dict[str, GMSObject]:
        return {d.frozen_key: d for d in self.datasets}

    def query(self, *, concepts: Iterable[str] = (), source_id: str | None = None,
              domain: str | None = None, availability: str | None = None,
              route: str | None = None, max_bytes: int | None = None,
              text: str | None = None) -> list[GMSObject]:
        want = {x.lower() for x in concepts if x}
        needle = (text or "").lower().strip()
        out = []
        for d in self.datasets:
            if source_id and d.source_id != source_id:
                continue
            if domain and d.domain != domain:
                continue
            if availability and d.availability != availability:
                continue
            if route and d.analysis_route != route:
                continue
            if max_bytes is not None and d.bytes is not None and d.bytes > max_bytes:
                continue
            if want and not (want & {c.lower() for c in d.concepts}):
                continue
            if needle and needle not in " ".join(
                [d.id, d.domain, d.source_id, d.dataset_id, d.remote_path, " ".join(d.concepts)]
            ).lower():
                continue
            out.append(d)
        return sorted(out, key=lambda x: (x.domain, x.source_id, x.dataset_id, x.remote_path))


def _sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        while chunk := fh.read(8 * 1024 * 1024):
            h.update(chunk)
    return h.hexdigest()


def _availability(status: str, sha256: str | None) -> Availability:
    s = (status or "").upper()
    if s in GOOD_STATUSES and sha256:
        return Availability.AVAILABLE
    if s in BLOCKED_STATUSES:
        return Availability.BLOCKED
    if s in PARTIAL_STATUSES:
        return Availability.PARTIAL
    if s in FAILED_STATUSES:
        return Availability.UNAVAILABLE
    return Availability.NOT_YET_INGESTED


def _route(source_id: str, remote_path: str, size: int | None,
           direct_fetch_limit: int) -> tuple[AnalysisRoute, bool]:
    sid = (source_id or "").upper()
    p = (remote_path or "").upper()
    # The OpenAlex snapshot is intentionally query-only.  Presence in the lake
    # is not permission to transfer ~TB of raw shards through a GitHub runner.
    if "OPENALEX" in sid or "OPENALEX" in p:
        return AnalysisRoute.QUERY_LAYER_REQUIRED, False
    if "02_CURATED" in p or "/CURATED/" in p or p.startswith("02_CURATED/"):
        return AnalysisRoute.CURATED_QUERY, True
    if size is not None and size > direct_fetch_limit:
        return AnalysisRoute.QUERY_LAYER_REQUIRED, False
    return AnalysisRoute.DIRECT_FETCH, True


def _pick_table(conn: sqlite3.Connection) -> tuple[str, set[str]]:
    tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")]
    best: tuple[int, str, set[str]] | None = None
    for t in tables:
        cols = {r[1] for r in conn.execute(f'PRAGMA table_info("{t}")')}
        score = len(cols & {"remote_path", "sha256", "source_id", "dataset_id", "status"})
        if "remote_path" in cols and score >= 2:
            cand = (score, t, cols)
            if best is None or cand[0] > best[0]:
                best = cand
    if not best:
        raise GMSLakeError("SQLite manifest has no table containing remote_path plus manifest metadata")
    return best[1], best[2]


def _get(row: sqlite3.Row, names: Iterable[str], default=None):
    keys = set(row.keys())
    for n in names:
        if n in keys and row[n] not in (None, ""):
            return row[n]
    return default


def read_manifest(path: str, *, repo_sha: str = "", direct_fetch_limit: int = 2_000_000_000) -> list[GMSObject]:
    """Read one downloaded SQLite manifest locally.

    The SQLite file must already be on local ephemeral storage.  Opening SQLite
    directly on a Drive mount is intentionally unsupported.
    """
    if not os.path.isfile(path):
        raise GMSLakeError(f"manifest is not a local file: {path}")
    manifest_hash = _sha256_file(path)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        table, cols = _pick_table(conn)
        rows = conn.execute(f'SELECT * FROM "{table}"').fetchall()
    finally:
        conn.close()
    out: list[GMSObject] = []
    for row in rows:
        remote = str(_get(row, ["remote_path", "drive_path", "path"], "")).strip("/")
        if not remote:
            continue
        source = str(_get(row, ["source_id", "source", "provider"], "UNKNOWN"))
        domain = str(_get(row, ["domain", "domain_id"], remote.split("/", 1)[0] if "/" in remote else "unknown"))
        dataset = str(_get(row, ["dataset_id", "dataset", "name"], os.path.basename(remote)))
        sha = _get(row, ["sha256", "sha_256", "content_sha256"])
        status = str(_get(row, ["status", "state"], "UNKNOWN"))
        raw_bytes = _get(row, ["bytes", "content_length", "size", "file_size"])
        try:
            size = int(raw_bytes) if raw_bytes is not None else None
        except (TypeError, ValueError):
            size = None
        route, direct = _route(source, remote, size, direct_fetch_limit)
        avail = _availability(status, str(sha) if sha else None)
        # A query-only object can be available in storage without being safe to
        # direct-fetch. These are separate concepts by design.
        concepts_raw = _get(row, ["concepts", "concept", "tags"], "")
        if isinstance(concepts_raw, str):
            concepts = [c.strip().lower() for c in concepts_raw.replace(";", ",").split(",") if c.strip()]
        else:
            concepts = []
        out.append(GMSObject(
            id=f"{source}/{dataset}",
            domain=domain,
            source_id=source,
            dataset_id=dataset,
            remote_path=remote,
            sha256=str(sha) if sha else None,
            bytes=size,
            status=status,
            availability=avail.value,
            analysis_route=route.value,
            direct_fetch=direct,
            concepts=concepts,
            granularity=_get(row, ["granularity"]),
            coverage_start=_get(row, ["coverage_start", "start_date", "start_year"]),
            coverage_end=_get(row, ["coverage_end", "end_date", "end_year"]),
            last_checked=_get(row, ["last_checked", "checked_at"]),
            last_changed=_get(row, ["last_changed", "changed_at", "last_modified"]),
            manifest_path=os.path.basename(path),
            manifest_sha256=manifest_hash,
            lake_repo_sha=repo_sha or None,
            licence=_get(row, ["licence", "license"]),
        ))
    return out


def build_catalog(manifest_paths: Iterable[str], *, lake_repo: str = "", lake_repo_sha: str = "",
                  direct_fetch_limit: int = 2_000_000_000) -> GMSCatalog:
    manifests = []
    by_path: dict[str, GMSObject] = {}
    for path in sorted(manifest_paths):
        mh = _sha256_file(path)
        objs = read_manifest(path, repo_sha=lake_repo_sha, direct_fetch_limit=direct_fetch_limit)
        manifests.append({"name": os.path.basename(path), "sha256": mh, "objects": len(objs)})
        for obj in objs:
            # Remote path is the canonical object identity. If two manifests
            # disagree, prefer the record with a hash and a more useful status;
            # never duplicate the same bytes under two candidate identities.
            prev = by_path.get(obj.remote_path)
            if prev is None or (not prev.sha256 and obj.sha256):
                by_path[obj.remote_path] = obj
    return GMSCatalog(
        lake_repo=lake_repo,
        lake_repo_sha=lake_repo_sha,
        generated_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        manifests=manifests,
        datasets=sorted(by_path.values(), key=lambda x: x.remote_path),
    )


def fetch_frozen_from_gms(client: DriveClient, root_folder_id: str, manifest: FreezeManifest,
                          catalog: GMSCatalog, dest_dir: str) -> dict[str, str]:
    """Fetch only the objects named by the freeze, using catalog paths/hashes."""
    index = catalog.by_key()
    got: dict[str, str] = {}
    for key, expected in sorted(manifest.dataset_hashes.items()):
        obj = index.get(key)
        if obj is None:
            raise GMSLakeError(f"frozen object is not in the current GMS catalog: {key}")
        if obj.availability != Availability.AVAILABLE.value:
            raise GMSLakeError(f"{key}: lake availability is {obj.availability}, not AVAILABLE")
        if not obj.sha256:
            raise GMSLakeError(f"{key}: catalog has no SHA-256")
        if obj.sha256 != expected:
            raise GMSLakeError(
                f"{key}: catalog hash {obj.sha256[:12]} differs from frozen {expected[:12]}"
            )
        if not obj.direct_fetch or obj.analysis_route == AnalysisRoute.QUERY_LAYER_REQUIRED.value:
            raise GMSLakeError(
                f"{key}: direct fetch is forbidden; analysis_route={obj.analysis_route}. "
                "Request a curated/query-layer extract and freeze that output instead."
            )
        dest = os.path.join(dest_dir, key)
        try:
            got[key] = client.download_path(root_folder_id, key, dest, expected_sha256=expected)
        except DriveError as exc:
            raise GMSLakeError(str(exc)) from exc
    return got


def query_plan(catalog: GMSCatalog, *, concepts: Iterable[str] = (), source_id: str | None = None,
               text: str | None = None, columns: Iterable[str] = (), filters: Iterable[str] = (),
               max_rows: int | None = None) -> dict[str, Any]:
    """Create a metadata-first request for the smallest useful data slice.

    This does not pretend a server-side query layer exists where it does not.
    It records exactly what a future curation/query worker should materialize,
    and it strongly prefers CURATED_QUERY/DIRECT_FETCH objects over raw query-
    required snapshots.
    """
    candidates = catalog.query(concepts=concepts, source_id=source_id, text=text)
    ranked = sorted(candidates, key=lambda d: (
        0 if d.analysis_route == AnalysisRoute.CURATED_QUERY.value else
        1 if d.analysis_route == AnalysisRoute.DIRECT_FETCH.value else 2,
        d.bytes if d.bytes is not None else 10**30,
        d.remote_path,
    ))
    return {
        "request_type": "GMS_QUERY_PLAN",
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "lake_repo": catalog.lake_repo,
        "lake_repo_sha": catalog.lake_repo_sha,
        "concepts": list(concepts),
        "source_id": source_id,
        "text": text,
        "columns": list(columns),
        "filters": list(filters),
        "max_rows": max_rows,
        "policy": "use the smallest curated/queryable object; never scan/fetch the whole lake",
        "candidates": [asdict(d) for d in ranked[:25]],
    }
