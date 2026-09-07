# Using the Management Science CoScientist from ChatGPT

The CoScientist supports **multiple independent papers at the same time**. GitHub contains operating code, role contracts, skills and policy; Google Drive contains the canonical paper registry, paper-scoped scientific state, Director state and immutable artifacts.

## Authoritative sources

- GitHub: `jawadresearchai-creator/coscientist`
- constitution: `AGENTS.md`
- reasoning map: `docs/REASONING_LAYER.md`
- roles: `agents/*/AGENT.md`
- skills: `skills/*/SKILL.md`
- Drive root: `MANAGEMENT_SCIENCES_COSCIENTIST_V4`
- root paper registry: `state/paper_registry.json`
- paper state: `state/<paper_id>/paper_state.json`
- paper Director: `state/<paper_id>/director.json`
- paper answer inbox: `state/<paper_id>/director_answer.json`
- immutable paper artifacts: `state/<paper_id>/...`

Do not let Project memory or old chats override GitHub/Drive.

## Multi-paper operating rule

There is no global one-paper lock.

Each registered paper has an independent lifecycle, pending Director action, freeze, AnalysisLock, receipts, datasets, results and manuscript. A `focus_paper_id` in `paper_registry.json` is only a UI/default-routing convenience and never prevents other papers from remaining active.

The legacy root files `state/single_paper.json`, `state/director.json` and `state/director_answer.json` are compatibility artifacts. New work routes through `paper_registry.json` and paper-scoped paths.

## At the beginning of every research-operating chat

1. Read live `AGENTS.md` and this file.
2. Read Drive `state/paper_registry.json`.
3. Determine which `paper_id` the user is asking about. If none is named, use `focus_paper_id` only as the default.
4. Read that paper's `paper_state.json` and `director.json`.
5. Determine the exact current `action_id`, `execution_profile` and integrity requirements for that paper.
6. Load the required role and skills.
7. Execute only that paper/action.
8. Persist valid work only into that paper's Drive namespace.
9. Never mutate another paper as a side effect.
10. Multiple papers may remain ACTIVE/REPAIR/PAUSED concurrently.

## Paper switching

Switching conversational focus from Paper A to Paper B does **not** withdraw, retire, freeze, pause or otherwise terminate Paper A.

The user can say:

```text
Work on MS-ASTRA-REVALUE-2026 now.
```

and later:

```text
Switch to MS-AI-PROC-COMP-2025.
```

Both papers remain independently active unless the user explicitly changes a paper's registry/lifecycle status.

## Owner withdrawal

Withdrawal is paper-local. An explicit request such as `stop MS-ASTRA-REVALUE-2026` may place only that paper in `USER_WITHDRAWN`. It must not affect any other paper.

Never infer withdrawal from a difficult repair, failed model, non-significance or the user temporarily working on another paper.

## Director routing

Use:

```bash
python -m coscientist.director_multi \
  --registry state/paper_registry.json \
  --paper-id <PAPER_ID> \
  status
```

To ensure/emit the next action for one paper:

```bash
python -m coscientist.director_multi \
  --registry state/paper_registry.json \
  --paper-id <PAPER_ID> \
  ensure
```

The mature Director logic remains paper-local. There is at most one pending Director action **per paper**, not one globally.

## Adding another paper

A new paper may be admitted while others are active. It gets its own state namespace under `state/<paper_id>/` and is added to `paper_registry.json`.

Example:

```bash
python -m coscientist.multi_paper \
  --registry state/paper_registry.json \
  create --charter charter.json
```

No existing paper needs to be withdrawn first.

## Legacy migration

To migrate the current legacy root paper into the multi-paper registry without deleting the old root files:

```bash
python -m coscientist.multi_paper \
  --registry state/paper_registry.json \
  migrate-legacy
```

The migration copies the legacy paper state/Director/answer into that paper's namespace and leaves the originals untouched for backward compatibility.

## Recommended Project instructions

```text
This Project operates the Management Science CoScientist in MULTI_PAPER mode.

Canonical repository:
jawadresearchai-creator/coscientist

At the beginning of every research-operating chat:
1. Read live AGENTS.md and docs/CHATGPT_START_HERE.md.
2. Read Drive state/paper_registry.json.
3. Select the explicit paper_id requested by the user, or use focus_paper_id only when none is specified.
4. Read state/<paper_id>/paper_state.json and state/<paper_id>/director.json.
5. Determine that paper's exact action_id, execution_profile and integrity requirements.
6. Execute only that paper's action and write only into that paper's namespace.
7. Multiple papers may remain active concurrently; switching focus does not withdraw another paper.
8. Use the GMS lake first and preserve each paper's independent outcome/freeze/analysis locks.
9. Humanizer changes expression only; it cannot change science, numbers, citations, methods, claim support, limitations or frozen design.
10. Continue autonomously through currently executable steps and report paper-specific status, blockers and exact next step.
```

## New-chat continuation prompt

```text
Resume the Management Science CoScientist from its live canonical GitHub and Google Drive MULTI_PAPER state. Read AGENTS.md, docs/CHATGPT_START_HERE.md and state/paper_registry.json first. Determine the paper_id I am asking about, then read that paper's paper_state.json and director.json, execute only its current action, and preserve all other papers unchanged.
```

For status across all papers:

```text
Read the live paper_registry.json and report every registered paper, its registry status, lifecycle stage, pending action, blockers and next step. Do not mutate anything.
```

For maximum autonomous continuation of one paper:

```text
Resume <PAPER_ID> from canonical multi-paper state and execute every currently available step for that paper only. Preserve all other paper state unchanged. Stop only when the next action genuinely requires a fresh independent-audit context, unresolved external dependency, scientific blocker or human decision.
```
