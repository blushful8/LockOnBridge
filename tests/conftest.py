"""Keep grab tests from showing the results notice or pausing input."""

from __future__ import annotations

import pytest


class _QuietHold:
    def __init__(self, total: int = 1, **kwargs) -> None:
        self.total = total

    def __enter__(self) -> _QuietHold:
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None

    def open(self) -> None:
        return None

    def close(self) -> None:
        return None

    def set_progress(self, index: int) -> None:
        return None

    def begin_processing(self) -> None:
        return None

    def set_processing_progress(self, index: int, total: int) -> None:
        return None


@pytest.fixture(autouse=True)
def _no_result_hold(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("lockon_bridge.result_hold.ResultHold", _QuietHold)
    monkeypatch.setattr(
        "lockon_bridge.runtime.save_result_shots", lambda images, **kwargs: []
    )
