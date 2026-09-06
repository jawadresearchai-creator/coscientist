---
name: LiteratureTheory
version: 1.0
description: Close the current literature gap and build a coherent theory/mechanism record for the one active Management Science paper.
---

# LiteratureTheory

## Scope

Use only for the active paper. Do not generate replacement topics or reopen broad discovery.

## Inputs

- Topic Charter and current paper state
- pending Director question/action id
- current literature evidence
- relevant GMS/OpenAlex literature context
- known threats and target journal family

## Procedure

1. Translate the research question into 3–6 searchable conceptual components: phenomenon, treatment/exposure, outcome, mechanism, setting, design.
2. Build the closest-paper set using current scholarly sources; use bibliographic graph/lake data for breadth and direct source verification for substantive claims.
3. For each closest paper record: exact identity, year, design/context, central claim, why it is close, and what it does not resolve.
4. Distinguish a true direct scoop from merely adjacent work. Do not use a numeric novelty score as a kill rule.
5. State the residual contribution in one falsifiable paragraph: what remains unanswered, why it matters, and what this study can establish that the closest papers cannot.
6. Build one principal explanatory chain:

```text
exposure/treatment -> mechanism -> intermediate consequence -> outcome
```

7. Identify the strongest rival explanation, not a weak straw man.
8. Derive observable implications that discriminate the principal mechanism from the rival where possible.
9. Translate theory into required constructs and data needs without deciding source suitability mechanically.
10. Update known threats conservatively.

## Quality tests

A strong output should answer:

- Which papers are genuinely closest?
- What exact residual contribution survives them?
- Is the mechanism causally/explanatorily coherent?
- What would the rival predict instead?
- Which observable patterns would distinguish them?
- Which constructs must be measured for the paper to remain viable?

## Prohibitions

- Do not fabricate citations or infer support from titles/abstract snippets alone.
- Do not add decorative theory stacks.
- Do not claim novelty because wording differs.
- Do not change lifecycle state.
- Do not decide dataset hashes, freeze, power, or execution.

## Output boundary

Return the structured fields required by `DEVELOP_LITERATURE_THEORY`, plus the required execution-role/skill metadata. The Director remains the state authority.
