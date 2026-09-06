# CoScientist v4.4.0 — Persistent Single-Paper Research Director

## Purpose

V4.4 converts the v4.3 single-paper constitution into a restart-safe practical
orchestration layer. The system still develops exactly one Management Science
paper at a time, but it can now persist one next research action across
GitHub-hosted runner boundaries and accept one structured reasoning-plane answer
without recreating topic discovery.

## Added

- `src/coscientist/director.py`
  - one pending action at a time;
  - bounded fresh discovery with a maximum shortlist of three;
  - exactly one Topic Charter admission;
  - literature/theory closure for the selected paper;
  - lake-first data feasibility with verified free-authoritative external gaps;
  - same-paper design repair;
  - hostile pre-freeze PASS/REPAIR/GENUINE_BLOCKER boundary;
  - real mechanical `FreezeManifest` creation before `FROZEN`;
  - analysis/result/manuscript/final-audit routing;
  - conjunctive final submission audit;
  - stale-answer idempotence and new-paper context reset.

- `src/coscientist/director_drive.py`
  - canonical Drive persistence for `director.json`;
  - one overwriteable `director_answer.json` inbox;
  - duplicate canonical-name refusal;
  - bounded control-file size and immediate JSON validation.

- `GUARANTEES_DIRECTOR.yaml`
  - ten executable Director guarantees mapped to named tests.

- `tests/test_director.py`
  - bounded discovery, discovery lock, lake tier priority, same-paper repairs,
    exact dataset hash/power requirements, real freeze creation, stale-answer
    safety, final-audit conjunction and Drive-inbox uniqueness.

## Workflow change

`cycle.yml` is now the persistent Research Director heartbeat:

```text
refresh lake
  -> pull single_paper.json
  -> pull director.json
  -> pull director_answer.json if present
  -> apply only a matching action_id
  -> ensure one next action
  -> push canonical paper + Director state
```

State is pushed only when every preceding pull/apply/ensure step succeeds. This
prevents a transient Drive or answer-validation failure from overwriting newer
canonical state.

The workflow also runs immediately when a v4.4 Director/cycle implementation is
merged to `main`; heartbeat-only bot commits do not match that path filter.

## What remains unchanged

- one active paper only;
- no reserve topics, portfolio or historical-candidate revival;
- evolve/repair before retirement;
- genuine-blocker-only retirement;
- GMS lake owns reusable ingestion;
- CoScientist owns scientific selection/materialisation;
- design freeze remains write-once and hash-bound;
- analysis lock remains separate from design freeze;
- confirmatory outcomes remain behind both locks;
- strict result/prose provenance remains mandatory;
- scientific state and results remain in private Google Drive, not public Git.

## Migration state

V4.4 is designed to start from the clean v4.3 `NO_ACTIVE_PAPER` state. The first
Director action should therefore be one bounded `DISCOVER_TOPIC` request based
on the **current** lake and **current** literature, with no historical candidate
lineage imported.
