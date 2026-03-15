param(
  [Parameter(Mandatory=$false)]
  [string]$Source = "\\Drgift\ETI\ØrMet",

  [Parameter(Mandatory=$false)]
  [string]$Destination = "${PSScriptRoot}\albums",

  [Parameter(Mandatory=$false)]
  [switch]$Recurse = $true,

  # Remove any non-3000x3000 jpg/jpeg already present in Destination
  [Parameter(Mandatory=$false)]
  [switch]$PruneNon3000Images = $true
)

$ErrorActionPreference = 'Stop'

if (-not (Test-Path -LiteralPath $Source)) {
  throw "Source path not found: $Source"
}

New-Item -ItemType Directory -Force -Path $Destination | Out-Null

$extensions = @('*.wav', '*.jpg', '*.jpeg')

Write-Host "Syncing audio + cover images" -ForegroundColor Cyan
Write-Host "  From: $Source"
Write-Host "  To:   $Destination"
Write-Host "  Types: $($extensions -join ', ')"

if ($PruneNon3000Images) {
  Get-ChildItem -LiteralPath $Destination -Recurse -File -Include *.jpg, *.jpeg -ErrorAction SilentlyContinue |
    Where-Object { $_.Name -notmatch '3000x3000' } |
    Remove-Item -Force -ErrorAction SilentlyContinue
}

$files = @()
foreach ($pattern in $extensions) {
  $gciParams = @{ LiteralPath = $Source; File = $true; Filter = $pattern }
  if ($Recurse) { $gciParams.Recurse = $true }
  $files += Get-ChildItem @gciParams
}

# Keep all .wav, but only keep cover images that are explicitly 3000x3000
$files = $files | Where-Object {
  $_.Extension -ieq '.wav' -or $_.Name -match '3000x3000'
}

if (-not $files) {
  Write-Host "No matching files found under: $Source" -ForegroundColor Yellow
  exit 0
}

foreach ($file in $files) {
  $relative = $file.FullName.Substring($Source.Length).TrimStart('\\')
  $target = Join-Path $Destination $relative
  $targetDir = Split-Path -Parent $target
  New-Item -ItemType Directory -Force -Path $targetDir | Out-Null
  Copy-Item -LiteralPath $file.FullName -Destination $target -Force
}

Write-Host "Copied $($files.Count) file(s)." -ForegroundColor Green
