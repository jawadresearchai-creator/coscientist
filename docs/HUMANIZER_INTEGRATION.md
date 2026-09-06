# Humanizer v2 integration policy

## Status

`skills/humanizer/SKILL.md` is the canonical manuscript-style policy for the Management Science CoScientist.

Humanizer is mandatory whenever a reasoning plane drafts, rewrites, or performs final stylistic polishing of manuscript prose. It is deliberately **not** a scientific-validation component.

## Execution order

Humanizer sits above the deterministic evidence layer and below final delivery:

```text
verified research state / literature / design / results
        -> numeric and citation provenance controls
        -> manuscript drafting or revision
        -> Humanizer v2 style pass
        -> scientific/final manuscript audit
        -> submission-ready manuscript
```

Scientific corrections always take priority over style. If a scientific audit changes meaning, evidence, estimates, citations, methods, limitations, or claim strength, rerun Humanizer only after those scientific changes are complete.

## Mandatory use

Reasoning planes must read and apply `skills/humanizer/SKILL.md` before:

- drafting Introduction, theory, Methods, Results, Discussion, limitations, conclusion, abstract, or other manuscript prose;
- revising manuscript prose after reviewer/audit repairs;
- whole-manuscript final polish;
- producing submission-ready prose.

The final style pass must include the skill's sentence-level, paragraph-level, section-level, and whole-manuscript audits.

## Hard boundary

Humanizer may change expression, rhythm, diction, syntax, paragraph architecture, transitions, emphasis, and rhetorical form.

Humanizer must not independently:

- change the scientific question, estimand, design, treatment, outcome, sample, methods, model, or analysis plan;
- change numerical values, symbols, result tokens, statistical conclusions, or frozen design identities;
- add or remove factual claims;
- add, remove, replace, or validate citations;
- decide novelty;
- judge whether evidence supports a claim;
- weaken or strengthen a claim beyond what the scientific audit permits;
- hide limitations or non-significant results;
- introduce artificial errors to imitate human writing.

If Humanizer conflicts with evidence integrity, provenance, journal requirements, or the frozen science, those higher-order constraints win.

## CoScientist operating rule

The deterministic kernel does not call an LLM and therefore does not "run" Humanizer itself. Instead, every manuscript-writing reasoning plane is governed by `AGENTS.md`, which requires it to load this skill before prose work. Scientific state remains in Drive and code/policy remains in GitHub.

This keeps the role separation explicit:

- deterministic core: state, locks, hashes, data, statistics, provenance;
- scientific reasoning plane: literature, theory, interpretation, hostile audit;
- Humanizer: manuscript expression only.

## Source

Integrated from the user-supplied `Humanizer_Skill_v2.0(1).zip`, Humanizer version 2.0.
