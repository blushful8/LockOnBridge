"""Calibrated RP/SL ROI load/save and lean path selection."""

from pathlib import Path

from PIL import Image

from lockon_bridge.roi_calib import (
    CalibratedRois,
    calibrated_from_dict,
    calibrated_to_dict,
    default_calibrated_rois,
    load_calibrated_rois,
    save_calibrated_rois,
)
from lockon_bridge.roi_calibrator_ui import _DEV_PASS_SHA256, verify_dev_passphrase
from lockon_bridge.roi_layout import select_reward_digit_rects


def test_dev_passphrase_rejects_wrong_and_empty():
    assert not verify_dev_passphrase("wrong")
    assert not verify_dev_passphrase("")
    assert len(_DEV_PASS_SHA256) == 64
    # Correct passphrase must never appear in the repository.


def test_default_calib_roundtrip(tmp_path: Path, monkeypatch):
    calib = default_calibrated_rois()
    raw = calibrated_to_dict(calib)
    assert "pairs" in raw["with"]
    assert len(raw["with"]["pairs"]) >= 1
    again = calibrated_from_dict(raw)
    assert again is not None
    assert again.without_premium.rp.left == calib.without_premium.rp.left
    assert again.with_premium.sl.bottom == calib.with_premium.sl.bottom

    monkeypatch.setattr(
        "lockon_bridge.roi_calib.user_calib_path",
        lambda: tmp_path / "roi_calibrated.json",
    )
    monkeypatch.setattr(
        "lockon_bridge.roi_calib.packaged_calib_path",
        lambda: tmp_path / "missing.json",
    )
    paths = save_calibrated_rois(calib, also_package=False)
    assert paths[0].is_file()
    loaded = load_calibrated_rois()
    assert loaded is not None
    assert loaded.without_premium.rp.tag.endswith("rp")


def test_legacy_v1_loads_as_single_pair():
    raw = {
        "version": 1,
        "with": {
            "rp": {"left": 0.1, "top": 0.1, "right": 0.2, "bottom": 0.15},
            "sl": {"left": 0.1, "top": 0.16, "right": 0.2, "bottom": 0.21},
        },
        "without": {
            "rp": {"left": 0.3, "top": 0.1, "right": 0.4, "bottom": 0.15},
            "sl": {"left": 0.3, "top": 0.16, "right": 0.4, "bottom": 0.21},
        },
    }
    calib = calibrated_from_dict(raw)
    assert calib is not None
    assert len(calib.with_premium.pairs) == 1
    assert calib.with_premium.rp.left == 0.1


def test_add_fallback_pair_syncs_both_columns():
    base = default_calibrated_rois()
    assert base.pair_count == 1
    synced = base.add_fallback_pair()
    assert synced.pair_count == 2
    assert len(synced.with_premium.pairs) == 2
    assert len(synced.without_premium.pairs) == 2
    trimmed = synced.remove_fallback_pair(1)
    assert trimmed.pair_count == 1


def test_uneven_columns_pad_on_load():
    raw = {
        "version": 2,
        "with": {
            "pairs": [
                {
                    "rp": {"left": 0.1, "top": 0.1, "right": 0.2, "bottom": 0.15},
                    "sl": {"left": 0.1, "top": 0.16, "right": 0.2, "bottom": 0.21},
                },
                {
                    "rp": {"left": 0.2, "top": 0.1, "right": 0.3, "bottom": 0.15},
                    "sl": {"left": 0.2, "top": 0.16, "right": 0.3, "bottom": 0.21},
                },
            ]
        },
        "without": {
            "pairs": [
                {
                    "rp": {"left": 0.3, "top": 0.1, "right": 0.4, "bottom": 0.15},
                    "sl": {"left": 0.3, "top": 0.16, "right": 0.4, "bottom": 0.21},
                }
            ]
        },
    }
    calib = calibrated_from_dict(raw)
    assert calib is not None
    assert calib.pair_count == 2
    assert len(calib.without_premium.pairs) == 2


def test_lean_rects_use_calibrated_when_present():
    img = Image.new("RGB", (1920, 1080), (0, 0, 0))
    rects = select_reward_digit_rects(img, dense=False, prefer_with=False)
    assert len(rects) == 2
    tags = {r.tag for r in rects}
    assert any(t.endswith("rp") for t in tags)
    assert any(t.endswith("sl") for t in tags)


def test_higher_packaged_version_beats_user(tmp_path: Path, monkeypatch):
    """Shipped update (higher version) replaces stale LocalAppData calib."""
    packaged = tmp_path / "packaged.json"
    user = tmp_path / "user.json"
    packaged.write_text(
        '{"version":3,"with":{"pairs":[{"rp":{"left":0.1,"top":0.1,"right":0.2,"bottom":0.15},'
        '"sl":{"left":0.1,"top":0.16,"right":0.2,"bottom":0.21}}]},'
        '"without":{"pairs":[{"rp":{"left":0.3,"top":0.1,"right":0.4,"bottom":0.15},'
        '"sl":{"left":0.3,"top":0.16,"right":0.4,"bottom":0.21}}]}}\n',
        encoding="utf-8",
    )
    user.write_text(
        '{"version":2,"with":{"pairs":[{"rp":{"left":0.5,"top":0.5,"right":0.6,"bottom":0.55},'
        '"sl":{"left":0.5,"top":0.56,"right":0.6,"bottom":0.61}}]},'
        '"without":{"pairs":[{"rp":{"left":0.7,"top":0.5,"right":0.8,"bottom":0.55},'
        '"sl":{"left":0.7,"top":0.56,"right":0.8,"bottom":0.61}}]}}\n',
        encoding="utf-8",
    )
    monkeypatch.setattr("lockon_bridge.roi_calib.packaged_calib_path", lambda: packaged)
    monkeypatch.setattr("lockon_bridge.roi_calib.user_calib_path", lambda: user)
    loaded = load_calibrated_rois()
    assert loaded is not None
    assert loaded.version == 3
    assert loaded.with_premium.rp.left == 0.1


def test_user_more_pairs_beats_same_version_packaged(tmp_path: Path, monkeypatch):
    """Local Save with extra fallback pairs must stick after reopen (.exe)."""
    packaged = tmp_path / "packaged.json"
    user = tmp_path / "user.json"
    packaged.write_text(
        '{"version":2,"with":{"pairs":[{"rp":{"left":0.1,"top":0.1,"right":0.2,"bottom":0.15},'
        '"sl":{"left":0.1,"top":0.16,"right":0.2,"bottom":0.21}}]},'
        '"without":{"pairs":[{"rp":{"left":0.3,"top":0.1,"right":0.4,"bottom":0.15},'
        '"sl":{"left":0.3,"top":0.16,"right":0.4,"bottom":0.21}}]}}\n',
        encoding="utf-8",
    )
    user.write_text(
        '{"version":2,"with":{"pairs":['
        '{"rp":{"left":0.5,"top":0.5,"right":0.6,"bottom":0.55},"sl":{"left":0.5,"top":0.56,"right":0.6,"bottom":0.61}},'
        '{"rp":{"left":0.55,"top":0.5,"right":0.65,"bottom":0.55},"sl":{"left":0.55,"top":0.56,"right":0.65,"bottom":0.61}},'
        '{"rp":{"left":0.6,"top":0.5,"right":0.7,"bottom":0.55},"sl":{"left":0.6,"top":0.56,"right":0.7,"bottom":0.61}}'
        ']},"without":{"pairs":['
        '{"rp":{"left":0.7,"top":0.5,"right":0.8,"bottom":0.55},"sl":{"left":0.7,"top":0.56,"right":0.8,"bottom":0.61}},'
        '{"rp":{"left":0.72,"top":0.5,"right":0.82,"bottom":0.55},"sl":{"left":0.72,"top":0.56,"right":0.82,"bottom":0.61}},'
        '{"rp":{"left":0.74,"top":0.5,"right":0.84,"bottom":0.55},"sl":{"left":0.74,"top":0.56,"right":0.84,"bottom":0.61}}'
        ']}}\n',
        encoding="utf-8",
    )
    monkeypatch.setattr("lockon_bridge.roi_calib.packaged_calib_path", lambda: packaged)
    monkeypatch.setattr("lockon_bridge.roi_calib.user_calib_path", lambda: user)
    loaded = load_calibrated_rois()
    assert loaded is not None
    assert loaded.pair_count == 3
    assert loaded.with_premium.rp.left == 0.5


def test_norm_rects_scale_across_resolutions():
    """Same fractions → proportional pixel boxes on 16:10 2K and 16:9 FullHD."""
    from lockon_bridge.roi_layout import pixel_box
    from lockon_bridge.roi_layout import NormRect

    rect = NormRect(0.40, 0.13, 0.46, 0.15, "t")
    box_2k = pixel_box(Image.new("RGB", (2560, 1600)), rect)
    box_fhd = pixel_box(Image.new("RGB", (1920, 1080)), rect)
    assert box_2k is not None and box_fhd is not None
    l2, t2, r2, b2 = box_2k
    l1, t1, r1, b1 = box_fhd
    assert abs((l2 / 2560) - (l1 / 1920)) < 0.002
    assert abs((t2 / 1600) - (t1 / 1080)) < 0.002
    assert abs((r2 / 2560) - (r1 / 1920)) < 0.002
    assert abs((b2 / 1600) - (b1 / 1080)) < 0.002


def test_dense_still_uses_catalogue():
    img = Image.new("RGB", (1920, 1080), (0, 0, 0))
    rects = select_reward_digit_rects(img, dense=True, prefer_with=False)
    assert len(rects) > 2
