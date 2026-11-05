#Requires -Version 5.1
$ErrorActionPreference = "Continue"
$ProductName = "LockOn Bridge"
$ProductId = "LockOnBridge"
$TaskName = "LockOn Bridge"

function Get-LockOnInstallRoot {
    $fromUninstall = $null
    $regUninstall = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\$ProductId"
    if (Test-Path $regUninstall) {
        $fromUninstall = (Get-ItemProperty -Path $regUninstall -ErrorAction SilentlyContinue).InstallLocation
    }
    $fromApp = $null
    $regApp = "HKCU:\Software\$ProductId"
    if (Test-Path $regApp) {
        $fromApp = (Get-ItemProperty -Path $regApp -ErrorAction SilentlyContinue).DataRoot
    }
    foreach ($candidate in @($fromApp, $fromUninstall)) {
        if ($candidate -and (Test-Path -LiteralPath $candidate)) {
            return $candidate
        }
    }
    return (Join-Path $env:LOCALAPPDATA $ProductId)
}

$InstallRoot = Get-LockOnInstallRoot
$LegacyRoot = Join-Path $env:LOCALAPPDATA $ProductId

Write-Host "Uninstalling $ProductName…"

try { Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue } catch {}
try { Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue } catch {}

Get-CimInstance Win32_Process -Filter "Name = 'python.exe' OR Name = 'pythonw.exe' OR Name = 'py.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -match 'lockon_bridge|LockOnBridge' } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }

try {
    Get-NetFirewallRule -DisplayName "LockOn Bridge 8112" -ErrorAction SilentlyContinue |
        Remove-NetFirewallRule -ErrorAction SilentlyContinue
} catch {}

$regPath = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\$ProductId"
if (Test-Path $regPath) { Remove-Item -Path $regPath -Recurse -Force }
$regApp = "HKCU:\Software\$ProductId"
if (Test-Path $regApp) { Remove-Item -Path $regApp -Recurse -Force }

$roots = @($InstallRoot)
if ($LegacyRoot -and ($LegacyRoot -ne $InstallRoot)) {
    $roots += $LegacyRoot
}
$removeLines = foreach ($r in ($roots | Select-Object -Unique)) {
    "Remove-Item -LiteralPath '$($r -replace "'","''")' -Recurse -Force -ErrorAction SilentlyContinue"
}
$removeCmd = @"
Start-Sleep -Seconds 2
$($removeLines -join "`n")
"@
Start-Process powershell.exe -ArgumentList @("-NoProfile", "-Command", $removeCmd) -WindowStyle Hidden

Write-Host "Done. $ProductName will disappear from Apps after a moment."
if ($Host.Name -eq "ConsoleHost") {
    Start-Sleep -Seconds 2
}
