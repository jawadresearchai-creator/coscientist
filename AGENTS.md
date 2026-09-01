# CoScientist V4 — agent contract

Read this before touching anything. It is the constitution both Codex and
Claude inherit, so no session spends turns relearning the rules.

## What this system is

An autonomous research engine. It discovers candidates, kills the weak ones
cheaply, carries exactly one survivor through a frozen confirmatory design,
and produces a submission-ready manuscript with only author details left as
placeholders.

## The one rule that everything else serves

**Nothing in `src/coscientist/` may call an LLM.**

The deterministic core runs on cron with no subscription, all the way to a
finished analysis and a manuscript skeleton. Judgment work leaves the core as
a *ticket* and is answered later, if and when quota exists.

This is not a preference. It is the reason the engine survives a quota that
expires mid-run. If you find yourself adding an API call to a model inside the
core, you are reintroducing the failure the architecture was built to remove.

## The quota boundary

```
        Claude  (audits science)      Codex  (audits engineering)
             |  ranked findings            |  failing tests
========== QUOTA BOUNDARY — nothing below needs a subscription ==========
                        judgment queue  (tickets wait here)
                        deterministic core  (never waits on them)
```

Claude emits **ranked findings**, each naming the deterministic change that
fixes it. Codex emits **failing tests** — simultaneously the finding, the
specification and the acceptance criterion. Neither writes production
artifacts. Both emit inputs the core consumes.

Fixes go through the pipeline, never around it. An LLM patching an analysis
directly is a provenance hole nobody can reconstruct later.

## For Codex specifically

You are the repair plane. You will be given a **spec plus a failing test**, and
you are done when the test passes. Never accept an open-ended goal — if a task
arrives without an unambiguous stop condition, say so rather than improvising
one.

Run locally under `codex exec` with a ChatGPT sign-in. The GitHub Action
variant needs a paid API key and is out of scope for this project.

Quota discipline, in order of impact:
- one invocation per failing job, never per idea
- `--output-schema` so results parse without another round trip
- let Actions do every repetition; cron is free, your windows are not

## Boundaries that are not negotiable

**The outcome lock.** Above it, evolving a candidate's design, sample or
measurement is ordinary science. Below it, the same act is specification
search. `rescope.propose_rescope` refuses to run on a frozen candidate — do not
add a bypass.

**A rescoped study is a different study.** When the lake supplies a substitute
construct, novelty (G2) and power (G4) are re-adjudicated, never inherited.
Carrying forward a gate pass earned by the original scope is the subtle way
this feature rots.

**The registry is the source of truth.** A secrets list is a derived view of
one subset of it. Every `OPEN` source needs no credential and appears in no
secrets list; reading credentials as a census of data is a mistake this
project has actually made, and `test_secrets_list_is_not_a_census_of_sources`
exists to stop it recurring.

**Never query BigQuery blind.** `bq.guarded_query` dry-runs first and refuses
above a byte ceiling. Removing that guard risks a month's free tier in one
statement.

## Gate order, fixed by cost

| Gate | Test | Cost | Kind |
|------|------|------|------|
| G1 | failure collision | seconds | mechanical |
| G2 | novelty displacement | ~20 searches | retrieval mechanical, verdict judgment |
| G3 | access class, then reachability | minutes | mechanical |
| G4 | power preflight | ~1 hour | mechanical |

A candidate never reaches an expensive gate until it has survived the cheaper
ones. G3 asks whether the engine is *permitted* to have the data, not only
whether a URL responds — an identity-gated source is permanently unavailable
to an autonomous engine.

## Source admissibility

| Class | Admissible | Meaning |
|-------|-----------|---------|
| `OPEN` | yes | no account, no key |
| `HELD` | yes | keyed, key already in Actions secrets |
| `KEYED_NEW` | no | free key, needs a fresh application |
| `IDENTITY_GATED` | no | identity proofing, residency, video |
| `PAID` | no | any charge or credit balance |

Separately: `redistributable` decides whether the lake may archive a source.
Licensing for archival is stricter than for query, so screen on it first.

## When acquisition fails

Fall back to the lake, redefine what needs redefining, move on. Do not kill a
candidate for a transport failure and do not stall waiting for a human. The
order is: lake → external → recovery → lake-bounded rescope → retire.

## House style

- Plain dataclasses. No pydantic, no framework CI has to resolve.
- Errors say what went wrong and what to do about it.
- Every state write is atomic (`os.replace`), so a killed process never leaves
  a half-written file.
- Comments explain *why*, especially where the reason is a past failure.

## The guarantee registry

`GUARANTEES.yaml` is the answer to a pattern rather than a bug. Every claim the
docs make names the test that enforces it, and `test_every_guarantee_names_a_real_test`
fails if that test does not exist. If you add a guarantee to any document, add
it here with its test in the same commit.

## Defects fixed in 4.1.2 — do not reintroduce

24. **`if stored_hash:` failed open.** Deleting the checksum field disabled
    tamper detection entirely. A missing hash is now invalid.
25. **The outcome lock was a mutable string.** `status = "ACTIVE"` re-enabled
    rescoping on a frozen design. The lock is `Candidate.freeze_hash`; status
    is display only.
26. **Re-saving the same design rewrote its provenance.** Same hash is now a
    no-op returning the existing id; `allow_replace` is gone entirely.
27. **G3 only sought alternates for unmapped constructs.** A construct pinned
    to a gated source went straight to the lake past a usable OPEN route.
    Every concept-compatible route is now tried, in usability order.
28. **P-values were bound by proximity.** Three attempts at tuning a heuristic
    was three too many. Field tokens -- `[[R:token|p]]` -- write the
    association down; loose mode now flags a multi-estimate line as ambiguous
    rather than silently guessing.
29. **Strict mode was unusable.** Only the estimate had a token form, so CI, p
    and N had to be allow-listed. All fields render now.
30. **Exact matching ignored units.** 2.41 USD validated 2.41 pp. Both paths
    are unit-gated.
31. **`from_manifest` bypassed the uniqueness check.** It appended instead of
    using `add()`, so duplicate tokens survived persistence.
32. **Provenance skipped its freeze check when the file was missing.** The
    most alarming state produced a clean pass. `--no-freeze-check` is now the
    only way to skip, and it is named as forbidden in submission workflows.
33. **`study` and `cycle` scopes were the same thing.** The 25 GB per-paper
    ceiling reset every cycle.
34. **`EngineState` reported a stale engine version.** It is a state SCHEMA
    version, versioned independently.
35. **Platform sources answered for datasets they do not own.** BigQuery is
    marked `platform: true` and falls back to PROVENANCE_ONLY without a
    dataset.

## Defects fixed in 4.1.1 — do not reintroduce

These were found by review of a release whose suite was fully green.
Green means the tests that exist pass; it never means the code is right.

11. **`bq.py` imported a class that had been renamed.** The whole BigQuery
    route was dead across two releases while 86 tests passed, because no
    test imported it. `test_integrity.py` now imports every module.
12. **`freeze-verify` computed DRIFTED, then printed "freeze intact" and
    exited 0.** It runs the check and now acts on it: exit 2 on any
    dataset MISSING, DRIFTED or UNREADABLE.
13. **`analysis.yml` verified the freeze before fetching the data.** It
    was checking bytes that did not exist yet. Fetch, then verify.
14. **A freeze manifest could be silently overwritten** by an entirely
    different design, which made "one-way door" false at the file layer.
    `save` is write-once unless `allow_replace=True`.
15. **Provenance ignored the p-operator.** `p > 0.5` validated against an
    actual p of 0.0003 -- endorsing a false claim of non-significance.
16. **Provenance matched across incompatible units.** 241 USD divided by
    100 validated "2.41 pp". Scale conversion now requires percent-like
    units on both sides.
17. **A p-claim could be satisfied by an unrelated record.** It is now
    bound to the estimate matched on the same line.
18. **G3 took the first concept match.** Alphabetical order put a
    credentialed source ahead of an equally good keyless one. Sources are
    ranked by admissibility, credentials present, then OPEN over HELD.
19. **G4 turned engine failures into scientific FAIL.** Added `BLOCKED`;
    only `FAIL` retires a candidate.
20. **Dataset licence was not wired to write-back.** `archive_policy` now
    reads the dataset's own declared policy first.
21. **Budgets never reset.** Lines carry `scope` and `period_key`, so a
    monthly cap is neither spent per cycle nor spent once forever.
22. **`cycle.yml` used `exit 0` to "halt".** That ends the step, not the
    job. Now a step output gates the later steps.
23. **The package reported three different versions.** One source of
    truth in `__init__.py`; a test asserts pyproject agrees.

## Defects fixed in 4.0.1 — do not reintroduce

Each of these shipped in 4.0.0 and was found by review. Each now has a
regression test named after it.

1. **G3 skipped unmapped constructs.** `if not con.preferred_source: continue`
   meant a candidate whose only outcome had no data source returned PASS.
   Unmapped now resolves via the registry by concept, or blocks.
2. **G4 did not enforce pre-period-only.** The docstring promised it; nothing
   checked. `pre_period_end` is now required and every row is verified.
3. **Rescope dropped controls by role.** A covariate absorbing the principal
   confound was expendable because its role string said "control". Constructs
   now carry `necessity`, defaulting to ESSENTIAL; only OPTIONAL drops.
4. **G4 killed marginal cases.** A generic approximation now ends a candidate
   only beyond a 3x margin; between 1x and 3x it DEFERs to G4B.
5. **`budget.Ledger` collided with `state.Ledger`.** Renamed `BudgetLedger`.
6. **Budgets reset every cycle.** `load_or_new` persists them, so a monthly
   cap cannot be spent once per cycle.
7. **`write-back-always` contradicted the registry.** Deposit is tiered by
   licence: RAW / DERIVED / PROVENANCE_ONLY.
8. **Licence sat on the platform.** BigQuery carries many datasets under
   different terms; licence now attaches per dataset in the lake catalog.
9. **Overlapping workflow runs could race state.** Added a concurrency group.
10. **cycle.yml committed research state to git.** Only the operational
    heartbeat is committed; scientific state belongs in Drive.

## The outcome lock is now a hash

`freeze.py` replaced the `status == "FROZEN"` flag. The manifest hashes
question, estimand, design, sample, treatment, outcome, controls, exclusions,
window, models, contrasts, multiplicity policy and every dataset SHA-256.
Provenance metadata sits outside the hash, so freezing the same design twice
gives the same id.

Three rules follow, and none of them are negotiable:

- **Every downstream artifact carries the hash it was produced under.** Call
  `require_freeze` before publishing anything.
- **A freeze is a one-way door.** Re-freezing raises. A genuine change needs a
  POST_FREEZE_CHANGE record and reclassifies the affected analysis as
  exploratory.
- **You cannot freeze against unidentified data.** At least one dataset hash
  is required, because a design frozen against unknown bytes cannot be
  reproduced or audited.

## Numbers in the manuscript

`manuscript.py` emits the bridge: every reportable number exists exactly once
as a `ResultRecord` with a token, its uncertainty, and the model and script
that produced it. Cite `[[R:token]]` in prose rather than retyping digits.

`provenance.py` then reads the finished manuscript, extracts every number, and
fails if any cannot be traced to a record. It errs toward false positives on
purpose: an unrecognised legitimate number costs one allow-list entry, an
invented one that slips through costs a retraction.

When adding to it, watch the two bugs it has already had. Confidence levels
("95% CI") are conventions, not findings. And rescaling across the
percent/proportion boundary must carry precision with it -- reusing the
written precision after dividing by 100 widens tolerance a hundredfold, which
once let an invented 41.7 match a genuine 0.4471.

## Strict mode for confirmatory manuscripts

`validate(..., require_tokens=True)` is the mode to use for anything that will
be submitted. Bare-number matching proves a number RESEMBLES some result; it
cannot prove it is the RIGHT result, so a genuine 2.41 from one endpoint will
validate an unrelated 2.41 claim elsewhere. Requiring `[[R:token]]` makes the
association explicit. The bare-number sweep stays on as a secondary net.

## The analysis engine (v4.1.3)

The chain is Drive -> frozen data -> freeze-verify -> R/Stan/Python -> streams
-> collect -> bridge -> provenance -> Drive. Each arrow is a gate that exits
non-zero, not a warning, because a warning in a forty-minute job is a line of
log nobody reads.

**The R/Python joint is a contract, not a convention.** `R/emit.R` writes
newline-delimited JSON in `broom::tidy()`'s vocabulary; `emit.py` validates and
assembles it. Three properties make it a contract:

- **Truncation is detected.** `em$done()` is the last line of every script. A
  script that errors never reaches it, and an unsealed stream refuses the whole
  bundle. Without this a script that dies at model 4 of 9 produces a bundle
  that assembles cleanly and looks finished.
- **Incoherent statistics are refused.** An estimate outside its own interval,
  swapped CI bounds, a negative SE, p outside [0,1], more clusters than rows.
  Each is the signature of arguments passed in the wrong order -- a bug whose
  output is a table that reads perfectly well.
- **Non-finite values are refused,** on both sides. Note the R-side subtlety:
  `is.na(NaN)` is TRUE, so NaN took the NA branch and was dropped exactly like
  an absent value. A singular cluster variance matrix returns a NaN standard
  error routinely, and the record went out reporting an estimate with no
  uncertainty. `is.nan()` must be checked first.

**`00_setup.R` enforces the freeze from R.** Python controlled what landed on
disk; nothing stopped a script reading a file sitting beside it. `cos_data()`
refuses any dataset the freeze does not name and re-hashes what it loads.
`cos_seed()` derives the RNG seed from the freeze hash, so a bootstrap cannot
be reseeded until it agrees.

**Figures are bound to results.** A figure asserts a finding as surely as a
sentence, and the provenance validator reads prose. A figure declaring no
result tokens is the one claim in this pipeline no validator could check, so it
is refused rather than warned about.

**Results go to Drive, never to git.** `analysis.yml` previously ended with
`git add -f results/manuscript/`, contradicting the rule the rest of the system
is built on. The job now holds `permissions: contents: read` -- it does not
have the capability at all -- and `rm -rf data` runs unconditionally so fetched
datasets do not outlive the run.

**renv.lock is generated, never written.** A hand-typed lock either fails to
restore or restores something other than what it names. `R/dependencies.R`
declares the stack with a reason per package; the lock comes from
`renv::snapshot()` on a real installation. The workflow refuses to run without
one.

## Two locks, not one (v4.1.5)

The design freeze says which question. The **analysis lock** says which
analysis. Only the first existed for four versions, and the gap between them is
the most important thing in this codebase:

- **Script hashes taken at collection time** prove *this code produced this
  number*. That is reproducibility provenance.
- **The analysis lock** proves *this code existed before anyone saw the number*.
  That is pre-specification provenance, and only it supports a confirmatory
  claim.

Without the second, editing `03_primary_models.R` after outcomes were visible,
trying specifications until one worked, and collecting the winner produced a
manifest that was entirely truthful and entirely misleading.

`AnalysisLock.verify()` checks **both directions** — locked scripts unchanged,
and no new scripts present. Checking only the first would let a fresh
`07_extra_models.R` run alongside the locked ones, which is the same search
wearing a different hat.

The lock also **records what the code can do**. R cannot be sandboxed from
here; `read.csv`, `download.file`, `system()` and `set.seed()` stay reachable,
so `cos_data()` is a sanctioned loader, not a capability boundary. Saying
otherwise would be the fail-open pattern again. `scan_io` finds every such call
and hashes it into the lock, where it must be acknowledged — unsanctioned
acquisition becomes a declared fact rather than an invisible one.

## When adding to this system

Read `GUARANTEES.yaml` first. Every claim these documents make names the test
that enforces it, and two meta-tests close the loop: a guarantee naming a
missing test fails, and so does a `[GUARANTEE: ...]` reference with no registry
entry, and so does a guarantee no document claims.

That registry exists because six review rounds found the same shape every time
-- **the documentation claims a guarantee, the code implements a narrower
one**. Patching instances does not stop the next instance. If you add a
guarantee to the prose, add the entry and the test in the same commit.

The other recurring shape: **fail-open defaults, optional parameters on
load-bearing checks, and proximity heuristics standing in for semantics**. When
a check can be skipped, make skipping explicit and loud; when an association
matters, write it down rather than inferring it from nearness.

Five specific lessons, each earned:

- **A side effect hidden in a default argument runs only when the default
  does.** `emitter(script, dir = .cos_results_dir())` had the mkdir inside the
  default, so passing `dir` explicitly skipped directory creation entirely.
- **Test what you ship, in the language you ship it in.** The suite was green
  while `cycle.yml` -- the engine's heartbeat -- contained invalid YAML that
  GitHub would have refused to load.
- **Fixing an instance does not fix the class.** `FreezeManifest.load` was
  corrected to reject a missing checksum. The identical `if stored and ...`
  fail-open then shipped in `ResultsBundle.from_manifest`, where deleting both
  integrity fields let a rewritten estimate load cleanly. When you fix a
  fail-open, grep for its shape everywhere else.
- **Self-reported provenance is not provenance.** A record naming its own
  producing script could name any other real, hashed script instead. Identity
  now comes from the runner via `COSCIENTIST_CURRENT_SCRIPT`, and the stream
  filename, the sentinel and the record must all agree.
- **`if: always()` on a delivery step undoes every gate above it.** The
  collector refused a partial analysis; the publish step uploaded it anyway.
  Authoritative destinations take verified output or nothing.

## Known gaps — declared, not hidden

Not built: G2 novelty retrieval, candidate generation and tournament, the cycle
orchestrator, ticket workers with executable defaults, G4B Monte Carlo power,
the journal genome, the reviewer court, and the GMS server-side query/curation
worker. Provider ingestion adapters are intentionally not duplicated here: the
GMS data lake owns ingestion; CoScientist consumes its manifests/catalog and
requests only selected frozen objects or curated query outputs.

Built and tested through 4.2.0: the analysis-code lock, the streaming Drive
adapter with paginated listing and content-resolved duplicates, the R/Python
results contract with runner-supplied identity, the R engine layer, figure
binding, fail-closed manifest integrity, semantic result macros, and the
analysis workflow that chains them with the code lock verified before execution
and delivery gated on success.

Deliberately NOT claimed: a Stan runner (a `.stan` file is a model, not an
analysis -- it is driven from locked R or Python), an R sandbox (the lock
records unsanctioned IO, it does not prevent it), and a figure *quality*
engine. Figure provenance, existence, format and declared DPI are checked;
clipping, overlapping labels, unreadable fonts and misleading axes are not.

The study-specific R scripts are still the analyst's (or the orchestrator's) to
write. What surrounds them -- what they may read, what they must emit, what
happens when they die -- is built and tested.


## Execution-integrity closure (v4.1.5)

The confirmatory boundary is now checked before outcome access, not merely
before execution. A clean runner bootstraps the write-once freeze and analysis
lock from Drive, verifies the locked code/environment/execution plan, and only
then retrieves outcome bytes. [GUARANTEE: STUDY_STATE_BOOTSTRAPS_BEFORE_OUTCOMES]
[GUARANTEE: ANALYSIS_LOCK_IS_VERIFIED_BEFORE_OUTCOME_ACCESS]

Freezing is recursive: the candidate's construct collection and each construct's
concept collection become immutable tuples, so in-place `append`/`pop` cannot
walk around `__setattr__`. [GUARANTEE: FROZEN_SCIENTIFIC_CONTAINERS_ARE_IMMUTABLE]

Python now has the same producer-identity contract as R: the GitHub runner
supplies `COSCIENTIST_CURRENT_SCRIPT`, and neither the stream path, record, nor
completion sentinel may claim another producer. [GUARANTEE: PYTHON_SCRIPT_IDENTITY_COMES_FROM_THE_RUNNER]
[GUARANTEE: STREAM_PRODUCER_WITNESSES_AGREE]

The analysis lock distinguishes the set of hashed sources from the executable
plan. Stan models are hashed inputs to a locked R/Python driver, not direct
runner stages; numbered R/Python drivers execute in global numeric order, and
that exact plan is verified against the lock. [GUARANTEE: ACTUAL_EXECUTION_PLAN_IS_LOCKED]

The lock event has a second receipt hash over the stable lock hash, freeze hash,
lock timestamp and engine version. Changing chronology metadata is therefore
detectable. [GUARANTEE: ANALYSIS_LOCK_RECEIPT_IS_TAMPER_EVIDENT]

Drive publication is commit-marked rather than pretending multi-file uploads
are atomic: each run has its own folder and `_COMPLETE.json` is uploaded last.
A partial network failure can leave staging debris, but never an authoritative
run. [GUARANTEE: DRIVE_PUBLISH_REQUIRES_COMPLETION_MARKER]

For G3, missing source metadata is not silently interpreted as suitability. An
essential construct whose required granularity/coverage cannot be verified is
deferred for metadata verification rather than passed or scientifically
retired. [GUARANTEE: UNKNOWN_SOURCE_SUITABILITY_IS_NOT_ASSUMED]

Finally, an analysis lock cannot be verified without the design freeze it binds
to. [GUARANTEE: ANALYSIS_VERIFY_REQUIRES_DESIGN_FREEZE]


## GMS lake boundary (v4.2.0)

The GMS data lake is the single data backend. Never teach CoScientist to ingest
SEC/Census/EIA/OpenAlex/etc a second time. `gms_lake.py` consumes small local
copies of the lake's SQLite manifests and creates `state/gms_lake_catalog.json`.
Use the catalog for discovery and G3; use Drive only to retrieve the exact
frozen path/hash selected by the design. Never recursively browse the entire
lake.

Prefer curation/query over raw transfer. `QUERY_LAYER_REQUIRED` means the data
exists but an ordinary runner must not fetch it; materialize the smallest
curated extract, record the query request/provenance, then freeze the extract.
OpenAlex raw is always in this class. `PARTIAL`, `FAILED/BLOCKED`, and absent
objects are explicit states, not reasons to crash unrelated candidates.

GitHub access to the GMS repository is read-only in this integration. The lake
watchdog owns ingestion and recovery. CoScientist may read HEAD/workflow health
and pin catalog provenance; it must not dispatch or cancel ingestion jobs.
