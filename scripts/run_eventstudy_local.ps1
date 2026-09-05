$ErrorActionPreference = 'Stop'

Write-Host 'AI-Washing Event Study — local Drive-backed extraction'

$matches = @()
Get-PSDrive -PSProvider FileSystem | ForEach-Object {
    $root = $_.Root
    $candidates = @(
        (Join-Path $root 'My Drive\Raw Data\raw_filings'),
        (Join-Path $root 'Raw Data\raw_filings')
    )
    foreach ($candidate in $candidates) {
        if (Test-Path $candidate) {
            $matches += $candidate
        }
    }
}
$matches = $matches | Sort-Object -Unique

if ($matches.Count -ne 1) {
    Write-Host "Could not uniquely resolve Google Drive raw_filings. Matches: $($matches -join ', ')"
    Write-Host 'Pass the path manually instead:'
    Write-Host '  python -m coscientist.eventstudy_local --raw-dir "<path>" --out-dir "<Event Study path>\_phase2_v2_local" --mode full --workers 4'
    exit 2
}

$raw = $matches[0]
$driveRoot = Split-Path (Split-Path (Split-Path $raw -Parent) -Parent) -Parent
$eventCandidates = @(
    (Join-Path $driveRoot 'My Drive\Event Study'),
    (Join-Path $driveRoot 'Event Study')
)
$event = $eventCandidates | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $event) {
    Write-Host "Raw corpus found at $raw but Event Study project folder was not found on the same Drive mount."
    exit 3
}

$out = Join-Path $event '_phase2_v2_local'
Write-Host "Raw corpus : $raw"
Write-Host "Output     : $out"

if (-not (Test-Path '.venv')) {
    python -m venv .venv
}
& .\.venv\Scripts\python.exe -m pip install --upgrade pip
& .\.venv\Scripts\python.exe -m pip install -e '.[dev]'

# Full mode is resumable. Re-running the same command skips completed checkpoints.
& .\.venv\Scripts\python.exe -m coscientist.eventstudy_local --raw-dir $raw --out-dir $out --mode full --workers 4
$code = $LASTEXITCODE

Write-Host "Extraction exit code: $code"
Write-Host "Status file: $out\LOCAL_RUN_SUMMARY.json"
exit $code
