param(
    [string]$SecretFile = ".secrets/local-secrets.dpapi.json",
    [string]$ImportFromEnvFile = ".env",
    [string[]]$SecretNames = @(
        "EASYDNS_PASSWORD",
        "LDAP_ADMIN_PASSWORD",
        "LDAP_DEFAULT_PASSWORD",
        "STRIPE_SECRET_KEY",
        "MERCADOPAGO_ACCESS_TOKEN",
        "OIDC_CLIENT_SECRET"
    )
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
$dirPath = Split-Path -Parent $fullPath
if ($dirPath -and -not (Test-Path $dirPath)) {
    New-Item -ItemType Directory -Path $dirPath -Force | Out-Null
}

$envSource = @{}
$envFilePath = Join-Path (Get-Location) $ImportFromEnvFile
if (Test-Path $envFilePath) {
    foreach ($line in Get-Content -Path $envFilePath) {
        $trim = [string]$line
        $trim = $trim.Trim()
        if (-not $trim -or $trim.StartsWith("#") -or -not $trim.Contains("=")) {
            continue
        }
        $pair = $trim.Split("=", 2)
        if ($pair.Count -ne 2) {
            continue
        }
        $key = $pair[0].Trim()
        $value = $pair[1].Trim().Trim('"')
        if ($key) {
            $envSource[$key] = $value
        }
    }
}

$payload = @{}
foreach ($name in $SecretNames) {
    $secure = $null
    if ($envSource.ContainsKey($name) -and -not [string]::IsNullOrWhiteSpace($envSource[$name])) {
        $secure = ConvertTo-SecureString -String $envSource[$name] -AsPlainText -Force
    }
    else {
        $secure = Read-Host "Enter value for $name (leave empty to skip)" -AsSecureString
        $plain = Convert-SecureToPlain -SecureValue $secure
        if ([string]::IsNullOrWhiteSpace($plain)) {
            continue
        }
    }
    $payload[$name] = ConvertFrom-SecureString -SecureString $secure
}

if ($payload.Count -eq 0) {
    Write-Host "No secrets were provided. Nothing saved."
    exit 0
}

$payload | ConvertTo-Json -Depth 5 | Set-Content -Path $fullPath -Encoding UTF8
Write-Host "Saved encrypted secrets to $fullPath"
Write-Host "These values are encrypted with your current Windows profile (DPAPI)."
