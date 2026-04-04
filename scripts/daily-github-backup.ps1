param(
    [string]$RepoPath = "D:\Apache",
    [string]$Branch = "apache",
    [string]$Remote = "origin"
)

$ErrorActionPreference = "Stop"

$logDir = Join-Path $env:ProgramData "ApacheBackup"
$logFile = Join-Path $logDir "daily-backup.log"
if (-not (Test-Path $logDir)) {
    New-Item -ItemType Directory -Path $logDir -Force | Out-Null
}

function Write-Log {
    param([string]$Message)
    $line = "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') | $Message"
    Add-Content -Path $logFile -Value $line
    Write-Host $line
}

try {
    Set-Location $RepoPath

    git rev-parse --is-inside-work-tree | Out-Null

    Write-Log "Starting daily backup for $RepoPath"

    git add -A

    git diff --cached --quiet
    if ($LASTEXITCODE -ne 0) {
        $msg = "Automated daily backup snapshot: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')"
        git commit -m $msg | Out-Null
        Write-Log "Committed local changes"
    }
    else {
        Write-Log "No local changes to commit"
    }

    git push $Remote $Branch | Out-Null
    Write-Log "Pushed branch $Branch"

    $tag = "restore-point-$(Get-Date -Format 'yyyyMMdd-HHmmss')"
    git tag $tag
    git push $Remote $tag | Out-Null
    Write-Log "Created and pushed tag $tag"

    Write-Log "Daily backup completed successfully"
    exit 0
}
catch {
    Write-Log "ERROR: $($_.Exception.Message)"
    exit 1
}
