from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
import winreg
from pathlib import Path

from . import __version__
from .paths import (
    PRODUCT_ID,
    PRODUCT_NAME,
    TASK_NAME,
    app_executable,
    app_install_dir,
    data_root,
    desktop_shortcut_path,
    installed_exe_path,
    installed_uninstall_path,
    is_frozen,
    log_dir,
)

log = logging.getLogger("lockon_bridge")

# Prevent console flashes when spawning helpers on Windows.
_CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)


def _run_ps(script: str) -> subprocess.CompletedProcess[str]:
    """Run PowerShell with no visible window."""
    startupinfo = None
    if sys.platform == "win32":
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startupinfo.wShowWindow = 0  # SW_HIDE
    return subprocess.run(
        [
            "powershell.exe",
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-WindowStyle",
            "Hidden",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            script,
        ],
        capture_output=True,
        text=True,
        check=False,
        startupinfo=startupinfo,
        creationflags=_CREATE_NO_WINDOW if sys.platform == "win32" else 0,
    )


def _run_hidden(args: list[str]) -> subprocess.CompletedProcess[str]:
    startupinfo = None
    if sys.platform == "win32":
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startupinfo.wShowWindow = 0
    return subprocess.run(
        args,
        capture_output=True,
        text=True,
        check=False,
        startupinfo=startupinfo,
        creationflags=_CREATE_NO_WINDOW if sys.platform == "win32" else 0,
    )


def _q(value: str) -> str:
    return value.replace("'", "''")


def _launch_parts() -> tuple[str, str, str]:
    """Return (execute, arguments, working_directory) for Scheduled Task."""
    if is_frozen():
        exe = str(installed_exe_path() if installed_exe_path().is_file() else app_executable())
        return exe, "--background", str(Path(exe).parent)
    py = Path(sys.executable)
    pythonw = py.with_name("pythonw.exe")
    execute = str(pythonw if pythonw.is_file() else py)
    package_root = Path(__file__).resolve().parent.parent
    return execute, "-m lockon_bridge --background", str(package_root)


def ensure_install_copy() -> Path | None:
    """
    Copy the onedir bundle (exe + _internal) into LocalAppData\\LockOnBridge\\app
    and create a Desktop shortcut to the installed exe.
    """
    if not is_frozen():
        return None
    src_exe = app_executable()
    src_dir = src_exe.parent
    dst_dir = app_install_dir()
    dst_exe = installed_exe_path()
    data_root().mkdir(parents=True, exist_ok=True)
    log_dir().mkdir(parents=True, exist_ok=True)
    try:
        if src_exe.resolve() != dst_exe.resolve():
            if dst_dir.exists():
                for item in src_dir.iterdir():
                    target = dst_dir / item.name
                    if item.is_dir():
                        if target.exists():
                            shutil.rmtree(target, ignore_errors=True)
                        shutil.copytree(item, target, dirs_exist_ok=True)
                    else:
                        shutil.copy2(item, target)
            else:
                shutil.copytree(src_dir, dst_dir)
            log.info("Installed onedir copy → %s", dst_dir)
        ensure_desktop_shortcut(dst_exe if dst_exe.is_file() else src_exe)
        return dst_exe if dst_exe.is_file() else src_exe
    except OSError as exc:
        log.warning("Could not copy install folder: %s", exc)
        ensure_desktop_shortcut(src_exe)
        return src_exe


def ensure_desktop_shortcut(target: Path | None = None) -> bool:
    """Create or refresh a Desktop shortcut that opens the Bridge control window."""
    exe = target
    if exe is None:
        if is_frozen() and installed_exe_path().is_file():
            exe = installed_exe_path()
        else:
            exe = app_executable()
    if not exe.is_file():
        return False

    shortcut = desktop_shortcut_path()
    script = f"""
$ErrorActionPreference = 'Stop'
$shell = New-Object -ComObject WScript.Shell
$lnkPath = '{_q(str(shortcut))}'
$shortcut = $shell.CreateShortcut($lnkPath)
$shortcut.TargetPath = '{_q(str(exe))}'
$shortcut.WorkingDirectory = '{_q(str(exe.parent))}'
$shortcut.WindowStyle = 1
$shortcut.Description = '{_q(PRODUCT_NAME)}'
$shortcut.IconLocation = '{_q(str(exe))},0'
$shortcut.Save()
"""
    result = _run_ps(script)
    if result.returncode != 0:
        log.warning("desktop shortcut failed: %s", (result.stderr or result.stdout).strip())
        return False
    log.info("Desktop shortcut → %s", shortcut)
    return True


def remove_desktop_shortcut() -> None:
    path = desktop_shortcut_path()
    try:
        if path.is_file():
            path.unlink()
            log.info("Desktop shortcut removed")
    except OSError as exc:
        log.warning("Could not remove desktop shortcut: %s", exc)


def register_autostart() -> bool:
    execute, arguments, cwd = _launch_parts()
    if is_frozen():
        ensure_install_copy()
        execute = str(installed_exe_path() if installed_exe_path().is_file() else app_executable())
        cwd = str(Path(execute).parent)
        arguments = "--background"

    # Prefer schtasks (no PowerShell window) with a silent fallback to PS.
    # /RL LIMITED = standard user; /F = overwrite.
    tr = f'"{execute}" {arguments}'.strip()
    result = _run_hidden(
        [
            "schtasks.exe",
            "/Create",
            "/TN",
            TASK_NAME,
            "/TR",
            tr,
            "/SC",
            "ONLOGON",
            "/RL",
            "LIMITED",
            "/F",
        ]
    )
    if result.returncode == 0:
        log.info("Autostart registered via schtasks (%s)", TASK_NAME)
        return True

    script = f"""
$ErrorActionPreference = 'Stop'
$taskName = '{_q(TASK_NAME)}'
$action = New-ScheduledTaskAction -Execute '{_q(execute)}' -Argument '{_q(arguments)}' -WorkingDirectory '{_q(cwd)}'
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)
$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited
Unregister-ScheduledTask -TaskName $taskName -Confirm:$false -ErrorAction SilentlyContinue
Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Settings $settings -Principal $principal -Force | Out-Null
"""
    result_ps = _run_ps(script)
    if result_ps.returncode != 0:
        log.warning(
            "register_autostart failed (schtasks: %s; ps: %s)",
            (result.stderr or result.stdout).strip(),
            (result_ps.stderr or result_ps.stdout).strip(),
        )
        return False
    log.info("Autostart registered (%s)", TASK_NAME)
    return True


def unregister_autostart() -> None:
    _run_hidden(["schtasks.exe", "/Delete", "/TN", TASK_NAME, "/F"])
    _run_ps(
        f"Unregister-ScheduledTask -TaskName '{_q(TASK_NAME)}' -Confirm:$false "
        f"-ErrorAction SilentlyContinue; "
        f"Stop-ScheduledTask -TaskName '{_q(TASK_NAME)}' -ErrorAction SilentlyContinue"
    )
    log.info("Autostart removed")


def _uninstall_command() -> tuple[str, str]:
    """Return (UninstallString, QuietUninstallString) for Apps & Features."""
    if is_frozen():
        uninstaller = installed_uninstall_path()
        if not uninstaller.is_file():
            # ZIP / first-run folder before LocalAppData copy exists
            sibling = app_executable().parent / "uninstall.exe"
            if sibling.is_file():
                uninstaller = sibling
        if uninstaller.is_file():
            cmd = f'"{uninstaller}"'
            return cmd, f"{cmd} --quiet"
        exe = installed_exe_path() if installed_exe_path().is_file() else app_executable()
        cmd = f'"{exe}" --uninstall'
        return cmd, cmd
    cmd = f'"{sys.executable}" -m lockon_bridge --uninstall'
    return cmd, cmd


def register_uninstall_entry() -> None:
    exe = installed_exe_path() if is_frozen() and installed_exe_path().is_file() else app_executable()
    uninstall, quiet = _uninstall_command()
    try:
        key = winreg.CreateKey(
            winreg.HKEY_CURRENT_USER,
            rf"Software\Microsoft\Windows\CurrentVersion\Uninstall\{PRODUCT_ID}",
        )
        with key:
            winreg.SetValueEx(key, "DisplayName", 0, winreg.REG_SZ, PRODUCT_NAME)
            winreg.SetValueEx(key, "Publisher", 0, winreg.REG_SZ, "LockOn")
            winreg.SetValueEx(key, "DisplayVersion", 0, winreg.REG_SZ, __version__)
            winreg.SetValueEx(key, "InstallLocation", 0, winreg.REG_SZ, str(data_root()))
            winreg.SetValueEx(key, "NoModify", 0, winreg.REG_DWORD, 1)
            winreg.SetValueEx(key, "NoRepair", 0, winreg.REG_DWORD, 1)
            winreg.SetValueEx(key, "UninstallString", 0, winreg.REG_SZ, uninstall)
            winreg.SetValueEx(key, "QuietUninstallString", 0, winreg.REG_SZ, quiet)
            if is_frozen() and exe.is_file():
                winreg.SetValueEx(key, "DisplayIcon", 0, winreg.REG_SZ, str(exe))
        log.info("Uninstall registry entry registered → %s", uninstall)
    except OSError as exc:
        log.warning("register_uninstall_entry failed: %s", exc)


def remove_uninstall_entry() -> None:
    try:
        winreg.DeleteKey(
            winreg.HKEY_CURRENT_USER,
            rf"Software\Microsoft\Windows\CurrentVersion\Uninstall\{PRODUCT_ID}",
        )
    except FileNotFoundError:
        pass
    except OSError as exc:
        log.warning("remove_uninstall_entry failed: %s", exc)


# Defender / SmartScreen often auto-adds Block rules for unsigned LockOnBridge.exe.
# Those Block-all-TCP rules beat a port Allow and make the phone time out on :8112
# while War Thunder :8111 still works.
_BLOCK_RULE_NAMES = (
    "lockonbridge.exe",
    "LockOn Bridge - OCR companion for War Thunder",
    "lockonbridge (1)",
    "lockonbridge (2)",
)
_APP_ALLOW_NAME = "LockOn Bridge App Allow"


def _bridge_exe_for_firewall() -> Path | None:
    for candidate in (installed_exe_path(), app_executable()):
        try:
            if candidate.is_file():
                return candidate.resolve()
        except OSError:
            continue
    return None


def _purge_bridge_block_rules() -> None:
    for name in _BLOCK_RULE_NAMES:
        _run_hidden(
            [
                "netsh",
                "advfirewall",
                "firewall",
                "delete",
                "rule",
                f"name={name}",
            ]
        )


def _firewall_has_block_on_bridge() -> bool:
    for name in _BLOCK_RULE_NAMES:
        result = _run_hidden(
            [
                "netsh",
                "advfirewall",
                "firewall",
                "show",
                "rule",
                f"name={name}",
            ]
        )
        out = ((result.stdout or "") + (result.stderr or "")).lower()
        if result.returncode == 0 and "no rules match" not in out and "block" in out:
            return True
    return False


def _app_allow_present() -> bool:
    result = _run_hidden(
        [
            "netsh",
            "advfirewall",
            "firewall",
            "show",
            "rule",
            f"name={_APP_ALLOW_NAME}",
        ]
    )
    out = (result.stdout or "") + (result.stderr or "")
    return (
        result.returncode == 0
        and "No rules match" not in out
        and _APP_ALLOW_NAME.lower() in out.lower()
        and "Allow" in out
    )


def ensure_firewall_rule(port: int, *, allow_elevate: bool = True) -> bool:
    """
    Ensure the phone can reach Bridge on TCP ``port``.

    Port Allow alone is not enough — Windows Defender often adds a Block rule on
    LockOnBridge.exe that still drops inbound connections. We purge those Blocks
    and add both a port Allow and a program Allow.
    """
    name = f"LockOn Bridge {int(port)}"
    if (
        firewall_rule_present(port)
        and _app_allow_present()
        and not _firewall_has_block_on_bridge()
    ):
        return True

    exe = _bridge_exe_for_firewall()
    exe_arg = str(exe) if exe is not None else ""

    def _apply_local() -> bool:
        _purge_bridge_block_rules()
        _run_hidden(
            [
                "netsh",
                "advfirewall",
                "firewall",
                "delete",
                "rule",
                f"name={name}",
            ]
        )
        _run_hidden(
            [
                "netsh",
                "advfirewall",
                "firewall",
                "delete",
                "rule",
                f"name={_APP_ALLOW_NAME}",
            ]
        )
        port_rc = _run_hidden(
            [
                "netsh",
                "advfirewall",
                "firewall",
                "add",
                "rule",
                f"name={name}",
                "dir=in",
                "action=allow",
                "protocol=TCP",
                f"localport={int(port)}",
                "profile=any",
            ]
        ).returncode
        app_rc = 0
        if exe_arg:
            app_rc = _run_hidden(
                [
                    "netsh",
                    "advfirewall",
                    "firewall",
                    "add",
                    "rule",
                    f"name={_APP_ALLOW_NAME}",
                    "dir=in",
                    "action=allow",
                    f"program={exe_arg}",
                    "enable=yes",
                    "profile=any",
                    "protocol=TCP",
                ]
            ).returncode
        return (
            port_rc == 0
            and firewall_rule_present(port)
            and (not exe_arg or _app_allow_present())
            and not _firewall_has_block_on_bridge()
        )

    if _apply_local():
        log.info("Firewall allow TCP %s + app (%s)", port, name)
        return True

    if not allow_elevate:
        return (
            firewall_rule_present(port)
            and _app_allow_present()
            and not _firewall_has_block_on_bridge()
        )

    # One UAC prompt: purge Blocks, add port + program Allow, soft Defender exclusion.
    elevate_script = f"""
$ErrorActionPreference = 'Continue'
$names = @({", ".join(repr(n) for n in _BLOCK_RULE_NAMES)})
foreach ($n in $names) {{
  netsh advfirewall firewall delete rule name="$n" | Out-Null
}}
netsh advfirewall firewall delete rule name='{name}' | Out-Null
netsh advfirewall firewall delete rule name='{_APP_ALLOW_NAME}' | Out-Null
netsh advfirewall firewall add rule name='{name}' dir=in action=allow protocol=TCP localport={int(port)} profile=any | Out-Null
"""
    if exe_arg:
        elevate_script += f"""
netsh advfirewall firewall add rule name='{_APP_ALLOW_NAME}' dir=in action=allow program='{_q(exe_arg)}' enable=yes profile=any protocol=TCP | Out-Null
try {{
  Add-MpPreference -ExclusionPath '{_q(str(data_root()))}' -ErrorAction SilentlyContinue
  Add-MpPreference -ExclusionProcess 'LockOnBridge.exe' -ErrorAction SilentlyContinue
}} catch {{}}
"""
    elevate_script += "exit 0\n"

    elevated = _run_ps(
        f"""
$tmp = Join-Path $env:TEMP ('lockon_fw_' + [guid]::NewGuid().ToString() + '.ps1')
@'
{elevate_script}
'@ | Set-Content -Path $tmp -Encoding UTF8
$p = Start-Process -FilePath powershell.exe -ArgumentList @(
  '-NoLogo','-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass','-File',$tmp
) -Verb RunAs -Wait -PassThru -WindowStyle Hidden
Remove-Item -Force $tmp -ErrorAction SilentlyContinue
if ($null -eq $p) {{ exit 1 }}
exit $p.ExitCode
"""
    )
    ok = (
        elevated.returncode == 0
        and firewall_rule_present(port)
        and (not exe_arg or _app_allow_present())
        and not _firewall_has_block_on_bridge()
    )
    if ok:
        log.info("Firewall allow TCP %s + app via elevation", port)
    else:
        log.warning(
            "Elevated firewall fix failed (rc=%s) — phone may not reach Bridge :%s",
            elevated.returncode,
            port,
        )
    return ok


def firewall_rule_present(port: int = 8112) -> bool:
    name = f"LockOn Bridge {int(port)}"
    result = _run_hidden(
        [
            "netsh",
            "advfirewall",
            "firewall",
            "show",
            "rule",
            f"name={name}",
        ]
    )
    out = (result.stdout or "") + (result.stderr or "")
    return result.returncode == 0 and "No rules match" not in out and name.lower() in out.lower()


def remove_firewall_rule(port: int = 8112) -> None:
    name = f"LockOn Bridge {int(port)}"
    _run_hidden(
        [
            "netsh",
            "advfirewall",
            "firewall",
            "delete",
            "rule",
            f"name={name}",
        ]
    )
    _run_hidden(
        [
            "netsh",
            "advfirewall",
            "firewall",
            "delete",
            "rule",
            f"name={_APP_ALLOW_NAME}",
        ]
    )
    _purge_bridge_block_rules()


def stop_other_bridge_processes() -> None:
    """Stop other LockOn Bridge agents (not the current PID)."""
    me = os.getpid()
    try:
        import psutil
    except ImportError:
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
        return

    for proc in psutil.process_iter(["pid", "name", "cmdline"]):
        try:
            pid = int(proc.info["pid"] or 0)
            if pid == me or pid <= 0:
                continue
            name = (proc.info.get("name") or "").lower()
            cmdline = " ".join(proc.info.get("cmdline") or []).lower()
            if "lockonbridge" in name or "lockon_bridge" in cmdline:
                proc.kill()
        except (psutil.Error, OSError, TypeError, ValueError):
            continue


def prepare_enabled_runtime(*, port: int, allow_firewall_elevate: bool = False) -> None:
    """
    One-shot setup when Bridge is turned ON / starts enabled.
    Firewall elevation is opt-in from the UI (clear prompt before UAC).
    """
    stop_other_bridge_processes()
    ensure_install_copy()
    register_autostart()
    register_uninstall_entry()
    ensure_firewall_rule(port, allow_elevate=allow_firewall_elevate)


def full_uninstall() -> None:
    unregister_autostart()
    stop_other_bridge_processes()
    remove_firewall_rule(8112)
    remove_uninstall_entry()
    remove_desktop_shortcut()
    root = str(data_root()).replace("'", "''")
    remove_cmd = (
        f"Start-Sleep -Seconds 2; "
        f"Remove-Item -LiteralPath '{root}' -Recurse -Force -ErrorAction SilentlyContinue"
    )
    startupinfo = None
    if sys.platform == "win32":
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startupinfo.wShowWindow = 0
    subprocess.Popen(
        [
            "powershell.exe",
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-WindowStyle",
            "Hidden",
            "-Command",
            remove_cmd,
        ],
        close_fds=True,
        startupinfo=startupinfo,
        creationflags=_CREATE_NO_WINDOW if sys.platform == "win32" else 0,
    )
