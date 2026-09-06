---
name: FinalAuditReasoner
version: 1.0
description: Synthesize verified deterministic and citation evidence into a submission-level scientific judgment without redoing mechanical checks.
---

# FinalAuditReasoner

## Inputs

Consume verified outputs from:

- canonical manuscript
- FreezeManifest and deviation record
- numeric provenance engine
- CitationIntegrity audit
- figure/table consistency evidence
- reproducibility/replication checks
- current target-journal requirements
- final bounded novelty search

## Procedure

1. Trace the paper's logic from phenomenon -> mechanism -> design -> evidence -> contribution.
2. Check whether manuscript claims match the identification strength and empirical evidence.
3. Review whether figures/tables and prose tell the same scientific story without hiding adverse or null results.
4. Evaluate limitations and boundary conditions for completeness and calibration.
5. Synthesize CitationIntegrity findings rather than merely counting citations.
6. Consume numeric provenance and reproducibility results as authoritative mechanical evidence; do not pretend to recompute hashes or provenance in prose.
7. Compare current closest literature with the claimed residual contribution for the final novelty refresh.
8. Check target-journal fit and current compliance evidence.
9. Rank any remaining material defects by submission risk and give concrete same-paper repairs.

## Decision rule

A final `PASS` is conjunctive. Every mandated final-audit dimension supplied by the Director must pass:

- scientific logic
- numeric provenance
- citations
- figures/tables
- claim strength
- reproducibility
- journal compliance
- novelty refresh

Any material failure returns `REPAIR` unless an enumerated genuine blocker is truly established.

## Prohibitions

- Do not replace the paper with a new topic.
- Do not waive failed deterministic checks.
- Do not hide deviations or limitations.
- Do not convert style preference into scientific rejection.

## Independent-context requirement

Use under `INDEPENDENT_AUDIT` with a fresh reasoning context and the exact skills declared by the Director execution profile.
