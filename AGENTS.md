# CoScientist V4.6.1 — agent and reasoning contract

Read this before changing or operating the Management Science CoScientist. This is the active constitution for Codex, Claude, ChatGPT and any other reasoning or repair plane.

## Mission

CoScientist develops **one Management Science paper at a time** from focused topic admission through literature, theory, data, design, confirmatory analysis, manuscript, audit and submission readiness.

It does **not** run parallel active papers or a permanent candidate tournament. Once a topic is admitted, broad discovery stops until that paper is submission-ready, scientifically retired, or the owner explicitly withdraws it.

Historical candidate IDs, rankings, novelty scores, court outcomes and continuation handoffs are archival evidence only. They are not active inputs to selection, ranking, rejection, continuation, or source choice.

## One-paper rule

The canonical active scientific state is `single_paper.json` in the V4 Google Drive state folder. Exactly one paper may be active.

The one-paper rule prevents **parallel active papers**. It does **not** remove the owner's right to change research priorities.

Broad discovery is allowed when:
- no paper is active;
- the current paper is `SUBMISSION_READY`;
- the paper is `RETIRED` for an enumerated genuine scientific blocker; or
- the owner has explicitly placed it in `USER_WITHDRAWN`.

### Owner withdrawal

An explicit owner instruction such as "stop this topic", "I do not want to continue this paper", or "switch to X instead" authorizes deterministic owner withdrawal.

Owner withdrawal:
- may occur at any non-terminal stage;
- is a human preference/priority/strategy decision, **not** a scientific failure;
- must use the dedicated withdrawal route and must never be falsely encoded as a genuine blocker;
- clears the old pending Director action through reconciliation;
- releases the one-paper lock;
- may carry an explicit `owner_next_topic_direction` into the next discovery action;
- does not authorize running two papers at once.

A model must never infer withdrawal merely from null results, a failed model, a difficult repair, or apparent loss of interest. The owner must explicitly direct it.

When the owner names the next topic, the next discovery action should evaluate and refine that direction rather than silently substituting an unrelated topic. The system may reject or reshape it only for real scientific/data/identification reasons.

## Research Director rule

`director.json` is the canonical orchestration record. It may contain **one pending research action**. `director_answer.json` is the single overwriteable reasoning-plane inbox. Never create an alternate Director state, second answer inbox, parallel work queue, or paper-specific candidate queue.

The V4.6.1 operating facade is `src/coscientist/director_v461.py`. It preserves V4.6 integrity receipts and adds owner-directed withdrawal/discovery routing.

When operating as a reasoning plane:
1. read current `single_paper.json` and `director.json`;
2. identify exact `action_id` and `execution_profile`;
3. load required role contract and skills;
4. answer only that action;
5. use current literature/web evidence and the current GMS lake as required;
6. return one structured `director_answer.json` with `execution_role` and `skills_used`;
7. do not mutate lifecycle stage by hand;
8. do not answer stale actions after the Director has moved on.

The deterministic Director validates answers before state advances.

## Reasoning roles

There are only two LLM execution roles plus the deterministic Director.

### Scientific Reasoning
Contract: `agents/scientific_reasoning/AGENT.md`.
Use for literature/theory, data-feasibility reasoning, design closure, manuscript drafting and ordinary repair.

### Independent Audit
Contract: `agents/independent_audit/AGENT.md`.
Use for `PRE_FREEZE_AUDIT` and `FINAL_AUDIT`. These require a genuinely fresh reasoning context and `fresh_context_attested: true`.

The audit role is read-only toward canonical scientific state. It returns findings through the Director.

### Deterministic actions
`CREATE_FREEZE`, `RUN_ANALYSIS`, and explicit owner withdrawal are deterministic control actions. Do not fabricate LLM scientific judgments for them.

## Skill rule

Skills are reusable expert procedures, not autonomous actors:
- `skills/humanizer/SKILL.md` — Humanizer v2.0;
- `skills/literature_theory/SKILL.md` — LiteratureTheory v1.0;
- `skills/study_design_reasoner/SKILL.md` — StudyDesignReasoner v1.0;
- `skills/hostile_reviewer/SKILL.md` — HostileReviewer v1.0;
- `skills/manuscript_writer/SKILL.md` — ManuscriptWriter v1.0;
- `skills/citation_integrity/SKILL.md` — CitationIntegrity v1.0;
- `skills/final_audit_reasoner/SKILL.md` — FinalAuditReasoner v1.0.

Do not create a swarm of permanent Literature/Data/Statistics/Journal/Figure agents merely because those tasks exist.

## Genuine blockers

Scientific retirement is permitted only for:
- direct scoop with no defensible residual contribution;
- essential-data or essential-measurement impossibility after repair routes are exhausted;
- identification impossibility for the intended core claim;
- fundamental power failure that an honest design cannot repair;
- legal or ethical impossibility;
- fundamental construct failure.

API outages, transfer errors, a missing optional control, one failed model, non-significance, a nearby paper, a failed workflow, or an arbitrary novelty score are repair problems, not retirement grounds.

**Owner withdrawal is separate from this list.** The owner does not need to manufacture a scientific blocker to stop a topic.

## Evolve first

If the owner still wants the paper, pre-freeze evolution has no arbitrary count budget. Improve measurement, data, mechanism, identification, sample, or design as needed. A substantive evolution re-runs the checks it invalidates.

Below the outcome lock, scientific state is immutable. A post-freeze design change cannot silently rewrite a confirmatory claim.

## Bounded novelty checks

There is no numeric novelty score that mechanically kills a paper. Novelty is a scientific judgment about residual contribution. Use bounded closures at admission, before freeze, and immediately before submission.

## Data order — lake first

The Global Management Science data lake owns ingestion. CoScientist owns scientific selection. For every required construct use:

```text
03_RESEARCH mart
    -> 02_CURATED object
    -> 01_RAW_IMMUTABLE / query-layer materialisation
    -> admissible official external source
    -> defensible pre-freeze evolution
    -> genuine essential-data blocker
```

Do not reacquire data already held adequately. External retrieval fills a defined gap. Large/query-native data should be reduced to the smallest useful paper-specific extract and that extract should be frozen.

## Freeze only after feasibility

Before freeze establish outcome-blind source access/licence, schema, joins, granularity, coverage, sample construction, treatment/support variation, essential-variable availability, realistic missingness/attrition, pre-period noise/dependence, plausible power/MDE, and exact dataset SHA-256 identities.

Then perform one consolidated hostile pre-freeze review. A `CREATE_FREEZE` action requires the real mechanical `FreezeManifest`; only then may lifecycle become `FROZEN`.

## Outcome and analysis locks

The design freeze fixes scientific design. The AnalysisLock fixes code, environment and execution plan before confirmatory outcome access. Both remain one-way doors for confirmatory claims.

## Integrity receipts

Mechanically knowable facts come from deterministic evidence, not LLM assertions. V4.6+ uses write-once receipts for dataset identity, deterministic power, analysis completion and final-audit mechanical evidence.

## Manuscript integrity

The results bridge is authoritative for empirical numbers. Confirmatory prose uses strict result-token provenance. Final submission readiness requires every mandated final-audit check to pass.

## Humanizer manuscript-style rule

Humanizer controls **expression only**. It may improve naturalness, directness, rhythm, diction, paragraph architecture and rhetorical restraint, but it must not independently change scientific meaning, methods, numbers, result tokens, citations, evidence support, claim strength, limitations, or frozen design.

## GitHub / Drive boundary

GitHub contains code, tests, workflows, role contracts, skills and registries. Google Drive contains scientific state, Director state, frozen identities, research outputs and manuscripts. Workflows must never commit unsubmitted scientific state/results to Git.

## Engineering rule

Every deterministic guarantee claimed by the system belongs in a `GUARANTEES*.yaml` registry file and names a real test. Documentation and tests move together. Green tests establish encoded contracts; they never replace scientific review.

## Canonical documentation

- `docs/SINGLE_PAPER_RESEARCH_HANDBOOK.md`
- `docs/REASONING_LAYER.md`
- `docs/CHATGPT_START_HERE.md`
- `docs/HUMANIZER_INTEGRATION.md`

If older candidate/court/handoff documentation conflicts with this contract, the current V4.6.1 constitution wins.
