# CoScientist v4.2.0 — GMS Lake Integration

This release does not duplicate provider ingestion. It makes the existing
Global Multidisciplinary Management-Science data lake the single data backend
for CoScientist.

## Built

- `gms_lake.py`: local SQLite-manifest reader, dynamic catalog, partial-status
  model, query/direct-fetch routing, frozen-object resolver.
- `github.py`: read-only GMS repository HEAD and workflow status.
- Drive OAuth compatibility with the rclone token JSON already used by GMS.
- Segment-by-segment Drive path traversal; no recursive lake crawl.
- `lake-sync`, `lake-doctor`, `lake-query`, `gms-query-plan`, `gms-fetch`,
  `github-status` CLI commands.
- G3 understands `QUERY_LAYER_REQUIRED` and defers rather than attempting a
  giant raw fetch.
- OpenAlex raw is always query-layer-required.
- Other objects above the direct-fetch ceiling are query-layer-required.
- Existing `02_CURATED` paths are preferred by query planning.
- Analysis workflow now verifies the analysis lock before outcome access,
  refreshes the GMS catalog, and retrieves only frozen GMS objects.
- Doctor/cycle workflows refresh and validate the catalog.

## Query-first rule

A research question should not trigger a raw-lake download. The system now
works in this order:

1. query the compact catalog;
2. prefer an existing curated/query output;
3. request/materialize the minimum rows/columns/time range if needed;
4. freeze that output's SHA-256;
5. fetch only the frozen bytes for confirmatory analysis.

The server-side lake query/curation worker itself is still owned by the GMS
lake and is not fabricated inside CoScientist. Until a source has such a query
route, `QUERY_LAYER_REQUIRED` is a mechanical DEFER rather than permission to
pull its raw snapshot.

## Deliberately unchanged

Design freeze semantics, analysis lock, result bridge, provenance, G1/G4A,
ticket schema, source-specific GMS ingestion and watchdog logic.

## Still unbuilt after this release

G2 novelty retrieval, autonomous candidate generation/tournament/evolution,
full cycle orchestration, executable ticket workers, G4B Monte Carlo power,
journal genome, reviewer court, and the GMS server-side query/curation worker.
