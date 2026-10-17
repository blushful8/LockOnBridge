"""Game grabs must not use the capture paths that stick until Alt+Tab."""

from __future__ import annotations

from PIL import Image

from lockon_bridge.ocr_isolate import _creationflags
from lockon_bridge.wgc_capture import client_crop_box
from lockon_bridge.win_priority import BELOW_NORMAL_PRIORITY_CLASS


def test_client_crop_drops_the_window_frame() -> None:
    box = client_crop_box(
        (2000, 1200),
        (10, 20, 2010, 1220),
        (18, 60),
        (1984, 1140),
    )
    assert box[0] >= 0 and box[1] > 0
    assert box[2] < 2000
    assert box[3] < 1200


def test_client_crop_covers_a_borderless_window() -> None:
    assert client_crop_box((1920, 1080), (0, 0, 1920, 1080), (0, 0), (1920, 1080)) == (
        0,
        0,
        1920,
        1080,
    )


def test_grab_does_not_fall_back_to_gdi_or_dxgi(monkeypatch) -> None:
    import lockon_bridge.capture as cap

    monkeypatch.setattr(cap, "find_war_thunder_hwnd", lambda: 1)
    monkeypatch.setattr(cap, "is_war_thunder_foreground", lambda: True)
    monkeypatch.setattr(cap.user32, "IsIconic", lambda _hwnd: False)

    def _refuse(*_args, **_kwargs):
        raise AssertionError("hitching capture was used")

    monkeypatch.setattr(cap, "_grab_hwnd_screen", _refuse)
    monkeypatch.setattr(cap, "_grab_hwnd_gdi", _refuse)
    monkeypatch.setattr(cap, "_grab_hwnd_mss", _refuse)
    monkeypatch.setattr(
        "lockon_bridge.wgc_capture.grab_window_client",
        lambda _hwnd, timeout=0.45: Image.new("RGB", (800, 600), (40, 40, 40)),
    )
    frame = cap.grab_wt_client_image(
        focus=False,
        require_foreground=True,
        allow_mss=True,
        prefer_window_buffer=True,
    )
    assert frame is not None
    assert frame.size == (800, 600)


def test_grab_returns_none_when_wgc_fails(monkeypatch) -> None:
    import lockon_bridge.capture as cap

    monkeypatch.setattr(cap, "find_war_thunder_hwnd", lambda: 1)
    monkeypatch.setattr(cap, "is_war_thunder_foreground", lambda: True)
    monkeypatch.setattr(cap.user32, "IsIconic", lambda _hwnd: False)

    def _refuse(*_args, **_kwargs):
        raise AssertionError("hitching capture was used")

    monkeypatch.setattr(cap, "_grab_hwnd_screen", _refuse)
    monkeypatch.setattr(cap, "_grab_hwnd_gdi", _refuse)
    monkeypatch.setattr(cap, "_grab_hwnd_mss", _refuse)
    monkeypatch.setattr("lockon_bridge.wgc_capture.grab_window_client", lambda *_a, **_k: None)
    assert cap.grab_wt_client_image(require_foreground=False) is None


def test_ocr_worker_is_created_below_normal() -> None:
    assert _creationflags() & BELOW_NORMAL_PRIORITY_CLASS
