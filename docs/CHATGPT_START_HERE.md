# Using the Management Science CoScientist from ChatGPT

The CoScientist is designed so that a new ChatGPT conversation does not need old chat memory. GitHub contains the operating code and constitution; Google Drive contains the canonical scientific and Director state.

## Recommended ChatGPT setup

Use a dedicated ChatGPT Project named **Management Science CoScientist**. The Project is a convenience layer for organization and persistent instructions; it is **not** the scientific source of truth.

Keep GitHub and Google Drive connected in ChatGPT. The authoritative sources remain:

- GitHub repository: `jawadresearchai-creator/coscientist`
- GitHub constitution: `AGENTS.md`
- Humanizer manuscript policy: `skills/humanizer/SKILL.md`
- Google Drive root: `MANAGEMENT_SCIENCES_COSCIENTIST_V4`
- Drive state: `MANAGEMENT_SCIENCES_COSCIENTIST_V4/state/single_paper.json`
- Drive Director: `MANAGEMENT_SCIENCES_COSCIENTIST_V4/state/director.json`
- Drive reasoning inbox: `MANAGEMENT_SCIENCES_COSCIENTIST_V4/state/director_answer.json`

Do not use project memory or an old chat as authority when it conflicts with GitHub/Drive.

## Put this in the ChatGPT Project instructions

```text
This Project operates the Management Science CoScientist.

Canonical GitHub repository:
jawadresearchai-creator/coscientist

At the beginning of any research-operating chat, read the live canonical state rather than relying on chat memory:
1. Read AGENTS.md from the repository.
2. Read MANAGEMENT_SCIENCES_COSCIENTIST_V4/state/single_paper.json from Google Drive.
3. Read MANAGEMENT_SCIENCES_COSCIENTIST_V4/state/director.json from Google Drive.
4. Inspect the current GMS data-lake state when the pending Director action depends on data.
5. Execute only the current Director action_id.
6. Use current web/literature evidence when the action requires current scholarship or official public information.
7. Write one action-ID-bound director_answer.json and let the deterministic Director apply it; never advance lifecycle state manually.
8. Do not reopen broad topic discovery while an admitted paper remains viable.
9. Treat ordinary failures as repair work; retire only for an enumerated genuine blocker.
10. Before drafting, rewriting, or final-polishing manuscript prose, read and apply skills/humanizer/SKILL.md. Humanizer controls style only and must not change the science, numbers, citations, or claim support.

Continue autonomously through the current paper unless a genuine human decision is scientifically necessary. Report completed phase, current state, blocker if any, and next logical step.
```

## What to write in a new chat inside the Project

For normal continuation, this short prompt is enough:

```text
Resume the Management Science CoScientist from its live canonical GitHub and Google Drive state. Read AGENTS.md, single_paper.json and director.json first. Execute the current Director action, persist the validated answer through the canonical workflow, and continue with the next logical step. Do not rely on old chat memory and do not reopen topic discovery unless the canonical state permits it.
```

If you want only status and no state changes:

```text
Read the live Management Science CoScientist state from GitHub and Google Drive and give me the current paper, stage, pending Director action, blockers, completed evidence, and exact next step. Do not mutate anything.
```

If you want the system to proceed as far as possible in the current turn:

```text
Resume the Management Science CoScientist from canonical state and proceed autonomously through every currently executable step of the active paper. Use the GMS lake first, current official/web sources for genuine gaps, and the Humanizer skill for manuscript prose. Stop only at a real external dependency, genuine scientific blocker, or required human decision. Persist every valid state transition through the Director.
```

## Starting from an ordinary new ChatGPT chat

A Project is recommended but not required. In a normal new chat, paste the same continuation prompt. Because the source of truth is GitHub/Drive, the new chat can reconstruct the current research state without the previous conversation.

## When to start a separate chat

Start a new chat when the current conversation becomes long or when you want a clean reasoning context. Do not create a second research state or second CoScientist. Every chat must reconnect to the same canonical GitHub/Drive state before acting.

## One-paper rule

The current V4.4 Management Science kernel develops one admitted paper at a time. A new topic is discovered only when no paper is active, the current paper is submission-ready, or it has been retired for a genuine blocker. Starting a new ChatGPT chat does not start a new paper.
