# CoScientist v4.2.0 — Persistent Integration State

This file is the durable orientation checkpoint for future sessions. It contains identifiers and secret names only, never secret values.

## Repositories

- CoScientist: `jawadresearchai-creator/coscientist` (private)
- GMS data lake: `jawadresearchai-creator/gms-data-lake` (control plane / ingestion owner)
- Local CoScientist checkout: `E:\coscientist`
- Local GMS checkout: `E:\gms-data-lake`

## Integration contract

- GitHub is the control/provenance plane.
- Google Drive is the data plane.
- GMS owns source acquisition and ingestion.
- CoScientist reads GMS GitHub status and GMS SQLite manifests, selects/freeze-locks data, and fetches only frozen direct-fetch objects.
- CoScientist must not duplicate provider-specific ingestion adapters or credentials.
- Raw OpenAlex remains query-layer-required; do not direct-fetch the full snapshot through ordinary runners.

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

`GMSDL_GITHUB_TOKEN` is intentionally optional while the GMS repository remains public. Do not add a broad personal token unless GitHub API rate limits or repository visibility require it.

## Google Drive folders

GMS lake root:

- `GLOBAL_MULTIDISCIPLINARY_MANAGEMENT_SCIENCE_DATA_LAKE`
- folder id: `1z_47NQlOLY1L0zB-AXb-_3lwON0m2hJE`

GMS manifest folder:

- `00_CONTROL/manifests`
- folder id: `1_zHlFWcC45gMylLypVfwcRycZTLn-lgA`

CoScientist durable roots:

- state: `MANAGEMENT_SCIENCES_COSCIENTIST_V4/state`
- confirmatory results: `MANAGEMENT_SCIENCES_COSCIENTIST_V4/results_confirmatory`
- development results: `MANAGEMENT_SCIENCES_COSCIENTIST_V4/results_development`

## Workflows

- `ci.yml`: Linux tests on push / PR / manual dispatch.
- `doctor.yml`: weekly and manual GMS GitHub + Drive OAuth + manifest/catalog health.
- `cycle.yml`: deterministic cycle with GMS catalog refresh.
- `analysis.yml`: freeze-locked GMS data fetch and analysis workflow.

## Resume rule

In a new session, verify live state rather than trusting this checkpoint blindly:

```powershell
cd E:\coscientist
git status --short --branch
$gh='C:\Program Files\GitHub CLI\gh.exe'
& $gh run list -R jawadresearchai-creator/coscientist --limit 10
& $gh variable list -R jawadresearchai-creator/coscientist
& $gh secret list -R jawadresearchai-creator/coscientist
```

Never print secret values. If Drive OAuth liveness fails, repair the OAuth credential; do not redesign the integration.
