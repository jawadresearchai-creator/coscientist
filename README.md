# CoScientist V4 — deterministic core + pre-specified analysis engine

A **deterministic research-governance kernel** for an autonomous CoScientist
engine, and the analysis engine that runs underneath it. It runs with no LLM
and no subscription; Claude and Codex are optional auditors reached through a
queue, never components in the pipeline.

The governance layer, the analysis engine and the manuscript bridge are built
and tested. The GMS data-lake adapter is also built: the lake remains the sole
ingestion backend, while CoScientist reads its manifests/catalog and retrieves
only selected frozen objects or requests curated/query-layer extracts. The
research loop that would drive these pieces — candidate generation, G2 novelty
retrieval and the cycle orchestrator — is not yet built.

## Why it is shaped this way

Every design here failed at least once on the same species of assumption:
something asserted in prose and never enforced in code. A credential that
turned out to be identity-gated. A subscription that could expire mid-run. A
credentials list mistaken for a census of sources. A `freeze-verify` that
computed DRIFTED and then printed "freeze intact". A module nobody imported, so
nobody noticed it had not compiled for two releases. A `cycle.yml` containing
`run: echo "TODO: orchestrator."` — invalid YAML, which GitHub would have
refused to load, in the workflow that is supposed to be the engine's heartbeat.

Five review rounds all found the same shape: the documentation claims a
guarantee, the code implements a narrower one. So `GUARANTEES.yaml` lists every
guarantee this system claims and names the test that enforces it. Two
meta-tests close the loop in both directions: every guarantee must name a test
that exists, and every `[GUARANTEE: …]` reference in these documents must
resolve to an entry. A claim in the docs with no test behind it now breaks the
build, and so does a guarantee nobody claims.

## Install

```bash
pip install -e ".[dev]"
pytest -q            # 283 tests, no credentials needed
```

The R half of the suite skips itself when `Rscript` is absent, so the Python
tests run anywhere. CI installs R, and the round-trip tests then run for real.

## Two locks, not one

The design freeze says **which question**. The analysis lock says **which
analysis**. Both are needed, and for four versions only the first existed.

Script hashes taken when results are collected prove that *this code produced
this number*. They cannot prove that *this code existed before anyone saw the
number* — so the following was possible, and nothing in the system could see it:

```
design freezes → outcomes visible → edit 03_primary_models.R → try another
specification → edit again → keep the one that works → collect()
```

Afterwards the manifest is entirely truthful and entirely misleading. That is
the difference between **reproducibility provenance** and **pre-specification
provenance**, and only the second supports a confirmatory claim.

```bash
coscientist analysis-lock --freeze state/freeze.json    # after the freeze, BEFORE outcomes
```

The lock hashes every script, every environment lock and the execution order.
Verification then refuses an edited script
`[GUARANTEE: ANALYSIS_CODE_IS_PRE_SPECIFIED]`, a script added afterwards to run
alongside the locked ones `[GUARANTEE: NEW_SCRIPTS_CANNOT_JOIN_AFTER_THE_LOCK]`,
a changed `renv.lock` `[GUARANTEE: ENVIRONMENT_IS_LOCKED_WITH_THE_CODE]`, and
re-locking over the top once the answer is known
`[GUARANTEE: ANALYSIS_LOCK_IS_WRITE_ONCE]`. A lock whose own checksum has been
deleted is invalid rather than unchecked
`[GUARANTEE: ANALYSIS_LOCK_REQUIRES_ITS_HASH]`.

It also records what the code *can* do. R cannot be sandboxed from here:
`read.csv`, `download.file`, `system()` and `set.seed()` all remain reachable,
so `cos_data()` is a **sanctioned loader, not a capability boundary**. Rather
than claiming otherwise, every such call is found, hashed into the lock, and
must be acknowledged `[GUARANTEE: UNSANCTIONED_IO_IS_DECLARED]` — unsanctioned
acquisition becomes a declared fact instead of an invisible one.

## The analysis engine

```
GMS manifests/catalog ──▶ selected frozen bytes/query extract ──▶ freeze-verify
                                                    │
                                                    ▼
                                      analysis-verify ──▶ R / Python
                                                                        │
                                                                 streams (JSONL)
                                                                        │
                                                                    collect ──▶ bridge
                                                                        │
                                                         strict provenance ──▶ Drive
```

```bash
coscientist lake-sync      --out state/gms_lake_catalog.json
coscientist gms-fetch      --manifest state/freeze.json --catalog state/gms_lake_catalog.json --dest data
coscientist freeze-verify  --manifest state/freeze.json --data-dir data
coscientist analysis-verify --lock state/analysis_lock.json
coscientist plan | while read -r s; do COSCIENTIST_CURRENT_SCRIPT="$s" Rscript "$s"; done
coscientist collect        --streams results/streams --out results/manuscript
coscientist provenance     reports/manuscript.md --require-tokens
coscientist publish        --dir results
```

`coscientist plan` is the single definition of what runs; the workflow reads it
rather than globbing separately `[GUARANTEE: ONE_DEFINITION_OF_WHAT_RUNS]`.
Stan deliberately has no runner of its own — a `.stan` file is a model, not an
analysis, so it is invoked from a locked R or Python driver and hashed with them.

**Data in, results out, code in the middle.** The fetch downloads exactly what
the freeze names and nothing else in the folder
`[GUARANTEE: FETCH_IS_LIMITED_TO_FROZEN_DATA]`, hashing in 8 MB chunks as the
bytes arrive so a dataset never has to fit in RAM
`[GUARANTEE: LARGE_DATASETS_ARE_STREAMED]` — the 25 GB per-study ceiling was
previously unreachable by the code that implements it. Listing follows
pagination `[GUARANTEE: DRIVE_LISTING_IS_COMPLETE]`, and because Drive permits
duplicate filenames, the candidate whose bytes match the freeze is used rather
than whichever came last in the listing
`[GUARANTEE: DUPLICATE_NAMES_RESOLVE_BY_CONTENT]`.

Results go back to Drive **only when the run passed**
`[GUARANTEE: ONLY_VERIFIED_RESULTS_ARE_PUBLISHED]`. That step used to carry
`if: always()`, so an analysis that died halfway was correctly refused by the
collector and then uploaded to the authoritative folder anyway — undoing the
stream-sealing architecture two steps above it. A failed run now leaves
diagnostics under a name nobody could mistake for a result
`[GUARANTEE: FAILED_RUNS_ARE_QUARANTINED]`. Nothing scientific is committed to
git `[GUARANTEE: RESEARCH_STATE_NEVER_ENTERS_GIT]`, which is what lets the
repository become public without publishing an unsubmitted finding.

**The R/Python joint.** The econometrics are R; the bridge is Python. R scripts
do not print numbers, they emit them — one JSON object per reportable quantity,
in `broom::tidy()`'s own vocabulary, at full double precision
`[GUARANTEE: R_AND_PYTHON_AGREE]`.

```r
source("R/00_setup.R"); source("R/emit.R")
cos_setup(); em <- emitter("R/03_primary_models.R")

m <- fixest::feols(y ~ treat_post | firm + quarter, data = cos_data("panel.csv"),
                   cluster = ~ firm)
em$tidy(broom::tidy(m, conf.int = TRUE), keep = "treat_post", prefix = "h1_",
        units = "pp", n_obs = nobs(m), model = "TWFE, firm + quarter FE")
em$done()
```

The runner exports `COSCIENTIST_CURRENT_SCRIPT`, and `emitter()` takes its
identity from that rather than from a string the script chooses. Provenance was
self-reported: a record could name a different — real, hashed — script as its
producer and pass every check. Now the stream filename, the sentinel and every
record must agree `[GUARANTEE: SCRIPT_IDENTITY_COMES_FROM_THE_RUNNER]`.

`em$done()` is the last line, and only there. A script that errors never
reaches it, the stream stays unsealed, and the collector refuses the entire
bundle `[GUARANTEE: ANALYSIS_TRUNCATION_IS_DETECTED]`. This is the failure the
contract exists for: a script that dies two thirds through leaves a well-formed
file containing half an analysis, and those records are real — which is exactly
what makes them dangerous. They assemble into something that looks finished.

Four more things are refused at the emit boundary, each the signature of
arguments passed in the wrong order — a mistake whose output is a table that
reads perfectly well:

- an estimate outside its own confidence interval, a negative standard error,
  a p-value outside [0,1], more clusters than observations
  `[GUARANTEE: INCOHERENT_STATISTICS_ARE_REFUSED]`
- a NaN or infinite estimate `[GUARANTEE: NON_FINITE_VALUES_ARE_REFUSED]`,
  including on the R side, where `is.na(NaN)` is TRUE and a NaN standard error
  was therefore dropped exactly like an absent one
  `[GUARANTEE: A_NAN_STANDARD_ERROR_IS_NEVER_DROPPED]`
- a result naming a script that was never hashed, which breaks the chain from
  design to data to code to results
  `[GUARANTEE: RESULTS_NAME_THE_CODE_THAT_MADE_THEM]`
- two aliases setting the same field to different values, which is resolved by
  refusing rather than by guessing which the author meant

**The R side enforces the freeze too.** `cos_data()` loads a dataset only if
the freeze names it `[GUARANTEE: R_CANNOT_READ_UNFROZEN_DATA]` and re-hashes it
on the way in. Python controlled what arrived on disk; nothing stopped a script
reading a file sitting alongside. `cos_seed()` derives the RNG seed from the
freeze hash `[GUARANTEE: RANDOMNESS_IS_DERIVED_FROM_THE_DESIGN]` — a seed the
analyst picks is a free parameter, and a bootstrap can be re-run under new
seeds until it agrees with nobody recording that it happened.

**Figures are claims.** "Effects emerge in quarter three and persist" is
asserted by a picture as much as by a sentence, and the provenance validator
reads prose. So every figure declares the results it depicts, those tokens must
resolve, and a figure bound to nothing is refused
`[GUARANTEE: FIGURES_ARE_BOUND_TO_RESULTS]`. The manifest carries each figure's
own SHA-256, so it names bytes rather than intentions.

The R stack is declared in `R/dependencies.R` with a reason per package —
`fixest`, `did`, `didimputation`, `did2s`, `HonestDiD`, `synthdid`,
`clubSandwich`, `fwildclusterboot`, `marginaleffects`, `cmdstanr`. `renv.lock`
is **generated** by snapshotting a real installation, never hand-written; the
analysis workflow refuses to run without one.


## GMS data-lake integration

CoScientist does **not** duplicate the lake's ingestion engine.  The boundary is:

```text
GitHub = control/provenance plane
Google Drive = data plane

gms-data-lake repository -> ingestion/watchdog/registry/manifests
CoScientist repository    -> scientific gates/freezes/analysis/manuscript
                                  |
                                  v
                           GMS lake adapter
                                  |
                       small SQLite manifests
                                  |
                    metadata/catalog/query planning
                                  |
               only selected frozen bytes or curated extract
```

`coscientist lake-sync` downloads only the small SQLite control manifests to
ephemeral disk, opens them locally, and builds `state/gms_lake_catalog.json`.
Drive itself is never used as a multi-terabyte search index. `coscientist
lake-query` searches that compact catalog, while `coscientist gms-query-plan`
records the columns/filters/concepts required for the smallest useful slice.

Large raw/query-native objects are not treated as ordinary downloadable files.
OpenAlex is always `QUERY_LAYER_REQUIRED`; other objects above the configured
direct-fetch ceiling are too.  A candidate can therefore see that data exists
without a GitHub runner accidentally downloading a snapshot measured in
hundreds of gigabytes. G3 returns DEFER when an essential construct is present
only through such a query/curation route.

The freeze format is unchanged. Its dataset keys may now be canonical
lake-relative paths, for example:

```text
01_RAW_IMMUTABLE/01_FINANCE_AND_CAPITAL_MARKETS/SEC_BULK/companyfacts.zip
    -> sha256...
```

`gms-fetch` resolves only those paths, streams only those bytes, and requires
the catalog SHA-256 to equal the frozen SHA-256 before acquisition. For large
raw sources the correct workflow is: issue/materialize a curated query result,
then freeze that result -- not fetch the raw source.

Drive credentials accept both a raw refresh token and the rclone OAuth JSON
representation already used by the GMS lake, extracting only `refresh_token`
at runtime. Credentials remain GitHub Secrets; no token value belongs in the
repository.

Required integration configuration:

```text
GMSDL_GITHUB_REPO              owner/gms-data-lake
GMSDL_GITHUB_TOKEN             optional read-only token
GMSDL_MANIFEST_FOLDER_ID       Drive folder holding SQLite manifests
GMSDL_DRIVE_ROOT_FOLDER_ID     GLOBAL_MULTIDISCIPLINARY_MANAGEMENT_SCIENCE_DATA_LAKE root
GDRIVE_STATE_FOLDER_ID         CoScientist scientific state root
GDRIVE_RESULTS_FOLDER_ID       confirmatory results root
GDRIVE_DEVELOPMENT_RESULTS_FOLDER_ID exploratory results root
```

The GitHub client is intentionally read-only: it pins repository HEAD and reads
workflow health. CoScientist does not dispatch ingestion, retry OpenAlex, or
edit the GMS repository; the lake watchdog retains ownership of ingestion.

## The confirmatory boundary

```bash
coscientist freeze-verify --manifest state/freeze.json --data-dir data/
coscientist provenance reports/manuscript.md --require-tokens
```

**The freeze is a hash** over the design and every dataset SHA-256
`[GUARANTEE: FREEZE_HASH_COVERS_DESIGN]`, computed order-independently and
ignoring provenance metadata. Freezing requires the datasets to be identified
`[GUARANTEE: FREEZE_REQUIRES_DATASET_IDENTITY]`. Edit the manifest file and
`load` refuses it `[GUARANTEE: FREEZE_DETECTS_TAMPERING]`; delete the stored
checksum and it refuses that too, rather than treating an absent hash as
nothing to check `[GUARANTEE: FREEZE_REQUIRES_STORED_HASH]`. A freeze is
write-once `[GUARANTEE: FREEZE_IS_WRITE_ONCE]` and there is no flag that
permits replacing one `[GUARANTEE: FREEZE_HAS_NO_ESCAPE_HATCH]`.

The lock is the hash, not the `status` string
`[GUARANTEE: LOCK_IS_A_HASH_NOT_A_LABEL]`, and the marker cannot be cleared
once set — nor can the design it marks be edited around it
`[GUARANTEE: FROZEN_CANDIDATES_ARE_IMMUTABLE]`, constructs included
`[GUARANTEE: SEALING_REACHES_THE_CONSTRUCTS]`. A candidate and its freeze must
agree on treatment, outcome and controls, not merely on question and design
`[GUARANTEE: CANDIDATE_AND_FREEZE_DESCRIBE_ONE_STUDY]`. A dataset that has drifted or gone missing forbids analysis outright
`[GUARANTEE: DATASET_DRIFT_BLOCKS_ANALYSIS]`.

**The bridge is small and tokened.** Every reportable number exists exactly
once `[GUARANTEE: RESULT_TOKENS_ARE_UNIQUE]`, with its CI, its model and its
script. The manuscript cites `[[R:h1_did|estimate]]`, not `2.41`.

**Provenance is checked, not hoped for.** A fabricated 14.2, or a digit-swapped
2.14 where the estimate was 2.41, fails the build
`[GUARANTEE: FABRICATED_NUMBERS_FAIL]`. A p-threshold is tested in the
direction it was written `[GUARANTEE: P_OPERATOR_IS_HONOURED]`. A value from an
incompatible unit family cannot validate a claim
`[GUARANTEE: UNITS_GATE_NUMERIC_MATCHING]` — 241 USD once satisfied a claim of
2.41 pp by division. Association is written down, not inferred from proximity,
because proximity picks the wrong record in "2.41 pp and −0.312 pp (p < 0.001)"
`[GUARANTEE: FIELD_TOKENS_BIND_STATISTICS_TO_RECORDS]`, and a fully tokenised
manuscript passes strict mode `[GUARANTEE: STRICT_MODE_IS_USABLE]`.

Strict mode has no way around it: the `{{lit:}}` escape was stripped before any
checking and never inspected, so it was a documented bypass of the one
mechanism that stops invention `[GUARANTEE: STRICT_MODE_HAS_NO_ESCAPE_HATCH]`.
Numbers that are legitimately not results — α, a rate quoted from a cited paper
— cite the literal registry instead, where each has an id and a source. The allow-list is refused
there too `[GUARANTEE: STRICT_MODE_FORBIDS_THE_ALLOW_LIST]`: it waved a bare
number through with no id and no source, which made "no escape hatch" false the
moment anyone used it.

Field tokens bind a number to a record, but they cannot make the surrounding
sentence true — strict mode happily rendered "The p-value was
`[[R:h1|estimate]]`" as "The p-value was 2.41 pp". Arbitrary language is not
checkable; a small closed vocabulary of cues that state the statistic outright
is `[GUARANTEE: PROSE_AND_CITATION_MUST_AGREE]`. Prefer the named macros —
`[[EST:t]]`, `[[CI:t]]`, `[[P:t]]`, `[[N:t]]`, `[[SE:t]]`, `[[CLUST:t]]` —
which say what they are.

Editing anything in a written manifest, figure bindings and script hashes
included, fails on load `[GUARANTEE: MANIFEST_TAMPERING_IS_DETECTED]`, and a
manifest with its integrity hashes stripped is refused rather than trusted
`[GUARANTEE: MANIFEST_REQUIRES_ITS_INTEGRITY_HASHES]` — the identical
fail-open the freeze had already been fixed for, reappearing one layer up.

## The gauntlet

Cheapest killer first, so a candidate never reaches an expensive gate until it
has survived every cheaper one.

| gate | cost | what it asks |
|------|------|--------------|
| G1 | seconds | has this lineage already been retired? |
| G2 | ~20 searches | is there anything left to displace? *(not built)* |
| G3 | minutes | is the engine *permitted* to have the data? |
| G4 | ~1 hour | can this design detect an effect the mechanism could produce? |

G3 asks about permission, not just whether a URL responds. Identity-gated and
paid sources are never admissible `[GUARANTEE: INADMISSIBLE_SOURCES_ARE_REFUSED]`;
being queryable is separate from being archivable
`[GUARANTEE: ARCHIVAL_RIGHTS_ARE_SEPARATE_FROM_ACCESS]`; and the secrets list
is not a census of sources — admissible sources exist that carry no secret at
all `[GUARANTEE: SECRETS_ARE_NOT_A_SOURCE_CENSUS]`, a mistake that once
produced the confident and wrong conclusion that the lake held no finance data.

Every concept-compatible external route is tried before the lake
`[GUARANTEE: G3_EXHAUSTS_EXTERNAL_ROUTES]`, ranked by usability rather than
alphabetically. Reachable is not suitable: a source that responds with the
wrong granularity or coverage is not a route
`[GUARANTEE: G3_CHECKS_SUITABILITY_NOT_JUST_REACHABILITY]`. A primary construct
with no usable source never returns PASS
`[GUARANTEE: G3_BLOCKS_UNMAPPED_PRIMARY_CONSTRUCTS]`. And because substituting
a source alters scientific state, G3 will not do it to a frozen candidate
`[GUARANTEE: FROZEN_DESIGNS_ARE_NOT_REROUTED]`.

G4 recomputes what C716 discovered too late: an MDE of 6.5–7pp against a shock
mechanically worth 2.1bp of assets. It refuses any panel containing
post-treatment rows `[GUARANTEE: POWER_GATE_SEES_ONLY_PRE_PERIOD]`, returns
BLOCKED rather than FAIL when the orchestrator hands it the wrong dataframe
`[GUARANTEE: ENGINE_FAILURE_IS_NOT_SCIENTIFIC_FAILURE]`, and defers rather than
killing inside a 3× margin
`[GUARANTEE: CHEAP_SCREEN_DOES_NOT_ADJUDICATE_CLOSE_CALLS]` — a generic
approximation is entitled to kill the hopeless, not to adjudicate the close.

## Rescope: what happens when acquisition fails

An acquisition failure must not simply kill a candidate. The engine falls back
to what the lake already holds, redefines whatever needs redefining, and moves
on — above the outcome lock only
`[GUARANTEE: RESCOPE_IS_PRE_FREEZE_ONLY]`, because rescoping below it is
choosing a question to fit data whose results are already visible. A rescoped
study is a different study, so G2 and G4 are re-run rather than inherited
`[GUARANTEE: RESCOPE_REJUDGES_NOVELTY_AND_POWER]`. Only a construct explicitly
marked OPTIONAL may be dropped: a covariate absorbing the principal confound is
not expendable because its role string says "control"
`[GUARANTEE: ESSENTIAL_CONSTRUCTS_ARE_NEVER_DROPPED]`.

## The quota boundary

The core emits a ticket and carries on with its declared default. A ticket is
drained only when quota exists. So there is no such thing as quota dying
mid-execution — an LLM is never in the middle of anything, only at a boundary
answering a question already written down.

Exactly one ticket type blocks: the pre-freeze audit. Everything below the
outcome lock inherits the frozen design and cannot be repaired without losing
confirmatory status, so freezing unaudited is worse than waiting.

Resources are metered on their own clocks — a monthly BigQuery cap, a per-cycle
ticket allowance, a per-**paper** 25 GB acquisition ceiling
`[GUARANTEE: BUDGET_PERIODS_ARE_DISTINCT]`. The CLI passes the study id
through, because a fixed abstraction with an unfixed caller is an unfixed
system `[GUARANTEE: BUDGET_SCOPE_IS_ENFORCED_AT_THE_CALLER]`.

## Layout

```
registry/sources.yaml        the source of truth about reachable data
registry/literals.example.yaml numbers that are not results but need an origin
registry/lake_catalog.*.json what the lake already holds
policy.yaml                  standing approval policy: auto / ask / never
GUARANTEES.yaml              every claim, and the test that enforces it
R/
  emit.R                     the R half of the results contract
  00_setup.R                 freeze enforcement, frozen loading, derived seed
  dependencies.R             the declared stack, with a reason per package
src/coscientist/
  registry.py                access classes, redistribution rights, concept search
  lake.py                    lake catalog, read-first
  gms_lake.py                GMS SQLite manifests, catalog/query routes, frozen fetch
  github.py                  read-only GMS repository HEAD/workflow status
  rescope.py                 lake-bounded redefinition when acquisition fails
  power.py                   G4 — the minimum detectable effect
  gates.py                   G1, G3, G4 in cost order
  analysis_lock.py           pre-specification: what code may run, hashed
  drive.py                   Drive acquisition and delivery, streamed
  emit.py                    the Python half of the results contract
  figures.py                 figures bound to the results they depict
  manuscript.py              the bridge: ResultRecord, ResultsBundle
  provenance.py              every number in the prose traces to a result
  freeze.py                  the outcome lock
  tickets.py                 the judgment queue and standing policy
  budget.py                  metered resources on their own clocks
  bq.py                      BigQuery with a mandatory dry-run byte ceiling
  state.py                   checkpointed state and append-only ledger
.github/workflows/           doctor (weekly), cycle (6-hourly), analysis
```

Every production module is imported by the suite
`[GUARANTEE: EVERY_MODULE_IMPORTS]` — `bq.py` kept a stale import across two
releases while 86 tests passed, because nothing loaded it. The version is
declared in exactly one place `[GUARANTEE: ONE_VERSION_SOURCE]`, after a
package that reported 4.0.0, 4.0.1 and 4.1.0 simultaneously.

The workflows are code and are tested as code: every file parses as YAML and
every `run:` body parses as shell `[GUARANTEE: WORKFLOWS_ARE_VALID]`, and the
analysis stages are checked to run in the only safe order
`[GUARANTEE: ANALYSIS_STAGES_RUN_IN_ORDER]` — an earlier version verified
before fetching, so verification passed by having nothing to check.

## What is not built yet

G2 novelty retrieval, candidate generation/tournament, the full cycle
orchestrator, ticket workers with executable defaults, G4B design-specific
Monte Carlo power, the journal genome, the reviewer court, and the lake's
server-side curation/query worker. Provider-specific acquisition adapters are
**deliberately not a CoScientist gap**: the GMS data lake owns provider ingestion.

## Deployment

Keep the **CoScientist repository and GMS data-lake repository separate but
connected**. GitHub is the control/provenance plane; Google Drive is the data
plane. CoScientist reads the GMS repository HEAD/workflow health and the lake's
Drive manifests, but it neither moves datasets through GitHub nor duplicates
the ingestion code.

Integration settings expected by the workflows:

```text
GMSDL_GITHUB_REPO                 repository variable: owner/gms-data-lake
GMSDL_GITHUB_TOKEN                optional read-only secret
GMSDL_MANIFEST_FOLDER_ID          Drive control-manifest folder
GMSDL_DRIVE_ROOT_FOLDER_ID        GMS lake root
GDRIVE_CLIENT_ID                  shared Google OAuth client
GDRIVE_CLIENT_SECRET              shared Google OAuth secret
GDRIVE_TOKEN                      raw refresh token or rclone token JSON
GDRIVE_STATE_FOLDER_ID            separate CoScientist scientific state root
GDRIVE_RESULTS_FOLDER_ID          confirmatory output root
GDRIVE_DEVELOPMENT_RESULTS_FOLDER_ID exploratory output root
```

Research state -- freezes, candidate registers, manuscripts and results --
stays in the CoScientist Drive area, never in git. The GMS root remains owned
by the lake (`00_CONTROL`, `01_RAW_IMMUTABLE`, `02_CURATED`, and related
operational/data-census areas).
