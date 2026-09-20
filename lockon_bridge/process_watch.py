from __future__ import annotations

import time
from typing import Iterable

import psutil

# War Thunder client process names (Steam / Gaijin launcher).
# psutil usually returns "aces.exe"; some hosts report the stem only.
WT_PROCESS_NAMES = frozenset({"aces.exe", "aces_be.exe", "aces", "aces_be"})

# Avoid hammering process_iter when several loops ask "is WT up?" close together.
_CACHE_TTL_SEC = 2.5
_pids_cache: tuple[float, tuple[int, ...]] | None = None


def _normalize(name: str | None) -> str:
    raw = (name or "").strip().lower()
    return raw[:-4] if raw.endswith(".exe") else raw


def war_thunder_pids(
    names: Iterable[str] = WT_PROCESS_NAMES,
    *,
    force: bool = False,
) -> list[int]:
    global _pids_cache
    now = time.monotonic()
    if (
        not force
        and _pids_cache is not None
        and (now - _pids_cache[0]) < _CACHE_TTL_SEC
    ):
        return list(_pids_cache[1])

    wanted = {_normalize(n) for n in names}
    found: list[int] = []
    for proc in psutil.process_iter(["pid", "name"]):
        try:
            if _normalize(proc.info.get("name")) in wanted:
                found.append(int(proc.info["pid"]))
        except (psutil.Error, TypeError, ValueError):
            continue
    _pids_cache = (now, tuple(found))
    return found


def is_war_thunder_running(*, force: bool = False) -> bool:
    return bool(war_thunder_pids(force=force))


def wait_until_war_thunder_starts(idle_poll_sec: float = 120.0) -> None:
    """Block with low CPU until aces.exe appears. No game API / OCR / HTTP here."""
    while not is_war_thunder_running(force=True):
        time.sleep(max(5.0, idle_poll_sec))


def wait_until_war_thunder_stops(check_sec: float = 3.0) -> None:
    while is_war_thunder_running(force=True):
        time.sleep(max(1.0, check_sec))
