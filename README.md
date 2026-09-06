# CoScientist V4.6.1 — single-paper Management Science research engine

CoScientist is a deterministic research-governance and analysis kernel for **one Management Science paper at a time**. Reasoning models may perform judgment, literature interpretation, adversarial review and prose through explicit handoffs, but `src/coscientist/` never calls an LLM directly.

## Operating rule

Exactly one paper may be scientifically active. The rule prevents parallel papers; it does **not** trap the owner in a topic.

Broad topic discovery is allowed when there is no active paper, the current paper is `SUBMISSION_READY`, it is `RETIRED` for a genuine scientific blocker, or the owner explicitly marks it `USER_WITHDRAWN`.

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

At any non-terminal active stage:
  explicit owner instruction -> USER_WITHDRAWN -> fresh discovery
```

`RETIRED` remains reserved for genuine scientific impossibility. `USER_WITHDRAWN` is a separate owner priority/preference decision and does not require inventing a blocker.

If the owner explicitly names the next topic, that direction is carried into the next Director discovery action. The system evaluates and refines that topic rather than silently substituting an unrelated one.

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

## Owner withdrawal

An explicit owner instruction such as "stop this topic", "I don't want to continue this paper", or "switch to X instead" authorizes deterministic withdrawal.

Withdrawal:
- is valid at any non-terminal active stage;
- is not a scientific failure;
- clears the stale pending Director action through reconciliation;
- releases the one-paper lock;
- may carry `owner_next_topic_direction` into the next discovery action;
- never creates two active papers.

A reasoning model must not infer owner withdrawal from null results, difficult repairs, or ordinary failure. It requires an explicit owner instruction.

The v4.6.1 facade is:

```bash
python -m coscientist.director_v461 --state state/single_paper.json \
  --director state/director.json --catalog state/gms_lake_catalog.json status

python -m coscientist.director_v461 --state state/single_paper.json \
  --director state/director.json --catalog state/gms_lake_catalog.json \
  withdraw --reason "Owner changed research priority" \
  --next-topic "event study of a new AI model release"
```

## V4.6 integrity rule

**Reasoning may propose, interpret and audit. Mechanically knowable facts must come from deterministic receipts.**

Write-once lifecycle receipts include:
- `DATASET_SET` — exact GMS dataset identities and SHA-256 values;
- `POWER` — deterministic pre-period power/MDE result;
- `ANALYSIS` — freeze, AnalysisLock, exact Git SHA, result-manifest hash, workflow run and verified publication;
- `FINAL_AUDIT_EVIDENCE` — manuscript hash plus mechanically verified numeric provenance and reproducibility evidence.

### Hardened gates

- `DESIGN_CLOSURE` ignores reasoning-supplied dataset hashes and power PASS values and injects canonical receipts.
- `PRE_FREEZE_AUDIT` PASS is conjunctive across novelty, measurement, identification, power and access/licence/ethics.
- reasoning-declared external-source verification cannot close essential data feasibility; the source must first be materialised/registered in GMS.
- `RESULTS_COMPLETE` requires a canonical analysis receipt bound to the active freeze and AnalysisLock.
- final PASS consumes `FINAL_AUDIT_EVIDENCE` for mechanically knowable audit dimensions.

## Per-paper immutable Drive state

Mutable orchestration projections remain in the canonical state root. Each paper may also have a unique child folder containing write-once lifecycle artifacts such as dataset/power receipts, freeze, AnalysisLock, analysis receipt and final-audit evidence.

Different bytes cannot replace an existing immutable artifact in place.

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

## Humanizer boundary

Humanizer is mandatory for manuscript drafting/revision/final polish but controls expression only. It must not independently change scientific meaning, methods, design, numbers, result tokens, citations, evidence support, claim strength, limitations or frozen scientific state.

## Security boundary

The Drive-credential-bearing research cycle has `contents: read` and `actions: write` only to dispatch the confirmatory analysis workflow; it does not push scientific state or heartbeat commits to the public repository.

## Install and test

```bash
pip install -e ".[dev]"
pytest -q
```

The v4.6.1 tree currently collects **358 tests**. The suite covers package integrity, one-paper governance, explicit owner withdrawal, role/skill routing, freeze/AnalysisLock/provenance contracts, and adversarial integrity gates.

## ChatGPT operation

Use one ChatGPT Project named **Management Science CoScientist**. GitHub/Drive remain authoritative over chat memory. In every new chat, read `AGENTS.md`, `docs/CHATGPT_START_HERE.md`, `single_paper.json`, and `director.json`, then load the role contract and exact skills named by the pending action's execution profile.

An explicit owner request to change topic should invoke `USER_WITHDRAWN`; do not falsely claim the old topic has a genuine blocker and do not tell the owner they are forced to continue it.

## Scope

The current engine is optimized for reproducible secondary/open-data quantitative Management Science research: archival, panel, quasi-experimental, event-study and related designs. Future study-type adapters may extend that scope without weakening the one-active-paper rule or the judgment-vs-enforcement boundary.
