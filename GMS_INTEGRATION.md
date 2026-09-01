# CoScientist ↔ GMS Data Lake Integration (v4.2.0)

## Contract

**GMS data lake owns acquisition and curation infrastructure.**
**CoScientist owns scientific selection, freezing, analysis, and reporting.**

CoScientist never searches the whole Drive lake for an answer. It downloads
small control manifests, queries metadata, selects the minimum required object
or curated query output, freezes its SHA-256, and only then retrieves those
specific bytes.

### Control plane

- GMS GitHub repo: ingestion code, registry, Actions/watchdog, status.
- CoScientist GitHub repo: scientific kernel, workflows, analysis code.
- `src/coscientist/github.py` is read-only.

### Data plane

- Google Drive `00_CONTROL`: manifests/catalog/checkpoints.
- `01_RAW_IMMUTABLE`: source bytes owned by the lake.
- `02_CURATED`: query/curation outputs.
- CoScientist state/results live under separate Drive roots.

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

## Query-first rule

1. Search the catalog, never raw Drive.
2. Prefer an existing curated object.
3. Otherwise request/materialize only the required columns/rows/years.
4. If a raw object is small and explicitly `DIRECT_FETCH`, it may be frozen and fetched.
5. If `QUERY_LAYER_REQUIRED`, direct fetch is mechanically refused.
6. Freeze the query output's bytes before confirmatory analysis.

This allows the partially completed lake to be useful immediately without
waiting for every domain or the OpenAlex backfill to finish.
