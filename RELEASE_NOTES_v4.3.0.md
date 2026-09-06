# CoScientist v4.3.0 — Single-Paper Operating Model

## Purpose

This release changes the Management Science research orchestration model while
preserving the v4 reproducibility kernel.

The previous active workflow behaved like a candidate tournament: discover,
rank, kill, rescope, revive and continue candidate lineages. The new operating
contract is deliberately simpler and more research-realistic:

> Select one strong paper, stop broad discovery, evolve and repair that paper
> until it is submission-ready or genuinely impossible to complete.

## Scientific operating changes

- Exactly one paper may be active.
- Broad topic discovery locks immediately after admission.
- Historical candidate/slate/ranking/court state is archive-only and is not
  imported into the active state.
- Ordinary infrastructure/data friction is repair work, not a reason to start
  a replacement topic.
- Retirement requires an enumerated genuine scientific blocker.
- Pre-freeze topic evolution has no arbitrary rescope-count budget.
- Novelty is handled through bounded scientific closures rather than a permanent
  numeric kill tournament.
- G3 is lake-first: existing research-ready/curated holdings are preferred
  before external reacquisition.
- Null confirmatory results do not reopen topic discovery or specification
  search.

## New state machine

`src/coscientist/single_paper.py` adds the canonical lifecycle:

```text
SELECTED -> DEVELOPING -> DATA_FEASIBLE -> DESIGN_READY -> FROZEN
         -> ANALYZING -> RESULTS_COMPLETE -> MANUSCRIPT -> FINAL_AUDIT
         -> SUBMISSION_READY
```

`RETIRED` is terminal and requires one of the permitted genuine blockers.

The migration intentionally starts active Management Science state at
`NO_ACTIVE_PAPER`; previous candidate progress is not resumed.

## Data changes

Construct resolution now follows:

```text
03_RESEARCH -> 02_CURATED -> 01_RAW/query materialisation
            -> admissible official external source
            -> pre-freeze evolution -> genuine blocker
```

The Global Management Science lake continues to own ingestion. CoScientist uses
existing holdings before retrieving duplicates externally.

## Workflow changes

- `cycle.yml` becomes a focused single-paper heartbeat.
- Legacy `candidate_generation` and `historical_candidate_revival` policy
  actions are disabled.
- The cycle reports the current paper and its next action; it does not generate
  competing topics while a viable paper is active.
- Scientific state remains Drive-only.

## Safeguards preserved

The operating-model change does **not** weaken:

- design-freeze immutability;
- exact dataset identity and drift detection;
- analysis-lock pre-specification;
- pre-period-only power checks;
- separation of engine failure from scientific failure;
- confirmatory/exploratory separation;
- result-token uniqueness;
- figure/result binding;
- strict numeric provenance;
- source access/licence separation;
- guarded BigQuery/query-layer usage;
- Drive-only scientific output.

## Executable guarantees

The guarantee system now loads the historical `GUARANTEES.yaml` plus the v4.3
extension `GUARANTEES_SINGLE_PAPER.yaml` as a single registry. Duplicate IDs,
missing tests, undocumented guarantees and dangling documentation references
remain build failures.

## GitHub-hosted runner visibility change

On September 6, 2026 private-repository hosted workflows began failing before a
runner was assigned (`runner_id=0`, zero steps), after earlier successful runs.
The repository is intended to become public so standard GitHub-hosted runners
use the public-repository free runner pool. Repository Actions secrets remain
server-side; scientific state and data remain private in Google Drive.

Before changing visibility, the current tree was checked for embedded
`GDRIVE_TOKEN`, `client_secret` and `api_key` values and none were found. Full
historical secret-scanning administration still requires GitHub's repository
settings/secret-scanning interface.
