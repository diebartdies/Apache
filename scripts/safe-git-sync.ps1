param(
    [string]$RepoPath = "D:\Apache",
    [string]$Remote = "origin",
    [string]$Branch = "",
    [string]$CommitMessage = "chore: sync local changes",
    [switch]$AutoAdd
)

$ErrorActionPreference = "Stop"

function Write-Info([string]$m) {
    Write-Host "[INFO] $m"
}

function Write-Warn([string]$m) {
    Write-Host "[WARN] $m" -ForegroundColor Yellow
}

function Write-Err([string]$m) {
    Write-Host "[ERROR] $m" -ForegroundColor Red
}

try {
    Set-Location $RepoPath

    git rev-parse --is-inside-work-tree | Out-Null

    if ([string]::IsNullOrWhiteSpace($Branch)) {
        $Branch = (git branch --show-current).Trim()
    }

    if ([string]::IsNullOrWhiteSpace($Branch)) {
        throw "Could not detect current branch."
    }

    Write-Info "Repo: $RepoPath"
    Write-Info "Flow: commit -> pull --rebase -> push"
    Write-Info "Target: $Remote/$Branch"

    if ($AutoAdd) {
        Write-Info "Staging all changes (git add -A)..."
        git add -A
    }

    # Commit only if there are staged changes
    git diff --cached --quiet
    $hasStaged = ($LASTEXITCODE -ne 0)

    if ($hasStaged) {
        Write-Info "Creating commit..."
        git commit -m $CommitMessage | Out-Null
        Write-Info "Commit created."
    }
    else {
        Write-Warn "No staged changes. Skipping commit."
    }

    # Require clean working tree before pull --rebase to avoid accidental conflicts
    $status = git status --porcelain
    if ($status) {
        Write-Warn "Working tree is not clean. Commit/stash remaining changes before pull --rebase."
        Write-Warn "Run: git status"
        exit 1
    }

    Write-Info "Pulling latest changes with rebase..."
    git pull --rebase $Remote $Branch
    if ($LASTEXITCODE -ne 0) {
        Write-Err "Pull/rebase failed (likely conflict). Resolve conflicts, then run:"
        Write-Err "  git rebase --continue"
        Write-Err "or abort with:"
        Write-Err "  git rebase --abort"
        exit 1
    }

    Write-Info "Pushing to $Remote/$Branch..."
    git push $Remote $Branch
    if ($LASTEXITCODE -ne 0) {
        Write-Err "Push failed. Check remote permissions or branch protection rules."
        exit 1
    }

    Write-Info "Done. Local and remote are synced safely."
    exit 0
}
catch {
    Write-Err $_.Exception.Message
    exit 1
}
