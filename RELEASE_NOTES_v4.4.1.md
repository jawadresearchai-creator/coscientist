# CoScientist v4.4.1 — Humanizer manuscript policy

## Purpose

This release integrates the user-supplied Humanizer v2.0 skill into the Management Science CoScientist without allowing a style layer to alter scientific state.

## Added

- `skills/humanizer/SKILL.md` — canonical Humanizer v2.0 manuscript-writing skill.
- `docs/HUMANIZER_INTEGRATION.md` — explicit boundary between style, scientific reasoning, and deterministic provenance.
- `docs/CHATGPT_START_HERE.md` — reusable ChatGPT Project/new-chat bootstrap instructions.

## Constitution change

`AGENTS.md` now requires every reasoning plane to read and apply Humanizer before drafting, rewriting, or final-polishing manuscript prose.

Humanizer may change expression, rhythm, diction, sentence/paragraph architecture, transitions, and rhetorical restraint. It may not independently change scientific meaning, study logic, methods, numerical results, result tokens, citations, evidence support, claim strength, limitations, or the frozen design.

Scientific audits remain authoritative. If a scientific repair changes the manuscript, Humanizer is rerun afterward as a style-only pass.

## ChatGPT usage

The recommended user interface is a dedicated ChatGPT Project named `Management Science CoScientist` with the permanent instructions supplied in `docs/CHATGPT_START_HERE.md`. A Project is optional; any ordinary new chat can resume by reading the same canonical GitHub/Drive state.

The GitHub/Drive state remains authoritative over Project memory or prior conversations.
