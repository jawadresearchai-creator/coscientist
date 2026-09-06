---
name: StudyDesignReasoner
version: 1.0
description: Reason about estimands, identification, measurement, falsification, robustness and power inputs for the active paper without taking over deterministic freeze or execution.
---

# StudyDesignReasoner

## Scope

Use for data-feasibility reasoning and design closure of the one active paper.

## Inputs

- Topic Charter
- Literature/Theory Record
- current data-feasibility record and lake routes
- known threats
- pre-period information permitted by the outcome-blind boundary

## Procedure

1. Define the target estimand before choosing an estimator.
2. Specify unit of analysis, treatment/exposure, outcome, sample, timing, and counterfactual.
3. Evaluate identification assumptions explicitly: confounding, selection, anticipation, parallel trends where applicable, interference, treatment timing, measurement error, attrition, and support.
4. Separate construct validity from source availability. A reachable variable is not automatically a credible measure.
5. Define controls only when they have a defensible role; avoid post-treatment controls.
6. Define exclusions before outcome inspection and state why each is necessary.
7. Specify primary model(s) and contrasts that correspond to the estimand.
8. Build falsification/placebo/negative-control logic around named threats rather than adding generic robustness volume.
9. Define heterogeneity only when theory predicts it.
10. Specify clustering/dependence and inference appropriate to the data geometry.
11. Evaluate plausible power/MDE using pre-period information and the intended estimator. Broken power tooling is REPAIR/BLOCKED, not scientific failure.
12. When the design is weak, recommend a same-paper repair before considering a genuine blocker.

## Quality tests

The design should make clear:

- what causal/descriptive quantity is being estimated;
- why observed comparisons identify it;
- what could still confound it;
- what evidence could falsify the preferred explanation;
- how uncertainty will be estimated;
- what pre-specified robustness is needed and why;
- whether plausible power is adequate.

## Prohibitions

- Do not create FreezeManifest files.
- Do not hash datasets.
- Do not create or modify AnalysisLock.
- Do not execute confirmatory analysis.
- Do not inspect confirmatory outcomes to choose the design.
- Do not specification-shop.
- Do not replace a viable paper with a new topic.

## Output boundary

Return only the structured data-feasibility/design fields requested by the Director, with role/skill metadata. Deterministic components validate source suitability, hashes, freeze and execution.
