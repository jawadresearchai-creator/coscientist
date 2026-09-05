$ErrorActionPreference = 'Stop'

Write-Host 'AI-Washing Event Study — self-hosted Drive pipeline'
Write-Host "Commit: $env:GITHUB_SHA"

function Resolve-DrivePaths {
    $rawMatches = @()
    Get-PSDrive -PSProvider FileSystem | ForEach-Object {
        $root = $_.Root
        foreach ($candidate in @(
            (Join-Path $root 'My Drive\Raw Data\raw_filings'),
            (Join-Path $root 'Raw Data\raw_filings')
        )) {
            if (Test-Path $candidate) { $rawMatches += $candidate }
        }
    }
    $rawMatches = $rawMatches | Sort-Object -Unique
    if ($rawMatches.Count -ne 1) {
        throw "Could not uniquely resolve Google Drive Raw Data\raw_filings. Matches: $($rawMatches -join ', ')"
    }

    $raw = $rawMatches[0]
    $rawData = Split-Path $raw -Parent
    $myDrive = Split-Path $rawData -Parent
    $event = Join-Path $myDrive 'Event Study'
    if (-not (Test-Path $event)) {
        throw "Found raw corpus at $raw but Event Study was not found at $event"
    }
    return @{ Raw = $raw; Event = $event }
}

function Invoke-Extractor([string]$Python, [string]$Raw, [string]$Out, [string]$Mode, [int]$Workers) {
    New-Item -ItemType Directory -Force -Path $Out | Out-Null
    Write-Host "Running $Mode extraction -> $Out"
    & $Python -m coscientist.eventstudy_local --raw-dir $Raw --out-dir $Out --mode $Mode --workers $Workers
    return $LASTEXITCODE
}

function Copy-Atomic([string]$Source, [string]$Destination) {
    $parent = Split-Path $Destination -Parent
    New-Item -ItemType Directory -Force -Path $parent | Out-Null
    $tmp = "$Destination.part"
    Copy-Item -LiteralPath $Source -Destination $tmp -Force
    Move-Item -LiteralPath $tmp -Destination $Destination -Force
}

$paths = Resolve-DrivePaths
$raw = $paths.Raw
$event = $paths.Event
$count = (Get-ChildItem -LiteralPath $raw -File -Filter '*.txt').Count
if ($count -ne 7361) { throw "Raw corpus count mismatch: expected 7361, observed $count" }

Write-Host "Raw corpus : $raw"
Write-Host "Event Study: $event"
Write-Host "Filings    : $count"

# Use a persistent venv outside the repository checkout so reruns are fast.
$venvRoot = Join-Path $env:USERPROFILE '.coscientist-eventstudy-venv'
$python = Join-Path $venvRoot 'Scripts\python.exe'
if (-not (Test-Path $python)) {
    python -m venv $venvRoot
}
& $python -m pip install --upgrade pip
& $python -m pip install -e "$env:GITHUB_WORKSPACE[dev]"

$runtime = Join-Path $event '_phase2_v2_self_hosted'
$smokeOut = Join-Path $runtime 'smoke'
$qaOut = Join-Path $runtime 'qa100'
$fullOut = Join-Path $runtime 'full'

# Fast representative checks first; they run automatically and require no user interaction.
$code = Invoke-Extractor $python $raw $smokeOut 'smoke' 1
if ($code -ne 0) { throw "Representative smoke extraction failed with exit code $code" }
$code = Invoke-Extractor $python $raw $qaOut 'qa' 4
if ($code -ne 0) { throw "100-file extraction check failed with exit code $code" }

# Full run is filing-level checkpointed. A workflow rerun resumes rather than restarts.
$code = Invoke-Extractor $python $raw $fullOut 'full' 4

$summaryPath = Join-Path $fullOut 'LOCAL_RUN_SUMMARY.json'
if (-not (Test-Path $summaryPath)) { throw 'Full extraction produced no LOCAL_RUN_SUMMARY.json' }
$summary = Get-Content -LiteralPath $summaryPath -Raw | ConvertFrom-Json

# Always persist the current status in Drive so ChatGPT can inspect it even after a failed run.
$statusObject = [ordered]@{
    status = $(if ($summary.complete_for_mode) { 'COMPLETE' } else { 'INCOMPLETE' })
    github_sha = $env:GITHUB_SHA
    github_run_id = $env:GITHUB_RUN_ID
    execution_plane = 'GITHUB_SELF_HOSTED_WINDOWS'
    raw_dir = $raw
    raw_count = $count
    parser_version = $summary.parser_version
    processed_ok = $summary.processed_ok
    failed = $summary.failed
    section_hash_collision_rows = $summary.section_hash_collision_rows
    ai_candidate_rows = $summary.ai_candidate_rows
    total_bytes = $summary.total_bytes
    filename_set_sha256 = $summary.observed_filename_set_sha256
    outcome_inspected = 'NO'
}
$statusJson = $statusObject | ConvertTo-Json -Depth 5
$statusPath = Join-Path $event '00_Protocol\PHASE2_EXTRACTION_STATUS_v2.json'
New-Item -ItemType Directory -Force -Path (Split-Path $statusPath -Parent) | Out-Null
Set-Content -LiteralPath "$statusPath.part" -Value $statusJson -Encoding UTF8
Move-Item -LiteralPath "$statusPath.part" -Destination $statusPath -Force

if ($code -ne 0 -or -not $summary.complete_for_mode) {
    Write-Host $statusJson
    throw "Full extraction incomplete. processed_ok=$($summary.processed_ok), failed=$($summary.failed), collisions=$($summary.section_hash_collision_rows)"
}

# Publish only corrected compact derived outputs into the existing Event Study structure.
Copy-Atomic (Join-Path $fullOut 'section_index.csv') (Join-Path $event '03_Extracted_Sections\section_index_v2.csv')
Copy-Atomic (Join-Path $fullOut 'extraction_failures.csv') (Join-Path $event '03_Extracted_Sections\extraction_failures_v2.csv')
Copy-Atomic (Join-Path $fullOut 'ai_sentence_candidates.csv') (Join-Path $event '04_AI_Sentence_Corpus\ai_sentence_candidates_v2.csv')
Copy-Atomic $statusPath (Join-Path $event '00_Protocol\PHASE2_EXTRACTION_AUDIT_v2.json')

$marker = [ordered]@{
    STATUS = 'COMPLETE'
    CORPUS_FILINGS = 7361
    PARSER_VERSION = $summary.parser_version
    GITHUB_SHA = $env:GITHUB_SHA
    SECTION_HASH_COLLISIONS = $summary.section_hash_collision_rows
    AI_CANDIDATE_ROWS = $summary.ai_candidate_rows
    OUTCOME_INSPECTED = 'NO'
} | ConvertTo-Json
$markerPath = Join-Path $event '00_Protocol\PHASE2_EXTRACTION_COMPLETE_v2.json'
Set-Content -LiteralPath "$markerPath.part" -Value $marker -Encoding UTF8
Move-Item -LiteralPath "$markerPath.part" -Destination $markerPath -Force

Write-Host 'PHASE2_SELF_HOSTED_COMPLETE'
Write-Host $marker
