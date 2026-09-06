# CoScientist V4.5 — single-paper Management Science research engine

CoScientist is a deterministic research-governance and analysis kernel for **one Management Science paper at a time**. Reasoning models may perform judgment, literature interpretation, adversarial review and prose through explicit handoffs, but `src/coscientist/` never calls an LLM directly.

## Operating rule

Exactly one paper may be scientifically active. Broad topic discovery is allowed only when there is no active paper, or when the current paper is `SUBMISSION_READY` or has been `RETIRED` for a genuine blocker.

After topic admission, all literature search, data work, theory, design, analysis and manuscript work serve the same paper. Ordinary friction triggers repair/evolution, not replacement.

The canonical lifecycle is:

```text
NO_ACTIVE_PAPER
  -> SELECTED
  -> DEVELOPING
  -> DATA_FEASIBLE
  -> DESIGN_READY
  -> FROZEN
  -> ANALYZING
  -> RESULTS_COMPLETE
  -> MANUSCRIPT
  -> FINAL_AUDIT
  -> SUBMISSION_READY
```

`RETIRED` is permitted only for a genuine scientific blocker: direct scoop with no residual contribution, essential-data/measurement impossibility, identification impossibility, fundamental power failure, legal/ethical impossibility, or fundamental construct failure.

See `docs/SINGLE_PAPER_RESEARCH_HANDBOOK.md` for the research process and `docs/REASONING_LAYER.md` for V4.5 role/skill routing.

## Research Director

The persistent Single-Paper Research Director converts the canonical lifecycle into exactly one next action at a time and stores that action in Google Drive so a runner can stop and resume without losing scientific context.

The three small control records are:

```text
single_paper.json       scientific lifecycle and one active Topic Charter
director.json           one persistent next action + compact phase records
director_answer.json    one overwriteable reasoning-plane answer inbox
```

A stale answer is a no-op. A runner restart does not recreate topic discovery. A new paper resets Director records instead of inheriting historical candidate state.

## V4.5 reasoning roles

V4.5 adds an execution profile around every Director action while keeping V4.4 scientific-state logic unchanged.

There are only three architectural roles:

1. **Research Director** — deterministic authority; not an LLM agent.
2. **Scientific Reasoning** — normal LLM context for literature, theory, design reasoning, manuscript drafting and ordinary repair.
3. **Independent Audit** — fresh adversarial LLM context for pre-freeze and final audit.

The current skills are:

- Humanizer v2.0
- LiteratureTheory v1.0
- StudyDesignReasoner v1.0
- HostileReviewer v1.0
- ManuscriptWriter v1.0
- CitationIntegrity v1.0
- FinalAuditReasoner v1.0

Action routing is explicit:

| Director action | Execution role | Required skills |
|---|---|---|
| `DISCOVER_TOPIC` | SCIENTIFIC_REASONING | none permanently required |
| `DEVELOP_LITERATURE_THEORY` | SCIENTIFIC_REASONING | LiteratureTheory, CitationIntegrity |
| `DATA_FEASIBILITY` | SCIENTIFIC_REASONING | StudyDesignReasoner |
| `DESIGN_CLOSURE` | SCIENTIFIC_REASONING | StudyDesignReasoner |
| `PRE_FREEZE_AUDIT` | INDEPENDENT_AUDIT | HostileReviewer, CitationIntegrity |
| `CREATE_FREEZE` | DETERMINISTIC | none |
| `RUN_ANALYSIS` | DETERMINISTIC | none |
| `MANUSCRIPT_DRAFT` | SCIENTIFIC_REASONING | ManuscriptWriter, CitationIntegrity, Humanizer |
| `FINAL_AUDIT` | INDEPENDENT_AUDIT | FinalAuditReasoner, CitationIntegrity, HostileReviewer |
| `REPAIR` | SCIENTIFIC_REASONING | defect-specific |

Reasoning answers must declare `execution_role` and exact `skills_used` versions. Independent-audit answers also require `fresh_context_attested: true`. This is auditable metadata; the operating environment must actually use a fresh reasoning context.

Existing V4.4 pending actions remain valid. The first V4.5 ensure/status pass derives and persists the execution profile without changing the existing action ID.

Use the V4.5 facade for operating commands:

```bash
python -m coscientist.director_v45 --state state/single_paper.json \
  --director state/director.json --catalog state/gms_lake_catalog.json status

python -m coscientist.director_v45 --state state/single_paper.json \
  --director state/director.json --catalog state/gms_lake_catalog.json \
  ensure --action-out state/director_action.json

python -m coscientist.director_v45 --state state/single_paper.json \
  --director state/director.json --catalog state/gms_lake_catalog.json \
  apply --answer state/director_answer.json
```

## Data rule — lake first

The Global Management Science data lake owns ingestion; CoScientist owns scientific selection. For each required construct the order is:

1. `03_RESEARCH` research-ready mart;
2. `02_CURATED` object;
3. `01_RAW_IMMUTABLE` / query-layer materialisation;
4. admissible official external source;
5. defensible pre-freeze evolution of the same paper;
6. genuine essential-data blocker.

Do not reacquire data already held adequately. Large/query-native sources are materialised as the smallest useful extract and that extract is frozen.

## Freeze only after feasibility

Before crossing the outcome lock, establish outcome-blind source access/licence, schema and joins, granularity and coverage, sample construction, treatment/support variation, essential-variable availability, realistic missingness/attrition, pre-period noise/dependence, plausible power/MDE, and exact dataset SHA-256 identities.

Then run one consolidated hostile pre-freeze audit. A Director status of `CREATE_FREEZE` is not itself a freeze: the deterministic mechanical step writes a real `FreezeManifest`, and only then does the paper become `FROZEN`.

## Two locks

The design freeze fixes **which scientific question/design**. The analysis lock fixes **which code/environment/plan** before confirmatory outcome access.

```text
Drive state -> analysis-lock verify -> frozen data -> freeze-verify
           -> R/Python/Stan driver -> streams -> results bridge
           -> strict numeric provenance -> Drive results
```

Scientific state and results live in Google Drive, never Git. Code, tests, workflows, role contracts and skills live in GitHub.

## Humanizer boundary

Humanizer is mandatory for manuscript drafting/revision/final polish but controls expression only. It must not independently change scientific meaning, methods, design, numbers, result tokens, citations, evidence support, claim strength, limitations or frozen scientific state.

## Install and test

```bash
pip install -e ".[dev]"
pytest -q            # 340 tests expected after the v4.5 reasoning-layer migration
```

The suite includes package-import integrity checks, so every production module must import successfully. `GUARANTEES.yaml` remains the executable contract registry, extended by versioned `GUARANTEES*.yaml` files.

## ChatGPT operation

Use one ChatGPT Project named **Management Science CoScientist**. GitHub/Drive remain authoritative over chat memory. In every new chat, read `AGENTS.md`, `docs/REASONING_LAYER.md`, `single_paper.json`, `director.json`, then load the role contract and exact skills named by the pending action's execution profile.

If an action requires `INDEPENDENT_AUDIT`, execute it in a fresh chat/context that reconnects to the same canonical state; do not create another CoScientist or another paper.

See `docs/CHATGPT_START_HERE.md` for the copy/paste bootstrap prompt.

## Scope

The current engine is optimized for reproducible secondary/open-data quantitative Management Science research: archival, panel, quasi-experimental, event-study and related designs. Future study-type modules may extend that scope without weakening the one-active-paper rule or the judgment-vs-enforcement boundary.
