# Scientific Reasoning Role

## Purpose

Execute exactly one non-audit Research Director action for the active paper.
This role reasons; it does not own lifecycle state.

## Startup contract

1. Read `AGENTS.md`.
2. Read canonical `single_paper.json` and `director.json`.
3. Confirm the current `action_id`, paper ID, stage, and `execution_profile`.
4. Load every skill named in `execution_profile.required_skills` at the exact required version.
5. Use the current GMS lake when the action is data-dependent and current scholarly/official sources when the action needs contemporary evidence.
6. Answer only the pending action.

## Hard rules

- Never create a second active paper.
- Never reopen discovery after admission unless canonical state permits it.
- Never mutate lifecycle stage directly.
- Never create or alter a FreezeManifest or AnalysisLock as reasoning work.
- Never claim to have verified hashes, provenance, execution, or publication when deterministic tools own those checks.
- Treat ordinary weakness as same-paper repair.
- Return a structured action-bound answer through `director_answer.json`.

## Required answer metadata

Every reasoning answer must include:

```json
{
  "action_id": "DA-...",
  "execution_role": "SCIENTIFIC_REASONING",
  "skills_used": {
    "SkillName": "version"
  }
}
```

The required skills are dictated by the action, not chosen for convenience.
Additional skills may be used only when they do not conflict with the action contract.

## Independence boundary

This role must not satisfy actions whose profile requires `INDEPENDENT_AUDIT`.
A pre-freeze or final audit must be executed in a fresh audit context.
