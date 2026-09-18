#Requires -Version 5.1
<#
.SYNOPSIS
  Builds LockOnBridge.exe (windowed, no console) into dist\
#>
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root

Write-Host "=== Building LockOn Bridge .exe ===" -ForegroundColor Cyan

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
if (Test-Path "$Root\dist\LockOnBridge.exe") { Remove-Item -Force "$Root\dist\LockOnBridge.exe" }

& $py -m PyInstaller --noconfirm --clean LockOnBridge.spec
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

$out = Join-Path $Root "dist\LockOnBridge.exe"
if (-not (Test-Path $out)) {
    Write-Host "Build failed - exe not found." -ForegroundColor Red
    exit 1
}

$sizeMb = [math]::Round((Get-Item $out).Length / 1MB, 1)
Write-Host ""
Write-Host ('OK: {0} ({1} MB)' -f $out, $sizeMb) -ForegroundColor Green
Write-Host "Double-click the exe, enable Bridge in the window (or leave disabled = zero footprint)."
