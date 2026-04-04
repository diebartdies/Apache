param(
    [string]$ConfigPath = "D:\Apache\scripts\repo-sync-config.json"
)

$ErrorActionPreference = "Stop"

$logDir = Join-Path $env:ProgramData "RepoSync"
$logFile = Join-Path $logDir "sync-all.log"
$mutexNames = @(
    "Global\GitHubAutoSyncAllReposMutex",
    "Local\GitHubAutoSyncAllReposMutex"
)
$mutex = $null
$ownsMutex = $false

if (-not (Test-Path $logDir)) {
    New-Item -ItemType Directory -Path $logDir -Force | Out-Null
}

function Write-Log {
    param([string]$Message)
    $line = "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') | $Message"
    Add-Content -Path $logFile -Value $line
    Write-Host $line
}

function Ensure-ConfigFile {
    param([string]$Path)

    if (Test-Path $Path) {
        return
    }

    $defaultConfig = @{
        repositories = @("D:\Apache")
    } | ConvertTo-Json -Depth 4

    $configDir = Split-Path -Parent $Path
    if (-not (Test-Path $configDir)) {
        New-Item -ItemType Directory -Path $configDir -Force | Out-Null
    }

    Set-Content -Path $Path -Value $defaultConfig
    Write-Log "Config file was missing. Created default config at: $Path"
}

function Write-MemorySnapshot {
    param([string]$Context)

    try {
        $proc = Get-Process -Id $PID -ErrorAction Stop
        $workingSetMb = [Math]::Round($proc.WorkingSet64 / 1MB, 2)
        $privateMb = [Math]::Round($proc.PrivateMemorySize64 / 1MB, 2)
        Write-Log "[memory] $Context | WS=${workingSetMb}MB Private=${privateMb}MB"
    }
    catch {
        Write-Log "[memory] $Context | unavailable: $($_.Exception.Message)"
    }
}

function Remove-AdditionalSyncProcesses {
    param([int]$CurrentPid)

    try {
        $pattern = "sync-all-github-repos\.ps1|run-sync-hidden\.vbs"
        $processes = Get-CimInstance Win32_Process -Filter "Name='powershell.exe' OR Name='pwsh.exe' OR Name='wscript.exe' OR Name='cscript.exe'" |
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

function Test-SyncSafety {
    param([string]$RepoPath)

    # Anything staged for deletion?
    $deletedPaths = @(git diff --cached --name-only --diff-filter=D)
    if (-not $deletedPaths) {
        return $true
    }

    $criticalPaths = @(
        "compose.yaml",
        "Dockerfile",
        "nginx/nginx.conf",
        "scripts/repo-sync-config.json",
        "scripts/sync-all-github-repos.ps1",
        "webapp/app.py",
        ".github/workflows/terraform.yml",
        ".github/workflows/pullApache.yml"
    )

    $criticalDeleted = @()
    foreach ($path in $deletedPaths) {
        if (
            $criticalPaths -contains $path -or
            $path -like "webapp/*" -or
            $path -like "albums/*"
        ) {
            $criticalDeleted += $path
        }
    }

    if ($criticalDeleted.Count -gt 0) {
        Write-Log "[$RepoPath] SAFETY STOP: critical files/folders are staged for deletion."
        foreach ($p in $criticalDeleted) {
            Write-Log "[$RepoPath]   DELETE BLOCKED: $p"
        }
        git reset | Out-Null
        return $false
    }

    if ($deletedPaths.Count -ge 10) {
        Write-Log "[$RepoPath] SAFETY STOP: $($deletedPaths.Count) deletions detected (possible bad sync state)."
        git reset | Out-Null
        return $false
    }

    return $true
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

        if (-not (Test-SyncSafety -RepoPath $RepoPath)) {
            Write-Log "[$RepoPath] Sync skipped due to safety guard."
            return
        }

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
        $pushExit = $LASTEXITCODE

        if ($pushExit -ne 0) {
            Write-Log "[$RepoPath] Standard push failed on '$branch' (exit=$pushExit). Trying upstream push..."
            git push -u origin $branch | Out-Null
            $pushExit = $LASTEXITCODE
        }

        if ($pushExit -ne 0) {
            Write-Log "[$RepoPath] Push FAILED to $origin on '$branch' (exit=$pushExit)"
            return
        }

        Write-Log "[$RepoPath] Push completed to $origin"
    }
    catch {
        Write-Log "[$RepoPath] ERROR: $($_.Exception.Message)"
    }
    finally {
        [System.GC]::Collect()
        [System.GC]::WaitForPendingFinalizers()
        Pop-Location
    }
}

function Acquire-ScriptMutex {
    foreach ($name in $mutexNames) {
        try {
            $createdNew = $false
            $candidate = [System.Threading.Mutex]::new($true, $name, [ref]$createdNew)
            return @{
                Mutex = $candidate
                CreatedNew = $createdNew
                Name = $name
            }
        }
        catch [System.UnauthorizedAccessException] {
            Write-Log "Mutex '$name' unavailable: $($_.Exception.Message)"
        }
    }

    throw "Could not acquire a process mutex for repo sync."
}

try {
    $mutexResult = Acquire-ScriptMutex
    $mutex = $mutexResult.Mutex
    $ownsMutex = $true
    if (-not $mutexResult.CreatedNew) {
        Write-Log "Another sync instance is already running; exiting."
        exit 0
    }
    Write-Log "Using mutex '$($mutexResult.Name)'"

    Remove-AdditionalSyncProcesses -CurrentPid $PID

    Ensure-ConfigFile -Path $ConfigPath

    $config = Get-Content -Path $ConfigPath -Raw | ConvertFrom-Json
    if (-not $config.repositories -or $config.repositories.Count -eq 0) {
        throw "No repositories defined in config: $ConfigPath"
    }

    Write-MemorySnapshot -Context "before sync"
    Write-Log "--- sync-all start ---"
    foreach ($repo in $config.repositories) {
        Sync-Repo -RepoPath $repo
        Write-MemorySnapshot -Context "after $repo"
    }
    Write-Log "--- sync-all end ---"
    Write-MemorySnapshot -Context "after sync"
    exit 0
}
catch {
    Write-Log "FATAL: $($_.Exception.Message)"
    exit 1
}
finally {
    if ($mutex -and $ownsMutex) {
        try {
            $mutex.ReleaseMutex() | Out-Null
        }
        catch {
            # no-op
        }
        $mutex.Dispose()
    }
}
