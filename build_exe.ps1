#Requires -Version 5.1
<#
.SYNOPSIS
  Builds LockOn Bridge as an onedir folder (no UPX) and packs dist\LockOnBridge.zip
#>
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root

Write-Host "=== Building LockOn Bridge (onedir, no UPX) ===" -ForegroundColor Cyan

$py = $null
try {
    $py = (& py -3 -c "import sys; print(sys.executable)" 2>$null)
} catch {}
if (-not $py) {
    Write-Host "Python 3 required (py launcher)." -ForegroundColor Red
    exit 1
}

Write-Host "Using $py"
& $py -m pip install -r requirements.txt -q
& $py -m pip install pyinstaller -q
& $py (Join-Path $Root "assets\generate_icon.py")

if (Test-Path "$Root\build") { Remove-Item -Recurse -Force "$Root\build" }
if (Test-Path "$Root\dist\LockOnBridge") { Remove-Item -Recurse -Force "$Root\dist\LockOnBridge" }
if (Test-Path "$Root\dist\LockOnBridge.zip") { Remove-Item -Force "$Root\dist\LockOnBridge.zip" }
if (Test-Path "$Root\dist\LockOnBridge.exe") { Remove-Item -Force "$Root\dist\LockOnBridge.exe" }

& $py -m PyInstaller --noconfirm --clean LockOnBridge.spec
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

$folder = Join-Path $Root "dist\LockOnBridge"
$exe = Join-Path $folder "LockOnBridge.exe"
if (-not (Test-Path $exe)) {
    Write-Host "Build failed - exe not found in onedir output." -ForegroundColor Red
    exit 1
}

$zip = Join-Path $Root "dist\LockOnBridge.zip"
Compress-Archive -Path $folder -DestinationPath $zip -Force

$sizeMb = [math]::Round((Get-Item $zip).Length / 1MB, 1)
Write-Host ""
Write-Host ('OK folder: {0}' -f $folder) -ForegroundColor Green
Write-Host ('OK zip: {0} ({1} MB)' -f $zip, $sizeMb) -ForegroundColor Green
Write-Host "Distribute the ZIP (extract, then run LockOnBridge.exe). Avoid UPX/one-file to reduce Defender false positives."
