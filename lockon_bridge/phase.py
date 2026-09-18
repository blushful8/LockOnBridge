from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class GamePhaseSnapshot:
    in_battle: bool
    mission_status: str | None


def _get_json(url: str, timeout: float = 1.5) -> Any | None:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8", errors="replace"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError):
        return None


def read_phase(host: str = "127.0.0.1", port: int = 8111) -> GamePhaseSnapshot | None:
    """
    Mirrors LockOn phone logic roughly: mission running OR map_info.valid ⇒ in battle.
    """
    root = f"http://{host}:{port}"
    mission = _get_json(f"{root}/mission.json")
    map_info = _get_json(f"{root}/map_info.json")
    if mission is None and map_info is None:
        return None

    status = None
    if isinstance(mission, dict):
        raw = mission.get("status")
        if isinstance(raw, str):
            status = raw.strip().lower()

    map_valid = False
    if isinstance(map_info, dict):
        map_valid = bool(map_info.get("valid"))

    mission_running = status == "running"
    in_battle = mission_running or map_valid
    return GamePhaseSnapshot(in_battle=in_battle, mission_status=status)
