#Requires -Version 5.1
$ErrorActionPreference = "Continue"
$ProductName = "LockOn Bridge"
$ProductId = "LockOnBridge"
$TaskName = "LockOn Bridge"
$InstallRoot = Join-Path $env:LOCALAPPDATA "LockOnBridge"

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

# Remove install tree (schedule after this process exits if we live inside it)
$removeCmd = @"
Start-Sleep -Seconds 2
Remove-Item -LiteralPath '$InstallRoot' -Recurse -Force -ErrorAction SilentlyContinue
"@
Start-Process powershell.exe -ArgumentList @("-NoProfile", "-Command", $removeCmd) -WindowStyle Hidden

Write-Host "Done. $ProductName will disappear from Apps after a moment."
if ($Host.Name -eq "ConsoleHost") {
    Start-Sleep -Seconds 2
}
