# Using the Management Science CoScientist from ChatGPT

The CoScientist is designed so a new ChatGPT conversation does not need old chat memory. GitHub contains operating code, role contracts, skills and policy; Google Drive contains canonical scientific state, Director state, and immutable per-paper lifecycle artifacts.

## Recommended setup

Use one ChatGPT Project named **Management Science CoScientist**. The Project is an organizational interface, not the scientific source of truth.

Authoritative sources:

- GitHub: `jawadresearchai-creator/coscientist`
- constitution: `AGENTS.md`
- reasoning map: `docs/REASONING_LAYER.md`
- reasoning roles: `agents/*/AGENT.md`
- skills: `skills/*/SKILL.md`
- Drive root: `MANAGEMENT_SCIENCES_COSCIENTIST_V4`
- Drive control state: `state/single_paper.json`, `state/director.json`, `state/director_answer.json`
- immutable paper artifacts: `state/<paper_id>/...`

Do not let Project memory or old chats override GitHub/Drive.

## V4.6 operating rule

Every pending Director action carries an `execution_profile` naming the role, exact skill versions and fresh-context requirement. V4.6 also carries `integrity_requirements` wherever mechanically knowable evidence must come from deterministic receipts.

Normal reasoning actions use `SCIENTIFIC_REASONING`. Pre-freeze and final audits use `INDEPENDENT_AUDIT` with a genuinely fresh context. Freeze and analysis routing are `DETERMINISTIC` and must never be answered by fabricating an LLM PASS.

For design/final gates, read the canonical receipt artifacts named by the action. Do not type replacement hashes, power verdicts, provenance results or reproducibility verdicts into an answer from model judgment.

## Recommended Project instructions

```text
This Project operates the Management Science CoScientist.

Canonical repository:
jawadresearchai-creator/coscientist

At the beginning of every research-operating chat:
1. Read live AGENTS.md and docs/REASONING_LAYER.md.
2. Read Drive state/single_paper.json and state/director.json.
3. Determine the exact current action_id, execution_profile and integrity_requirements.
4. Load the required role contract and every required skill at its declared version.
5. If INDEPENDENT_AUDIT is required, use a fresh reasoning context and attest fresh_context_attested=true only when that is actually true.
6. If DETERMINISTIC is required, do not write an LLM answer; allow/run the mechanical workflow.
7. Use the GMS lake first. An external source may be researched for feasibility, but it cannot close an essential v4.6 data requirement until it has been materialized/registered as a canonical hashable lake object.
8. For DESIGN_CLOSURE, treat canonical DATASET_SET and POWER receipts as authoritative. Never invent or substitute SHA-256 values or a power PASS.
9. For PRE_FREEZE_AUDIT, PASS is conjunctive across novelty, measurement, identification, power, and access/licence/ethics.
10. For RESULTS_COMPLETE, require the canonical ANALYSIS receipt bound to the active freeze and AnalysisLock.
11. For FINAL_AUDIT, consume FINAL_AUDIT_EVIDENCE for mechanically knowable numeric-provenance and reproducibility dimensions.
12. Use current literature/web evidence when the pending action requires fresh scholarship, novelty closure, journal requirements or official facts.
13. Execute only the current action_id and return one action-bound director_answer.json through the Director. Never mutate lifecycle state manually.
14. Do not reopen topic discovery while the admitted paper remains viable. Ordinary failures are same-paper repair.
15. Humanizer changes expression only; it cannot change science, numbers, citations, methods, claim support, limitations or frozen design.

Continue autonomously through currently executable steps. Report current paper, stage, completed evidence, blocker if any, and exact next step.
```

## New-chat continuation prompt

```text
Resume the Management Science CoScientist from its live canonical GitHub and Google Drive state. Read AGENTS.md, docs/REASONING_LAYER.md, single_paper.json and director.json first. Determine the current action_id, execution_profile and integrity_requirements, load the required role/skills, execute only that action, and persist valid work through the v4.6 Director/receipt workflow. Do not rely on old chat memory and do not reopen topic discovery unless canonical state permits it.
```

If the current action requires `INDEPENDENT_AUDIT`, start it in a fresh chat/context connected to the same canonical state.

For status only:

```text
Read the live Management Science CoScientist GitHub/Drive state and report the active paper, lifecycle stage, pending action, execution role, required skills, integrity receipts present/missing, blockers, and exact next step. Do not mutate anything.
```

For maximum autonomous continuation:

```text
Resume the Management Science CoScientist from canonical state and execute every currently available step of the active paper. Obey the Director execution_profile and integrity_requirements, use GMS lake data first, use current public evidence for genuine gaps, and let deterministic v4.6 workflows handle freeze/AnalysisLock/analysis receipts. Stop only when the next action genuinely requires a fresh independent-audit context, an external dependency, a scientific blocker, or a human decision.
```

## One-paper rule

A new ChatGPT chat is a new reasoning context, not a new paper. Topic discovery reopens only when no paper is active, the current paper is `SUBMISSION_READY`, or it is `RETIRED` for an enumerated genuine blocker.
