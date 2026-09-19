#Requires -Version 5.1
<#
.SYNOPSIS
  Builds LockOn Bridge as an onedir folder (no UPX) and packs dist\LockOnBridge.zip
  Prefers Python 3.12 (Defender ML flags 3.14+ PyInstaller stubs more often).
#>
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root

Write-Host "=== Building LockOn Bridge (onedir, no UPX) ===" -ForegroundColor Cyan

$py = $null
foreach ($ver in @("-3.12", "-3.11", "-3")) {
    try {
        $candidate = (& py $ver -c "import sys; print(sys.executable)" 2>$null)
        if ($candidate -and (Test-Path $candidate.Trim())) {
            $py = $candidate.Trim()
            break
        }
    } catch {}
}
if (-not $py) {
    Write-Host "Python 3.12+ required (py launcher). Prefer 3.12 for fewer Defender false positives." -ForegroundColor Red
    exit 1
}

Write-Host "Using $py"
& $py -c "import sys; print(sys.version)"
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
$uninst = Join-Path $folder "uninstall.exe"
if (-not (Test-Path $exe)) {
    Write-Host "Build failed - exe not found in onedir output." -ForegroundColor Red
    exit 1
}
if (-not (Test-Path $uninst)) {
    Write-Host "Build failed - uninstall.exe not found next to LockOnBridge.exe." -ForegroundColor Red
    exit 1
}

$zip = Join-Path $Root "dist\LockOnBridge.zip"
if (Test-Path $zip) { Remove-Item -Force $zip }
Add-Type -AssemblyName System.IO.Compression.FileSystem
[System.IO.Compression.ZipFile]::CreateFromDirectory(
    $folder,
    $zip,
    [System.IO.Compression.CompressionLevel]::Optimal,
    $true  # include base folder name LockOnBridge/
)

$sizeMb = [math]::Round((Get-Item $zip).Length / 1MB, 1)
$sha = (Get-FileHash $zip -Algorithm SHA256).Hash
Write-Host ""
Write-Host ('OK folder: {0}' -f $folder) -ForegroundColor Green
Write-Host ('OK zip: {0} ({1} MB)' -f $zip, $sizeMb) -ForegroundColor Green
Write-Host ('SHA256: {0}' -f $sha) -ForegroundColor Cyan
Write-Host "Distribute the ZIP (extract, then run LockOnBridge.exe). Avoid UPX/one-file to reduce Defender false positives."
