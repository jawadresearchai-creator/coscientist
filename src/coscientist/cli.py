"""Command line entry points for the deterministic core.

Everything here runs with no LLM and no subscription. Judgment work leaves as
a ticket; the core never waits on one, except for the pre-freeze audit.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any

import glob
import hashlib
import subprocess
import tempfile
from datetime import datetime, timezone

from .analysis_lock import (AnalysisLock, AnalysisLockViolation, discover_scripts,
                            execution_plan, scan_io)
from .budget import DEFAULT_BUDGETS, BudgetLedger
from .drive import (CredentialError, DriveClient, DriveCredentials, DriveError,
                    access_token)
from .emit import EmitError, collect
from .freeze import FreezeManifest, FreezeViolation, verify_datasets, hash_file
from .manuscript import ResultsBundle, hash_scripts, new_bundle
from .freeze import require_freeze
from .provenance import ProvenanceError, validate
from .lake import LakeCatalog
from .github import GitHubReadClient, GitHubReadError
from .gms_lake import (AnalysisRoute, Availability, GMSCatalog, GMSLakeError,
                       build_catalog, fetch_frozen_from_gms, query_plan)
from .registry import SourceRegistry
from .state import EngineState, Ledger
from .tickets import Policy, TicketQueue


def _default(path: str, *parts: str) -> str:
    return os.path.join(path, *parts)


def cmd_doctor(args: argparse.Namespace) -> int:
    """Weekly source and credential verification.

    Catches the two silent killers: a source whose access class has changed
    under it (USPTO retired its bulk hosts and nothing noticed for months),
    and a credential that quietly expired (Google refresh tokens die after
    seven days while a consent screen is in Testing).
    """
    registry = SourceRegistry.load(args.registry)
    rows = registry.audit()
    problems = [r for r in rows if not r["secrets_ok"]]

    print(f"{'SOURCE':<32} {'CLASS':<16} {'ADMIT':<6} {'ARCHIVE':<17} SECRETS")
    print("-" * 92)
    for r in rows:
        print(
            f"{r['id']:<32} {r['access_class']:<16} "
            f"{'yes' if r['admissible'] else 'no':<6} "
            f"{r['archive_status']:<17} "
            f"{'ok' if r['secrets_ok'] else 'MISSING ' + ','.join(r['missing_secrets'])}"
        )

    admissible = [r for r in rows if r["admissible"]]
    print(f"\n{len(admissible)}/{len(rows)} sources admissible; {len(problems)} with missing secrets")
    if problems:
        print("Missing secrets are an infrastructure failure, not a scientific one.")

    if args.live:
        # A token present in the environment is not a token that still works.
        # This is the whole point of the weekly job: Google expires refresh
        # tokens after seven days while the consent screen sits in Testing, and
        # the failure otherwise surfaces mid-build on the one night the engine
        # had something to acquire.
        print("\nCredential liveness")
        try:
            creds = DriveCredentials.from_env()
        except CredentialError as exc:
            print(f"  drive     NOT CONFIGURED -- {exc}")
            return 1
        try:
            token = access_token(creds)
        except CredentialError as exc:
            print(f"  drive     DEAD -- {exc}")
            return 1
        print(f"  drive     ok (access token {token[:8]}..., refreshed just now)")
    return 1 if problems and args.strict else 0


def cmd_registry(args: argparse.Namespace) -> int:
    registry = SourceRegistry.load(args.registry)
    if args.concept:
        hits = registry.by_concept([args.concept], admissible_only=not args.all)
        if not hits:
            print(f"no admissible source measures {args.concept!r}")
            return 1
        for s in hits:
            print(f"{s.id:<32} {s.access_class.value:<16} {' '.join(s.concepts[:4])}")
        return 0
    for s in sorted(registry.sources.values(), key=lambda x: x.id):
        flag = "" if s.admissible else "  [INADMISSIBLE]"
        print(f"{s.id:<32} {s.access_class.value:<16}{flag}")
    return 0


def cmd_tickets(args: argparse.Namespace) -> int:
    queue = TicketQueue(args.queue)
    policy = Policy.load(args.policy) if os.path.exists(args.policy) else Policy({}, "ask")
    digest = queue.digest(policy)

    blocking = queue.blocking_open()
    if blocking:
        print("BLOCKING -- the core is waiting on these:")
        for t in blocking:
            print(f"  {t.id}  {t.subject}")
        print()

    for bucket in ("ask", "auto", "never"):
        items = digest[bucket]
        if not items:
            continue
        print(f"{bucket.upper()} ({len(items)})")
        for it in items:
            mark = " [BLOCKING]" if it["blocking"] else ""
            print(f"  {it['id']}{mark}")
            print(f"    {it['subject']}")
            print(f"    agent={it['agent']}  cost={it['est_cost'] or 'n/a'}")
            print(f"    if skipped: {it['if_skipped']}")
        print()

    if not any(digest.values()) and not blocking:
        print("queue empty; the core is unblocked")
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    state = EngineState.load(args.state)
    stage, step = state.resume_point()
    queue = TicketQueue(args.queue)
    blocking = queue.blocking_open()

    print(f"stage         {stage or '-'}")
    print(f"step          {step or '-'}")
    print(f"checkpoints   {len(state.data.get('checkpoints', []))}")
    print(f"open tickets  {len(queue.open_tickets())} ({len(blocking)} blocking)")
    print(f"core status   {'WAITING on pre-freeze audit' if blocking else 'RUNNABLE'}")
    return 0


def cmd_lake(args: argparse.Namespace) -> int:
    catalog = LakeCatalog.load(args.catalog)
    print(f"{len(catalog.datasets)} datasets in catalog")
    for ds in sorted(catalog.datasets.values(), key=lambda d: d.id):
        span = f"{ds.coverage_start or '?'}..{ds.coverage_end or '?'}"
        print(f"  {ds.id:<32} {ds.domain:<12} {ds.granularity or '-':<16} {span}")
    return 0


def cmd_budget(args: argparse.Namespace) -> int:
    path = os.path.join(os.path.dirname(args.state) or ".", "budget.json")
    # The abstraction distinguished study from cycle; the CLI then called it
    # without a study id, so `study or cycle` fell back and the 25 GB
    # per-paper ceiling reset every cycle again. A fixed abstraction with an
    # unfixed caller is an unfixed system.
    budget = BudgetLedger.load_or_new(path, args.cycle, DEFAULT_BUDGETS, args.study)
    print(f"{'LINE':<20} {'USED':>18} {'LIMIT':>18}   PCT")
    print("-" * 68)
    for line in budget.lines.values():
        flag = "  <-- 80%" if line.warn else ""
        print(
            f"{line.name:<20} {line.used:>18,.0f} {line.limit:>18,.0f} "
            f"{line.fraction:>6.1%}{flag}"
        )
    warnings = budget.warnings()
    if warnings:
        print("\nWARNING: " + "; ".join(warnings))
    print(f"\npersisted at {path} -- spend accumulates across cycles, "
          "so a monthly cap cannot be spent once per cycle.")
    budget.save(path)
    return 0


def cmd_freeze_verify(args: argparse.Namespace) -> int:
    """Recompute the freeze hash and compare it to what was stored.

    This is what makes the outcome lock a mechanism. A design edited after the
    freeze fails here rather than being noticed by whoever happens to reread it.
    """
    try:
        manifest = FreezeManifest.load(args.manifest)
    except FreezeViolation as exc:
        print(f"FREEZE VIOLATION\n{exc}")
        return 2
    except FileNotFoundError:
        print(f"no freeze manifest at {args.manifest}")
        return 1

    print(f"freeze id    {manifest.freeze_id}")
    print(f"hash         {manifest.freeze_hash}")
    print(f"frozen at    {manifest.frozen_at}")
    print(f"engine       {manifest.engine_version}")
    print(f"datasets     {len(manifest.dataset_hashes)}")

    if not args.data_dir:
        print("\nmanifest self-consistent; datasets NOT checked (pass --data-dir)")
        return 0

    checks = verify_datasets(manifest, args.data_dir)
    for ch in checks:
        print(f"  {ch.name:<28} {ch.expected[:16]}...  {ch.status}")

    bad = [ch for ch in checks if not ch.ok]
    if bad:
        print(f"\nFREEZE VIOLATION -- {len(bad)} dataset(s) not as frozen.")
        print("The design was registered against specific bytes. Analysing different "
              "ones produces a result no manifest describes, so analysis is forbidden.")
        return 2
    print("\nfreeze intact; all datasets match")
    return 0


def _load_literals(path: str | None) -> dict[str, Any]:
    """Numbers with a stated origin that are not results: alpha, a rate quoted
    from a cited paper, a statutory threshold."""
    if not path or not os.path.exists(path):
        return {}
    import yaml
    with open(path, "r", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh) or {}
    return raw.get("literals", raw)


def cmd_provenance(args: argparse.Namespace) -> int:
    """Fail the build if any number in the manuscript is not in the results.

    The manuscript bridge stops transcription errors. This stops invention.
    """
    bundle = ResultsBundle.from_manifest(args.results)

    # Fail closed. `if os.path.exists(...)` silently skipped the check when the
    # manifest was absent, so a missing freeze -- the most alarming possible
    # state -- produced a clean pass. Skipping is now explicit and named.
    if args.no_freeze_check:
        print("WARNING: freeze binding NOT checked (--no-freeze-check). "
              "Never use this flag in a submission workflow.")
    else:
        if not os.path.exists(args.freeze):
            print(f"FREEZE MISSING -- no manifest at {args.freeze}.\n"
                  "Results cannot be bound to a design that is not there.")
            return 2
        require_freeze(FreezeManifest.load(args.freeze), bundle.freeze_hash)

    with open(args.manuscript, "r", encoding="utf-8") as fh:
        text = fh.read()
    allow = [float(x) for x in (args.allow or [])]
    literals = _load_literals(args.literals)
    try:
        report = validate(text, bundle, allow=allow, literals=literals,
                          require_tokens=args.require_tokens)
    except ProvenanceError as exc:
        # A structural violation -- a forbidden escape, or a citation to a
        # literal nobody registered. It stops the build exactly like an
        # untraceable number does, so it has to report like one rather than
        # surfacing as a traceback forty minutes into a CI run.
        print(f"PROVENANCE VIOLATION\n{exc}")
        return 1
    print(report.summary())
    if not report.ok:
        print("\nEach line above is a number with no result behind it. Either cite the "
              "token that produced it, or allow-list it if it is genuinely not a finding.")
    return 0 if report.ok else 1



# ---------------------------------------------------------------- analysis ---

# `analysis_scripts` used to redefine the same globs the lock module owns.
# Two definitions of "what can produce a number" drift, and the one that
# drifted would be the one deciding which scripts get hashed.
analysis_scripts = discover_scripts


def environment_hash(root: str = ".") -> str:
    """What the analysis ran inside.

    renv.lock and pyproject pin the libraries; the interpreter versions pin the
    rest. Without this the bundle records which code ran but not what it ran on,
    and 'reproducible' stops at the repository boundary.
    """
    h = hashlib.sha256()
    for name in ("renv.lock", "pyproject.toml"):
        path = os.path.join(root, name)
        if os.path.exists(path):
            with open(path, "rb") as fh:
                h.update(name.encode())
                h.update(fh.read())
    h.update(sys.version.encode())
    try:
        r = subprocess.run(["R", "--version"], capture_output=True, text=True, timeout=10)
        if r.returncode == 0:
            h.update(r.stdout.splitlines()[0].encode())
    except Exception:
        h.update(b"R:absent")
    return h.hexdigest()


def _drive() -> DriveClient:
    return DriveClient(DriveCredentials.from_env())



def cmd_analysis_lock(args: argparse.Namespace) -> int:
    """Register the analysis code, after the design freeze and before outcomes.

    The design freeze says which question. This says which analysis. Without
    it the engine can prove a result is reproducible but not that it was
    pre-specified, and only the second supports a confirmatory claim.
    """
    try:
        freeze = FreezeManifest.load(args.freeze)
    except FileNotFoundError:
        print(f"no freeze manifest at {args.freeze}; freeze the design first")
        return 1
    except FreezeViolation as exc:
        print(f"FREEZE VIOLATION\n{exc}")
        return 2

    scripts = discover_scripts(args.root)
    if not scripts:
        print("no analysis scripts found under R/, python/ or stan/")
        return 1

    findings = scan_io(scripts, args.root)
    if findings:
        print(f"{len(findings)} unsanctioned IO or seeding call(s):")
        for f in findings[:30]:
            print(f"  {f.script}:{f.line:<5} {f.call:<28} {f.why}")
        print()

    try:
        lock = AnalysisLock.create(freeze, scripts, root=args.root,
                                   acknowledge_io=args.acknowledge_io)
        lock.save(args.out)
    except AnalysisLockViolation as exc:
        print(f"CANNOT LOCK\n{exc}")
        return 2

    print(f"lock id       {lock.lock_id}")
    print(f"freeze        {lock.freeze_id}")
    print(f"scripts       {len(lock.script_hashes)}")
    print(f"environment   {', '.join(sorted(lock.env_hashes))}")
    print(f"plan          {' -> '.join(execution_plan(args.root)) or '(none numbered)'}")
    print(f"\nwritten to {args.out}. The code is now pre-specified; editing any of "
          "it before the run will fail verification.")
    return 0


def cmd_analysis_verify(args: argparse.Namespace) -> int:
    """Refuse to run code that is not the code that was locked."""
    try:
        lock = AnalysisLock.load(args.lock)
    except FileNotFoundError:
        print(f"NO ANALYSIS LOCK at {args.lock}.\n"
              "Results produced without one are reproducible but not "
              "pre-specified, so they cannot be reported as confirmatory.")
        return 2
    except AnalysisLockViolation as exc:
        print(f"ANALYSIS LOCK VIOLATION\n{exc}")
        return 2

    try:
        freeze = FreezeManifest.load(args.freeze)
    except FileNotFoundError:
        print(f"NO DESIGN FREEZE at {args.freeze}. An analysis lock cannot be "
              "verified without the frozen design it is supposed to bind to.")
        return 2
    except FreezeViolation as exc:
        print(f"FREEZE VIOLATION\n{exc}")
        return 2

    print(f"lock id    {lock.lock_id}")
    print(f"locked at  {lock.locked_at}")
    print(f"scripts    {len(lock.script_hashes)}")
    if lock.io_findings:
        print(f"declared   {len(lock.io_findings)} acknowledged IO/seeding call(s)")

    problems = lock.verify(args.root, freeze=freeze)
    if problems:
        print("\nANALYSIS LOCK VIOLATION -- the code about to run is not the code "
              "that was locked:")
        for p in problems:
            print(f"  - {p}")
        print("\nRunning edited code against frozen data produces a result that is "
              "reproducible but no longer pre-specified.")
        return 2
    print("\nanalysis code intact; every locked script and environment file matches")
    return 0


def cmd_plan(args: argparse.Namespace) -> int:
    """Print the scripts to execute, in order, one per line.

    The workflow reads this instead of globbing for itself, so there is one
    definition of what runs rather than a shell pattern and a Python function
    that agree until they do not.
    """
    for script in execution_plan(args.root):
        print(script)
    return 0


def cmd_state_fetch(args: argparse.Namespace) -> int:
    """Bootstrap the immutable study state from Drive before outcome access."""
    root = args.folder or os.environ.get("GDRIVE_STATE_FOLDER_ID", "")
    if not root:
        print("no Drive state root (pass --folder or set GDRIVE_STATE_FOLDER_ID)")
        return 1
    try:
        client = _drive()
        got = client.fetch_study_state(root, args.study, args.dest)
        # Self-hashes are the authority after transport.  Downloading bytes is
        # not enough: both files must validate before any outcome is fetched.
        freeze = FreezeManifest.load(os.path.join(args.dest, "freeze.json"))
        lock = AnalysisLock.load(os.path.join(args.dest, "analysis_lock.json"))
        if freeze.candidate_id != args.study:
            raise DriveError(
                f"state folder {args.study!r} contains freeze for {freeze.candidate_id!r}"
            )
        if lock.freeze_hash != freeze.freeze_hash:
            raise DriveError(
                "analysis lock and design freeze in Drive describe different studies"
            )
    except (CredentialError, DriveError, FreezeViolation, AnalysisLockViolation,
            FileNotFoundError) as exc:
        print(f"STATE BOOTSTRAP FAILED\n{exc}")
        return 2
    for name, digest in sorted(got.items()):
        print(f"  {name:<24} {digest[:16]}  ok")
    print(f"\nstate for {args.study} bootstrapped to {args.dest}; freeze {freeze.freeze_id}")
    return 0


def cmd_fetch(args: argparse.Namespace) -> int:
    """Pull exactly the datasets the freeze names out of Drive.

    Exactly, and nothing else. A folder listing is not a shopping list: the
    freeze decides what this analysis is allowed to see, so an extra file
    sitting in the same Drive folder never reaches the runner at all.
    """
    try:
        manifest = FreezeManifest.load(args.manifest)
    except FileNotFoundError:
        print(f"no freeze manifest at {args.manifest}; nothing to fetch")
        return 1
    except FreezeViolation as exc:
        print(f"FREEZE VIOLATION\n{exc}")
        return 2

    folder = args.folder or os.environ.get("GDRIVE_DATA_FOLDER_ID") \
        or os.environ.get("GDRIVE_ROOT_FOLDER_ID", "")
    if not folder:
        print("no Drive folder id (pass --folder or set GDRIVE_DATA_FOLDER_ID)")
        return 1

    try:
        client = _drive()
        got = client.fetch_frozen(folder, manifest.dataset_hashes, args.dest)
    except (CredentialError, DriveError) as exc:
        print(f"ACQUISITION FAILED\n{exc}")
        return 2

    for name, digest in sorted(got.items()):
        print(f"  {name:<36} {digest[:16]}  ok")
    print(f"\n{len(got)} frozen dataset(s) in {args.dest}, each hash-matched on arrival")
    return 0


def cmd_github_status(args: argparse.Namespace) -> int:
    """Read the GMS repository control-plane status. Never writes GitHub."""
    try:
        client = GitHubReadClient.from_env()
        status = client.status(run_limit=args.runs)
    except GitHubReadError as exc:
        print(f"GITHUB STATUS FAILED\n{exc}")
        return 2
    print(f"repo          {status.repo}")
    print(f"branch        {status.default_branch}")
    print(f"head          {status.head_sha}")
    for r in status.latest_runs:
        print(f"  {str(r.get('name') or '-'):30} {str(r.get('status') or '-'):12} "
              f"{str(r.get('conclusion') or '-'):12} {str(r.get('head_sha') or '')[:12]}")
    return 0


def _gms_repo_sha(args: argparse.Namespace) -> tuple[str, str]:
    repo = args.repo or os.environ.get("GMSDL_GITHUB_REPO", "")
    sha = args.repo_sha or ""
    if sha:
        return repo, sha
    if repo:
        try:
            st = GitHubReadClient.from_env().status(run_limit=1)
            return st.repo, st.head_sha
        except GitHubReadError:
            # Catalog construction may continue from Drive manifests when the
            # public control plane is temporarily unavailable; the missing SHA
            # remains explicit rather than invented.
            return repo, ""
    return "", ""


def cmd_lake_sync(args: argparse.Namespace) -> int:
    """Build a compact GMS catalog from local copies of SQLite manifests."""
    repo, sha = _gms_repo_sha(args)
    temp = None
    manifest_paths: list[str] = []
    try:
        if args.manifest_dir:
            for name in os.listdir(args.manifest_dir):
                if name.lower().endswith((".sqlite", ".sqlite3", ".db")):
                    manifest_paths.append(os.path.join(args.manifest_dir, name))
        else:
            folder = args.manifest_folder or os.environ.get("GMSDL_MANIFEST_FOLDER_ID", "")
            if not folder:
                print("no manifest source: pass --manifest-dir/--manifest-folder or set GMSDL_MANIFEST_FOLDER_ID")
                return 1
            temp = tempfile.TemporaryDirectory(prefix="coscientist-gms-manifests-")
            manifest_paths = _drive().fetch_sqlite_manifests(folder, temp.name)
        if not manifest_paths:
            print("no SQLite manifests found")
            return 1
        catalog = build_catalog(manifest_paths, lake_repo=repo, lake_repo_sha=sha,
                                direct_fetch_limit=args.direct_fetch_limit)
        catalog.save(args.out)
    except (CredentialError, DriveError, GMSLakeError, OSError) as exc:
        print(f"LAKE SYNC FAILED\n{exc}")
        return 2
    finally:
        if temp is not None:
            temp.cleanup()
    counts: dict[str, int] = {}
    routes: dict[str, int] = {}
    for d in catalog.datasets:
        counts[d.availability] = counts.get(d.availability, 0) + 1
        routes[d.analysis_route] = routes.get(d.analysis_route, 0) + 1
    print(f"catalog       {args.out}")
    print(f"repo          {catalog.lake_repo or '-'}")
    print(f"repo sha      {catalog.lake_repo_sha or 'UNPINNED'}")
    print(f"manifests     {len(catalog.manifests)}")
    print(f"objects       {len(catalog.datasets)}")
    print("availability  " + ", ".join(f"{k}={v}" for k,v in sorted(counts.items())))
    print("routes        " + ", ".join(f"{k}={v}" for k,v in sorted(routes.items())))
    return 0


def cmd_lake_doctor(args: argparse.Namespace) -> int:
    try:
        catalog = GMSCatalog.load(args.catalog)
    except (OSError, json.JSONDecodeError, TypeError) as exc:
        print(f"LAKE DOCTOR FAILED\n{exc}")
        return 2
    available = [d for d in catalog.datasets if d.availability == Availability.AVAILABLE.value]
    query_only = [d for d in available if not d.direct_fetch]
    bad_hash = [d for d in available if not d.sha256]
    print(f"repo sha      {catalog.lake_repo_sha or 'UNPINNED'}")
    print(f"manifests     {len(catalog.manifests)}")
    print(f"objects       {len(catalog.datasets)}")
    print(f"available     {len(available)}")
    print(f"query-only    {len(query_only)}")
    print(f"bad hashes    {len(bad_hash)}")
    if bad_hash:
        return 2
    if args.require_repo_sha and not catalog.lake_repo_sha:
        print("catalog is not pinned to a GMS repository commit")
        return 2
    return 0


def cmd_lake_query(args: argparse.Namespace) -> int:
    catalog = GMSCatalog.load(args.catalog)
    concepts = args.concept or []
    hits = catalog.query(concepts=concepts, source_id=args.source, domain=args.domain,
                         availability=args.availability, route=args.route,
                         max_bytes=args.max_bytes, text=args.text)
    for d in hits[:args.limit]:
        size = str(d.bytes) if d.bytes is not None else "?"
        print(f"{d.availability:<16} {d.analysis_route:<20} {size:>12}  {d.remote_path}")
    print(f"\n{len(hits)} matching object(s); displayed {min(len(hits), args.limit)}")
    return 0 if hits else 1


def cmd_gms_fetch(args: argparse.Namespace) -> int:
    try:
        freeze = FreezeManifest.load(args.manifest)
        catalog = GMSCatalog.load(args.catalog)
    except (FileNotFoundError, FreezeViolation, OSError, json.JSONDecodeError) as exc:
        print(f"GMS FETCH FAILED\n{exc}")
        return 2
    root = args.root_folder or os.environ.get("GMSDL_DRIVE_ROOT_FOLDER_ID", "")
    if not root:
        print("no GMS Drive root (pass --root-folder or set GMSDL_DRIVE_ROOT_FOLDER_ID)")
        return 1
    try:
        got = fetch_frozen_from_gms(_drive(), root, freeze, catalog, args.dest)
    except (CredentialError, DriveError, GMSLakeError) as exc:
        print(f"GMS FETCH FAILED\n{exc}")
        return 2
    for name, digest in sorted(got.items()):
        print(f"  {name:<72} {digest[:16]}  ok")
    print(f"\n{len(got)} frozen GMS object(s) fetched; no unrelated lake bytes were read")
    return 0


def cmd_gms_query_plan(args: argparse.Namespace) -> int:
    catalog = GMSCatalog.load(args.catalog)
    plan = query_plan(catalog, concepts=args.concept or [], source_id=args.source,
                      text=args.text, columns=args.column or [], filters=args.filter or [],
                      max_rows=args.max_rows)
    text = json.dumps(plan, indent=2, sort_keys=True)
    if args.out:
        os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(text + "\n")
        print(args.out)
    else:
        print(text)
    return 0 if plan["candidates"] else 1


def cmd_collect(args: argparse.Namespace) -> int:
    """Turn the analysis streams into the manuscript bridge.

    This is where R's output becomes something the writer may cite. Nothing
    reaches the bundle that did not come through a sealed stream, and nothing
    is written if any stream is unsealed.
    """
    try:
        manifest = FreezeManifest.load(args.manifest)
    except FileNotFoundError:
        print(f"no freeze manifest at {args.manifest}")
        return 1
    except FreezeViolation as exc:
        print(f"FREEZE VIOLATION\n{exc}")
        return 2

    scripts = analysis_scripts(args.root)

    # Bind the results to the analysis lock, not merely to the scripts as they
    # happen to be now. Hashing the code at collection time records what ran;
    # the lock records what was registered before anyone saw an outcome.
    lock_id = lock_hash = ""
    if os.path.exists(args.lock):
        try:
            lock = AnalysisLock.load(args.lock)
            lock.require(args.root, freeze=manifest)
        except AnalysisLockViolation as exc:
            print(f"ANALYSIS LOCK VIOLATION\n{exc}")
            return 2
        lock_id, lock_hash = lock.lock_id, lock.lock_hash
    elif args.require_lock:
        print(f"NO ANALYSIS LOCK at {args.lock}. Results collected without one are "
              "reproducible but not pre-specified; pass --no-require-lock only for "
              "exploratory work.")
        return 2

    bundle = new_bundle(
        manifest,
        script_hashes=hash_scripts([os.path.join(args.root, s) for s in scripts]),
        env_hash=environment_hash(args.root),
        analysis_lock_id=lock_id,
        analysis_lock_hash=lock_hash,
    )
    try:
        collect(args.streams, bundle, known_scripts=scripts, figure_base_dir=args.root)
    except EmitError as exc:
        print(f"ANALYSIS INCOMPLETE\n{exc}")
        return 2

    written = bundle.write(args.out)
    print(f"{len(bundle.records)} result(s), {len(bundle.figures)} figure(s)")
    for path in written:
        print(f"  wrote {path}")
    print(f"\nresults hash {bundle.results_hash[:16]}  under freeze {manifest.freeze_id}")
    print(f"analysis plan {lock_id or 'NOT LOCKED -- exploratory only'}")
    return 0


def cmd_publish(args: argparse.Namespace) -> int:
    """Publish a verified package transactionally to a run-specific Drive folder.

    Drive has no multi-file transaction.  The equivalent is a staging/run
    folder plus a completion marker written *last*.  A network failure can
    leave a partial folder, but consumers never treat it as authoritative
    because it lacks `_COMPLETE.json`.
    """
    folder = args.folder or os.environ.get("GDRIVE_RESULTS_FOLDER_ID", "")
    if not folder:
        print("no Drive folder id (pass --folder or set GDRIVE_RESULTS_FOLDER_ID)")
        return 1
    if not os.path.isdir(args.dir):
        print(f"nothing to publish: {args.dir} does not exist")
        return 1

    paths = [os.path.join(dp, f) for dp, _, fs in os.walk(args.dir) for f in fs]
    if not paths:
        print(f"nothing to publish: {args.dir} is empty")
        return 1

    run_id = args.run_id or os.environ.get("GITHUB_RUN_ID") or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    study = args.study or "study"
    mode = args.mode or "confirmatory"
    run_name = f"{study}__{mode}__run-{run_id}"
    manifest_files: list[dict[str, str | int]] = []

    try:
        client = _drive()
        run_folder = client.create_folder(run_name, folder)
        for path in sorted(paths):
            rel = os.path.relpath(path, args.dir).replace(os.sep, "__")
            digest = hash_file(path)
            file_id = client.upload(path, run_folder, name=rel)
            manifest_files.append({"name": rel, "sha256": digest,
                                   "bytes": os.path.getsize(path), "drive_id": file_id})
            print(f"  {rel:<44} {file_id}")

        completion = {
            "study": study,
            "run_id": str(run_id),
            "mode": mode,
            "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "files": manifest_files,
        }
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False,
                                         encoding="utf-8") as fh:
            json.dump(completion, fh, indent=2, sort_keys=True)
            marker = fh.name
        try:
            marker_id = client.upload(marker, run_folder, name="_COMPLETE.json")
        finally:
            os.remove(marker)
    except (CredentialError, DriveError, OSError) as exc:
        print(f"PUBLISH FAILED\n{exc}\n"
              f"Any partially uploaded run folder is non-authoritative because "
              "_COMPLETE.json was not written.")
        return 2
    print(f"\n{len(paths)} file(s) delivered to run folder {run_name} ({run_folder})")
    print(f"completion marker {marker_id}; this run is now authoritative for mode={mode}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="coscientist", description="CoScientist V4 deterministic core")
    p.add_argument("--registry", default="registry/sources.yaml")
    p.add_argument("--catalog", default="registry/lake_catalog.example.json")
    p.add_argument("--queue", default="state/tickets")
    p.add_argument("--policy", default="policy.yaml")
    p.add_argument("--state", default="state/engine.json")
    sub = p.add_subparsers(dest="cmd", required=True)

    d = sub.add_parser("doctor", help="verify sources and credentials")
    d.add_argument("--strict", action="store_true", help="exit non-zero on missing secrets")
    d.add_argument("--live", action="store_true",
                   help="actually exchange the refresh token; a present credential "
                        "is not a working one")
    d.set_defaults(func=cmd_doctor)

    r = sub.add_parser("registry", help="list sources, or find one by concept")
    r.add_argument("--concept")
    r.add_argument("--all", action="store_true", help="include inadmissible sources")
    r.set_defaults(func=cmd_registry)

    t = sub.add_parser("tickets", help="show the judgment queue digest")
    t.set_defaults(func=cmd_tickets)

    s = sub.add_parser("status", help="where the engine is and whether it is blocked")
    s.set_defaults(func=cmd_status)

    l = sub.add_parser("lake", help="list the legacy/local lake catalog")
    l.set_defaults(func=cmd_lake)

    gh = sub.add_parser("github-status", help="read GMS data-lake GitHub control-plane status")
    gh.add_argument("--runs", type=int, default=10)
    gh.set_defaults(func=cmd_github_status)

    ls = sub.add_parser("lake-sync", help="build the GMS catalog from SQLite manifests")
    ls.add_argument("--manifest-dir", help="local directory containing downloaded SQLite manifests")
    ls.add_argument("--manifest-folder", help="Drive folder id containing SQLite manifests")
    ls.add_argument("--out", default="state/gms_lake_catalog.json")
    ls.add_argument("--repo")
    ls.add_argument("--repo-sha")
    ls.add_argument("--direct-fetch-limit", type=int, default=2_000_000_000)
    ls.set_defaults(func=cmd_lake_sync)

    ld = sub.add_parser("lake-doctor", help="verify the GMS catalog is mechanically usable")
    ld.add_argument("--catalog", default="state/gms_lake_catalog.json")
    ld.add_argument("--require-repo-sha", action="store_true")
    ld.set_defaults(func=cmd_lake_doctor)

    lq = sub.add_parser("lake-query", help="query GMS metadata without browsing/downloading the lake")
    lq.add_argument("--catalog", default="state/gms_lake_catalog.json")
    lq.add_argument("--concept", action="append")
    lq.add_argument("--source")
    lq.add_argument("--domain")
    lq.add_argument("--availability")
    lq.add_argument("--route")
    lq.add_argument("--max-bytes", type=int)
    lq.add_argument("--text")
    lq.add_argument("--limit", type=int, default=50)
    lq.set_defaults(func=cmd_lake_query)

    gf = sub.add_parser("gms-fetch", help="fetch only frozen objects selected from the GMS catalog")
    gf.add_argument("--manifest", default="state/freeze.json")
    gf.add_argument("--catalog", default="state/gms_lake_catalog.json")
    gf.add_argument("--root-folder", help="Drive id of GLOBAL_MULTIDISCIPLINARY_MANAGEMENT_SCIENCE_DATA_LAKE")
    gf.add_argument("--dest", default="data")
    gf.set_defaults(func=cmd_gms_fetch)

    qp = sub.add_parser("gms-query-plan", help="plan the smallest curated/query slice for a research need")
    qp.add_argument("--catalog", default="state/gms_lake_catalog.json")
    qp.add_argument("--concept", action="append")
    qp.add_argument("--source")
    qp.add_argument("--text")
    qp.add_argument("--column", action="append")
    qp.add_argument("--filter", action="append")
    qp.add_argument("--max-rows", type=int)
    qp.add_argument("--out")
    qp.set_defaults(func=cmd_gms_query_plan)

    b = sub.add_parser("budget", help="show the cycle resource budget")
    b.add_argument("--cycle", default="cycle-001")
    b.add_argument("--study", help="required when any budget line is study-scoped")
    b.set_defaults(func=cmd_budget)

    f = sub.add_parser("freeze-verify", help="recompute and check the freeze hash")
    f.add_argument("--manifest", default="state/freeze.json")
    f.add_argument("--data-dir", help="re-hash datasets in this directory")
    f.set_defaults(func=cmd_freeze_verify)

    v = sub.add_parser("provenance", help="check every number in a manuscript")
    v.add_argument("manuscript")
    v.add_argument("--results", default="results/manuscript/ANALYSIS_MANIFEST.json")
    v.add_argument("--allow", nargs="*", help="numbers that are legitimately not results")
    v.add_argument("--freeze", default="state/freeze.json",
                   help="design the results must have been produced under")
    v.add_argument("--require-tokens", action="store_true",
                   help="strict: every result claim must cite [[R:token|field]]")
    v.add_argument("--literals", default="registry/literals.yaml",
                   help="registry backing [[L:id]] citations")
    v.add_argument("--no-freeze-check", action="store_true",
                   help="development only; forbidden in submission workflows")
    v.set_defaults(func=cmd_provenance)

    sf = sub.add_parser("state-fetch", help="bootstrap freeze + analysis lock from Drive")
    sf.add_argument("--study", required=True)
    sf.add_argument("--dest", default="state")
    sf.add_argument("--folder", help="Drive root containing one state folder per study")
    sf.set_defaults(func=cmd_state_fetch)

    fetch = sub.add_parser("fetch", help="download the frozen datasets from Drive")
    fetch.add_argument("--manifest", default="state/freeze.json")
    fetch.add_argument("--dest", default="data")
    fetch.add_argument("--folder", help="Drive folder id holding the datasets")
    fetch.set_defaults(func=cmd_fetch)

    col = sub.add_parser("collect", help="assemble analysis streams into the bridge")
    col.add_argument("--streams", default="results/streams")
    col.add_argument("--out", default="results/manuscript")
    col.add_argument("--manifest", default="state/freeze.json")
    col.add_argument("--root", default=".", help="repo root, for script hashing")
    col.add_argument("--lock", default="state/analysis_lock.json")
    col.add_argument("--no-require-lock", dest="require_lock", action="store_false",
                     help="exploratory only: collect without a pre-registered analysis plan")
    col.set_defaults(func=cmd_collect, require_lock=True)

    al = sub.add_parser("analysis-lock", help="register the analysis code before outcomes")
    al.add_argument("--freeze", default="state/freeze.json")
    al.add_argument("--out", default="state/analysis_lock.json")
    al.add_argument("--root", default=".")
    al.add_argument("--acknowledge-io", action="store_true",
                    help="declare unsanctioned reads/network/seeding rather than removing them")
    al.set_defaults(func=cmd_analysis_lock)

    av = sub.add_parser("analysis-verify", help="check the code against the analysis lock")
    av.add_argument("--lock", default="state/analysis_lock.json")
    av.add_argument("--freeze", default="state/freeze.json")
    av.add_argument("--root", default=".")
    av.set_defaults(func=cmd_analysis_verify)

    pl = sub.add_parser("plan", help="the analysis scripts to run, in order")
    pl.add_argument("--root", default=".")
    pl.set_defaults(func=cmd_plan)

    pub = sub.add_parser("publish", help="upload the bridge to Drive")
    pub.add_argument("--dir", default="results")
    pub.add_argument("--folder", help="Drive folder id to deliver into")
    pub.set_defaults(func=cmd_publish)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
