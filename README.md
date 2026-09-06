# CoScientist V4.3 — single-paper Management Science research engine

CoScientist is a deterministic research-governance and analysis kernel for
**one Management Science paper at a time**. Reasoning models may perform
judgment, literature interpretation, adversarial review and prose through
explicit handoffs/tickets, but `src/coscientist/` never calls an LLM directly.

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

The scheduled cycle uses `coscientist.single_paper_drive` to synchronize the
one canonical `single_paper.json` with the V4 Google Drive state folder. While
a viable paper is active, discovery is mechanically locked.

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
- plausible power/MDE for the intended estimator.

Then run one consolidated hostile pre-freeze audit. There is no hard numerical
novelty threshold and no permanent candidate tournament.

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
pytest -q            # 315 tests expected after the v4.3 migration
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
