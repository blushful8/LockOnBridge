"""On-demand EasyOCR.

It stays out of the shipped exe. A private Python venv under LocalAppData
installs it the first time it is selected, or when OCR.space is unreachable
or out of monthly quota.
"""

from __future__ import annotations

import json
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

_READER = r'''
import json, os, sys
os.environ.setdefault("GLOG_minloglevel", "2")
os.environ.setdefault("FLAGS_minloglevel", "2")
sys.stdout.reconfigure(encoding="utf-8")
engine, path = sys.argv[1], sys.argv[2]
lines = []
if engine == "easyocr":
    import easyocr
    reader = easyocr.Reader(["uk", "en"], gpu=False, verbose=False)
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
    from .paths import data_root

    return data_root() / "ocr-venv" / "Scripts" / "python.exe"


def _reader_path() -> Path:
    from .paths import data_root

    return data_root() / "ocr-venv" / "read_zone.py"


def engine_ready(engine: str) -> bool:
    if engine in _ready:
        return True
    py = _venv_python()
    if not py.is_file():
        return False
    module = "easyocr"
    try:
        done = subprocess.run(
            [str(py), "-c", f"import {module}"],
            capture_output=True,
            timeout=120,
            check=False,
            **_hidden_process(),
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    if done.returncode == 0:
        _ready.add(engine)
        return True
    return False


def _base_python() -> str | None:
    if not getattr(sys, "frozen", False):
        return sys.executable
    local = (
        Path(os.environ.get("LOCALAPPDATA", ""))
        / "Programs"
        / "Python"
        / "Python312"
        / "python.exe"
    )
    if local.is_file():
        return str(local)
    py = shutil.which("py")
    if py:
        try:
            done = subprocess.run(
                [py, "-3.12", "-c", "import sys; print(sys.executable)"],
                capture_output=True,
                text=True,
                timeout=20,
                check=False,
                **_hidden_process(),
            )
        except (OSError, subprocess.TimeoutExpired):
            done = None
        if done is not None and done.returncode == 0:
            path = done.stdout.strip()
            if path:
                return path
    found = shutil.which("python")
    return found


def begin_install(engine: str) -> str:
    """Start a background install. Returns ready, installing, or missing-python."""
    if engine not in LOCAL_ENGINES:
        return "ready"
    if engine_ready(engine):
        return "ready"
    if _base_python() is None:
        return "missing-python"
    with _install_lock:
        if engine in _installing:
            return "installing"
        _installing.add(engine)

    def work() -> None:
        try:
            ensure_installed(engine)
        except Exception as exc:  # noqa: BLE001
            log.warning("local OCR install failed: %s", exc)
        finally:
            with _install_lock:
                _installing.discard(engine)

    threading.Thread(target=work, name=f"ocr-install-{engine}", daemon=True).start()
    return "installing"


def ensure_installed(engine: str) -> None:
    """Block until the venv can import this engine."""
    if engine_ready(engine):
        return
    base = _base_python()
    if not base:
        raise RuntimeError("Немає Python 3.12, щоб поставити локальний OCR")
    py = _venv_python()
    if not py.is_file():
        log.info("creating local OCR venv")
        subprocess.run(
            [base, "-m", "venv", str(py.parent.parent)],
            check=True,
            timeout=180,
            **_hidden_process(),
        )
    _reader_path().write_text(_READER, encoding="utf-8")
    log.info("installing %s", engine)
    subprocess.run(
        [
            str(py),
            "-m",
            "pip",
            "install",
            "--disable-pip-version-check",
            "--no-input",
            *_PACKAGES[engine],
        ],
        check=True,
        timeout=3600,
        **_hidden_process(),
    )
    if not engine_ready(engine):
        raise RuntimeError(f"{engine} встановився, але імпорт не вдався")


def read_text(image, *, engine: str, wait: bool) -> str:
    """OCR one crop. ``wait`` blocks on first install (the test window)."""
    if wait:
        ensure_installed(engine)
    elif not engine_ready(engine):
        state = begin_install(engine)
        if state == "missing-python":
            raise RuntimeError("Немає Python 3.12, щоб поставити локальний OCR")
        raise RuntimeError(f"{engine} ще встановлюється")
    _reader_path().parent.mkdir(parents=True, exist_ok=True)
    _reader_path().write_text(_READER, encoding="utf-8")
    tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
    tmp_path = tmp.name
    tmp.close()
    try:
        image.save(tmp_path, format="PNG")
        done = subprocess.run(
            [str(_venv_python()), str(_reader_path()), engine, tmp_path],
            capture_output=True,
            text=True,
            timeout=180,
            check=False,
            encoding="utf-8",
            errors="replace",
            env={**os.environ, "PYTHONIOENCODING": "utf-8"},
            **_hidden_process(),
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
