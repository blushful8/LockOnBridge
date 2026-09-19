"""Multi-backend OCR: Windows.Media.Ocr + optional Tesseract (all WT languages)."""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import urllib.request
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageEnhance, ImageOps

from .paths import data_root
from .wt_languages import (
    ALL_TESS_LANGS,
    TESSDATA_FAST_BASE,
    get_wt_language,
)

log = logging.getLogger("lockon_bridge")


def tessdata_dir() -> Path:
    path = data_root() / "tessdata"
    path.mkdir(parents=True, exist_ok=True)
    return path


def find_tesseract_exe() -> Path | None:
    env = os.environ.get("TESSERACT_CMD") or os.environ.get("TESSDATA_PREFIX")
    candidates: list[Path] = []
    which = shutil.which("tesseract")
    if which:
        candidates.append(Path(which))
    candidates.extend(
        [
            Path(r"C:\Program Files\Tesseract-OCR\tesseract.exe"),
            Path(r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe"),
            data_root() / "tesseract" / "tesseract.exe",
        ]
    )
    for path in candidates:
        if path.is_file():
            return path
    # TESSDATA_PREFIX sometimes points at tessdata folder
    if env:
        p = Path(env)
        if p.name.lower() == "tessdata":
            exe = p.parent / "tesseract.exe"
            if exe.is_file():
                return exe
    return None


def tesseract_available() -> bool:
    return find_tesseract_exe() is not None


def list_local_tessdata() -> list[str]:
    root = tessdata_dir()
    return sorted(p.stem for p in root.glob("*.traineddata"))


def missing_tessdata_for_wt(wt_ui_language: str) -> list[str]:
    lang = get_wt_language(wt_ui_language)
    have = set(list_local_tessdata())
    # Also accept system tessdata next to tesseract.exe
    exe = find_tesseract_exe()
    if exe is not None:
        sys_dir = exe.parent / "tessdata"
        if sys_dir.is_dir():
            have.update(p.stem for p in sys_dir.glob("*.traineddata"))
    return [code for code in lang.tesseract_langs if code not in have]


def download_tessdata(langs: list[str], *, timeout: int = 120) -> tuple[bool, str]:
    """
    Download official tessdata_fast models into LocalAppData\\LockOnBridge\\tessdata.
    Apache-2.0 from github.com/tesseract-ocr/tessdata_fast.
    """
    root = tessdata_dir()
    lines: list[str] = []
    ok = True
    for code in langs:
        dest = root / f"{code}.traineddata"
        if dest.is_file() and dest.stat().st_size > 10_000:
            lines.append(f"{code}: already present")
            continue
        url = TESSDATA_FAST_BASE.format(lang=code)
        try:
            log.info("Downloading tessdata %s", url)
            urllib.request.urlretrieve(url, dest)  # noqa: S310 — fixed official GitHub URL
            if not dest.is_file() or dest.stat().st_size < 1000:
                ok = False
                lines.append(f"{code}: download incomplete")
                if dest.is_file():
                    dest.unlink(missing_ok=True)  # type: ignore[call-arg]
            else:
                lines.append(f"{code}: downloaded ({dest.stat().st_size // 1024} KB)")
        except Exception as exc:  # noqa: BLE001
            ok = False
            lines.append(f"{code}: FAIL {exc}")
            if dest.is_file():
                try:
                    dest.unlink()
                except OSError:
                    pass
    return ok, "\n".join(lines)


def ensure_core_tessdata() -> tuple[bool, str]:
    """Ensure eng+ukr+rus at minimum when Tesseract is used."""
    exe = find_tesseract_exe()
    have_sys: set[str] = set()
    if exe is not None:
        sys_dir = exe.parent / "tessdata"
        if sys_dir.is_dir():
            have_sys = {p.stem for p in sys_dir.glob("*.traineddata")}
    need = [
        c
        for c in ("eng", "ukr", "rus")
        if c not in set(list_local_tessdata()) and c not in have_sys
    ]
    if not need:
        return True, "core tessdata present"
    return download_tessdata(need)


def install_tesseract_via_winget(*, timeout: int = 600) -> tuple[bool, str]:
    """
    One-click install of UB-Mannheim Tesseract via winget (official package).
    Returns (ok, detail). Requires network + may show winget UI briefly.
    """
    import subprocess

    winget = shutil.which("winget")
    if winget is None:
        return (
            False,
            "winget not found. Install Tesseract manually:\n"
            "https://github.com/UB-Mannheim/tesseract/wiki",
        )
    try:
        proc = subprocess.run(
            [
                winget,
                "install",
                "--id",
                "UB-Mannheim.TesseractOCR",
                "-e",
                "--accept-package-agreements",
                "--accept-source-agreements",
            ],
            capture_output=True,
            text=True,
            timeout=timeout,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except subprocess.TimeoutExpired:
        return False, "winget timed out"
    except OSError as exc:
        return False, str(exc)
    out = ((proc.stdout or "") + "\n" + (proc.stderr or "")).strip()
    # winget exit 0 = installed; -1978335189 / other codes may mean already installed
    if tesseract_available() or proc.returncode == 0 or "already installed" in out.lower():
        ok_data, data_detail = ensure_core_tessdata()
        # Also pull models for current WT language if settings available
        try:
            from .settings import load_settings

            wt = load_settings().wt_ui_language or "uk"
            missing = missing_tessdata_for_wt(wt)
            if missing:
                ok2, d2 = download_tessdata(missing)
                ok_data = ok_data and ok2
                data_detail = f"{data_detail}\n{d2}"
        except Exception:  # noqa: BLE001
            pass
        if tesseract_available():
            return True, f"Tesseract ready.\n{data_detail}\n{out[-400:]}"
    return False, out[-800:] or f"winget exit {proc.returncode}"


def tesseract_recommended_for(wt_ui_language: str) -> bool:
    """True when Windows OCR alone is a weak fit (e.g. Ukrainian)."""
    lang = get_wt_language(wt_ui_language)
    if lang.code == "uk":
        return True
    if lang.code == "be":
        return True
    return False


# --- Windows OCR (existing engine wrappers live in capture; re-export helpers) ---


def windows_ocr_variants(png: bytes) -> list[tuple[str, str]]:
    from .capture import ocr_png_variants_windows

    return ocr_png_variants_windows(png)


def tesseract_ocr_variants(png: bytes, wt_ui_language: str) -> list[tuple[str, str]]:
    """Run Tesseract for the WT UI language (+ eng). Returns [(engine_id, text)]."""
    exe = find_tesseract_exe()
    if exe is None:
        return []
    try:
        import pytesseract
    except ImportError:
        log.info("pytesseract not installed — skip Tesseract backend")
        return []

    pytesseract.pytesseract.tesseract_cmd = str(exe)
    lang = get_wt_language(wt_ui_language)
    # Prefer LocalAppData tessdata when we downloaded packs there. Ensure eng is
    # present too — exclusive --tessdata-dir cannot see system eng otherwise.
    local = tessdata_dir()
    ensure_core_tessdata()
    use_local = any(local.glob("*.traineddata"))
    # pytesseract passes config tokens split on spaces; do not quote the path.
    config = f"--tessdata-dir {local}" if use_local else ""

    image = Image.open(BytesIO(png))
    # Mild preprocess similar to Windows path.
    image = ImageOps.autocontrast(image, cutoff=1)
    image = ImageEnhance.Contrast(image).enhance(1.2)

    variants: list[tuple[str, str]] = []
    # Combined lang string first (best for mixed UI), then singles.
    combos: list[str] = []
    joined = "+".join(lang.tesseract_langs)
    combos.append(joined)
    for code in lang.tesseract_langs:
        if code not in combos:
            combos.append(code)

    for combo in combos:
        try:
            text = pytesseract.image_to_string(image, lang=combo, config=config) or ""
        except Exception as exc:  # noqa: BLE001
            log.info("tesseract %s failed: %s", combo, exc)
            continue
        stripped = text.strip()
        if stripped:
            variants.append((f"tesseract:{combo}", stripped))
    if use_local and not variants:
        # Fallback: system tessdata (often eng-only) rather than silent empty.
        log.info("tesseract produced no text with local tessdata — retrying system packs")
        for combo in combos:
            try:
                text = pytesseract.image_to_string(image, lang=combo) or ""
            except Exception as exc:  # noqa: BLE001
                log.info("tesseract system %s failed: %s", combo, exc)
                continue
            stripped = text.strip()
            if stripped:
                variants.append((f"tesseract:{combo}", stripped))
    return variants


def ocr_all_backends(
    png: bytes,
    *,
    wt_ui_language: str = "uk",
    backend: str = "auto",
) -> list[tuple[str, str]]:
    """
    backend: auto | windows | tesseract
    auto = Windows packs + Tesseract (if installed)
    """
    mode = (backend or "auto").strip().lower()
    variants: list[tuple[str, str]] = []
    if mode in ("auto", "windows"):
        try:
            variants.extend(windows_ocr_variants(png))
        except Exception as exc:  # noqa: BLE001
            log.warning("Windows OCR failed: %s", exc)
    if mode in ("auto", "tesseract"):
        try:
            variants.extend(tesseract_ocr_variants(png, wt_ui_language))
        except Exception as exc:  # noqa: BLE001
            log.warning("Tesseract OCR failed: %s", exc)
    return variants


def describe_ocr_status(wt_ui_language: str) -> str:
    lang = get_wt_language(wt_ui_language)
    parts: list[str] = []
    from .capture import list_installed_ocr_languages

    win = list_installed_ocr_languages()
    parts.append("Windows OCR: " + (", ".join(t for t, _ in win) if win else "(none)"))
    tess = find_tesseract_exe()
    if tess is None:
        parts.append("Tesseract: not installed")
    else:
        missing = missing_tessdata_for_wt(wt_ui_language)
        parts.append(f"Tesseract: {tess}")
        if missing:
            parts.append(f"Missing tessdata for {lang.code}: {', '.join(missing)}")
        else:
            parts.append(f"tessdata OK for {lang.code}: {', '.join(lang.tesseract_langs)}")
    if lang.code == "uk":
        parts.append(
            "Note: Windows has no Ukrainian OCR pack; Tesseract ukr (or Russian Win OCR) is needed for real Cyrillic."
        )
    return "\n".join(parts)
