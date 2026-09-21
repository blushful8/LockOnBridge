"""NormPoint mapping for Messages envelope clicks across resolutions."""

from __future__ import annotations

from PIL import Image

from lockon_bridge.wt_messages_ui import envelope_click_candidates, norm_to_client_xy


def test_norm_scales_across_resolutions() -> None:
    """Same content-frame fraction → proportional pixels on FHD vs 4K."""
    fhd = Image.new("RGB", (1920, 1080))
    uhd = Image.new("RGB", (3840, 2160))
    nx, ny = 0.972, 0.958
    x1, y1 = norm_to_client_xy(1920, 1080, nx, ny, frame=fhd)
    x2, y2 = norm_to_client_xy(3840, 2160, nx, ny, frame=uhd)
    assert abs(x1 / 1920 - x2 / 3840) < 0.002
    assert abs(y1 / 1080 - y2 / 2160) < 0.002
    assert abs(x1 / 1920 - nx) < 0.002
    assert abs(y1 / 1080 - ny) < 0.002


def test_envelope_candidates_include_norm_cluster() -> None:
    frame = Image.new("RGB", (1920, 1080), (40, 40, 40))
    cands = envelope_click_candidates(frame)
    assert len(cands) >= 5
    assert any(label.startswith("norm:") for label, _x, _y in cands)
    # All points in bottom-right quadrant.
    for _label, x, y in cands:
        assert x > 1920 * 0.75
        assert y > 1080 * 0.80
