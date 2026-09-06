# Using the Management Science CoScientist from ChatGPT

The CoScientist is designed so that a new ChatGPT conversation does not need old chat memory. GitHub contains the operating code, role contracts, skills and constitution; Google Drive contains the canonical scientific and Director state.

## Recommended ChatGPT setup

Use a dedicated ChatGPT Project named **Management Science CoScientist**. The Project is a convenience layer for organization and persistent instructions; it is **not** the scientific source of truth.

Keep GitHub and Google Drive connected in ChatGPT. The authoritative sources remain:

- GitHub repository: `jawadresearchai-creator/coscientist`
- GitHub constitution: `AGENTS.md`
- Reasoning-layer map: `docs/REASONING_LAYER.md`
- Scientific reasoning role: `agents/scientific_reasoning/AGENT.md`
- Independent audit role: `agents/independent_audit/AGENT.md`
- Skills: `skills/*/SKILL.md`
- Google Drive root: `MANAGEMENT_SCIENCES_COSCIENTIST_V4`
- Drive state: `MANAGEMENT_SCIENCES_COSCIENTIST_V4/state/single_paper.json`
- Drive Director: `MANAGEMENT_SCIENCES_COSCIENTIST_V4/state/director.json`
- Drive reasoning inbox: `MANAGEMENT_SCIENCES_COSCIENTIST_V4/state/director_answer.json`

Do not use Project memory or an old chat as authority when it conflicts with GitHub/Drive.

## V4.5 role rule

Every pending Director action has an `execution_profile` that names:

- the required role;
- the exact required skill versions;
- whether a fresh reasoning context is required.

Normal research actions use `SCIENTIFIC_REASONING`. Pre-freeze and final audits use `INDEPENDENT_AUDIT` and require a genuinely fresh context. Mechanical freeze and analysis actions use `DETERMINISTIC` and must not be answered as LLM judgment.

## Put this in the ChatGPT Project instructions

```text
This Project operates the Management Science CoScientist.

Canonical GitHub repository:
jawadresearchai-creator/coscientist

At the beginning of any research-operating chat, read the live canonical state rather than relying on chat memory:
1. Read AGENTS.md from the repository.
2. Read docs/REASONING_LAYER.md.
3. Read MANAGEMENT_SCIENCES_COSCIENTIST_V4/state/single_paper.json from Google Drive.
4. Read MANAGEMENT_SCIENCES_COSCIENTIST_V4/state/director.json from Google Drive.
5. Determine the exact current action_id and its execution_profile.
6. If the role is SCIENTIFIC_REASONING, read agents/scientific_reasoning/AGENT.md and every required skills/*/SKILL.md at the specified versions.
7. If the role is INDEPENDENT_AUDIT, do not audit in the same reasoning context that developed/wrote the paper. Start a fresh audit context, read agents/independent_audit/AGENT.md and every required audit skill, then attest fresh_context_attested=true in the answer.
8. If the role is DETERMINISTIC, do not fabricate a reasoning answer; execute the named mechanical workflow/tooling.
9. Inspect the current GMS data-lake state when the pending Director action depends on data.
10. Use current web/literature evidence when the action requires current scholarship, novelty closure, journal requirements or official public information.
11. Execute only the current Director action_id.
12. Write one action-ID-bound director_answer.json with execution_role and skills_used metadata and let the deterministic Director apply it; never advance lifecycle state manually.
13. Do not reopen broad topic discovery while an admitted paper remains viable.
14. Treat ordinary failures as repair work; retire only for an enumerated genuine blocker.
15. Humanizer controls manuscript expression only and must never change science, numbers, citations, claim support, limitations or frozen design.

Continue autonomously through the current paper unless a genuine human decision is scientifically necessary. Report completed phase, current state, blocker if any, and next logical step.
```

## What to write in a new chat inside the Project

For normal continuation, this short prompt is enough:

```text
Resume the Management Science CoScientist from its live canonical GitHub and Google Drive state. Read AGENTS.md, docs/REASONING_LAYER.md, single_paper.json and director.json first. Determine the current action_id and execution_profile, load the required role contract and exact skill versions, execute only that action, persist the validated answer through the canonical workflow, and continue with the next logical step. Do not rely on old chat memory and do not reopen topic discovery unless canonical state permits it.
```

If the current action requires `INDEPENDENT_AUDIT`, start that audit in a fresh chat/context rather than continuing in the development/writing context. The fresh audit must still reconnect to the same GitHub/Drive state and answer the same action ID.

If you want only status and no state changes:

```text
Read the live Management Science CoScientist state from GitHub and Google Drive and give me the current paper, stage, pending Director action, execution role, required skills, blockers, completed evidence, and exact next step. Do not mutate anything.
```

If you want the system to proceed as far as possible in the current turn:

```text
Resume the Management Science CoScientist from canonical state and proceed autonomously through every currently executable step of the active paper. Obey each Director execution_profile, use the GMS lake first, current official/web sources for genuine gaps, and the required skills at their declared versions. Stop when the next step requires a fresh independent-audit context, a real external dependency, genuine scientific blocker, or required human decision. Persist every valid state transition through the Director.
```

## Starting from an ordinary new ChatGPT chat

A Project is recommended but not required. In a normal new chat, paste the same continuation prompt. Because the source of truth is GitHub/Drive, the new chat can reconstruct the current research state without the previous conversation.

## When to start a separate chat

Start a new chat when the current conversation becomes long, when you want a clean reasoning context, or whenever the Director requires `INDEPENDENT_AUDIT`. Do not create a second research state or second CoScientist. Every chat must reconnect to the same canonical GitHub/Drive state before acting.

## One-paper rule

The Management Science kernel develops one admitted paper at a time. A new topic is discovered only when no paper is active, the current paper is submission-ready, or it has been retired for a genuine blocker. Starting a new ChatGPT chat does not start a new paper.
