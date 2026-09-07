# CoScientist V4.7.0 — multi-paper Management Science research engine

CoScientist is a deterministic research-governance and analysis kernel for **multiple independent Management Science papers at the same time**. Reasoning models may perform judgment, literature interpretation, adversarial review and prose through explicit handoffs, but `src/coscientist/` never calls an LLM directly.

## Operating rule

There is no global one-paper lock.

Every paper owns its own lifecycle state, Director state, answer inbox, freeze, AnalysisLock, receipts, datasets, results and manuscript. Multiple papers may be `ACTIVE`, `REPAIR` or `PAUSED` concurrently.

A root registry indexes them:

```text
state/paper_registry.json
state/<paper_id>/paper_state.json
state/<paper_id>/director.json
state/<paper_id>/director_answer.json
```

A `focus_paper_id` is only a default routing/UI convenience. Changing focus does not pause, withdraw, retire or replace any other paper.

## Paper-local lifecycle

Each paper independently follows:

```text
SELECTED
  -> DEVELOPING
  -> DATA_FEASIBLE
  -> DESIGN_READY
  -> FROZEN
  -> ANALYZING
  -> RESULTS_COMPLETE
  -> MANUSCRIPT
  -> FINAL_AUDIT
  -> SUBMISSION_READY
```

A paper may independently become `USER_WITHDRAWN` by explicit owner instruction or `RETIRED` for a genuine scientific blocker. Those transitions affect only that paper.

## Multi-paper router

Use the paper registry and an explicit paper ID:

```bash
python -m coscientist.director_multi \
  --registry state/paper_registry.json \
  --paper-id MS-ASTRA-REVALUE-2026 \
  status
```

To list or change registry focus:

```bash
python -m coscientist.multi_paper --registry state/paper_registry.json list
python -m coscientist.multi_paper --registry state/paper_registry.json \
  focus --paper-id MS-ASTRA-REVALUE-2026
```

The mature `SinglePaperState` implementation remains as the **paper-local** lifecycle state machine for backward compatibility. Its name no longer means that the whole CoScientist may operate only one paper.

## Legacy compatibility

The old root files:

```text
state/single_paper.json
state/director.json
state/director_answer.json
```

are retained as compatibility/history artifacts. New work uses `paper_registry.json` and paper-scoped paths.

A non-destructive migration utility copies the legacy current paper into its own namespace while leaving those original files untouched.

## Few roles, many skills

There are three architectural roles:

1. **Research Director** — deterministic authority; not an LLM agent.
2. **Scientific Reasoning** — normal LLM context for literature, theory, design reasoning, manuscript drafting and ordinary repair.
3. **Independent Audit** — fresh adversarial LLM context for pre-freeze and final audit.

The current skills are Humanizer v2.0, LiteratureTheory v1.0, StudyDesignReasoner v1.0, HostileReviewer v1.0, ManuscriptWriter v1.0, CitationIntegrity v1.0, and FinalAuditReasoner v1.0.

## Director rule

There is at most one pending Director action **per paper**, not one globally.

Switching from Paper A to Paper B does not withdraw or terminate Paper A. Each Director action is routed through that paper's own `director.json` and `director_answer.json`.

## Integrity rule

**Reasoning may propose, interpret and audit. Mechanically knowable facts must come from deterministic receipts.**

Write-once lifecycle receipts include:
- `DATASET_SET` — exact dataset identities and SHA-256 values;
- `POWER` — deterministic pre-period power/MDE result;
- `ANALYSIS` — freeze, AnalysisLock, exact Git SHA, result-manifest hash, workflow run and verified publication;
- `FINAL_AUDIT_EVIDENCE` — manuscript hash plus mechanically verified numeric provenance and reproducibility evidence.

Receipts are paper-scoped.

## Data rule — lake first

For each required construct use:
1. `03_RESEARCH` research-ready mart;
2. `02_CURATED` object;
3. `01_RAW_IMMUTABLE` / query-layer materialisation;
4. admissible official external source materialised into the lake;
5. defensible pre-freeze evolution of the same paper;
6. genuine essential-data blocker.

Do not reacquire data already held adequately. Materialize the smallest useful paper-specific extract and hash it before confirmatory design closure.

## Freeze and analysis autonomy

Each paper has its own independent freeze/analysis sequence:

```text
PRE_FREEZE PASS
  -> CREATE_FREEZE
  -> persist state/<paper_id>/freeze.json
  -> create AnalysisLock
  -> persist state/<paper_id>/analysis_lock.json
  -> dispatch analysis workflow for that paper
  -> ANALYZING
  -> verified analysis receipt
  -> RESULTS_COMPLETE
```

Paper A may be frozen while Paper B remains in data feasibility.

## Humanizer boundary

Humanizer is mandatory for manuscript drafting/revision/final polish but controls expression only. It must not independently change scientific meaning, methods, design, numbers, result tokens, citations, evidence support, claim strength, limitations or frozen scientific state.

## GitHub / Drive boundary

GitHub contains code, tests, workflows, role contracts, skills and registry/schema logic. Google Drive contains canonical `paper_registry.json`, paper-specific state, Director state, immutable research artifacts, results and manuscripts.

Scientific workflows must be explicitly paper-scoped and must not write another paper's state as a side effect.

## Install and test

```bash
pip install -e ".[dev]"
pytest -q
```

The V4.7.0 tree currently collects **414 tests**. The suite covers package integrity, paper-local lifecycle compatibility, multi-paper concurrency/isolation, Director routing, explicit owner withdrawal, freeze/AnalysisLock/provenance contracts, Astra V3 repair invariants, current-period cross-taxonomy SEC fact selection, literal security-identifier preservation (including ticker `NA`), literal-reader recursion protection, and adversarial integrity gates.

## ChatGPT operation

In every new research-operating chat, read `AGENTS.md`, `docs/CHATGPT_START_HERE.md`, and Drive `state/paper_registry.json`. Select the explicit `paper_id` requested by the user, then read only that paper's state/Director inputs and preserve all other paper state unchanged.

## Scope

The engine is optimized for reproducible secondary/open-data quantitative Management Science research: archival, panel, quasi-experimental, event-study and related designs. Multiple papers may use different designs and be at different lifecycle stages concurrently without weakening paper-level outcome locks or the judgment-vs-enforcement boundary.