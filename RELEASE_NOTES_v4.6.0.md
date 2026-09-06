# CoScientist v4.6.0 — Integrity & Autonomy Closure

## Purpose

V4.6 keeps the one-paper Research Director and v4.5 reasoning-role architecture, but closes lifecycle gates that previously trusted reasoning-plane assertions for mechanically knowable facts.

## Added

- `src/coscientist/integrity.py`: write-once, tamper-evident lifecycle receipts.
- `src/coscientist/integrity_cli.py`: deterministic receipt generation for datasets, power, analysis, and final-audit mechanical evidence.
- `src/coscientist/study_state_drive.py`: one immutable per-paper Drive artifact folder under the canonical state root.
- `src/coscientist/director_v46.py`: integrity facade over v4.5 role/skill routing and the v4.4 scientific state machine.
- `GUARANTEES_INTEGRITY.yaml` and `tests/test_integrity_v46.py`.

## Hardened gates

- `DESIGN_CLOSURE` no longer trusts LLM-supplied SHA-256 values. Dataset hashes are injected from a canonical `DATASET_SET` receipt.
- `DESIGN_CLOSURE` requires a deterministic `POWER` receipt with `powered=true`.
- `PRE_FREEZE_AUDIT` PASS is conjunctive across novelty, measurement, identification, power, and access/licence/ethics.
- Reasoning-declared external-source verification cannot close v4.6 data feasibility; essential external data must be materialised/registered into the GMS lake first.
- final PASS requires a `FINAL_AUDIT_EVIDENCE` receipt for mechanically knowable numeric-provenance and reproducibility dimensions.

## Mechanical autonomy closure

The scheduled cycle now uses `director_v46` and can:

1. create the `FreezeManifest` after hostile pre-freeze PASS;
2. persist `freeze.json` write-once under the active paper's Drive state folder;
3. create and persist `analysis_lock.json`;
4. dispatch `analysis.yml` only after those artifacts exist;
5. leave the paper `FROZEN` if dispatch fails;
6. mark the paper `ANALYZING` only after successful dispatch;
7. consume only a canonical `analysis_receipt.json` before moving to `RESULTS_COMPLETE`.

`analysis.yml` no longer defaults to historical study `C705`. It requires an explicit active paper id and exact Git SHA, checks out that commit, verifies the AnalysisLock before outcome access, publishes verified confirmatory results, then writes the canonical analysis receipt.

## Security improvement

The credential-bearing `cycle.yml` no longer has `contents: write` and no longer pushes heartbeat commits into the public repository. It has `contents: read` plus `actions: write` solely to dispatch the existing analysis workflow.

## Deliberately not claimed by v4.6.0

- GitHub branch/ruleset administration still needs repository-owner configuration because the current connector lacks administration permission.
- third-party GitHub Actions are not yet commit-SHA pinned.
- Python dependency resolution is not yet locked to an exact resolver lockfile.
- transactional/hash-chained mutable state (`single_paper.json` + `director.json`) is a subsequent hardening step.
- study-type-specific design adapters remain future work.

These are important improvements, but they do not block the immediate pre-freeze integrity closure provided by v4.6.0.
