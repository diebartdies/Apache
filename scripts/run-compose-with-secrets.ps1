param(
    [string]$SecretFile = ".secrets/local-secrets.dpapi.json",
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$ComposeArgs
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Convert-SecureToPlain([securestring]$SecureValue) {
    if (-not $SecureValue) { return "" }
    $bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($SecureValue)
    try {
        return [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr)
    }
    finally {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr)
    }
}

$fullPath = Join-Path (Get-Location) $SecretFile
if (-not (Test-Path $fullPath)) {
    throw "Encrypted secret file not found: $fullPath. Run scripts/save-local-secrets.ps1 first."
}

if (-not $ComposeArgs -or $ComposeArgs.Count -eq 0) {
    $ComposeArgs = @("up", "--build", "-d", "apache", "nginx")
}

$json = Get-Content -Raw -Path $fullPath | ConvertFrom-Json -AsHashtable
$injectedKeys = @()

try {
    foreach ($key in $json.Keys) {
        $secure = ConvertTo-SecureString -String $json[$key]
        $plain = Convert-SecureToPlain -SecureValue $secure
        Set-Item -Path "Env:$key" -Value $plain
        $injectedKeys += $key
    }

    Write-Host ("Injected " + $injectedKeys.Count + " secret(s) into process environment.")
    Write-Host ("Running: docker compose " + ($ComposeArgs -join " "))
    docker compose @ComposeArgs
    $exitCode = $LASTEXITCODE
}
finally {
    foreach ($key in $injectedKeys) {
        Remove-Item -Path "Env:$key" -ErrorAction SilentlyContinue
    }
}

exit $exitCode
