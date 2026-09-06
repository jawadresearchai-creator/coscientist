# CoScientist V4.4 — single-paper Management Science research engine

CoScientist is a deterministic research-governance and analysis kernel for
**one Management Science paper at a time**. Reasoning models may perform
judgment, literature interpretation, adversarial review and prose through
explicit handoffs, but `src/coscientist/` never calls an LLM directly.

## Operating rule

Exactly one paper may be scientifically active. Broad topic discovery is
allowed only when there is no active paper, or when the current paper is
`SUBMISSION_READY` or has been `RETIRED` for a genuine blocker.

After topic admission, all literature search, data work, theory, design,
analysis and manuscript work serve the same paper. Ordinary friction triggers
repair/evolution, not replacement.

Historical candidate IDs, rankings, novelty scores, courts and continuation
handoffs are archival only. They do not seed, rank, kill or revive the active
paper.

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

`RETIRED` is permitted only for a genuine scientific blocker: direct scoop
with no residual contribution, essential-data/measurement impossibility,
identification impossibility, fundamental power failure, legal/ethical
impossibility, or fundamental construct failure.

See `docs/SINGLE_PAPER_RESEARCH_HANDBOOK.md` for the full operating process.

## Research Director

V4.4 adds a persistent **Single-Paper Research Director**. It converts the
canonical lifecycle into exactly one next action at a time and stores that
action in Google Drive so a GitHub-hosted runner can stop and resume without
losing scientific context.

The three small control records are:

```text
single_paper.json       scientific lifecycle and one active Topic Charter
director.json           one persistent next action + compact phase records
director_answer.json    one overwriteable reasoning-plane answer inbox
```

The cycle is idempotent:

1. refresh the current GMS lake catalog;
2. pull `single_paper.json` and `director.json` from Drive;
3. pull `director_answer.json` if present;
4. apply it only when its `action_id` matches the current pending action;
5. create at most one next Director action;
6. push the two canonical state files back to Drive.

A stale answer is a no-op. A runner restart does not recreate topic discovery.
A new paper resets Director records instead of inheriting historical candidate
state.

Director actions cover bounded topic discovery, literature/theory closure,
data feasibility, design closure, hostile pre-freeze audit, real freeze
creation, analysis routing, manuscript drafting and final audit.

```bash
python -m coscientist.director --state state/single_paper.json \
  --director state/director.json --catalog state/gms_lake_catalog.json status

python -m coscientist.director --state state/single_paper.json \
  --director state/director.json --catalog state/gms_lake_catalog.json \
  ensure --action-out state/director_action.json

python -m coscientist.director --state state/single_paper.json \
  --director state/director.json --catalog state/gms_lake_catalog.json \
  apply --answer state/director_answer.json
```

## Data rule — lake first

The Global Management Science data lake owns ingestion; CoScientist owns
scientific selection. For each required construct the order is:

1. `03_RESEARCH` research-ready mart;
2. `02_CURATED` object;
3. `01_RAW_IMMUTABLE` / query-layer materialisation;
4. admissible official external source;
5. defensible pre-freeze evolution of the same paper;
6. genuine essential-data blocker.

Do not reacquire data already held adequately. Large/query-native sources are
materialised as the smallest useful extract and that extract is frozen.

The Director validates that every essential construct has either a suitable
lake route or a verified free authoritative external route before moving the
paper to `DATA_FEASIBLE`. Missing essential data keeps the same paper in
`REPAIR`.

## Single-paper state

Local commands:

```bash
python -m coscientist.single_paper init
python -m coscientist.single_paper status
python -m coscientist.single_paper next-action
python -m coscientist.single_paper admit --charter topic_charter.json
python -m coscientist.single_paper transition --stage DEVELOPING
python -m coscientist.single_paper evolve --note "measurement refinement"
```

The scheduled cycle synchronizes the canonical `single_paper.json` and
`director.json` with the V4 Google Drive state folder. While a viable paper is
active, discovery is mechanically locked.

## Freeze only after feasibility

Before crossing the outcome lock, establish outcome-blind:

- source access and licence;
- schema and join keys;
- granularity and coverage;
- sample construction;
- treatment/support variation;
- essential-variable availability;
- realistic missingness/attrition;
- pre-period noise/dependence;
- plausible power/MDE for the intended estimator;
- exact dataset SHA-256 identities.

Then run one consolidated hostile pre-freeze audit. There is no hard numerical
novelty threshold and no permanent candidate tournament. A Director status of
`CREATE_FREEZE` is not itself a freeze: `director mechanical` writes a real
`FreezeManifest`, and only then does the paper become `FROZEN`.

## Two locks

The design freeze fixes **which scientific question/design**. The analysis
lock fixes **which code/environment/plan** before confirmatory outcome access.

```text
Drive state -> analysis-lock verify -> frozen data -> freeze-verify
           -> R/Python/Stan driver -> streams -> results bridge
           -> strict numeric provenance -> Drive results
```

Scientific state and results live in Google Drive, never Git. Code, tests,
workflows and registries live in GitHub.

## Analysis commands

```bash
coscientist lake-sync       --out state/gms_lake_catalog.json
coscientist gms-query-plan  --catalog state/gms_lake_catalog.json ...
coscientist freeze-verify   --manifest state/freeze.json --data-dir data
coscientist analysis-lock   --freeze state/freeze.json
coscientist analysis-verify --lock state/analysis_lock.json --freeze state/freeze.json
coscientist collect         --streams results/streams --out results/manuscript
coscientist provenance      reports/manuscript.md --require-tokens
```

The existing reproducibility kernel is retained: immutable frozen scientific
state, locked analysis code/environment, exact dataset identity, result-token
uniqueness, figure/result binding, strict numeric provenance, confirmatory vs
exploratory output separation, Drive-only scientific results, access/licence
separation and guarded query costs.

## Install and test

```bash
pip install -e ".[dev]"
pytest -q            # 332 tests expected after the v4.4 Director migration
```

The suite includes package-import integrity checks, so every new production
module must import successfully. `GUARANTEES.yaml` remains the executable
contract registry, extended by versioned `GUARANTEES*.yaml` files; the
integrity suite loads them as one registry and `docs/GUARANTEE_INDEX.md` maps
those guarantees back into the documentation.

## Scope

The current engine is optimized for reproducible secondary/open-data
quantitative Management Science research: archival, panel, quasi-experimental,
event-study and related designs. Future study-type modules may extend that
scope without weakening the one-active-paper rule.
