from __future__ import annotations

from pathlib import Path

from lockon_bridge import paths
from lockon_bridge.install_location import ensure_data_root_ready


def test_suggest_install_root_name():
    suggested = paths.suggest_install_root()
    assert suggested.name == paths.PRODUCT_ID


def test_ensure_silent_writes_suggested(monkeypatch, tmp_path):
    paths.clear_data_root_cache()
    monkeypatch.delenv("LOCKON_BRIDGE_DATA_DIR", raising=False)
    monkeypatch.setattr(paths, "is_frozen", lambda: True)
    monkeypatch.setattr(paths, "data_root_configured", lambda: False)
    monkeypatch.setattr(paths, "suggest_install_root", lambda: tmp_path / "LockOnBridge")

    written: list[Path] = []

    def _write(path: Path) -> Path:
        resolved = Path(path)
        resolved.mkdir(parents=True, exist_ok=True)
        paths._data_root_cache = resolved  # noqa: SLF001
        written.append(resolved)
        return resolved

    monkeypatch.setattr(paths, "write_data_root", _write)
    monkeypatch.setattr(
        "lockon_bridge.install_location.write_data_root",
        _write,
    )
    monkeypatch.setattr(
        "lockon_bridge.install_location.is_frozen",
        lambda: True,
    )
    monkeypatch.setattr(
        "lockon_bridge.install_location.data_root_configured",
        lambda: False,
    )

    assert ensure_data_root_ready(interactive=False) is True
    assert written
    assert written[0] == tmp_path / "LockOnBridge"
