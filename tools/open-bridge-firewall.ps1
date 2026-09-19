#Requires -RunAsAdministrator
# Allow LockOn Bridge so the phone can poll OCR rewards on TCP 8112.
# Also removes Defender Block-all rules on LockOnBridge.exe (those beat port Allow).
$port = 8112
$name = "LockOn Bridge $port"
$appAllow = "LockOn Bridge App Allow"
$exe = Join-Path $env:LOCALAPPDATA "LockOnBridge\app\LockOnBridge.exe"

$blockNames = @(
    "lockonbridge.exe",
    "LockOn Bridge - OCR companion for War Thunder",
    "lockonbridge (1)",
    "lockonbridge (2)"
)
foreach ($n in $blockNames) {
    netsh advfirewall firewall delete rule name="$n" | Out-Null
}

netsh advfirewall firewall delete rule name="$name" | Out-Null
netsh advfirewall firewall delete rule name="$appAllow" | Out-Null
netsh advfirewall firewall add rule name="$name" dir=in action=allow protocol=TCP localport=$port profile=any | Out-Null
if (Test-Path $exe) {
    netsh advfirewall firewall add rule name="$appAllow" dir=in action=allow program="$exe" enable=yes profile=any protocol=TCP | Out-Null
}
try {
    Add-MpPreference -ExclusionPath (Join-Path $env:LOCALAPPDATA "LockOnBridge") -ErrorAction SilentlyContinue
    Add-MpPreference -ExclusionProcess "LockOnBridge.exe" -ErrorAction SilentlyContinue
} catch {}

if ($LASTEXITCODE -eq 0) {
    Write-Host "OK: inbound TCP $port + app allow ($name)" -ForegroundColor Green
} else {
    Write-Host "Failed to add firewall rule." -ForegroundColor Red
    exit 1
}
