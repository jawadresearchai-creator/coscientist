---
name: CitationIntegrity
version: 1.0
description: Verify semantic citation integrity: existence, metadata, claim support, scope, currency and correct attachment.
---

# CitationIntegrity

## Purpose

CitationIntegrity is a semantic evidence audit, not a formatting pass.

For each material manuscript claim:

```text
claim -> citation -> what source actually establishes -> verdict
```

Allowed verdicts:

- `MATCH`
- `PARTIAL`
- `MISMATCH`
- `UNVERIFIABLE`

## Procedure

1. Verify that the cited work exists and that title/authors/year/DOI or stable identifier are correct.
2. Read enough of the source to determine what it actually establishes; do not infer support from a title alone.
3. Compare population, setting, construct, treatment/exposure, outcome, design and time period to the manuscript claim.
4. Determine whether the manuscript uses stronger causal, general, current or mechanistic language than the source supports.
5. Verify that the citation is attached to the correct clause/claim.
6. Check whether a claim framed as current requires newer evidence.
7. Identify citation chains where the cited secondary paper itself does not establish the original fact.
8. Record a precise repair: narrow claim, replace/add verified source, or mark unsupported.

## Boundaries

CitationIntegrity may recommend changes to claim wording or source selection, but scientific claim-strength changes must remain consistent with the paper's verified evidence and final audit.

## Prohibitions

- Do not fabricate missing references.
- Do not treat citation count as evidence quality.
- Do not validate a source solely because metadata resolves.
- Do not alter numeric results or frozen design.
- Do not perform Humanizer-style cosmetic rewriting.

## Output

Return a structured claim-level audit or the citation-related fields required by the current Director action, with source identifiers and concise evidence for every non-MATCH verdict.
