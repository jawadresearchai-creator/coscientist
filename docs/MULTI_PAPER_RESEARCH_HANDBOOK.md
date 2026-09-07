# Multi-Paper Research Handbook — CoScientist V4.7.0

## Purpose

CoScientist may operate multiple Management Science papers concurrently. Concurrency is achieved through strict paper namespaces, not by sharing one mutable Director or scientific state file.

## Canonical Drive layout

```text
MANAGEMENT_SCIENCES_COSCIENTIST_V4/
  state/
    paper_registry.json
    <paper_id>/
      paper_state.json
      director.json
      director_answer.json
      director_action.json
      receipts/
      freeze.json
      analysis_lock.json
      ...datasets, audits, results, manuscript artifacts...
```

The root registry indexes papers and may nominate a focus paper. Focus is only a default for interfaces; it is never a lock.

## Concurrency guarantees

- Multiple papers may be ACTIVE, REPAIR or PAUSED simultaneously.
- Every paper has its own lifecycle stage.
- Every paper has its own pending Director action.
- Every paper has its own outcome/freeze/AnalysisLock boundary.
- Every paper has its own receipt and results namespace.
- Switching conversational focus does not withdraw another paper.
- A workflow must receive or resolve an explicit `paper_id` before writing scientific state.

## Paper-local lifecycle

Each paper follows the mature lifecycle independently:

`SELECTED -> DEVELOPING -> DATA_FEASIBLE -> DESIGN_READY -> FROZEN -> ANALYZING -> RESULTS_COMPLETE -> MANUSCRIPT -> FINAL_AUDIT -> SUBMISSION_READY`

`RETIRED` and `USER_WITHDRAWN` are terminal for that paper only.

## Director operation

Use `src/coscientist/director_multi.py` / `python -m coscientist.director_multi`.

Example status:

```bash
python -m coscientist.director_multi \
  --registry state/paper_registry.json \
  --paper-id MS-ASTRA-REVALUE-2026 \
  status
```

The Director is still deterministic and still validates reasoning-plane answers. The only architectural change is that Director state is paper-scoped.

## Creating another paper

Create a new Topic Charter and run:

```bash
python -m coscientist.multi_paper \
  --registry state/paper_registry.json \
  create --charter new_charter.json
```

No existing paper needs to be completed or withdrawn.

## Focus

Use focus only to control default routing:

```bash
python -m coscientist.multi_paper \
  --registry state/paper_registry.json \
  focus --paper-id MS-ASTRA-REVALUE-2026
```

This does not change any other paper's status.

## Legacy compatibility

The old root files:

- `state/single_paper.json`
- `state/director.json`
- `state/director_answer.json`

are retained as compatibility/history artifacts. Migrate the current legacy paper with:

```bash
python -m coscientist.multi_paper \
  --registry state/paper_registry.json \
  migrate-legacy
```

`SinglePaperState` remains in the codebase as the paper-local state machine. It should not be interpreted as a global one-paper policy.

## Workflow isolation

Every data, analysis and manuscript workflow must:

1. receive an explicit `paper_id` or resolve the registry focus only if the user did not specify one;
2. read only that paper's state/Director inputs;
3. write only to that paper's Drive folder;
4. include `paper_id` in receipts/status files;
5. never advance another paper as a side effect.

## Astra migration note

`MS-ASTRA-REVALUE-2026` was historically marked `USER_WITHDRAWN` solely because the old global one-paper model required withdrawal before another topic could become active. Under V4.7.0 that historical withdrawal remains archival evidence, but the owner's explicit September 7, 2026 instruction to support multiple simultaneous papers authorizes Astra to be registered again for continued data-repair/research work without terminating `MS-AI-PROC-COMP-2025`.
