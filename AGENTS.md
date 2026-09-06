# CoScientist V4.3 — agent contract

Read this before changing or operating the Management Science CoScientist. This
is the active constitution for Codex, Claude, ChatGPT and any other reasoning or
repair plane.

## Mission

CoScientist develops **one Management Science paper at a time** from focused
topic admission through literature, theory, data, design, confirmatory analysis,
manuscript, audit and submission readiness.

It does **not** run a permanent candidate tournament. Once a topic is admitted,
broad discovery stops. The active question is evolved and repaired until it is
submission-ready or a genuine blocker makes completion scientifically
impossible.

Historical candidate IDs, rankings, novelty scores, court outcomes and
continuation handoffs are archival evidence only. They are not active inputs to
selection, ranking, rejection, continuation, or source choice.

## One-paper rule

The canonical active scientific state is `single_paper.json` in the V4 Google
Drive state folder. Exactly one paper may be active.

Broad discovery is allowed only when no paper is active, the current paper is
`SUBMISSION_READY`, or the paper is `RETIRED` for an enumerated genuine blocker.
Do not create reserve candidates, parallel papers, portfolios, leaderboards, or
replacement topics while the active paper remains viable.

## Genuine blockers

Retirement is permitted only for:

- direct scoop with no defensible residual contribution;
- essential-data or essential-measurement impossibility after repair routes are exhausted;
- identification impossibility for the intended core claim;
- fundamental power failure that an honest design cannot repair;
- legal or ethical impossibility;
- fundamental construct failure.

API outages, transfer errors, a missing optional control, one failed model,
non-significance, a nearby paper, a failed workflow, or an arbitrary novelty
score are repair problems, not retirement grounds.

## Evolve first

Pre-freeze evolution has no arbitrary count budget. Improve measurement, data,
mechanism, identification, sample, or design as needed while preserving focus
on the same paper. A substantive evolution re-runs the scientific checks it
invalidates; it does not reopen broad topic discovery.

Below the outcome lock, scientific state is immutable. A post-freeze design
change cannot silently rewrite a confirmatory claim.

## Bounded novelty checks

There is no numeric novelty score that mechanically kills a paper. Novelty is a
scientific judgment about residual contribution. Use bounded closures at
admission, before freeze, and immediately before submission. A paper is retired
for novelty only when a directly matching study leaves no meaningful residual
contribution without changing the core question.

## Data order — lake first

The Global Management Science data lake owns ingestion. CoScientist owns
scientific selection. For every required construct use:

```text
03_RESEARCH mart
    -> 02_CURATED object
    -> 01_RAW_IMMUTABLE / query-layer materialisation
    -> admissible official external source
    -> defensible pre-freeze evolution
    -> genuine essential-data blocker
```

Do not reacquire data already held adequately. External retrieval fills a
defined gap. Large/query-native data should be reduced to the smallest useful
paper-specific extract and that extract should be frozen.

Never query BigQuery blind. Never treat credentials as a census of sources.
Accessibility and redistribution rights remain separate properties.

## Freeze only after feasibility

Before freeze establish outcome-blind source access/licence, schema, joins,
granularity, coverage, sample construction, treatment/support variation,
essential-variable availability, realistic missingness/attrition, pre-period
noise/dependence, and plausible power/MDE for the intended design.

Then perform one consolidated hostile pre-freeze review with `PASS`, `REPAIR`,
or `GENUINE_BLOCKER`. Do not recreate a forest of separate candidate courts.

## Outcome and analysis locks

The design freeze fixes the scientific question, estimand, sample, treatment,
outcome, controls, exclusions, time window, primary models/contrasts,
multiplicity policy and exact dataset identities. The analysis lock then fixes
code, environment and execution plan before confirmatory outcome access. Both
remain one-way doors.

## Quota boundary

Nothing in `src/coscientist/` calls an LLM. Judgment can leave the deterministic
core as a ticket. Engineering, literature interpretation, manuscript prose and
hostile review may be performed by reasoning models, but fixes return through
reproducible code/state rather than patching results directly.

## Manuscript integrity

The results bridge is authoritative for empirical numbers. Confirmatory prose
uses strict result-token provenance and figures are bound to result tokens. The
manuscript-stage audit must also verify references and claim support.

## GitHub / Drive boundary

GitHub contains code, tests, workflows and registries. Google Drive contains
scientific state, frozen data identities, research outputs and manuscripts.
Workflows must never commit unsubmitted scientific state/results to Git.

## Engineering rule

Every deterministic guarantee claimed by the system belongs in
`GUARANTEES.yaml` and names a real test. Documentation and tests move together.
Green tests establish encoded contracts; they never replace scientific review.

## Canonical handbook

`docs/SINGLE_PAPER_RESEARCH_HANDBOOK.md` is the human-readable operating
handbook. If an older Management Sciences candidate/court/handoff document
conflicts with this contract, V4.3 wins for all active work.
