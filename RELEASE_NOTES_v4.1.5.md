# CoScientist v4.1.5 — Execution-Integrity Closure

This release closes the concrete boundary leaks found in the independent v4.1.4 audit without adding G2 or other feature growth.

## Corrections

1. Clean GitHub runners now bootstrap `freeze.json` and `analysis_lock.json` from a study-specific Google Drive state folder before outcome access.
2. The analysis lock is verified before any outcome dataset is downloaded.
3. Frozen Candidate scientific containers are recursively immutable (`constructs` and `concepts` become tuples).
4. Python result provenance is runner-derived, matching the R contract.
5. Stream filename, records, and completion sentinel must all identify the same producer.
6. The analysis lock stores and re-verifies the actual executable plan; R/Python stages use global numeric order and Stan files are hashed model inputs, not direct runner stages.
7. `analysis-verify` fails closed when the design freeze is missing.
8. Analysis-lock chronology metadata is protected by a second receipt hash.
9. Drive publication uses run-specific folders and writes `_COMPLETE.json` last; partial uploads are non-authoritative.
10. Loose-provenance workflow runs publish only to a separate development destination.
11. G3 no longer assumes missing source granularity/coverage metadata means compatibility; essential uncertainty yields `DEFER`.
12. Publish-package file hashing is streamed rather than reading an entire artifact into RAM.

## Verification

- 264 tests collected.
- 254 passed in the current sandbox.
- 10 R-dependent tests skipped because R/Rscript is not installed in the current sandbox.
- YAML and shell workflow validation pass.
- Additional adversarial reproductions confirm frozen-container mutation, Python producer spoofing, missing-freeze analysis verification, execution ordering, and unknown G3 suitability are closed.

## Still deliberately not built

G2 novelty retrieval, candidate generation/tournament, the full cycle orchestrator, G4B Monte Carlo power, journal genome, reviewer court, and a standalone Stan runner.
