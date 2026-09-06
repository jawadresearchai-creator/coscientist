# CoScientist V4.5 — agent and reasoning contract

Read this before changing or operating the Management Science CoScientist. This is the active constitution for Codex, Claude, ChatGPT and any other reasoning or repair plane.

## Mission

CoScientist develops **one Management Science paper at a time** from focused topic admission through literature, theory, data, design, confirmatory analysis, manuscript, audit and submission readiness.

It does **not** run a permanent candidate tournament. Once a topic is admitted, broad discovery stops. The active question is evolved and repaired until it is submission-ready or a genuine blocker makes completion scientifically impossible.

Historical candidate IDs, rankings, novelty scores, court outcomes and continuation handoffs are archival evidence only. They are not active inputs to selection, ranking, rejection, continuation, or source choice.

## One-paper rule

The canonical active scientific state is `single_paper.json` in the V4 Google Drive state folder. Exactly one paper may be active.

Broad discovery is allowed only when no paper is active, the current paper is `SUBMISSION_READY`, or the paper is `RETIRED` for an enumerated genuine blocker. Do not create reserve candidates, parallel papers, portfolios, leaderboards, or replacement topics while the active paper remains viable.

## Research Director rule

`director.json` is the canonical orchestration record. It may contain **one pending research action**. `director_answer.json` is the single overwriteable reasoning-plane inbox. Never create an alternate Director state, second answer inbox, parallel work queue, or paper-specific candidate queue.

The V4.5 operating facade is `src/coscientist/director_v45.py`. It preserves the V4.4 scientific-state logic and adds an execution profile containing the required role, required skill versions, and whether a fresh reasoning context is required.

When operating as a reasoning plane:

1. read the current `single_paper.json` and `director.json`;
2. identify the exact current `action_id` and `execution_profile`;
3. load the required role contract and every required skill at its exact version;
4. answer only the pending `action_id`;
5. use current web/literature evidence and the current GMS lake as required by that action;
6. write one structured `director_answer.json` containing `execution_role` and `skills_used` metadata;
7. do not mutate lifecycle stage by hand;
8. do not answer a stale action after the Director has moved on.

The deterministic Director validates the answer before state advances. A stale answer is ignored. Repeated cycles preserve the same pending action rather than generating more work.

## Reasoning roles

There are only two LLM execution roles plus the deterministic Director.

### Scientific Reasoning

Contract: `agents/scientific_reasoning/AGENT.md`.

Use for normal judgment work such as literature/theory, data-feasibility reasoning, design closure, manuscript drafting and ordinary repair. It cannot satisfy an action whose required role is `INDEPENDENT_AUDIT`.

### Independent Audit

Contract: `agents/independent_audit/AGENT.md`.

Use for `PRE_FREEZE_AUDIT` and `FINAL_AUDIT`. These actions require a genuinely fresh reasoning context and `fresh_context_attested: true`. The attestation is auditable metadata, not cryptographic proof; the execution environment must actually start a fresh audit context.

The audit role is read-only toward canonical scientific state. It returns PASS/REPAIR/GENUINE_BLOCKER findings through the Director and does not directly mutate state.

### Deterministic actions

`CREATE_FREEZE` and `RUN_ANALYSIS` are deterministic. Do not fabricate LLM answers for them. Execute the mechanical workflow/tooling.

## Skill rule

Skills are reusable expert procedures, not autonomous actors. The approved skill set is intentionally compact:

- `skills/humanizer/SKILL.md` — Humanizer v2.0;
- `skills/literature_theory/SKILL.md` — LiteratureTheory v1.0;
- `skills/study_design_reasoner/SKILL.md` — StudyDesignReasoner v1.0;
- `skills/hostile_reviewer/SKILL.md` — HostileReviewer v1.0;
- `skills/manuscript_writer/SKILL.md` — ManuscriptWriter v1.0;
- `skills/citation_integrity/SKILL.md` — CitationIntegrity v1.0;
- `skills/final_audit_reasoner/SKILL.md` — FinalAuditReasoner v1.0.

The Director execution profile decides which skills are required. Do not create a swarm of Literature/Data/Statistics/Journal/Figure agents merely because those tasks exist.

## Genuine blockers

Retirement is permitted only for:

- direct scoop with no defensible residual contribution;
- essential-data or essential-measurement impossibility after repair routes are exhausted;
- identification impossibility for the intended core claim;
- fundamental power failure that an honest design cannot repair;
- legal or ethical impossibility;
- fundamental construct failure.

API outages, transfer errors, a missing optional control, one failed model, non-significance, a nearby paper, a failed workflow, or an arbitrary novelty score are repair problems, not retirement grounds.

## Evolve first

Pre-freeze evolution has no arbitrary count budget. Improve measurement, data, mechanism, identification, sample, or design as needed while preserving focus on the same paper. A substantive evolution re-runs the scientific checks it invalidates; it does not reopen broad topic discovery.

Below the outcome lock, scientific state is immutable. A post-freeze design change cannot silently rewrite a confirmatory claim.

## Bounded novelty checks

There is no numeric novelty score that mechanically kills a paper. Novelty is a scientific judgment about residual contribution. Use bounded closures at admission, before freeze, and immediately before submission. A paper is retired for novelty only when a directly matching study leaves no meaningful residual contribution without changing the core question.

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

Never query BigQuery blind. Never treat credentials as a census of sources. Accessibility and redistribution rights remain separate properties.

## Freeze only after feasibility

Before freeze establish outcome-blind source access/licence, schema, joins, granularity, coverage, sample construction, treatment/support variation, essential-variable availability, realistic missingness/attrition, pre-period noise/dependence, plausible power/MDE, and exact dataset SHA-256 identities.

Then perform one consolidated hostile pre-freeze review with `PASS`, `REPAIR`, or `GENUINE_BLOCKER`. Do not recreate a forest of separate candidate courts.

A `CREATE_FREEZE` Director action is not permission to set a status flag. Run the mechanical freeze creator and require a real `FreezeManifest`; only then may the lifecycle become `FROZEN`.

## Outcome and analysis locks

The design freeze fixes the scientific question, estimand, sample, treatment, outcome, controls, exclusions, time window, primary models/contrasts, multiplicity policy and exact dataset identities. The analysis lock then fixes code, environment and execution plan before confirmatory outcome access. Both remain one-way doors.

## Quota boundary

Nothing in `src/coscientist/` calls an LLM. Judgment leaves the deterministic core as a persistent Director action or compatibility ticket. Engineering, literature interpretation, manuscript prose and hostile review may be performed by reasoning models, but fixes return through reproducible code/state rather than patching results directly.

## Manuscript integrity

The results bridge is authoritative for empirical numbers. Confirmatory prose uses strict result-token provenance and figures are bound to result tokens. The manuscript-stage audit must also verify references and claim support. Final submission readiness requires every mandated final-audit check to pass.

## Humanizer manuscript-style rule

`skills/humanizer/SKILL.md` is the mandatory writing-style policy for manuscript prose. Before drafting, rewriting, or final-polishing any manuscript section, the reasoning plane must read and apply that skill when it appears in the Director profile.

Humanizer controls **expression only**. It may improve naturalness, directness, rhythm, diction, paragraph architecture, transition density and rhetorical restraint, but it must not independently change scientific meaning, study logic, methods, numbers, result tokens, citations, evidence support, claim strength, limitations, or the frozen design. It must never fabricate mistakes to appear human.

Scientific validity, provenance, citation verification, statistical review, novelty and hostile audit remain separate CoScientist responsibilities. When a scientific repair changes manuscript meaning, rerun Humanizer only after the scientific repair is complete. If Humanizer conflicts with evidence integrity, provenance, journal requirements or frozen science, those constraints win.

## GitHub / Drive boundary

GitHub contains code, tests, workflows, role contracts, skills and registries. Google Drive contains scientific state, Director state, frozen data identities, research outputs and manuscripts. Workflows must never commit unsubmitted scientific state/results to Git.

## Engineering rule

Every deterministic guarantee claimed by the system belongs in one of the `GUARANTEES*.yaml` registry files and names a real test. Documentation and tests move together. Green tests establish encoded contracts; they never replace scientific review.

## Canonical documentation

- `docs/SINGLE_PAPER_RESEARCH_HANDBOOK.md` — human-readable research process.
- `docs/REASONING_LAYER.md` — V4.5 role/skill architecture.
- `docs/CHATGPT_START_HERE.md` — new-chat bootstrap guide.
- `docs/HUMANIZER_INTEGRATION.md` — Humanizer boundary.

If an older Management Sciences candidate/court/handoff document conflicts with this contract, V4.5 wins for all active work.
