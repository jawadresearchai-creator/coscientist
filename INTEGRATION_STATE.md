# CoScientist v4.4.0 — Persistent Integration State

This file is the durable orientation checkpoint for future sessions. It
contains identifiers and secret **names** only, never secret values.

## Repositories

- CoScientist: `jawadresearchai-creator/coscientist`
  - current migration branch: `single-paper-director-v4.4`
  - repository visibility: **public**; standard GitHub-hosted runners are now
    functioning without the private-repository Actions-minutes boundary.
  - scientific state/data remain private in Google Drive; public code visibility
    must never move research state into Git.
- GMS data lake: `jawadresearchai-creator/gms-data-lake` (control plane / ingestion owner)
- Local CoScientist checkout: `E:\coscientist`
- Local GMS checkout: `E:\gms-data-lake`

## Active scientific operating model

**Single paper only.** Broad discovery is allowed only when there is no active
paper, or after the active paper reaches `SUBMISSION_READY` or is `RETIRED` for
an enumerated genuine blocker.

Canonical Drive control filenames:

- `single_paper.json` — scientific lifecycle and one active Topic Charter;
- `director.json` — one persistent next Research Director action plus compact
  phase records;
- `director_answer.json` — one overwriteable reasoning-plane answer inbox.

At the v4.4 migration point, `single_paper.json` is intentionally
`NO_ACTIVE_PAPER`. Historical candidate IDs, slates, rankings, courts,
continuation handoffs and prior retirement queues remain archival evidence only
and are not imported into current state.

The Director never creates a portfolio. When discovery is legal it requests no
more than three fresh serious questions, requires exactly one selection, admits
that Topic Charter, and then locks broad discovery. Every later Director action
serves the same paper.

[GUARANTEE: SINGLE_ACTIVE_PAPER_ONLY]
[GUARANTEE: HISTORICAL_CANDIDATES_ARE_NOT_ACTIVE_INPUT]
[GUARANTEE: LEGACY_STATE_FIELDS_ARE_NOT_IMPORTED]
[GUARANTEE: DIRECTOR_DISCOVERY_IS_BOUNDED]
[GUARANTEE: DIRECTOR_ADMISSION_LOCKS_DISCOVERY]

## Research Director persistence contract

A cycle is restart-safe and idempotent:

1. refresh the current GMS lake catalog;
2. pull `single_paper.json`;
3. pull `director.json`;
4. pull `director_answer.json` if present;
5. apply the answer only if its `action_id` exactly matches the pending action;
6. create at most one next Director action;
7. write the canonical paper and Director state back to Drive only after every
   preceding state/answer operation succeeds.

If no answer has arrived, repeated cycles preserve the same pending action ID.
If a stale answer remains, it is a no-op. Multiple files with the canonical
answer name are refused as ambiguous. A newly admitted paper resets Director
records instead of inheriting old paper context.

The local `TicketQueue` remains a compatibility/engineering adapter. It is not
the authoritative cross-run scientific orchestration state because GitHub-hosted
runners are ephemeral and local `state/` is git-ignored.

[GUARANTEE: DIRECTOR_ONE_ACTION_ONLY]
[GUARANTEE: DIRECTOR_STALE_ANSWER_IS_SAFE]
[GUARANTEE: DIRECTOR_NEW_PAPER_RESETS_CONTEXT]
[GUARANTEE: DIRECTOR_ANSWER_INBOX_IS_UNIQUE]

## Integration contract

- GitHub is the code/control/provenance plane.
- Google Drive is the scientific/data/Director-state plane.
- GMS owns reusable source acquisition and ingestion.
- CoScientist owns scientific selection, paper-specific materialisation,
  freezing, analysis, manuscript and audit.
- CoScientist must not duplicate provider-specific ingestion adapters or
  credentials.
- Raw OpenAlex remains query-layer-required; do not direct-fetch the full
  snapshot through ordinary runners.
- Construct resolution is lake-first: research mart -> curated -> raw/query
  materialisation -> admissible external source -> pre-freeze evolution ->
  genuine blocker.
- An external source may close a real data gap only when it has been verified as
  free, authoritative/official, legally usable and suitable for the required
  granularity/coverage.
- `DATA_FEASIBLE` is not permission to freeze a query-native raw snapshot. The
  actual paper-specific extract must be materialised and exact SHA-256 identities
  must exist before design closure can pass.

[GUARANTEE: G3_IS_LAKE_FIRST]
[GUARANTEE: DIRECTOR_LAKE_TIER_PRIORITY]
[GUARANTEE: DIRECTOR_REPAIRS_SAME_PAPER]

## Persistent GitHub configuration

Repository variable:

- `GMSDL_GITHUB_REPO=jawadresearchai-creator/gms-data-lake`

Encrypted Actions secret names configured in the CoScientist repository:

- `GDRIVE_CLIENT_ID`
- `GDRIVE_CLIENT_SECRET`
- `GDRIVE_TOKEN`
- `GDRIVE_ROOT_FOLDER_ID`
- `GMSDL_DRIVE_ROOT_FOLDER_ID`
- `GMSDL_MANIFEST_FOLDER_ID`
- `GDRIVE_STATE_FOLDER_ID`
- `GDRIVE_RESULTS_FOLDER_ID`
- `GDRIVE_DEVELOPMENT_RESULTS_FOLDER_ID`
- `CONTACT_EMAIL`

Secret values must never be committed, logged, or copied into documentation.
Public repository visibility does not expose repository Actions secrets.

## Google Drive folders

GMS lake root:

- `GLOBAL_MULTIDISCIPLINARY_MANAGEMENT_SCIENCE_DATA_LAKE`
- folder id: `1z_47NQlOLY1L0zB-AXb-_3lwON0m2hJE`

GMS manifest folder:

- `00_CONTROL/manifests`
- folder id: `1_zHlFWcC45gMylLypVfwcRycZTLn-lgA`

CoScientist V4 root:

- `MANAGEMENT_SCIENCES_COSCIENTIST_V4`
- folder id: `1PPOf_Pi-cRWS8TsX6kgU7ikLxop2-kdT`

Durable roots:

- state: `MANAGEMENT_SCIENCES_COSCIENTIST_V4/state`
  - folder id: `1IFnkKcsw0-vtfzKXzoSfdFeB44bl7AZZ`
- confirmatory results: `MANAGEMENT_SCIENCES_COSCIENTIST_V4/results_confirmatory`
  - folder id: `1AYmCHc05fZSgm5sJKsuDbYnQqlAH15X6`
- development results: `MANAGEMENT_SCIENCES_COSCIENTIST_V4/results_development`
  - folder id: `1QMhb8eZdqYGIDTvj1a9y0G0xf4VLwW0s`

## Workflows

- `ci.yml`: full test suite on main push / PR / manual dispatch.
- `doctor.yml`: GMS GitHub + Drive OAuth + manifest/catalog health.
- `cycle.yml`: persistent single-paper Research Director heartbeat. It refreshes
  the lake, pulls both canonical states, consumes at most one matching answer,
  ensures one next action, and persists state back to Drive.
- `analysis.yml`: frozen-state + analysis-lock GMS fetch and analysis workflow.

The v4.4 `cycle.yml` also listens to a main-branch push that changes the Director
or cycle implementation. This bootstraps a newly merged Director immediately;
normal heartbeat-only commits do not match the path filter and therefore do not
create a recursion loop.

## Freeze and analysis boundary

A Director `PRE_FREEZE_AUDIT=PASS` does not itself freeze anything. The next
mechanical action must create a real write-once `FreezeManifest` containing the
approved design and exact dataset hashes. Only after that file exists may the
paper enter `FROZEN`.

Confirmatory analysis still requires the separate analysis lock, frozen input
verification, sealed result streams, results bridge and strict numeric
provenance. V4.4 adds orchestration; it does not weaken v4.3 reproducibility.

[GUARANTEE: DIRECTOR_FREEZE_IS_MECHANICAL]

## Actions runner incident — September 6, 2026

The private repository had successful Ubuntu-hosted workflows through September
3, 2026. By September 6 both the unchanged scheduled `cycle` and the v4.3
migration workflow failed in approximately two seconds with zero steps and no
assigned runner. After the repository was converted to public, hosted Ubuntu
runners immediately resumed, the v4.3 PR suite passed, and the post-merge main
CI passed. The runner incident is therefore resolved for the current public-code
architecture. Scientific Drive content remains private.

## Resume rule

In a new session:

1. Read `INTEGRATION_STATE.md`, `AGENTS.md`, and
   `docs/SINGLE_PAPER_RESEARCH_HANDBOOK.md`.
2. Verify repository visibility and current CI/cycle status live.
3. Read both canonical Drive files: `single_paper.json` and `director.json`.
4. If `director.json` has a pending action, perform **only that action**. Do not
   invent another topic or parallel task.
5. For a reasoning action, use the current lake and current public literature as
   requested, then write one answer bound to the exact `action_id` through the
   canonical `director_answer.json` inbox.
6. If no action exists, run Director `ensure`; it will create the legal next
   action from lifecycle state.
7. If a paper is active, never generate replacement topics; continue its current
   stage or repair the named weakness.
8. Verify the current lake catalog before any data decision.

Never print secret values. If Drive OAuth, GitHub Actions or data transport
fails, repair infrastructure; do not redesign or retire the paper merely because
execution failed.
