# Using the Management Science CoScientist from ChatGPT

The CoScientist is designed so a new ChatGPT conversation does not need old chat memory. GitHub contains operating code, role contracts, skills and policy; Google Drive contains canonical scientific state, Director state, and immutable per-paper lifecycle artifacts.

## Recommended setup

Use one ChatGPT Project named **Management Science CoScientist**. The Project is an organizational interface, not the scientific source of truth.

Authoritative sources:
- GitHub: `jawadresearchai-creator/coscientist`
- constitution: `AGENTS.md`
- reasoning map: `docs/REASONING_LAYER.md`
- roles: `agents/*/AGENT.md`
- skills: `skills/*/SKILL.md`
- Drive root: `MANAGEMENT_SCIENCES_COSCIENTIST_V4`
- Drive control state: `state/single_paper.json`, `state/director.json`, `state/director_answer.json`
- immutable paper artifacts: `state/<paper_id>/...`

Do not let Project memory or old chats override GitHub/Drive.

## V4.6.1 operating rule

Every pending Director action carries an `execution_profile` naming the role, exact skill versions and fresh-context requirement. Integrity requirements use deterministic receipts wherever mechanically knowable evidence must not come from LLM assertion.

Normal reasoning actions use `SCIENTIFIC_REASONING`. Pre-freeze and final audits use `INDEPENDENT_AUDIT`. Freeze/analysis routing and explicit owner withdrawal are deterministic control actions.

### Owner can change topics

The one-paper rule means **one active paper at a time**, not "the owner must finish every admitted topic."

If the owner explicitly says they do not want to continue the current paper, or directs a switch to another topic, execute the dedicated owner-withdrawal route. Do not demand a scientific blocker and do not falsely mark the paper `RETIRED`.

Owner withdrawal places the paper in `USER_WITHDRAWN`, releases the one-paper lock, clears the stale pending action through Director reconciliation, and immediately reopens discovery. If the owner names the next topic, preserve it as `owner_next_topic_direction`; the next discovery action should evaluate/refine that topic rather than substitute an unrelated one.

Never infer withdrawal from null findings, failed models, difficult repairs or ordinary user frustration. It requires an explicit owner instruction.

Example deterministic command:

```bash
python -m coscientist.director_v461 \
  --state state/single_paper.json \
  --director state/director.json \
  --catalog state/gms_lake_catalog.json \
  withdraw --reason "Owner changed research priority" \
  --next-topic "event study of the ChatGPT Astra release"
```

## Recommended Project instructions

```text
This Project operates the Management Science CoScientist.

Canonical repository:
jawadresearchai-creator/coscientist

At the beginning of every research-operating chat:
1. Read live AGENTS.md and docs/CHATGPT_START_HERE.md.
2. Read Drive state/single_paper.json and state/director.json.
3. Determine the exact current action_id, execution_profile and integrity_requirements.
4. Load the required role contract and required skills.
5. If INDEPENDENT_AUDIT is required, use a genuinely fresh reasoning context.
6. If DETERMINISTIC is required, execute the mechanical route; do not fabricate an LLM PASS.
7. Use the GMS lake first. Essential external data must be materialized/registered before feasibility closes.
8. Treat canonical DATASET_SET, POWER, ANALYSIS and FINAL_AUDIT_EVIDENCE receipts as authoritative where required.
9. Execute only the current Director action and persist validated state through the canonical workflow.
10. Keep exactly one paper active. Do not create parallel papers or reserve queues.
11. If I explicitly tell you to stop/switch the current topic, use USER_WITHDRAWN. I do not need a scientific blocker. Preserve any named next-topic direction and reopen one-paper discovery immediately.
12. Ordinary execution/scientific problems are same-paper repair unless I explicitly withdraw the paper or a genuine blocker is proven.
13. Humanizer changes expression only; it cannot change science, numbers, citations, methods, claim support, limitations or frozen design.

Continue autonomously through currently executable steps. Report current paper, stage, completed evidence, blocker if any, and exact next step.
```

## New-chat continuation prompt

```text
Resume the Management Science CoScientist from its live canonical GitHub and Google Drive state. Read AGENTS.md, docs/CHATGPT_START_HERE.md, single_paper.json and director.json first. Determine the current action_id, execution_profile and integrity requirements, load the required role/skills, execute only that action, and persist valid work through the v4.6.1 workflow. Do not rely on old chat memory.
```

If the current action requires `INDEPENDENT_AUDIT`, start it in a fresh context connected to the same canonical state.

For status only:

```text
Read the live Management Science CoScientist GitHub/Drive state and report the active paper, lifecycle stage, pending action, execution role, required skills, integrity receipts present/missing, blockers, owner next-topic direction if any, and exact next step. Do not mutate anything.
```

For maximum autonomous continuation:

```text
Resume the Management Science CoScientist from canonical state and execute every currently available step. Obey the Director execution_profile and integrity requirements, use GMS lake data first, use current public evidence for genuine gaps, and let deterministic workflows handle freeze/AnalysisLock/receipts. Stop only when the next action genuinely requires a fresh independent-audit context, an unresolved external dependency, a scientific blocker, or a human decision.
```

## One-paper rule

A new chat is a new reasoning context, not a new paper. Topic discovery reopens when no paper is active, the paper is `SUBMISSION_READY`, it is `RETIRED` for a genuine blocker, **or the owner explicitly withdraws it into `USER_WITHDRAWN`**.
