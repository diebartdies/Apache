param(
    [string]$TaskName = "GitHubAutoSyncAllRepos",
    [int]$EveryMinutes = 5,
    [string]$ConfigPath = "D:\Apache\scripts\repo-sync-config.json"
)

$ErrorActionPreference = "Stop"

$syncScript = Join-Path $PSScriptRoot "sync-all-github-repos.ps1"
if (-not (Test-Path $syncScript)) {
    throw "Sync script not found: $syncScript"
}
if (-not (Test-Path $ConfigPath)) {
    throw "Config file not found: $ConfigPath"
}

$taskCmd = "powershell.exe -NoProfile -ExecutionPolicy Bypass -File `"$syncScript`" -ConfigPath `"$ConfigPath`""

schtasks /Create /F /SC MINUTE /MO $EveryMinutes /TN $TaskName /TR $taskCmd /RU "$env:USERNAME" | Out-Null
Write-Host "Scheduled task '$TaskName' created. Runs every $EveryMinutes minute(s)."
