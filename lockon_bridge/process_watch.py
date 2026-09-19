from __future__ import annotations

import time
from typing import Iterable

import psutil

# War Thunder client process names (Steam / Gaijin launcher).
# psutil usually returns "aces.exe"; some hosts report the stem only.
WT_PROCESS_NAMES = frozenset({"aces.exe", "aces_be.exe", "aces", "aces_be"})


def _normalize(name: str | None) -> str:
    raw = (name or "").strip().lower()
    return raw[:-4] if raw.endswith(".exe") else raw


def war_thunder_pids(names: Iterable[str] = WT_PROCESS_NAMES) -> list[int]:
    wanted = {_normalize(n) for n in names}
    found: list[int] = []
    for proc in psutil.process_iter(["pid", "name"]):
        try:
            if _normalize(proc.info.get("name")) in wanted:
                found.append(int(proc.info["pid"]))
        except (psutil.Error, TypeError, ValueError):
            continue
    return found


def is_war_thunder_running() -> bool:
    return bool(war_thunder_pids())


def wait_until_war_thunder_starts(idle_poll_sec: float = 5.0) -> None:
    """Block with low CPU until aces.exe appears. No game API / OCR / HTTP here."""
    while not is_war_thunder_running():
        time.sleep(max(1.0, idle_poll_sec))


def wait_until_war_thunder_stops(check_sec: float = 2.0) -> None:
    while is_war_thunder_running():
        time.sleep(max(0.5, check_sec))
