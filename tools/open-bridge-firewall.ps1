#Requires -RunAsAdministrator
# Allow LockOn Bridge so the phone can poll OCR rewards on TCP 8112.
$port = 8112
$name = "LockOn Bridge $port"
netsh advfirewall firewall delete rule name="$name" | Out-Null
netsh advfirewall firewall add rule name="$name" dir=in action=allow protocol=TCP localport=$port profile=any
if ($LASTEXITCODE -eq 0) {
    Write-Host "OK: inbound TCP $port allowed ($name)" -ForegroundColor Green
} else {
    Write-Host "Failed to add firewall rule." -ForegroundColor Red
    exit 1
}
