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
    Combat session = mission map loaded (``map_info.valid``).

    ``mission.status == "running"`` alone is not enough: WT often leaves that stuck
    after preliminary results while the player is already in the hangar
    (``map_info.valid == false``). Mirror LockOn Android ``isSessionActive``.
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
    map_present = isinstance(map_info, dict)
    if map_present:
        map_valid = bool(map_info.get("valid"))

    mission_running = status == "running"
    if map_valid:
        in_battle = True
    elif not map_present and mission_running:
        # Map endpoint failed — fall back to mission status.
        in_battle = True
    else:
        in_battle = False
    return GamePhaseSnapshot(in_battle=in_battle, mission_status=status)
