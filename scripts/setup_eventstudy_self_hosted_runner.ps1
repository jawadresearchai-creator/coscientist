param(
    [string]$Token = "",
    [string]$RunnerRoot = "$env:USERPROFILE\actions-runner-eventstudy"
)

$ErrorActionPreference = 'Stop'
$RepoUrl = 'https://github.com/jawadresearchai-creator/coscientist'
$RunnerName = "eventstudy-$env:COMPUTERNAME"

Write-Host 'Event Study GitHub self-hosted runner bootstrap'
Write-Host 'This runner is intentionally configured under your signed-in Windows user,'
Write-Host 'not as NETWORK SERVICE, so it can see Google Drive for Desktop mounted drives.'

if (-not $Token) {
    $Token = Read-Host 'Paste the one-hour GitHub runner registration token'
}
if (-not $Token) { throw 'Runner registration token is required.' }

# Verify the Google Drive corpus is visible in this Windows user session before registering.
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
    throw "Google Drive raw_filings was not uniquely visible to this Windows user. Matches: $($rawMatches -join ', ')"
}
$count = (Get-ChildItem -LiteralPath $rawMatches[0] -File -Filter '*.txt').Count
if ($count -ne 7361) {
    throw "Expected 7,361 raw SEC .txt filings but found $count at $($rawMatches[0])."
}
Write-Host "Drive corpus visible: $($rawMatches[0]) ($count filings)"

New-Item -ItemType Directory -Force -Path $RunnerRoot | Out-Null
Set-Location $RunnerRoot

# Discover and download the current official Windows x64 runner release.
$headers = @{ 'User-Agent' = 'eventstudy-runner-bootstrap' }
$release = Invoke-RestMethod -Headers $headers -Uri 'https://api.github.com/repos/actions/runner/releases/latest'
$asset = $release.assets | Where-Object { $_.name -match '^actions-runner-win-x64-.*\.zip$' } | Select-Object -First 1
if (-not $asset) { throw 'Could not locate the current Windows x64 GitHub Actions runner release.' }
$zip = Join-Path $RunnerRoot $asset.name
if (-not (Test-Path (Join-Path $RunnerRoot 'config.cmd'))) {
    Write-Host "Downloading $($asset.name)"
    Invoke-WebRequest -Headers $headers -Uri $asset.browser_download_url -OutFile $zip
    Expand-Archive -LiteralPath $zip -DestinationPath $RunnerRoot -Force
    Remove-Item $zip -Force
}

# If this directory was previously configured, remove only the local registration files.
# A fresh runner token should be used when re-registering an existing installation.
if (Test-Path (Join-Path $RunnerRoot '.runner')) {
    Write-Host 'Runner directory is already configured; keeping the existing registration.'
} else {
    & (Join-Path $RunnerRoot 'config.cmd') `
        --unattended `
        --replace `
        --url $RepoUrl `
        --token $Token `
        --name $RunnerName `
        --labels 'eventstudy' `
        --work '_work'
    if ($LASTEXITCODE -ne 0) { throw "config.cmd failed with exit code $LASTEXITCODE" }
}

# Start under the signed-in user. This preserves visibility of Google Drive Desktop mounts.
$runCmd = Join-Path $RunnerRoot 'run.cmd'
$existing = Get-CimInstance Win32_Process | Where-Object {
    $_.CommandLine -and $_.CommandLine -like "*$RunnerRoot*Runner.Listener*"
}
if (-not $existing) {
    Start-Process -FilePath $runCmd -WorkingDirectory $RunnerRoot -WindowStyle Minimized
    Start-Sleep -Seconds 3
}

# Make it restart automatically after Windows sign-in without using a service account.
$startupDir = [Environment]::GetFolderPath('Startup')
$launcher = Join-Path $startupDir 'Start-EventStudy-GitHub-Runner.cmd'
$launcherText = "@echo off`r`ncd /d `"$RunnerRoot`"`r`nstart `"EventStudy GitHub Runner`" /min run.cmd`r`n"
Set-Content -LiteralPath $launcher -Value $launcherText -Encoding ASCII

Write-Host ''
Write-Host 'RUNNER_BOOTSTRAP_COMPLETE'
Write-Host "Runner name : $RunnerName"
Write-Host 'Labels      : self-hosted, Windows, X64, eventstudy'
Write-Host "Startup file: $launcher"
Write-Host 'The queued Event Study workflow can now be picked up by this laptop.'
