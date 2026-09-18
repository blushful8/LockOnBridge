from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path

from . import __version__
from .paths import app_install_dir, data_root, installed_exe_path, is_frozen

log = logging.getLogger("lockon_bridge")

GITHUB_LATEST = "https://api.github.com/repos/blushful8/LockOnBridge/releases/latest"
ASSET_NAME = "LockOnBridge.zip"
USER_AGENT = f"LockOnBridge/{__version__}"


@dataclass(frozen=True)
class ReleaseInfo:
    tag: str
    version: str
    download_url: str
    name: str


def _parse_version(raw: str) -> tuple[int, ...]:
    cleaned = raw.strip().lstrip("vV")
    parts: list[int] = []
    for chunk in cleaned.split("."):
        digits = "".join(ch for ch in chunk if ch.isdigit())
        if digits:
            parts.append(int(digits))
    return tuple(parts) if parts else (0,)


def is_newer(candidate: str, current: str = __version__) -> bool:
    return _parse_version(candidate) > _parse_version(current)


def fetch_latest_release(timeout: float = 15.0) -> ReleaseInfo:
    request = urllib.request.Request(
        GITHUB_LATEST,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": USER_AGENT,
        },
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        payload = json.loads(response.read().decode("utf-8"))
    tag = str(payload.get("tag_name") or "")
    version = tag.lstrip("vV") or str(payload.get("name") or "")
    assets = payload.get("assets") or []
    url = ""
    for asset in assets:
        name = str(asset.get("name") or "")
        if name == ASSET_NAME:
            url = str(asset.get("browser_download_url") or "")
            break
    if not url:
        # Fallback for older releases that only shipped a single .exe
        for asset in assets:
            if str(asset.get("name") or "") == "LockOnBridge.exe":
                url = str(asset.get("browser_download_url") or "")
                break
    if not tag or not url:
        raise RuntimeError("latest release has no LockOnBridge.zip (or .exe) asset")
    return ReleaseInfo(
        tag=tag,
        version=version,
        download_url=url,
        name=str(payload.get("name") or tag),
    )


def download_release_file(url: str, destination: Path, timeout: float = 180.0) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_suffix(destination.suffix + ".partial")
    request = urllib.request.Request(
        url,
        headers={"User-Agent": USER_AGENT, "Accept": "application/octet-stream"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response, partial.open("wb") as out:
        while True:
            chunk = response.read(1024 * 256)
            if not chunk:
                break
            out.write(chunk)
    partial.replace(destination)
    return destination


# Back-compat name used by the UI.
download_release_exe = download_release_file


def _extract_onedir(zip_path: Path, dest_dir: Path) -> Path:
    """Extract zip so dest_dir contains LockOnBridge.exe (+ _internal)."""
    staging = dest_dir.parent / (dest_dir.name + "_staging")
    if staging.exists():
        shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path, "r") as zf:
        zf.extractall(staging)

    # Zip may contain LockOnBridge/LockOnBridge.exe or LockOnBridge.exe at root.
    exe = next(staging.rglob("LockOnBridge.exe"), None)
    if exe is None:
        raise RuntimeError("downloaded zip does not contain LockOnBridge.exe")
    bundle_root = exe.parent
    if dest_dir.exists():
        shutil.rmtree(dest_dir, ignore_errors=True)
    shutil.move(str(bundle_root), str(dest_dir))
    shutil.rmtree(staging, ignore_errors=True)
    return dest_dir / "LockOnBridge.exe"


def apply_update_and_restart(downloaded: Path) -> None:
    """
    After this process exits, replace the installed onedir bundle and relaunch.
    `downloaded` is either LockOnBridge.zip or a legacy single .exe.
    """
    if not is_frozen():
        raise RuntimeError("not a frozen build")

    install_dir = app_install_dir()
    launch = installed_exe_path()
    data_root().mkdir(parents=True, exist_ok=True)
    log_dir = data_root() / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    pid = os.getpid()
    src = str(downloaded).replace("'", "''")
    install = str(install_dir).replace("'", "''")
    launch_q = str(launch).replace("'", "''")
    update_log = str(log_dir / "update.log").replace("'", "''")

    if downloaded.suffix.lower() == ".zip":
        script = f"""
$ErrorActionPreference = 'Stop'
$log = '{update_log}'
function Write-UpdLog([string]$msg) {{
  $line = ('{{0:yyyy-MM-dd HH:mm:ss}} {{1}}' -f (Get-Date), $msg)
  Add-Content -LiteralPath $log -Value $line -Encoding UTF8
}}
try {{
  Write-UpdLog 'update helper start'
  $pidToWait = {pid}
  $zip = '{src}'
  $installDir = '{install}'
  $launch = '{launch_q}'
  for ($i = 0; $i -lt 90; $i++) {{
    if (-not (Get-Process -Id $pidToWait -ErrorAction SilentlyContinue)) {{ break }}
    Start-Sleep -Milliseconds 500
  }}
  Start-Sleep -Milliseconds 1000
  Write-UpdLog ("extract " + $zip)
  $staging = Join-Path $env:TEMP ("lockon_bridge_upd_" + $pidToWait)
  if (Test-Path $staging) {{ Remove-Item -LiteralPath $staging -Recurse -Force }}
  New-Item -ItemType Directory -Force -Path $staging | Out-Null
  Expand-Archive -LiteralPath $zip -DestinationPath $staging -Force
  $exe = Get-ChildItem -Path $staging -Filter 'LockOnBridge.exe' -Recurse -ErrorAction SilentlyContinue |
    Select-Object -First 1
  if (-not $exe) {{ throw 'LockOnBridge.exe missing in update zip' }}
  $bundle = $exe.Directory.FullName
  Write-UpdLog ("bundle " + $bundle)
  $parent = Split-Path -Parent $installDir
  New-Item -ItemType Directory -Force -Path $parent | Out-Null
  $backup = $installDir + '.bak'
  if (Test-Path $backup) {{ Remove-Item -LiteralPath $backup -Recurse -Force -ErrorAction SilentlyContinue }}
  if (Test-Path $installDir) {{
    Rename-Item -LiteralPath $installDir -NewName (Split-Path -Leaf $backup) -Force
  }}
  Copy-Item -LiteralPath $bundle -Destination $installDir -Recurse -Force
  if (-not (Test-Path $launch)) {{ throw ("launch missing after copy: " + $launch) }}
  Write-UpdLog ("launch " + $launch)
  Start-Process -FilePath $launch -WorkingDirectory $installDir
  Remove-Item -LiteralPath $zip -Force -ErrorAction SilentlyContinue
  Remove-Item -LiteralPath $staging -Recurse -Force -ErrorAction SilentlyContinue
  Remove-Item -LiteralPath $backup -Recurse -Force -ErrorAction SilentlyContinue
  Write-UpdLog 'update helper done'
}} catch {{
  Write-UpdLog ("FAIL: " + $_.Exception.Message)
  try {{
    Add-Type -AssemblyName System.Windows.Forms
    [System.Windows.Forms.MessageBox]::Show(
      ("LockOn Bridge update failed:`n" + $_.Exception.Message + "`n`nSee:`n" + $log),
      'LockOn Bridge',
      'OK',
      'Error'
    ) | Out-Null
  }} catch {{}}
  exit 1
}}
"""
    else:
        script = f"""
$ErrorActionPreference = 'Stop'
$log = '{update_log}'
function Write-UpdLog([string]$msg) {{
  $line = ('{{0:yyyy-MM-dd HH:mm:ss}} {{1}}' -f (Get-Date), $msg)
  Add-Content -LiteralPath $log -Value $line -Encoding UTF8
}}
try {{
  Write-UpdLog 'update helper start (exe)'
  $pidToWait = {pid}
  $srcExe = '{src}'
  $launch = '{launch_q}'
  $installDir = '{install}'
  for ($i = 0; $i -lt 90; $i++) {{
    if (-not (Get-Process -Id $pidToWait -ErrorAction SilentlyContinue)) {{ break }}
    Start-Sleep -Milliseconds 500
  }}
  Start-Sleep -Milliseconds 1000
  New-Item -ItemType Directory -Force -Path $installDir | Out-Null
  Copy-Item -LiteralPath $srcExe -Destination (Join-Path $installDir 'LockOnBridge.exe') -Force
  Start-Process -FilePath $launch -WorkingDirectory $installDir
  Remove-Item -LiteralPath $srcExe -Force -ErrorAction SilentlyContinue
  Write-UpdLog 'update helper done (exe)'
}} catch {{
  Write-UpdLog ("FAIL: " + $_.Exception.Message)
  try {{
    Add-Type -AssemblyName System.Windows.Forms
    [System.Windows.Forms.MessageBox]::Show(
      ("LockOn Bridge update failed:`n" + $_.Exception.Message + "`n`nSee:`n" + $log),
      'LockOn Bridge',
      'OK',
      'Error'
    ) | Out-Null
  }} catch {{}}
  exit 1
}}
"""

    tmp = Path(tempfile.gettempdir()) / f"lockon_bridge_update_{pid}.ps1"
    tmp.write_text(script, encoding="utf-8")
    subprocess.Popen(
        [
            "powershell.exe",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-WindowStyle",
            "Hidden",
            "-File",
            str(tmp),
        ],
        close_fds=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    log.info("update helper scheduled for pid %s → %s (log %s)", pid, install_dir, log_dir / "update.log")

