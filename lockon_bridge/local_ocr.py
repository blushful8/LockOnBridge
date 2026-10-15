"""On-demand EasyOCR.

It stays out of the shipped exe. A private, isolated Python runtime and venv
installs it the first time it is selected, or when OCR.space is unreachable
or out of monthly quota.
"""

from __future__ import annotations

import json
import ctypes
import zipfile
import urllib.request
import logging
import os
import shutil
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

log = logging.getLogger("lockon_bridge.local_ocr")


def _hidden_process() -> dict[str, object]:
    """Keep the EasyOCR Python window off the desktop."""
    flags = 0
    startupinfo = None
    if sys.platform == "win32":
        flags = int(getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000))
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startupinfo.wShowWindow = 0
    return {"creationflags": flags, "startupinfo": startupinfo}

LOCAL_ENGINES = ("easyocr",)
_PACKAGES = {
    "easyocr": ("easyocr==1.7.2",),
}
_ready: set[str] = set()
_install_lock = threading.Lock()
_installing: set[str] = set()
_install_errors: dict[str, str] = {}
_install_gate = threading.Lock()
_spawn_lock = threading.Lock()


def _runtime_root() -> Path:
    # Keep the large private Python, wheels and models on D: when available.
    if Path("D:/").is_dir():
        root = Path("D:/CodexData/LockOnBridge/ocr")
    else:
        from .paths import data_root
        root = data_root() / "ocr"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _child_env() -> dict[str, str]:
    env = dict(os.environ)
    for key in list(env):
        if key.upper().startswith(("PYTHON", "_PYI", "_MEIPASS")):
            env.pop(key, None)
    root = _runtime_root()
    for name in ("TEMP", "TMP"):
        env[name] = str(root / "temp")
    (root / "temp").mkdir(exist_ok=True)
    env["PIP_CACHE_DIR"] = str(root / "pip-cache")
    env["EASYOCR_MODULE_PATH"] = str(root / "models")
    env["TORCH_HOME"] = str(root / "torch-cache")
    env["PYTHONIOENCODING"] = "utf-8"
    return env


def _run_python(args: list[str], *, timeout: int = 180, check: bool = False):
    # PyInstaller's DLL directory must not leak into an external interpreter.
    # Restore it immediately after spawn, not after a multi-minute download.
    with _spawn_lock:
        frozen = sys.platform == "win32" and getattr(sys, "frozen", False)
        if frozen:
            ctypes.windll.kernel32.SetDllDirectoryW(None)
        try:
            proc = subprocess.Popen(
                args, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, encoding="utf-8", errors="replace",
                env=_child_env(), **_hidden_process(),
            )
        finally:
            if frozen:
                ctypes.windll.kernel32.SetDllDirectoryW(str(sys._MEIPASS))
    try:
        out, err = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.communicate()
        raise RuntimeError("Local OCR timed out.")
    done = subprocess.CompletedProcess(args, proc.returncode, out, err)
    if check and done.returncode:
        detail = (err or out or f"exit={done.returncode}").strip()[-1600:]
        raise RuntimeError(detail)
    return done


def installation_state(engine: str) -> tuple[str, str]:
    with _install_lock:
        if engine in _installing:
            return "installing", ""
        if engine in _install_errors:
            return "failed", _install_errors[engine]
    return ("ready", "") if engine in _ready else ("idle", "")


# Shown to the caller when preparation is still running. Matched in layout_ocr.
STILL_PREPARING = "EasyOCR is still being prepared."

_READER = r'''
import json, os, sys
os.environ.setdefault("GLOG_minloglevel", "2")
os.environ.setdefault("FLAGS_minloglevel", "2")
sys.stdout.reconfigure(encoding="utf-8")
engine, path = sys.argv[1], sys.argv[2]
langs = json.loads(sys.argv[3]) if len(sys.argv) > 3 else ["en"]
lines = []
if engine == "easyocr":
    import easyocr
    reader = easyocr.Reader(langs, gpu=False, verbose=False)
    if path != "--prepare":
        lines = [str(part) for part in reader.readtext(path, detail=0)]
else:
    raise SystemExit("unknown engine")
sys.stdout.write(json.dumps({"text": "\n".join(lines)}, ensure_ascii=True))
'''


def cloud_error_is_fallback(message: str) -> bool:
    """True when OCR.space is down or has hit a usage wall."""
    text = (message or "").lower()
    needles = (
        "network error",
        "timed out",
        "timeout",
        "quota",
        "limit",
        "maximum",
        "http 403",
        "http 429",
        "http 402",
    )
    return any(needle in text for needle in needles)


def cloud_limited() -> bool:
    """Engine 3 monthly budget or the free daily wall is already spent."""
    try:
        from .ocr_quota import quota_view

        view = quota_view()
    except Exception:  # noqa: BLE001
        return False
    if int(view.get("left3") or 0) <= 0:
        return True
    return view.get("plan") == "free" and int(view.get("day_left") or 0) <= 0


def _venv_python() -> Path:
    return _runtime_root() / "venv" / "Scripts" / "python.exe"


def _reader_path() -> Path:
    return _runtime_root() / "read_zone.py"


def engine_ready(engine: str) -> bool:
    if engine in _ready:
        return True
    py = _venv_python()
    if not py.is_file() or not (_runtime_root() / "ready.json").is_file():
        return False
    try:
        done = _run_python(
            [str(py), "-I", "-c", "import easyocr, torch, numpy; print('ready')"],
            timeout=120,
        )
    except (OSError, RuntimeError):
        return False
    if done.returncode == 0:
        _ready.add(engine)
        return True
    return False


def _base_python() -> str:
    # A private, complete CPython avoids broken system installs and Store aliases.
    target = _runtime_root() / "python-3.12.10"
    py = target / "tools" / "python.exe"
    if py.is_file():
        done = _run_python([str(py), "-I", "-c", "import venv, ensurepip"], timeout=30)
        if done.returncode == 0:
            return str(py)
    log.info("downloading private Python 3.12 runtime")
    url = "https://api.nuget.org/v3-flatcontainer/python/3.12.10/python.3.12.10.nupkg"
    archive = _runtime_root() / "python-download.zip"
    try:
        with urllib.request.urlopen(url, timeout=120) as response, archive.open("wb") as out:
            shutil.copyfileobj(response, out)
        with zipfile.ZipFile(archive) as bundle:
            # Extract only CPython tools; reject paths escaping the private directory.
            for info in bundle.infolist():
                if not info.filename.startswith("tools/"):
                    continue
                dest = (target / info.filename).resolve()
                if not dest.is_relative_to(target.resolve()):
                    raise RuntimeError("The Python archive is invalid.")
                bundle.extract(info, target)
    finally:
        archive.unlink(missing_ok=True)
    _run_python([str(py), "-I", "-c", "import venv, ensurepip"], timeout=30, check=True)
    return str(py)


def begin_install(engine: str) -> str:
    """Start preparation off the UI thread. Explicit selection retries a failure."""
    if engine not in LOCAL_ENGINES or engine in _ready:
        return "ready"
    with _install_lock:
        if engine in _installing:
            return "installing"
        _install_errors.pop(engine, None)
        _installing.add(engine)

    def work() -> None:
        try:
            ensure_installed(engine)
        except Exception as exc:
            log.warning("local OCR preparation failed: %s", exc)
            with _install_lock:
                _install_errors[engine] = str(exc)
        finally:
            with _install_lock:
                _installing.discard(engine)

    threading.Thread(target=work, name=f"ocr-install-{engine}", daemon=True).start()
    return "installing"


def ensure_installed(engine: str) -> None:
    """Prepare dependencies AND models before making this backend selectable."""
    with _install_gate:
        if engine_ready(engine):
            return
        base = _base_python()
        py = _venv_python()
        # Re-run venv safely to repair interrupted/missing launchers.
        log.info("creating/repairing local OCR venv")
        _run_python([base, "-I", "-m", "venv", str(py.parent.parent)], check=True)
        _reader_path().write_text(_READER, encoding="utf-8")
        log.info("installing CPU OCR dependencies")
        _run_python([
            str(py), "-I", "-m", "pip", "install", "--disable-pip-version-check",
            "--no-input", "torch==2.6.0", "torchvision==0.21.0",
            "--index-url", "https://download.pytorch.org/whl/cpu",
        ], timeout=3600, check=True)
        _run_python([
            str(py), "-I", "-m", "pip", "install", "--disable-pip-version-check",
            "--no-input", "numpy==2.2.6", *_PACKAGES[engine],
        ], timeout=3600, check=True)
        log.info("preparing EasyOCR models")
        from .wt_languages import easyocr_lang_list

        langs = easyocr_lang_list(load_settings_language())
        _run_python([
            str(py), "-I", str(_reader_path()), engine, "--prepare",
            json.dumps(langs),
        ], timeout=900, check=True)
        (_runtime_root() / "ready.json").write_text(json.dumps({"engine": engine}), encoding="utf-8")
        _ready.add(engine)
        log.info("local OCR ready: %s", engine)


def load_settings_language() -> str:
    try:
        from .settings import load_settings

        return load_settings().wt_ui_language or "en"
    except Exception:  # noqa: BLE001
        return "en"


def _read_once(image, *, engine: str, langs: list[str]) -> str:
    _reader_path().parent.mkdir(parents=True, exist_ok=True)
    _reader_path().write_text(_READER, encoding="utf-8")
    tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False, dir=_child_env()["TEMP"])
    tmp_path = tmp.name
    tmp.close()
    try:
        image.save(tmp_path, format="PNG")
        done = _run_python(
            [
                str(_venv_python()), "-I", str(_reader_path()), engine, tmp_path,
                json.dumps(langs),
            ],
            timeout=300,
        )
    finally:
        try:
            os.remove(tmp_path)
        except OSError:
            pass
    if done.returncode != 0:
        detail = (done.stderr or done.stdout or "").strip()[-400:]
        raise RuntimeError(f"{engine} OCR failed: {detail}")
    line = ""
    for part in (done.stdout or "").splitlines():
        if part.startswith("{"):
            line = part
    if not line:
        return ""
    try:
        payload = json.loads(line)
    except json.JSONDecodeError:
        return ""
    return str(payload.get("text") or "").strip()


def read_text(image, *, engine: str, wait: bool, langs: list[str] | None = None) -> str:
    """OCR one crop. ``wait`` blocks on first install (the test window).

    The selected War Thunder language chooses the EasyOCR model. If that pass
    does not contain the vehicle-research row, one other script is tried.
    """
    if wait:
        ensure_installed(engine)
    elif not engine_ready(engine):
        state, detail = installation_state(engine)
        if state == "failed":
            raise RuntimeError(f"{engine}: {detail}")
        if state != "installing":
            begin_install(engine)
        raise RuntimeError(STILL_PREPARING)
    from .ocr_parse import vehicle_research_amount
    from .wt_languages import easyocr_fallback_lang_list, easyocr_lang_list

    ui_lang = load_settings_language()
    primary = list(langs or easyocr_lang_list(ui_lang))
    text = _read_once(image, engine=engine, langs=primary)
    if vehicle_research_amount(text, prefer_with=False) is not None:
        return text
    fallback = easyocr_fallback_lang_list(ui_lang)
    if fallback == primary:
        return text
    second = _read_once(image, engine=engine, langs=fallback)
    if vehicle_research_amount(second, prefer_with=False) is not None:
        return second
    return text or second
