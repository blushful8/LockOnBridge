#Requires -Version 5.1
<#
  Legacy helper. Prefer LockOnBridge.exe (Build LockOn Bridge.bat / Releases).
  Opens the control UI so the user can enable Bridge (autostart + agent).
#>
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Exe = Join-Path $Root "dist\LockOnBridge.exe"
if (Test-Path $Exe) {
    Start-Process -FilePath $Exe
    exit 0
}
Write-Host "No dist\LockOnBridge.exe — launching Python UI…" -ForegroundColor Yellow
Set-Location $Root
& py -3 -m pip install -r (Join-Path $Root "requirements.txt") -q
& py -3 -m lockon_bridge --ui
