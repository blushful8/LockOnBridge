"""Install official Windows OCR language packs (Microsoft Features on Demand).

Uses Add-WindowsCapability via Windows Update — no third-party downloads.
Ukrainian (uk-UA) is NOT offered by Microsoft OCR; for UA War Thunder UI we
recommend Russian OCR (Cyrillic) + English.
"""

from __future__ import annotations

import logging
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

from .capture import list_installed_ocr_languages

log = logging.getLogger("lockon_bridge")

# Bridge / WT UI language → preferred Microsoft OCR capability tags (best first).
OCR_RECOMMENDATIONS: dict[str, tuple[str, ...]] = {
    "en": ("en-US",),
    "uk": ("ru", "en-US"),  # no uk-UA OCR — Cyrillic via Russian
    "ru": ("ru", "en-US"),
    "de": ("de-DE", "en-US"),
    "fr": ("fr-FR", "en-US"),
    "es": ("es-ES", "en-US"),
    "it": ("it-IT", "en-US"),
    "pl": ("pl-PL", "en-US"),
    "pt": ("pt-BR", "en-US"),
    "cs": ("cs-CZ", "en-US"),
    "tr": ("tr-TR", "en-US"),
    "ja": ("ja-JP", "en-US"),
    "ko": ("ko-KR", "en-US"),
    "zh": ("zh-CN", "zh-TW", "en-US"),
    "hu": ("hu-HU", "en-US"),
    "ro": ("ro-RO", "en-US"),
    "sr": ("sr-Cyrl-RS", "en-US"),
}

# Human labels for the setup dialog (EN keys; UI layer localizes).
OCR_TAG_LABELS: dict[str, str] = {
    "en-US": "English (United States)",
    "ru": "Russian",
    "de-DE": "German",
    "fr-FR": "French",
    "es-ES": "Spanish",
    "it-IT": "Italian",
    "pl-PL": "Polish",
    "pt-BR": "Portuguese (Brazil)",
    "cs-CZ": "Czech",
    "tr-TR": "Turkish",
    "ja-JP": "Japanese",
    "ko-KR": "Korean",
    "zh-CN": "Chinese (Simplified)",
    "zh-TW": "Chinese (Traditional)",
    "hu-HU": "Hungarian",
    "ro-RO": "Romanian",
    "sr-Cyrl-RS": "Serbian (Cyrillic)",
}


@dataclass(frozen=True)
class OcrPackAdvice:
    ui_language: str
    recommended_tags: tuple[str, ...]
    installed_tags: tuple[str, ...]
    missing_tags: tuple[str, ...]
    note: str


def _normalize_installed() -> set[str]:
    tags: set[str] = set()
    for tag, _name in list_installed_ocr_languages():
        lower = tag.lower()
        tags.add(lower)
        if "-" in lower:
            tags.add(lower.split("-", 1)[0])
    return tags


def _tag_satisfied(tag: str, installed: set[str]) -> bool:
    lower = tag.lower()
    if lower in installed:
        return True
    base = lower.split("-", 1)[0]
    if base in installed:
        return True
    if base == "zh" and any(t.startswith("zh") for t in installed):
        return True
    return False


def advise_ocr_packs(ui_language: str) -> OcrPackAdvice:
    lang = (ui_language or "en").strip().lower()
    if lang.startswith("uk"):
        lang = "uk"
    elif lang.startswith("zh"):
        lang = "zh"
    preferred = OCR_RECOMMENDATIONS.get(lang, OCR_RECOMMENDATIONS["en"])
    installed = _normalize_installed()
    missing = tuple(tag for tag in preferred if not _tag_satisfied(tag, installed))
    note = ""
    if lang == "uk":
        note = (
            "Microsoft does not ship a Ukrainian Windows OCR pack. "
            "Russian OCR is the official Cyrillic option for Ukrainian War Thunder UI."
        )
    return OcrPackAdvice(
        ui_language=lang,
        recommended_tags=preferred,
        installed_tags=tuple(sorted(installed)),
        missing_tags=missing,
        note=note,
    )


def install_ocr_packs(tags: list[str]) -> tuple[bool, str]:
    """
    Install OCR packs from Microsoft Windows Update (Features on Demand).
    Shows a UAC elevation prompt. Returns (ok, message).
    """
    if not tags:
        return True, "Nothing to install."

    unique: list[str] = []
    seen: set[str] = set()
    for tag in tags:
        if tag in seen:
            continue
        seen.add(tag)
        unique.append(tag)

    status_path = Path(tempfile.gettempdir()) / f"lockon_ocr_install_{int(time.time())}.txt"
    script_path = Path(tempfile.gettempdir()) / f"lockon_ocr_install_{int(time.time())}.ps1"

    # Build capability names; try common FoD patterns per tag.
    cap_lines = []
    for tag in unique:
        cap_lines.append(f"Language.OCR~~~{tag}~0.0.1.0")

    script = f"""$ErrorActionPreference = 'Continue'
$statusFile = '{status_path.as_posix()}'
$names = @({", ".join(repr(n) for n in cap_lines)})
$lines = New-Object System.Collections.Generic.List[string]
$failed = $false
foreach ($n in $names) {{
  try {{
    $cap = Get-WindowsCapability -Online | Where-Object {{ $_.Name -eq $n }} | Select-Object -First 1
    if (-not $cap) {{
      $cap = Get-WindowsCapability -Online | Where-Object {{ $_.Name -like ('Language.OCR*' + ($n -replace 'Language.OCR~~~','' -replace '~0.0.1.0','') + '*') }} | Select-Object -First 1
    }}
    if (-not $cap) {{
      $lines.Add(($n + ' => NOT_IN_CATALOG'))
      $failed = $true
      continue
    }}
    if ($cap.State -eq 'Installed') {{
      $lines.Add(($cap.Name + ' => already Installed'))
      continue
    }}
    $r = Add-WindowsCapability -Online -Name $cap.Name -ErrorAction Stop
    $lines.Add(($cap.Name + ' => ' + $r.State))
  }} catch {{
    $failed = $true
    $lines.Add(($n + ' => FAIL ' + $_.Exception.Message))
  }}
}}
$lines.Add(('FAILED=' + $failed.ToString().ToLower()))
[System.IO.File]::WriteAllText($statusFile, ($lines -join [Environment]::NewLine), [System.Text.UTF8Encoding]::new($false))
if ($failed) {{ exit 1 }} else {{ exit 0 }}
"""
    try:
        script_path.write_text(script, encoding="utf-8")
    except OSError as exc:
        return False, f"Could not write installer script: {exc}"

    if status_path.is_file():
        try:
            status_path.unlink()
        except OSError:
            pass

    elevate = (
        f"Start-Process -FilePath powershell.exe -Verb RunAs -Wait -WindowStyle Hidden "
        f"-ArgumentList '-NoLogo -NoProfile -NonInteractive -WindowStyle Hidden "
        f"-ExecutionPolicy Bypass -File \"{script_path}\"'"
    )
    try:
        startupinfo = None
        if sys.platform == "win32":
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            startupinfo.wShowWindow = 0
        completed = subprocess.run(
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
                elevate,
            ],
            capture_output=True,
            text=True,
            timeout=900,
            check=False,
            startupinfo=startupinfo,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000),
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, f"Could not start elevated installer: {exc}"
    finally:
        try:
            script_path.unlink(missing_ok=True)  # type: ignore[call-arg]
        except TypeError:
            if script_path.is_file():
                script_path.unlink()
        except OSError:
            pass

    detail = ""
    if status_path.is_file():
        try:
            detail = status_path.read_text(encoding="utf-8", errors="replace").strip()
            status_path.unlink(missing_ok=True)  # type: ignore[call-arg]
        except OSError:
            pass

    if not detail and completed.returncode != 0:
        return False, (
            "Install cancelled or failed (UAC). "
            "You can add OCR packs manually: Settings → Time & language → Language."
        )

    if "FAILED=true" in detail.lower() or "not_in_catalog" in detail.lower():
        return False, detail or "One or more OCR packs could not be installed."

    # Verify via WinRT list
    after = advise_ocr_packs("en")
    still = [t for t in unique if not _tag_satisfied(t, set(after.installed_tags))]
    if still:
        return False, (
            (detail + "\n" if detail else "")
            + f"Still missing after install: {', '.join(still)}. "
            "Restart Bridge and check Windows Update."
        )
    return True, detail or "OCR packs installed from Microsoft."
