# CoScientist v4.3.0 — Persistent Integration State

This file is the durable orientation checkpoint for future sessions. It
contains identifiers and secret **names** only, never secret values.

## Repositories

- CoScientist: `jawadresearchai-creator/coscientist`
  - current migration branch: `single-paper-v4.3`
  - target visibility: **public** so standard GitHub-hosted runners do not depend
    on the private-repository Actions minutes allowance.
  - scientific state/data remain private in Google Drive; making the code repo
    public must never move research state into Git.
- GMS data lake: `jawadresearchai-creator/gms-data-lake` (control plane / ingestion owner)
- Local CoScientist checkout: `E:\coscientist`
- Local GMS checkout: `E:\gms-data-lake`

## Active scientific operating model

**Single paper only.** Broad discovery is allowed only when there is no active
paper, or after the active paper reaches `SUBMISSION_READY` or is `RETIRED` for
an enumerated genuine blocker.

Canonical Drive state filename:

- `single_paper.json`

The v4.3 migration deliberately starts Management Science active state at
`NO_ACTIVE_PAPER`. Historical candidate IDs, slates, rankings, courts,
continuation handoffs and prior retirement queues remain archival evidence only
and are not imported into `single_paper.json`.

[GUARANTEE: SINGLE_ACTIVE_PAPER_ONLY]
[GUARANTEE: HISTORICAL_CANDIDATES_ARE_NOT_ACTIVE_INPUT]
[GUARANTEE: LEGACY_STATE_FIELDS_ARE_NOT_IMPORTED]

## Integration contract

- GitHub is the code/control/provenance plane.
- Google Drive is the scientific/data plane.
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

[GUARANTEE: G3_IS_LAKE_FIRST]

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
- `cycle.yml`: single-paper heartbeat; refreshes lake catalog, loads canonical
  `single_paper.json`, reports the focused next action, and never runs a
  competing topic tournament.
- `analysis.yml`: frozen-state + analysis-lock GMS fetch and analysis workflow.

## Actions runner incident — September 6, 2026

The private repository had successful Ubuntu-hosted workflows through September
3, 2026. By September 6 both the unchanged scheduled `cycle` and the v4.3
migration workflow failed in approximately two seconds with zero steps,
`runner_id=0`, and no assigned runner. This matches the GitHub-hosted private
repository usage/budget exhaustion failure shape. The code repository is being
made public so standard hosted runners use the public-repository free runner
pool. Scientific Drive content remains private.

## Resume rule

In a new session:

1. Read `INTEGRATION_STATE.md`, `AGENTS.md`, and
   `docs/SINGLE_PAPER_RESEARCH_HANDBOOK.md`.
2. Verify repository visibility and CI status live.
3. Read the canonical Drive `single_paper.json`.
4. If no active paper exists, run bounded topic selection and admit exactly one.
5. If a paper is active, do not generate replacement topics; continue its
   current stage or repair its current blocker.
6. Verify the current lake catalog before any data decision.

Never print secret values. If Drive OAuth or transport fails, repair
infrastructure; do not redesign or retire the paper merely because execution
failed.
