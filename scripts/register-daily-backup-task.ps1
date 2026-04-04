param(
    [string]$TaskName = "ApacheDailyGitBackup",
    [string]$Time = "02:00"
)

$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
$backupScript = Join-Path $PSScriptRoot "daily-github-backup.ps1"

if (-not (Test-Path $backupScript)) {
    throw "Backup script not found: $backupScript"
}

$taskCmd = "powershell.exe -NoProfile -ExecutionPolicy Bypass -File `"$backupScript`" -RepoPath `"$repoRoot`" -Branch apache -Remote origin"

schtasks /Create /TN $TaskName /TR $taskCmd /SC DAILY /ST $Time /F | Out-Null
Write-Host "Scheduled task '$TaskName' created. Runs daily at $Time"
