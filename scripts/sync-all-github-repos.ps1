param(
    [string]$ConfigPath = "D:\Apache\scripts\repo-sync-config.json"
)

$ErrorActionPreference = "Stop"

$logDir = Join-Path $env:ProgramData "RepoSync"
$logFile = Join-Path $logDir "sync-all.log"
$mutexName = "Global\GitHubAutoSyncAllReposMutex"
$mutex = $null

if (-not (Test-Path $logDir)) {
    New-Item -ItemType Directory -Path $logDir -Force | Out-Null
}

function Write-Log {
    param([string]$Message)
    $line = "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') | $Message"
    Add-Content -Path $logFile -Value $line
    Write-Host $line
}

function Remove-AdditionalSyncProcesses {
    param([int]$CurrentPid)

    try {
        $pattern = "sync-all-github-repos\.ps1|run-sync-hidden\.vbs"
        $processes = Get-CimInstance Win32_Process |
            Where-Object {
                $_.ProcessId -ne $CurrentPid -and
                $_.CommandLine -and
                $_.CommandLine -match $pattern
            }

        foreach ($proc in $processes) {
            Stop-Process -Id $proc.ProcessId -Force -ErrorAction SilentlyContinue
            Write-Log "Killed additional sync instance PID=$($proc.ProcessId)"
        }
    }
    catch {
        Write-Log "Could not inspect/kill additional instances: $($_.Exception.Message)"
    }
}

function Sync-Repo {
    param([string]$RepoPath)

    if (-not (Test-Path $RepoPath)) {
        Write-Log "[$RepoPath] Skipped (path not found)"
        return
    }

    if (-not (Test-Path (Join-Path $RepoPath ".git"))) {
        Write-Log "[$RepoPath] Skipped (not a git repo)"
        return
    }

    Push-Location $RepoPath
    try {
        git rev-parse --is-inside-work-tree | Out-Null

        $origin = (git remote get-url origin 2>$null)
        if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($origin)) {
            Write-Log "[$RepoPath] Skipped (missing origin remote)"
            return
        }

        $branch = (git branch --show-current).Trim()
        if ([string]::IsNullOrWhiteSpace($branch)) {
            Write-Log "[$RepoPath] Skipped (cannot detect current branch)"
            return
        }

        git add -A

        git diff --cached --quiet
        $hasChanges = ($LASTEXITCODE -ne 0)

        if ($hasChanges) {
            $msg = "auto-sync $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')"
            git commit -m $msg | Out-Null
            Write-Log "[$RepoPath] Commit created on '$branch'"
        }
        else {
            Write-Log "[$RepoPath] No local changes"
        }

        git push origin $branch | Out-Null
        if ($LASTEXITCODE -ne 0) {
            git push -u origin $branch | Out-Null
        }

        Write-Log "[$RepoPath] Push completed to $origin"
    }
    catch {
        Write-Log "[$RepoPath] ERROR: $($_.Exception.Message)"
    }
    finally {
        Pop-Location
    }
}

try {
    $createdNew = $false
    $mutex = [System.Threading.Mutex]::new($true, $mutexName, [ref]$createdNew)
    if (-not $createdNew) {
        Write-Log "Another sync instance is already running; exiting."
        exit 0
    }

    Remove-AdditionalSyncProcesses -CurrentPid $PID

    if (-not (Test-Path $ConfigPath)) {
        throw "Config file not found: $ConfigPath"
    }

    $config = Get-Content -Path $ConfigPath -Raw | ConvertFrom-Json
    if (-not $config.repositories -or $config.repositories.Count -eq 0) {
        throw "No repositories defined in config: $ConfigPath"
    }

    Write-Log "--- sync-all start ---"
    foreach ($repo in $config.repositories) {
        Sync-Repo -RepoPath $repo
    }
    Write-Log "--- sync-all end ---"
    exit 0
}
catch {
    Write-Log "FATAL: $($_.Exception.Message)"
    exit 1
}
finally {
    if ($mutex) {
        try {
            $mutex.ReleaseMutex() | Out-Null
        }
        catch {
            # no-op
        }
        $mutex.Dispose()
    }
}
