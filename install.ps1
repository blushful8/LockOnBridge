#Requires -Version 5.1
<#
.SYNOPSIS
  Installs LockOn Bridge for the current user: auto-starts at logon, follows War Thunder,
  appears under Settings → Apps (Add or remove programs).
#>
$ErrorActionPreference = "Stop"
$ProductName = "LockOn Bridge"
$ProductId = "LockOnBridge"
$TaskName = "LockOn Bridge"
$SourceRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$InstallRoot = Join-Path $env:LOCALAPPDATA "LockOnBridge"

function Test-Python {
    try {
        & py -3 -c "import sys; print(sys.executable)" 2>$null
        return $true
    } catch {
        return $false
    }
}

Write-Host "=== $ProductName installer ===" -ForegroundColor Cyan

if (-not (Test-Python)) {
    Write-Host "Python 3 (py launcher) is required." -ForegroundColor Red
    Write-Host "Install from https://www.python.org/downloads/ and tick 'Add python.exe to PATH'."
    exit 1
}

# Stop previous instance / task
try {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
} catch {}
Get-CimInstance Win32_Process -Filter "Name = 'python.exe' OR Name = 'pythonw.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -match 'lockon_bridge' } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }

Write-Host "Install folder: $InstallRoot"
New-Item -ItemType Directory -Force -Path $InstallRoot | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $InstallRoot "logs") | Out-Null

# Copy package (not the whole repo junk)
$copyItems = @(
    "lockon_bridge",
    "requirements.txt",
    "README.md",
    "uninstall.ps1"
)
foreach ($item in $copyItems) {
    $src = Join-Path $SourceRoot $item
    $dst = Join-Path $InstallRoot $item
    if (Test-Path $src) {
        if (Test-Path $dst) { Remove-Item -Recurse -Force $dst }
        Copy-Item -Recurse -Force $src $dst
    }
}

Write-Host "Creating virtualenv…"
$venvPython = Join-Path $InstallRoot "venv\Scripts\python.exe"
$venvPythonw = Join-Path $InstallRoot "venv\Scripts\pythonw.exe"
if (-not (Test-Path $venvPython)) {
    & py -3 -m venv (Join-Path $InstallRoot "venv")
}
Write-Host "Installing dependencies (first time may take a minute)…"
& $venvPython -m pip install --upgrade pip -q
& $venvPython -m pip install -r (Join-Path $InstallRoot "requirements.txt") -q

Write-Host "Registering logon task (silent)…"
$action = New-ScheduledTaskAction `
    -Execute $venvPythonw `
    -Argument "-m lockon_bridge --idle-poll 30" `
    -WorkingDirectory $InstallRoot
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 1)
$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited
Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings -Principal $principal -Force | Out-Null
Start-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue

# Uninstall entry → Apps & features
$uninstallScript = Join-Path $InstallRoot "uninstall.ps1"
$regPath = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\$ProductId"
New-Item -Path $regPath -Force | Out-Null
Set-ItemProperty -Path $regPath -Name "DisplayName" -Value $ProductName
Set-ItemProperty -Path $regPath -Name "Publisher" -Value "LockOn"
Set-ItemProperty -Path $regPath -Name "DisplayVersion" -Value "0.1.0"
Set-ItemProperty -Path $regPath -Name "InstallLocation" -Value $InstallRoot
Set-ItemProperty -Path $regPath -Name "NoModify" -Value 1 -Type DWord
Set-ItemProperty -Path $regPath -Name "NoRepair" -Value 1 -Type DWord
$uninstallCmd = "powershell.exe -NoProfile -ExecutionPolicy Bypass -File `"$uninstallScript`""
Set-ItemProperty -Path $regPath -Name "UninstallString" -Value $uninstallCmd
Set-ItemProperty -Path $regPath -Name "QuietUninstallString" -Value $uninstallCmd

# Optional firewall rule for phone access (current user / private profile)
try {
    $ruleName = "LockOn Bridge 8112"
    Get-NetFirewallRule -DisplayName $ruleName -ErrorAction SilentlyContinue | Remove-NetFirewallRule -ErrorAction SilentlyContinue
    New-NetFirewallRule -DisplayName $ruleName -Direction Inbound -Protocol TCP -LocalPort 8112 -Action Allow -Profile Private -ErrorAction SilentlyContinue | Out-Null
} catch {
    Write-Host "Note: could not add firewall rule automatically (run as admin if the phone cannot reach :8112)." -ForegroundColor Yellow
}

Write-Host ""
Write-Host "Installed." -ForegroundColor Green
Write-Host "- Starts silently at Windows logon"
Write-Host "- Wakes only when War Thunder (aces.exe) is running; stops when the game exits"
Write-Host "- Remove via Settings → Apps → $ProductName → Uninstall"
Write-Host "- Logs: $InstallRoot\logs\bridge.log"
Write-Host ""
Write-Host "In the LockOn phone app: Settings → enable Use LockOn Bridge."
