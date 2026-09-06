# CoScientist ↔ GMS Data Lake Integration (v4.3.0)

## Contract

**The GMS data lake owns reusable acquisition and curation infrastructure.**
**CoScientist owns scientific selection, paper-specific materialisation,
freezing, analysis and reporting.**

The v4.3 operating model adds one further boundary: CoScientist works on exactly
one active paper. The lake may continue broad ingestion, but an admitted paper
does not reopen topic discovery merely because new datasets arrive.

## Control plane

- GMS GitHub repo: ingestion code, source registry, Actions/watchdog, manifests.
- CoScientist GitHub repo: one-paper scientific kernel, workflows, analysis code.
- `src/coscientist/github.py` remains read-only toward the lake repository.
- The canonical active-paper state is one Drive file: `single_paper.json`.

## Data plane

- Google Drive `00_CONTROL`: lake manifests/catalog/checkpoints.
- `01_RAW_IMMUTABLE`: source bytes owned by the lake.
- `02_CURATED`: normalized/curated objects and query outputs.
- `03_RESEARCH`: research-ready marts when available.
- CoScientist scientific state/results/manuscripts live under separate private
  Drive roots, not in GitHub.

## Lake-first scientific resolution

For every construct required by the active paper, use this order:

```text
03_RESEARCH mart
  -> 02_CURATED object
  -> 01_RAW_IMMUTABLE / query-layer materialisation
  -> admissible official external source
  -> defensible pre-freeze evolution of the same paper
  -> genuine essential-data blocker
```

A suitable exact lake holding is preferred before any external reach probe.
External acquisition is gap filling, not duplicate ingestion.

[GUARANTEE: G3_IS_LAKE_FIRST]

The older safety rules remain active:

- wrong or unknown granularity/coverage is not assumed suitable;
- every admissible external fallback is considered when the lake lacks an
  exact route;
- identity-gated and paid sources remain inadmissible by default;
- source access and redistribution rights are separate;
- BigQuery/query-native sources are never queried blindly.

[GUARANTEE: UNKNOWN_SOURCE_SUITABILITY_IS_NOT_ASSUMED]
[GUARANTEE: G3_CHECKS_SUITABILITY_NOT_JUST_REACHABILITY]
[GUARANTEE: G3_EXHAUSTS_EXTERNAL_ROUTES]

## Query/materialisation rule

1. Search the compact catalog, never the raw Drive tree.
2. Prefer a research-ready or curated object that exactly fits the construct.
3. If only a raw/query-native object exists, materialise the smallest useful
   paper-specific slice: required columns, rows, entities and periods only.
4. If an admissible external source is genuinely required, fetch only the
   missing scope and write back according to its archival policy.
5. Freeze the exact materialised bytes and SHA-256 before confirmatory analysis.
6. Fetch only those frozen bytes during the confirmatory run.

This keeps the multi-terabyte lake useful without forcing a runner to download
raw snapshots that the paper never needs.

## Commands

```bash
coscientist github-status
coscientist lake-sync --out state/gms_lake_catalog.json
coscientist lake-doctor --catalog state/gms_lake_catalog.json
coscientist lake-query --concept tax --availability AVAILABLE
coscientist gms-query-plan --concept tax --column firm_id --column year \
  --filter 'year<=2024' --out state/query_plan.json
coscientist gms-fetch --manifest state/freeze.json \
  --catalog state/gms_lake_catalog.json --dest data
```

## Active-paper state versus lake state

The lake catalog can change continuously as ingestion completes. The active
paper state cannot be silently replaced by that changing catalog. Before
freeze, new lake availability may support an explicit evolution of the same
paper. After freeze, dataset identity is locked and any scientific change is
handled as a post-freeze deviation/exploratory route rather than rewriting the
confirmatory study.

[GUARANTEE: PRE_FREEZE_EVOLUTION_STOPS_AT_FREEZE]
[GUARANTEE: DATASET_DRIFT_BLOCKS_ANALYSIS]

## Failure semantics

A transport/API/query-layer failure is infrastructure work. It does not retire
the paper. Retirement for data is allowed only after the lake, materialisation,
admissible external acquisition and defensible pre-freeze evolution routes are
exhausted for an **essential** construct.

[GUARANTEE: RETIREMENT_REQUIRES_GENUINE_BLOCKER]
