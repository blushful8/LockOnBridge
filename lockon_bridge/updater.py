from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from . import __version__
from .paths import data_root, installed_exe_path, is_frozen

log = logging.getLogger("lockon_bridge")

GITHUB_LATEST = "https://api.github.com/repos/blushful8/LockOnBridge/releases/latest"
ASSET_NAME = "LockOnBridge.exe"
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
        if str(asset.get("name") or "") == ASSET_NAME:
            url = str(asset.get("browser_download_url") or "")
            break
    if not tag or not url:
        raise RuntimeError("latest release has no LockOnBridge.exe asset")
    return ReleaseInfo(
        tag=tag,
        version=version,
        download_url=url,
        name=str(payload.get("name") or tag),
    )


def download_release_exe(url: str, destination: Path, timeout: float = 120.0) -> Path:
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


def apply_update_and_restart(new_exe: Path) -> None:
    """
    Schedule replacement of the running frozen exe after this process exits, then relaunch.
    """
    if not is_frozen():
        raise RuntimeError("not a frozen build")

    current = Path(sys.executable).resolve()
    install = installed_exe_path()
    data_root().mkdir(parents=True, exist_ok=True)

    # Prefer updating the stable install path; also replace the running copy if different.
    targets = [install]
    if current != install:
        targets.append(current)

    pid = os.getpid()
    script = f"""
$ErrorActionPreference = 'Stop'
$pidToWait = {pid}
$src = '{str(new_exe).replace("'", "''")}'
$targets = @({", ".join("'" + str(t).replace("'", "''") + "'" for t in targets)})
$launch = '{str(install).replace("'", "''")}'
for ($i = 0; $i -lt 60; $i++) {{
  if (-not (Get-Process -Id $pidToWait -ErrorAction SilentlyContinue)) {{ break }}
  Start-Sleep -Milliseconds 500
}}
Start-Sleep -Milliseconds 800
foreach ($dst in $targets) {{
  $dir = Split-Path -Parent $dst
  if ($dir) {{ New-Item -ItemType Directory -Force -Path $dir | Out-Null }}
  Copy-Item -LiteralPath $src -Destination $dst -Force
}}
Start-Process -FilePath $launch
Remove-Item -LiteralPath $src -Force -ErrorAction SilentlyContinue
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
    )
    log.info("update helper scheduled for pid %s → %s", pid, install)
