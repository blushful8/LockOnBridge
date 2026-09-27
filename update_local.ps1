#Requires -Version 5.1
<#
.SYNOPSIS
  Build onedir and refresh %LocalAppData%\LockOnBridge\app (no GitHub release).
#>
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root

Write-Host "=== Local update (build + install copy) ===" -ForegroundColor Cyan
& powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $Root "build_exe.ps1")
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

$src = Join-Path $Root "dist\LockOnBridge"
$dst = Join-Path $env:LOCALAPPDATA "LockOnBridge\app"
if (-not (Test-Path (Join-Path $src "LockOnBridge.exe"))) {
    Write-Host "Missing dist\LockOnBridge\LockOnBridge.exe" -ForegroundColor Red
    exit 1
}

Get-Process -Name "LockOnBridge" -ErrorAction SilentlyContinue | ForEach-Object {
    Write-Host "Stopping running LockOnBridge (pid $($_.Id))…"
    Stop-Process -Id $_.Id -Force -ErrorAction SilentlyContinue
}
Start-Sleep -Milliseconds 400

New-Item -ItemType Directory -Force -Path $dst | Out-Null
robocopy $src $dst /MIR /NFL /NDL /NJH /NJS /nc /ns /np | Out-Null
$rc = $LASTEXITCODE
# robocopy: 0-7 success
if ($rc -ge 8) {
    Write-Host "robocopy failed code $rc" -ForegroundColor Red
    exit $rc
}

$exe = Join-Path $dst "LockOnBridge.exe"
Write-Host ""
Write-Host ("OK local install: {0}" -f $exe) -ForegroundColor Green
Write-Host ("Updated: {0}" -f (Get-Item $exe).LastWriteTime)
Write-Host "Start from Desktop shortcut or that path. No GitHub release."
