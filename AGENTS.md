# CoScientist V4.7.0 — multi-paper agent and reasoning contract

Read this before changing or operating the Management Science CoScientist. This is the active constitution for Codex, Claude, ChatGPT and any other reasoning or repair plane.

## Mission

CoScientist may develop **multiple independent Management Science papers concurrently** from focused topic admission through literature, theory, data, design, confirmatory analysis, manuscript, audit and submission readiness.

Parallel papers are permitted. What is forbidden is **state contamination between papers**: one paper's Director action, freeze, AnalysisLock, data, results, manuscript or audit evidence must never silently drive another paper.

Historical candidate IDs, rankings, novelty scores and old handoffs are archival evidence only unless explicitly attached to the same paper.

## Multi-paper operating model

The canonical root index is `state/paper_registry.json` in the V4 Google Drive state folder.

Each paper owns a separate state namespace:

```text
state/<paper_id>/paper_state.json
state/<paper_id>/director.json
state/<paper_id>/director_answer.json
state/<paper_id>/director_action.json
state/<paper_id>/receipts/
state/<paper_id>/...paper artifacts...
```

`paper_registry.json` may contain a `focus_paper_id` for UI/default routing. **Focus is not exclusivity.** Any number of registered papers may be ACTIVE, REPAIR or PAUSED at the same time.

The legacy `SinglePaperState` class remains the paper-local lifecycle engine for backward compatibility. Its historical name means "one charter per paper-state file", not "one paper for the whole CoScientist".

Legacy root files `state/single_paper.json`, `state/director.json` and `state/director_answer.json` are compatibility artifacts. New work must use the registry and paper-scoped paths.

## Paper-local lifecycle rule

Each paper independently progresses through:

`SELECTED -> DEVELOPING -> DATA_FEASIBLE -> DESIGN_READY -> FROZEN -> ANALYZING -> RESULTS_COMPLETE -> MANUSCRIPT -> FINAL_AUDIT -> SUBMISSION_READY`

A paper may also become `RETIRED` for a genuine scientific blocker or `USER_WITHDRAWN` by explicit owner choice.

Lifecycle monotonicity is paper-local. Advancing, pausing, withdrawing or repairing one paper must not alter any other paper.

## Research Director rule

There is **one pending Director action per paper**, not one pending action for the whole CoScientist.

For paper `<paper_id>`:
- `state/<paper_id>/director.json` is the canonical orchestration record;
- `state/<paper_id>/director_answer.json` is that paper's overwriteable reasoning-plane inbox;
- `state/<paper_id>/director_action.json` is the current action projection when emitted.

Use `src/coscientist/director_multi.py` to route the mature Director facade to a selected paper.

When operating as a reasoning plane:
1. read `paper_registry.json`;
2. select the explicit `paper_id` (or use `focus_paper_id` only when the user has not specified one);
3. read that paper's `paper_state.json` and `director.json`;
4. identify exact `action_id` and `execution_profile`;
5. load required role contract and skills;
6. answer only that paper/action;
7. return only through that paper's `director_answer.json`;
8. do not mutate lifecycle stage by hand;
9. do not answer stale actions;
10. do not touch another paper's state as a side effect.

The deterministic Director validates answers before that paper advances.

## Adding and working on papers

A new paper may be created while other papers are active. Admission does not require withdrawal, retirement or completion of another paper.

The owner may work on Paper A, switch focus to Paper B, and later return to Paper A without terminating either.

Broad discovery may run for a new paper when explicitly requested. Discovery for Paper B does not reopen, rewrite or replace the charter of Paper A.

## Owner withdrawal

Explicit owner withdrawal is paper-local.

An instruction such as "stop paper X" places only that paper in `USER_WITHDRAWN`. It does not release or acquire a global lock because no global paper lock exists.

Never infer withdrawal merely from null results, a failed model, difficult repair or temporary lack of attention.

## Reasoning roles

There are two LLM execution roles plus the deterministic Director.

### Scientific Reasoning
Contract: `agents/scientific_reasoning/AGENT.md`.
Use for literature/theory, data-feasibility reasoning, design closure, manuscript drafting and ordinary repair.

### Independent Audit
Contract: `agents/independent_audit/AGENT.md`.
Use for `PRE_FREEZE_AUDIT` and `FINAL_AUDIT`. These require a genuinely fresh reasoning context and `fresh_context_attested: true`.

The audit role is read-only toward the selected paper's canonical scientific state.

### Deterministic actions
`CREATE_FREEZE`, `RUN_ANALYSIS`, explicit owner withdrawal and registry/state migration are deterministic control actions. Do not fabricate LLM scientific judgments for them.

## Skill rule

Skills are reusable expert procedures, not autonomous actors:
- `skills/humanizer/SKILL.md` — Humanizer v2.0;
- `skills/literature_theory/SKILL.md` — LiteratureTheory v1.0;
- `skills/study_design_reasoner/SKILL.md` — StudyDesignReasoner v1.0;
- `skills/hostile_reviewer/SKILL.md` — HostileReviewer v1.0;
- `skills/manuscript_writer/SKILL.md` — ManuscriptWriter v1.0;
- `skills/citation_integrity/SKILL.md` — CitationIntegrity v1.0;
- `skills/final_audit_reasoner/SKILL.md` — FinalAuditReasoner v1.0.

Do not create a permanent swarm merely because multiple papers exist. Concurrency is a state/routing capability, not a requirement to spawn one autonomous agent per paper.

## Genuine blockers

Scientific retirement of a selected paper is permitted only for:
- direct scoop with no defensible residual contribution;
- essential-data or essential-measurement impossibility after repair routes are exhausted;
- identification impossibility for the intended core claim;
- fundamental power failure that an honest design cannot repair;
- legal or ethical impossibility;
- fundamental construct failure.

API outages, transfer errors, missing optional controls, one failed model, non-significance or a failed workflow are repair problems for that paper, not retirement grounds.

## Evolve first

If the owner still wants a paper, pre-freeze evolution has no arbitrary count budget. Improve measurement, data, mechanism, identification, sample or design as needed. A substantive evolution re-runs the checks it invalidates.

Below that paper's outcome lock, its scientific state is immutable for confirmatory claims. Locks are independent across papers.

## Bounded novelty checks

There is no numeric novelty score that mechanically kills a paper. Novelty is a scientific judgment about residual contribution. Use bounded closures at admission, before freeze and immediately before submission.

## Data order — lake first

The Global Management Science data lake owns ingestion. Each paper owns scientific selection. For every required construct use:

```text
03_RESEARCH mart
    -> 02_CURATED object
    -> 01_RAW_IMMUTABLE / query-layer materialisation
    -> admissible official external source
    -> defensible pre-freeze evolution
    -> genuine essential-data blocker
```

Do not reacquire data already held adequately. Paper-specific extracts must be stored under that paper's artifact namespace and frozen by hash.

## Freeze only after feasibility

For each paper, before freeze establish outcome-blind source access/licence, schema, joins, granularity, coverage, sample construction, treatment/support variation, essential-variable availability, realistic missingness/attrition, pre-period noise/dependence, plausible power/MDE and exact dataset SHA-256 identities.

Then perform that paper's consolidated hostile pre-freeze review. A `CREATE_FREEZE` action requires its real mechanical `FreezeManifest`.

## Outcome and analysis locks

Every paper has its own design freeze and AnalysisLock. Paper A may be frozen while Paper B remains in data feasibility. Outcome access for one paper does not unlock or freeze another.

## Integrity receipts

Mechanically knowable facts come from deterministic evidence, not LLM assertions. Receipts are paper-scoped and must name the paper ID and the exact artifact identities they certify.

## Manuscript integrity

Each paper's results bridge is authoritative for its empirical numbers. Never copy result tokens between papers unless an explicit cross-paper synthesis design requires and records that linkage.

## Humanizer manuscript-style rule

Humanizer controls expression only. It may improve naturalness, directness, rhythm, diction, paragraph architecture and rhetorical restraint, but it must not independently change scientific meaning, methods, numbers, result tokens, citations, evidence support, claim strength, limitations or frozen design.

## GitHub / Drive boundary

GitHub contains code, tests, workflows, role contracts, skills and registries/schema logic. Google Drive contains canonical paper registry/state, Director state, frozen identities, research outputs and manuscripts.

Workflows must be explicitly paper-scoped and must never commit unsubmitted scientific state/results to Git.

## Engineering rule

Every deterministic guarantee claimed by the system belongs in a `GUARANTEES*.yaml` registry file and names a real test. Documentation and tests move together. Green tests establish encoded contracts; they never replace scientific review.

## Canonical documentation

- `docs/MULTI_PAPER_RESEARCH_HANDBOOK.md`
- `docs/CHATGPT_START_HERE.md`
- `docs/REASONING_LAYER.md`
- `docs/HUMANIZER_INTEGRATION.md`

If older single-paper documentation conflicts with this contract, this V4.7.0 constitution wins.
