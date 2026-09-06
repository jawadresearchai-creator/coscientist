# Management Science CoScientist — Single-Paper Research Handbook

Version 4.3.0

## 1. Mission

The CoScientist develops **one Management Science paper at a time**. It selects
one strong research question, stops broad topic discovery, and focuses all
subsequent literature, theory, data, design, analysis and writing activity on
making that paper scientifically stronger and submission-ready.

A new topic may be selected only when the current paper is:

- `SUBMISSION_READY`; or
- `RETIRED` because a genuine blocker makes the core study scientifically
  impossible to complete honestly.

There is no standing multi-paper portfolio, reserve queue, candidate
leaderboard, novelty tournament or continuous replacement search.

[GUARANTEE: SINGLE_ACTIVE_PAPER_ONLY]
[GUARANTEE: DISCOVERY_STOPS_AFTER_ADMISSION]
[GUARANTEE: TERMINAL_PAPER_REOPENS_DISCOVERY]

## 2. Canonical lifecycle

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

The active paper cannot skip stages or move backwards through the confirmatory
lifecycle. Pre-freeze scientific evolution happens *within* the current stage,
not by reopening broad discovery.

[GUARANTEE: SINGLE_PAPER_STAGE_IS_MONOTONIC]

## 3. What happens when the paper has a problem

The default response is **repair and evolve**, not replace.

| Problem | Default response |
|---|---|
| Required variable missing | Search research marts -> curated lake -> raw/query layer -> official public source |
| Weak measurement | Improve operationalization or validated proxy pre-freeze |
| Identification concern | Redesign the identification strategy pre-freeze |
| Power concern | Improve sample/treatment geometry or reassess estimand honestly |
| Nearby paper | Sharpen residual contribution and mechanism |
| API/transfer failure | Repair infrastructure; do not make a scientific rejection |
| Optional control unavailable | Drop only if it was explicitly declared optional |
| One model fails | Repair/diagnose model; do not replace topic |
| Null confirmatory result | Report honestly; do not specification-shop |

Pre-freeze evolution has no arbitrary count ceiling. It may happen repeatedly
while the paper remains viable. The moment the design is frozen, scientific
state becomes a one-way door.

[GUARANTEE: TOPIC_EVOLUTION_HAS_NO_ARBITRARY_BUDGET]
[GUARANTEE: PRE_FREEZE_EVOLUTION_STOPS_AT_FREEZE]
[GUARANTEE: RESCOPE_IS_PRE_FREEZE_ONLY]
[GUARANTEE: RESCOPE_REJUDGES_NOVELTY_AND_POWER]

## 4. Genuine blockers

Retirement is allowed only for a genuine blocker after reasonable repair paths
have been exhausted:

1. **Direct scientific displacement:** a directly matching study leaves no
   meaningful defensible residual contribution.
2. **Essential-data/measurement impossibility:** a required construct cannot be
   measured credibly from the lake or admissible external sources.
3. **Identification impossibility:** the core causal/descriptive claim cannot be
   identified with an honest design.
4. **Fundamental power failure:** the intended effect scale cannot be detected
   even after defensible design/sample repair.
5. **Legal or ethical impossibility.**
6. **Fundamental construct failure:** the central construct cannot be defined or
   operationalized credibly.

Temporary outages, workflow errors, missing optional variables, non-significant
results, a nearby paper, or a low arbitrary novelty score are not retirement
grounds.

[GUARANTEE: RETIREMENT_REQUIRES_GENUINE_BLOCKER]
[GUARANTEE: ENGINE_FAILURE_IS_NOT_SCIENTIFIC_FAILURE]
[GUARANTEE: CHEAP_SCREEN_DOES_NOT_ADJUDICATE_CLOSE_CALLS]

## 5. Topic selection

Topic selection occurs only when discovery is allowed.

1. Inspect the current data lake and current literature.
2. Generate a **small bounded shortlist**, normally no more than three serious
   questions.
3. Compare importance, mechanism, residual novelty, identification, data,
   measurement, power and journal relevance.
4. Select one.
5. Write a Topic Charter.
6. Admit it to `single_paper.json`.
7. Stop broad discovery.

The Topic Charter should state:

- paper ID and working title;
- research question;
- focal phenomenon;
- theoretical mechanism;
- expected contribution;
- unit of analysis;
- intended design;
- primary exposure/treatment;
- primary outcome;
- required constructs;
- target journal family;
- known threats.

Historical candidate IDs, old slate rankings, old court verdicts and retirement
queues are not imported into the new paper state.

[GUARANTEE: HISTORICAL_CANDIDATES_ARE_NOT_ACTIVE_INPUT]
[GUARANTEE: LEGACY_STATE_FIELDS_ARE_NOT_IMPORTED]

## 6. Literature and novelty

Use a two-layer literature process.

### Broad universe

Use OpenAlex/lake literature marts for:

- field mapping;
- topic trajectories;
- citation relationships;
- journal/author/institution context;
- candidate closest-paper retrieval.

### Close-paper verification

Use current scholarly/public sources for:

- latest online-first work;
- exact DOI/metadata verification;
- substantive reading of closest papers;
- claim-level novelty comparison.

Novelty is not a permanent numeric kill score. It is a judgment about residual
contribution. Perform bounded novelty closures at:

1. topic admission;
2. pre-freeze hostile review;
3. immediately before submission.

A nearby paper should normally trigger sharpening of theory, mechanism,
measurement, context, identification or boundary conditions. Retirement occurs
only when a direct scoop leaves no meaningful residual contribution without
changing the core question.

## 7. Theory and mechanism

Specify a coherent causal/explanatory chain:

```text
exposure/treatment -> mechanism -> intermediate consequence -> outcome
```

For each paper identify:

- the principal theoretical mechanism;
- the strongest rival explanation;
- observable implications distinguishing them;
- falsification/negative-control logic where feasible;
- boundary conditions/heterogeneity only when theoretically justified.

Prefer one coherent theoretical account to decorative multi-theory stacking.

## 8. Data architecture — lake first

The Global Management Science data lake owns ingestion. CoScientist owns
scientific selection.

For every required construct use this order:

```text
03_RESEARCH research-ready mart
  -> 02_CURATED normalized/curated object
  -> 01_RAW_IMMUTABLE or query-layer materialisation
  -> admissible official external source
  -> defensible pre-freeze evolution
  -> genuine essential-data blocker
```

A suitable exact lake holding must be selected before external probing.
External acquisition fills a defined gap; it does not duplicate data already
held adequately.

[GUARANTEE: G3_IS_LAKE_FIRST]

The older source safeguards remain active:

- unknown suitability is not assumed;
- wrong granularity/coverage is rejected;
- every admissible external fallback is exhausted when needed;
- identity-gated/paid sources remain inadmissible by default;
- credentials are not treated as a source census;
- archival rights are separate from query rights.

[GUARANTEE: UNKNOWN_SOURCE_SUITABILITY_IS_NOT_ASSUMED]
[GUARANTEE: G3_CHECKS_SUITABILITY_NOT_JUST_REACHABILITY]
[GUARANTEE: G3_EXHAUSTS_EXTERNAL_ROUTES]
[GUARANTEE: INADMISSIBLE_SOURCES_ARE_REFUSED]
[GUARANTEE: SECRETS_ARE_NOT_A_SOURCE_CENSUS]
[GUARANTEE: ARCHIVAL_RIGHTS_ARE_SEPARATE_FROM_ACCESS]

Large/query-native data are reduced to the smallest useful paper-specific
extract before freeze. Never query BigQuery blind.

## 9. Outcome-blind feasibility before freeze

Do not freeze a paper because the idea is attractive. Before freeze, prove that
the exact planned study can be executed without using confirmatory outcome
patterns to choose the design.

Check:

- source access/licence;
- schema and join keys;
- required granularity and coverage;
- sample construction;
- treatment/support variation;
- essential-variable completeness;
- missingness and attrition expectations;
- pre-period noise/dependence;
- plausible effect scale and MDE/power;
- legal/ethical constraints.

The power gate sees only pre-period information and blocks rather than
scientifically failing when its input contract is broken.

[GUARANTEE: POWER_GATE_SEES_ONLY_PRE_PERIOD]
[GUARANTEE: ENGINE_FAILURE_IS_NOT_SCIENTIFIC_FAILURE]

## 10. Consolidated hostile pre-freeze review

Use one consolidated review rather than a forest of candidate courts.

Ask:

- Is the residual contribution defensible against the closest literature?
- Is the mechanism coherent?
- Are constructs measured credibly?
- What is the strongest alternative explanation?
- Can the exact sample/data be built?
- Is plausible power adequate?
- Are access, licensing and ethics valid?

Allowed decisions:

- `PASS` — freeze may proceed;
- `REPAIR` — remain on the same paper and fix the named weakness;
- `GENUINE_BLOCKER` — retire only with an enumerated blocker.

## 11. Design freeze

After feasibility and hostile review pass, freeze:

- research question;
- estimand;
- design;
- sample definition;
- treatment/exposure;
- outcome;
- controls;
- exclusions;
- time window;
- primary models;
- primary contrasts;
- multiplicity policy;
- exact dataset SHA-256 identities.

The freeze is tamper-evident, write-once and has no replacement escape hatch.
Frozen scientific containers are immutable.

[GUARANTEE: FREEZE_HASH_COVERS_DESIGN]
[GUARANTEE: FREEZE_DETECTS_TAMPERING]
[GUARANTEE: FREEZE_REQUIRES_STORED_HASH]
[GUARANTEE: FREEZE_IS_WRITE_ONCE]
[GUARANTEE: FREEZE_HAS_NO_ESCAPE_HATCH]
[GUARANTEE: FREEZE_REQUIRES_DATASET_IDENTITY]
[GUARANTEE: LOCK_IS_A_HASH_NOT_A_LABEL]
[GUARANTEE: FROZEN_CANDIDATES_ARE_IMMUTABLE]
[GUARANTEE: SEALING_REACHES_THE_CONSTRUCTS]
[GUARANTEE: FROZEN_SCIENTIFIC_CONTAINERS_ARE_IMMUTABLE]
[GUARANTEE: CANDIDATE_AND_FREEZE_DESCRIBE_ONE_STUDY]

## 12. Analysis lock

Before confirmatory outcome access, lock:

- analysis scripts;
- actual numbered execution plan;
- environment lockfiles;
- acknowledged unsanctioned IO;
- design-freeze binding.

Any edited/new analysis script or changed environment fails verification.

[GUARANTEE: ANALYSIS_CODE_IS_PRE_SPECIFIED]
[GUARANTEE: NEW_SCRIPTS_CANNOT_JOIN_AFTER_THE_LOCK]
[GUARANTEE: ANALYSIS_LOCK_IS_WRITE_ONCE]
[GUARANTEE: ANALYSIS_LOCK_REQUIRES_ITS_HASH]
[GUARANTEE: ENVIRONMENT_IS_LOCKED_WITH_THE_CODE]
[GUARANTEE: UNSANCTIONED_IO_IS_DECLARED]
[GUARANTEE: ACTUAL_EXECUTION_PLAN_IS_LOCKED]
[GUARANTEE: ANALYSIS_LOCK_RECEIPT_IS_TAMPER_EVIDENT]
[GUARANTEE: ANALYSIS_VERIFY_REQUIRES_DESIGN_FREEZE]
[GUARANTEE: ANALYSIS_LOCK_IS_VERIFIED_BEFORE_OUTCOME_ACCESS]

## 13. Data fetch and quality audit

Fetch only frozen objects/extracts. Re-hash every input. Build the analysis table
with recorded joins and exclusions.

At minimum produce:

- variable dictionary;
- sample flow;
- missingness/merge audit;
- descriptive/support diagnostics;
- reproducible source/version manifest.

[GUARANTEE: FETCH_IS_LIMITED_TO_FROZEN_DATA]
[GUARANTEE: LARGE_DATASETS_ARE_STREAMED]
[GUARANTEE: DRIVE_LISTING_IS_COMPLETE]
[GUARANTEE: DUPLICATE_NAMES_RESOLVE_BY_CONTENT]
[GUARANTEE: DATASET_DRIFT_BLOCKS_ANALYSIS]
[GUARANTEE: R_CANNOT_READ_UNFROZEN_DATA]
[GUARANTEE: STUDY_STATE_BOOTSTRAPS_BEFORE_OUTCOMES]

## 14. Analysis

Run primary confirmatory models first. Report effect sizes and uncertainty, not
a binary significant/non-significant narrative. Run predeclared robustness and
falsification only to answer specific threats. Mark post-hoc analyses explicitly
exploratory.

A valid null finding is a result and never triggers topic replacement.

Randomness is design-derived, streams must seal cleanly, producer identity must
agree across records, and incoherent/non-finite statistics are refused.

[GUARANTEE: RANDOMNESS_IS_DERIVED_FROM_THE_DESIGN]
[GUARANTEE: ANALYSIS_TRUNCATION_IS_DETECTED]
[GUARANTEE: INCOHERENT_STATISTICS_ARE_REFUSED]
[GUARANTEE: NON_FINITE_VALUES_ARE_REFUSED]
[GUARANTEE: A_NAN_STANDARD_ERROR_IS_NEVER_DROPPED]
[GUARANTEE: R_AND_PYTHON_AGREE]
[GUARANTEE: RESULTS_NAME_THE_CODE_THAT_MADE_THEM]
[GUARANTEE: SCRIPT_IDENTITY_COMES_FROM_THE_RUNNER]
[GUARANTEE: PYTHON_SCRIPT_IDENTITY_COMES_FROM_THE_RUNNER]
[GUARANTEE: STREAM_PRODUCER_WITNESSES_AGREE]
[GUARANTEE: ONE_DEFINITION_OF_WHAT_RUNS]
[GUARANTEE: ANALYSIS_STAGES_RUN_IN_ORDER]

## 15. Results bridge and figures

Every reportable result exists exactly once as a tokened result record. Figures
must declare the result tokens they depict. Manifest tampering is detected.

[GUARANTEE: RESULT_TOKENS_ARE_UNIQUE]
[GUARANTEE: FIGURES_ARE_BOUND_TO_RESULTS]
[GUARANTEE: MANIFEST_TAMPERING_IS_DETECTED]
[GUARANTEE: MANIFEST_REQUIRES_ITS_INTEGRITY_HASHES]

## 16. Manuscript

Write from verified evidence and the results bridge. A typical archival
Management Science structure is:

1. Introduction
2. Context where needed
3. Theory/hypotheses where appropriate
4. Data/measurement
5. Empirical design
6. Results
7. Mechanisms/heterogeneity
8. Robustness/falsification
9. Discussion/implications
10. Limitations
11. Conclusion

Adapt to the target journal instead of forcing one rigid template.

Strict confirmatory numeric provenance must pass. Bare numbers that cannot be
bound to results fail. Units, p-operators and statistic-field identity are
checked.

[GUARANTEE: FABRICATED_NUMBERS_FAIL]
[GUARANTEE: P_OPERATOR_IS_HONOURED]
[GUARANTEE: UNITS_GATE_NUMERIC_MATCHING]
[GUARANTEE: FIELD_TOKENS_BIND_STATISTICS_TO_RECORDS]
[GUARANTEE: PROSE_AND_CITATION_MUST_AGREE]
[GUARANTEE: STRICT_MODE_IS_USABLE]
[GUARANTEE: STRICT_MODE_HAS_NO_ESCAPE_HATCH]
[GUARANTEE: STRICT_MODE_FORBIDS_THE_ALLOW_LIST]

## 17. Final audit

Before submission perform:

- scientific logic audit from phenomenon -> mechanism -> evidence -> contribution;
- freeze/deviation audit;
- numeric provenance audit;
- citation existence and claim-support audit;
- figure/table consistency audit;
- causal-language and claim-strength audit;
- replication/reproducibility audit;
- current target-journal compliance audit;
- final novelty refresh.

Only verified outputs reach the authoritative Drive results area. Failed runs
remain quarantined and no scientific state enters Git.

[GUARANTEE: ONLY_VERIFIED_RESULTS_ARE_PUBLISHED]
[GUARANTEE: FAILED_RUNS_ARE_QUARANTINED]
[GUARANTEE: DRIVE_PUBLISH_REQUIRES_COMPLETION_MARKER]
[GUARANTEE: RESEARCH_STATE_NEVER_ENTERS_GIT]

## 18. Minimal human-readable artifact set

A paper should normally expose only:

1. Topic Charter
2. Literature/Theory Record
3. Data and Design Plan
4. Freeze + Analysis Lock
5. Results Bundle
6. Canonical Manuscript
7. Final Audit + Replication Package

Machine manifests may be more numerous internally; humans should not have to
navigate a forest of candidate/court documents.

## 19. Engineering integrity

All production modules must import, the engine has one version source, workflow
YAML/shell must parse, and study budgets must not reset at cycle boundaries.

[GUARANTEE: EVERY_MODULE_IMPORTS]
[GUARANTEE: ONE_VERSION_SOURCE]
[GUARANTEE: WORKFLOWS_ARE_VALID]
[GUARANTEE: BUDGET_PERIODS_ARE_DISTINCT]
[GUARANTEE: BUDGET_SCOPE_IS_ENFORCED_AT_THE_CALLER]

## 20. One-sentence operating contract

The Management Science CoScientist selects one strong research question, stays
focused on it, uses the data lake and authoritative public sources to obtain the
minimum sufficient reproducible evidence, evolves the study rather than
continually replacing it, freezes only after outcome-blind feasibility is
proven, executes pre-specified reproducible analysis, produces a
provenance-backed manuscript, and begins no new paper until the current one is
submission-ready or genuinely impossible to complete.
