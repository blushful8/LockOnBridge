from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
from pathlib import Path

from .paths import (
    PRODUCT_ID,
    PRODUCT_NAME,
    TASK_NAME,
    app_executable,
    data_root,
    installed_exe_path,
    is_frozen,
    log_dir,
)

log = logging.getLogger("lockon_bridge")


def _run_ps(script: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            "powershell.exe",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            script,
        ],
        capture_output=True,
        text=True,
        check=False,
    )


def _launch_parts() -> tuple[str, str, str]:
    """Return (execute, arguments, working_directory) for Scheduled Task."""
    if is_frozen():
        exe = str(installed_exe_path() if installed_exe_path().is_file() else app_executable())
        return exe, "--background", str(Path(exe).parent)
    # Dev / source: prefer pythonw so no console flashes at logon.
    py = Path(sys.executable)
    pythonw = py.with_name("pythonw.exe")
    execute = str(pythonw if pythonw.is_file() else py)
    # Package root = parent of lockon_bridge/
    package_root = Path(__file__).resolve().parent.parent
    return execute, f'-m lockon_bridge --background', str(package_root)


def ensure_install_copy() -> Path | None:
    """
    When running as a frozen exe from Downloads/etc., copy into LocalAppData
    so autostart always points at a stable path.
    """
    if not is_frozen():
        return None
    src = app_executable()
    dst = installed_exe_path()
    data_root().mkdir(parents=True, exist_ok=True)
    log_dir().mkdir(parents=True, exist_ok=True)
    try:
        if src.resolve() != dst.resolve():
            shutil.copy2(src, dst)
            log.info("Installed copy → %s", dst)
        return dst
    except OSError as exc:
        log.warning("Could not copy exe to install folder: %s", exc)
        return src


def register_autostart() -> bool:
    execute, arguments, cwd = _launch_parts()
    if is_frozen():
        ensure_install_copy()
        execute = str(installed_exe_path() if installed_exe_path().is_file() else app_executable())
        cwd = str(Path(execute).parent)
        arguments = "--background"

    # Escape for PowerShell single-quoted strings
    def q(value: str) -> str:
        return value.replace("'", "''")

    script = f"""
$ErrorActionPreference = 'Stop'
$taskName = '{q(TASK_NAME)}'
$action = New-ScheduledTaskAction -Execute '{q(execute)}' -Argument '{q(arguments)}' -WorkingDirectory '{q(cwd)}'
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)
$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited
Unregister-ScheduledTask -TaskName $taskName -Confirm:$false -ErrorAction SilentlyContinue
Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Settings $settings -Principal $principal -Force | Out-Null
"""
    result = _run_ps(script)
    if result.returncode != 0:
        log.warning("register_autostart failed: %s", (result.stderr or result.stdout).strip())
        return False
    log.info("Autostart registered (%s)", TASK_NAME)
    return True


def unregister_autostart() -> None:
    script = f"""
Unregister-ScheduledTask -TaskName '{TASK_NAME}' -Confirm:$false -ErrorAction SilentlyContinue
Stop-ScheduledTask -TaskName '{TASK_NAME}' -ErrorAction SilentlyContinue
"""
    _run_ps(script)
    log.info("Autostart removed")


def register_uninstall_entry() -> None:
    exe = installed_exe_path() if is_frozen() and installed_exe_path().is_file() else app_executable()
    if is_frozen():
        uninstall = f'"{exe}" --uninstall'
    else:
        uninstall = (
            f'powershell.exe -NoProfile -ExecutionPolicy Bypass -Command '
            f'"& \'{sys.executable}\' -m lockon_bridge --uninstall"'
        )
    script = f"""
$regPath = 'HKCU:\\Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\{PRODUCT_ID}'
New-Item -Path $regPath -Force | Out-Null
Set-ItemProperty -Path $regPath -Name 'DisplayName' -Value '{PRODUCT_NAME}'
Set-ItemProperty -Path $regPath -Name 'Publisher' -Value 'LockOn'
Set-ItemProperty -Path $regPath -Name 'DisplayVersion' -Value '0.2.0'
Set-ItemProperty -Path $regPath -Name 'InstallLocation' -Value '{str(data_root()).replace("'", "''")}'
Set-ItemProperty -Path $regPath -Name 'NoModify' -Value 1 -Type DWord
Set-ItemProperty -Path $regPath -Name 'NoRepair' -Value 1 -Type DWord
Set-ItemProperty -Path $regPath -Name 'UninstallString' -Value '{uninstall.replace("'", "''")}'
Set-ItemProperty -Path $regPath -Name 'QuietUninstallString' -Value '{uninstall.replace("'", "''")}'
"""
    if is_frozen() and exe.is_file():
        script += f"\nSet-ItemProperty -Path $regPath -Name 'DisplayIcon' -Value '{str(exe).replace(chr(39), chr(39)+chr(39))}'"
    _run_ps(script)


def remove_uninstall_entry() -> None:
    _run_ps(
        f"Remove-Item -Path 'HKCU:\\Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\{PRODUCT_ID}' "
        f"-Recurse -Force -ErrorAction SilentlyContinue"
    )


def ensure_firewall_rule(port: int) -> None:
    script = f"""
$ruleName = 'LockOn Bridge {port}'
Get-NetFirewallRule -DisplayName $ruleName -ErrorAction SilentlyContinue | Remove-NetFirewallRule -ErrorAction SilentlyContinue
New-NetFirewallRule -DisplayName $ruleName -Direction Inbound -Protocol TCP -LocalPort {int(port)} -Action Allow -Profile Private -ErrorAction SilentlyContinue | Out-Null
"""
    _run_ps(script)


def remove_firewall_rule(port: int = 8112) -> None:
    _run_ps(
        f"Get-NetFirewallRule -DisplayName 'LockOn Bridge {port}' -ErrorAction SilentlyContinue | "
        f"Remove-NetFirewallRule -ErrorAction SilentlyContinue"
    )


def stop_other_bridge_processes() -> None:
    """Stop other LockOn Bridge agents (not the current PID)."""
    me = os.getpid()
    script = f"""
Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
  Where-Object {{
    $_.ProcessId -ne {me} -and (
      $_.Name -match 'LockOnBridge' -or
      ($_.CommandLine -and $_.CommandLine -match 'lockon_bridge')
    )
  }} |
  ForEach-Object {{ Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }}
"""
    _run_ps(script)


def full_uninstall() -> None:
    unregister_autostart()
    stop_other_bridge_processes()
    remove_firewall_rule(8112)
    remove_uninstall_entry()
    # Wipe data dir after this process exits (may include our exe).
    root = str(data_root()).replace("'", "''")
    remove_cmd = (
        f"Start-Sleep -Seconds 2; "
        f"Remove-Item -LiteralPath '{root}' -Recurse -Force -ErrorAction SilentlyContinue"
    )
    subprocess.Popen(
        ["powershell.exe", "-NoProfile", "-WindowStyle", "Hidden", "-Command", remove_cmd],
        close_fds=True,
    )
