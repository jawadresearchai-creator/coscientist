# CoScientist V4.6 — single-paper Management Science research engine

CoScientist is a deterministic research-governance and analysis kernel for **one Management Science paper at a time**. Reasoning models may perform judgment, literature interpretation, adversarial review and prose through explicit handoffs, but `src/coscientist/` never calls an LLM directly.

## Operating rule

Exactly one paper may be scientifically active. Broad topic discovery is allowed only when there is no active paper, or when the current paper is `SUBMISSION_READY` or has been `RETIRED` for a genuine blocker. Ordinary execution friction triggers same-paper repair, not replacement.

```text
NO_ACTIVE_PAPER
  -> SELECTED
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

`RETIRED` is permitted only for a genuine scientific blocker: direct scoop with no residual contribution, essential-data/measurement impossibility, identification impossibility, fundamental power failure, legal/ethical impossibility, or fundamental construct failure.

## Three control records

```text
single_paper.json       scientific lifecycle and one active Topic Charter
director.json           one persistent next action + compact phase records
director_answer.json    one overwriteable reasoning-plane answer inbox
```

A stale answer is a no-op. A runner restart does not recreate topic discovery. A new paper resets Director records instead of inheriting historical candidate state.

## Few roles, many skills

There are only three architectural roles:

1. **Research Director** — deterministic authority; not an LLM agent.
2. **Scientific Reasoning** — normal LLM context for literature, theory, design reasoning, manuscript drafting and ordinary repair.
3. **Independent Audit** — fresh adversarial LLM context for pre-freeze and final audit.

The current skills are Humanizer v2.0, LiteratureTheory v1.0, StudyDesignReasoner v1.0, HostileReviewer v1.0, ManuscriptWriter v1.0, CitationIntegrity v1.0, and FinalAuditReasoner v1.0.

Reasoning answers declare `execution_role` and exact `skills_used` versions. Independent-audit answers also require `fresh_context_attested: true`.

## V4.6 integrity rule

**Reasoning may propose, interpret and audit. Mechanically knowable facts must come from deterministic receipts.**

V4.6 adds write-once, tamper-evident lifecycle receipts:

- `DATASET_SET` — exact GMS dataset identities and SHA-256 values;
- `POWER` — deterministic pre-period power/MDE result bound to the input bytes and parameters;
- `ANALYSIS` — freeze, AnalysisLock, exact Git SHA, result-manifest hash, workflow run and verified publication;
- `FINAL_AUDIT_EVIDENCE` — manuscript hash plus mechanically verified numeric provenance and reproducibility evidence.

A receipt hash detects later mutation. Receipt provenance comes from the controlled deterministic workflow and immutable per-paper Drive write path; an LLM-supplied hash is not accepted as a substitute.

### Hardened gates

- `DESIGN_CLOSURE` ignores reasoning-supplied dataset hashes and power PASS values; it injects them from canonical receipts.
- `PRE_FREEZE_AUDIT` PASS is conjunctive across novelty, measurement, identification, power and access/licence/ethics.
- reasoning-declared external-source verification cannot close essential data feasibility; the source must first be materialised/registered into the GMS lake.
- `RESULTS_COMPLETE` requires the canonical analysis receipt bound to the active freeze and AnalysisLock.
- final PASS consumes `FINAL_AUDIT_EVIDENCE` for mechanically knowable audit dimensions.

Use the v4.6 facade for operating commands:

```bash
python -m coscientist.director_v46 --state state/single_paper.json \
  --director state/director.json --catalog state/gms_lake_catalog.json status

python -m coscientist.director_v46 --state state/single_paper.json \
  --director state/director.json --catalog state/gms_lake_catalog.json \
  ensure --action-out state/director_action.json

python -m coscientist.director_v46 --state state/single_paper.json \
  --director state/director.json --catalog state/gms_lake_catalog.json \
  --receipt-dir state/receipts apply --answer state/director_answer.json
```

## Per-paper immutable Drive state

The mutable orchestration projections stay in the canonical state root. Every admitted paper also receives one unique child folder containing write-once lifecycle artifacts, for example:

```text
state/
  single_paper.json
  director.json
  director_answer.json
  MS-LB-ENTRY-2026/
    dataset_receipt.json
    power_receipt.json
    freeze.json
    analysis_lock.json
    analysis_receipt.json
    final_audit_evidence.json
```

Different bytes cannot replace an existing immutable artifact in place.

## Data rule — lake first

The Global Management Science data lake owns ingestion; CoScientist owns scientific selection. For each required construct the order is:

1. `03_RESEARCH` research-ready mart;
2. `02_CURATED` object;
3. `01_RAW_IMMUTABLE` / query-layer materialisation;
4. admissible official external source materialised into the lake;
5. defensible pre-freeze evolution of the same paper;
6. genuine essential-data blocker.

Do not reacquire data already held adequately. Large/query-native sources are materialised as the smallest useful extract and that extract is hashed before confirmatory design closure.

## Freeze and analysis autonomy

The scheduled cycle can now close the mechanical handoffs itself:

```text
PRE_FREEZE PASS
  -> CREATE_FREEZE
  -> persist freeze.json
  -> create AnalysisLock
  -> persist analysis_lock.json
  -> dispatch analysis.yml
  -> ANALYZING
  -> wait for analysis_receipt.json
  -> RESULTS_COMPLETE
```

If freeze creation, lock creation or workflow dispatch fails, the paper does not advance past the last verified state.

`analysis.yml` has no historical default study. It requires the active paper id and the exact Git SHA whose code was locked, checks out that exact commit, verifies the AnalysisLock before outcome access, fetches only frozen data, executes the locked plan, assembles the ResultsBridge, publishes verified confirmatory results to Drive, and writes the immutable analysis receipt.

## Two locks

The design freeze fixes **which scientific question/design**. The analysis lock fixes **which code/environment/plan** before confirmatory outcome access.

```text
Drive state -> exact locked Git commit -> analysis-lock verify
           -> frozen data -> freeze-verify -> R/Python/Stan drivers
           -> sealed streams -> ResultsBridge -> verified publication
           -> analysis receipt
```

Scientific state and results live in Google Drive, never Git. Code, tests, workflows, role contracts and skills live in GitHub.

## Humanizer boundary

Humanizer is mandatory for manuscript drafting/revision/final polish but controls expression only. It must not independently change scientific meaning, methods, design, numbers, result tokens, citations, evidence support, claim strength, limitations or frozen scientific state.

## Security boundary

The Drive-credential-bearing research cycle no longer has `contents: write` and no longer pushes heartbeat commits into the public repository. It uses `contents: read` plus `actions: write` only to dispatch the existing confirmatory analysis workflow.

Repository rulesets, commit-SHA pinning of third-party Actions, and exact Python resolver locking are additional hardening items documented in `RELEASE_NOTES_v4.6.0.md`.

## Install and test

```bash
pip install -e ".[dev]"
pytest -q
```

The v4.6 tree currently collects **353 tests**. The suite includes package-import integrity, role/skill routing, one-paper lifecycle, freeze/AnalysisLock/provenance contracts, and adversarial v4.6 integrity-gate tests. `GUARANTEES.yaml` remains the executable contract registry, extended by versioned `GUARANTEES*.yaml` files.

## ChatGPT operation

Use one ChatGPT Project named **Management Science CoScientist**. GitHub/Drive remain authoritative over chat memory. In every new chat, read `AGENTS.md`, `docs/REASONING_LAYER.md`, `single_paper.json`, and `director.json`, then load the role contract and exact skills named by the pending action's execution profile.

If an action requires `INDEPENDENT_AUDIT`, execute it in a fresh chat/context that reconnects to the same canonical state; do not create another CoScientist or another paper.

## Scope

The current engine is optimized for reproducible secondary/open-data quantitative Management Science research: archival, panel, quasi-experimental, event-study and related designs. Future study-type adapters may extend that scope without weakening the one-active-paper rule or the judgment-vs-enforcement boundary.
