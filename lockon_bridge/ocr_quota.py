"""Developer-only OCR.space usage.

Regular users never see this. The parse response does not include a balance, so
remaining is the plan cap minus calls we can account for:

- this PC's successful conversions (errors are not billed)
- the PRO monthly report (through yesterday), when that endpoint answers

Free caps are the conservative default. Numbers above a free cap switch the
math to the PRO allowance.
"""

from __future__ import annotations

import json
import logging
import re
import threading
import urllib.error
import urllib.request
from datetime import date
from typing import Any, Callable

log = logging.getLogger("lockon_bridge.ocr_quota")

_FREE_MONTH = {2: 25_000, 3: 2_500}
_PRO_MONTH = {2: 300_000, 3: 30_000}
_FREE_DAY = 500
_USAGE_NAME = "ocr_usage.json"
_SERVER_URL = "https://myapi.ocr.space/conversions"

_lock = threading.Lock()
_listeners: list[Callable[[], None]] = []


def subscribe(listener: Callable[[], None]) -> None:
    if listener not in _listeners:
        _listeners.append(listener)


def _notify() -> None:
    for listener in list(_listeners):
        try:
            listener()
        except Exception:  # noqa: BLE001
            log.debug("quota listener failed", exc_info=True)


def _usage_path():
    from .paths import data_root

    return data_root() / _USAGE_NAME


def _empty(today: date | None = None) -> dict[str, Any]:
    day = today or date.today()
    return {
        "month": day.strftime("%Y-%m"),
        "local": {"2": 0, "3": 0},
        "today": day.isoformat(),
        "today_local": {"2": 0, "3": 0},
        "server": {"1": 0, "2": 0, "3": 0},
        "blocked_day": "",
    }


def _load() -> dict[str, Any]:
    path = _usage_path()
    raw: dict[str, Any] = {}
    if path.is_file():
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                raw = loaded
        except (OSError, json.JSONDecodeError):
            raw = {}
    ledger = _empty()
    today = date.today()
    month = today.strftime("%Y-%m")
    if raw.get("month") == month:
        ledger["local"] = _counts(raw.get("local"))
        ledger["server"] = _counts(raw.get("server"), engines=(1, 2, 3))
        ledger["blocked_day"] = str(raw.get("blocked_day") or "")
    if raw.get("today") == today.isoformat() and raw.get("month") == month:
        ledger["today_local"] = _counts(raw.get("today_local"))
    return ledger


def _counts(raw: Any, *, engines: tuple[int, ...] = (2, 3)) -> dict[str, int]:
    out = {str(engine): 0 for engine in engines}
    if not isinstance(raw, dict):
        return out
    for engine in engines:
        try:
            out[str(engine)] = max(0, int(raw.get(str(engine), raw.get(engine, 0)) or 0))
        except (TypeError, ValueError):
            out[str(engine)] = 0
    return out


def _save(ledger: dict[str, Any]) -> None:
    path = _usage_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(ledger, ensure_ascii=False), encoding="utf-8")
    except OSError as exc:
        log.debug("quota ledger not saved: %s", exc)


def note_success(engine: int) -> None:
    """Count one billed conversion. OCR.space does not bill error responses."""
    bucket = "3" if int(engine) == 3 else "2"
    with _lock:
        ledger = _load()
        ledger["local"][bucket] = int(ledger["local"].get(bucket, 0)) + 1
        ledger["today_local"][bucket] = int(ledger["today_local"].get(bucket, 0)) + 1
        _save(ledger)
    _notify()


def note_rejection(body: str) -> None:
    """A daily/hourly wall means nothing is left in that window."""
    match = re.search(
        r"maximum\s+(\d+)\s+number of times within\s+(\d+)",
        body or "",
        re.IGNORECASE,
    )
    if not match:
        return
    window = int(match.group(2))
    if window < 20 * 3600:
        return
    with _lock:
        ledger = _load()
        ledger["blocked_day"] = date.today().isoformat()
        _save(ledger)
    _notify()


def refresh_remote() -> None:
    """Ask OCR.space for month-to-yesterday totals. No-op without a real key."""
    from .ocr_space import _DEMO_KEY, resolve_api_key

    key = resolve_api_key()
    if not key or key == _DEMO_KEY:
        return
    req = urllib.request.Request(
        _SERVER_URL,
        data=b"",
        method="POST",
        headers={"apikey": key, "User-Agent": "LockOnBridge/quota"},
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            payload = json.loads(resp.read().decode("utf-8", "replace"))
    except (urllib.error.URLError, json.JSONDecodeError, TimeoutError, OSError) as exc:
        log.debug("quota remote refresh skipped: %s", exc)
        return
    if not isinstance(payload, dict):
        return
    server = {
        "1": _as_int(payload.get("count_engine1")),
        "2": _as_int(payload.get("count_engine2")),
        "3": _as_int(payload.get("count_engine3")),
    }
    with _lock:
        ledger = _load()
        ledger["server"] = server
        _save(ledger)
    _notify()


def quota_view(ledger: dict[str, Any] | None = None, *, today: date | None = None) -> dict[str, int | str]:
    """Used and remaining per engine. Pure aside from the default ledger load."""
    data = ledger if ledger is not None else _load()
    day = today or date.today()
    local = _counts(data.get("local"))
    today_local = _counts(data.get("today_local"))
    server = _counts(data.get("server"), engines=(1, 2, 3))
    if data.get("today") not in (None, day.isoformat()):
        today_local = {"2": 0, "3": 0}
    used2 = max(server["2"] + today_local["2"], local["2"]) + server["1"]
    used3 = max(server["3"] + today_local["3"], local["3"])
    plan = "pro" if used2 > _FREE_MONTH[2] or used3 > _FREE_MONTH[3] else "free"
    caps = _PRO_MONTH if plan == "pro" else _FREE_MONTH
    today_total = today_local["2"] + today_local["3"]
    blocked = str(data.get("blocked_day") or "") == day.isoformat()
    left2 = max(0, caps[2] - used2)
    left3 = max(0, caps[3] - used3)
    day_left = 0 if plan == "pro" else (0 if blocked else max(0, _FREE_DAY - today_total))
    return {
        "plan": plan,
        "used2": used2,
        "used3": used3,
        "left2": left2,
        "left3": left3,
        "cap2": caps[2],
        "cap3": caps[3],
        "today": today_total if not blocked else _FREE_DAY,
        "day_left": day_left,
        "day_cap": _FREE_DAY if plan == "free" else 0,
    }


def format_dev_quota(language: str, view: dict[str, int | str] | None = None) -> str:
    info = view if view is not None else quota_view()
    lang = (language or "en").lower()
    if lang.startswith("uk"):
        plan = "безкоштовний план" if info["plan"] == "free" else "PRO"
        day = _day_clause(info, "сьогодні", "денний ліміт вичерпано")
        return (
            f"API (dev): E2 залишок {info['left2']} з {info['cap2']} (використано {info['used2']})"
            f" · E3 залишок {info['left3']} з {info['cap3']} (використано {info['used3']})"
            f"{day} · {plan}"
        )
    if lang.startswith("ru"):
        plan = "бесплатный план" if info["plan"] == "free" else "PRO"
        day = _day_clause(info, "сегодня", "дневной лимит исчерпан")
        return (
            f"API (dev): E2 остаток {info['left2']} из {info['cap2']} (использовано {info['used2']})"
            f" · E3 остаток {info['left3']} из {info['cap3']} (использовано {info['used3']})"
            f"{day} · {plan}"
        )
    plan = "free plan" if info["plan"] == "free" else "PRO"
    day = _day_clause(info, "today", "daily limit reached")
    return (
        f"API (dev): E2 left {info['left2']} of {info['cap2']} (used {info['used2']})"
        f" · E3 left {info['left3']} of {info['cap3']} (used {info['used3']})"
        f"{day} · {plan}"
    )


def _day_clause(info: dict[str, int | str], label: str, blocked: str) -> str:
    if not info["day_cap"]:
        return ""
    if int(info["day_left"]) <= 0:
        return f" · {blocked}"
    return f" · {label} {info['today']}/{info['day_cap']}"


def _as_int(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0
