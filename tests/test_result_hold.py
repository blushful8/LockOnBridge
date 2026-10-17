"""Result-shot archive and notice placement stay off the reward crop."""

from __future__ import annotations

from PIL import Image

from lockon_bridge.capture_archive import save_result_shots
from lockon_bridge.i18n import strings_for
from lockon_bridge.result_hold import card_top_left, notice_origin_clear_of


def test_card_sits_outside_the_default_reward_crop() -> None:
    origin = card_top_left(
        2560,
        1600,
        (0.16, 0.12, 0.58, 0.68),
        460,
        168,
        margin=28,
    )
    card = (origin[0], origin[1], origin[0] + 460, origin[1] + 168)
    zone = (int(0.16 * 2560), int(0.12 * 1600), int(0.58 * 2560), int(0.68 * 1600))
    assert card[2] <= zone[0] or card[0] >= zone[2] or card[3] <= zone[1] or card[1] >= zone[3]


def test_card_moves_clear_of_a_bottom_zone() -> None:
    origin = card_top_left(1920, 1080, (0.1, 0.55, 0.9, 0.98), 400, 140, margin=20)
    assert origin[1] + 140 <= int(0.55 * 1080)


def test_save_result_shots_keeps_only_the_latest_burst(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("lockon_bridge.capture_archive.data_root", lambda: tmp_path)
    old = tmp_path / "captures"
    old.mkdir()
    (old / "older_fail_burst.png").write_bytes(b"stale")
    first = [Image.new("RGB", (8, 8), (i, 0, 0)) for i in range(8)]
    saved = save_result_shots(first)
    names = sorted(p.name for p in (tmp_path / "captures").glob("*.png"))
    assert len(saved) == 6
    assert len(names) == 6
    assert all(name.endswith(tuple(f"_shot{n}.png" for n in range(1, 7))) for name in names)
    assert not (tmp_path / "captures" / "older_fail_burst.png").exists()
    again = save_result_shots([Image.new("RGB", (8, 8), (0, 9, 0)) for _ in range(2)])
    left = list((tmp_path / "captures").glob("*.png"))
    assert len(again) == 2
    assert {p.name for p in left} == {p.name for p in again}


def test_notice_stays_off_a_fullscreen_game() -> None:
    game = (0, 0, 1920, 1080)
    assert notice_origin_clear_of(game, [(0, 0, 1920, 1040)], 560, 188) is None


def test_notice_uses_the_other_monitor_when_the_game_is_fullscreen() -> None:
    game = (0, 0, 1920, 1080)
    origin = notice_origin_clear_of(
        game,
        [(0, 0, 1920, 1040), (1920, 0, 3840, 1040)],
        560,
        188,
    )
    assert origin is not None
    card = (origin[0], origin[1], origin[0] + 560, origin[1] + 188)
    assert card[0] >= 1920
    assert card[2] <= 3840


def test_notice_uses_a_free_corner_when_the_game_is_windowed() -> None:
    game = (200, 100, 1000, 700)
    origin = notice_origin_clear_of(game, [(0, 0, 1920, 1040)], 400, 140, margin=12)
    assert origin is not None
    card = (origin[0], origin[1], origin[0] + 400, origin[1] + 140)
    assert card[2] <= game[0] or card[0] >= game[2] or card[3] <= game[1] or card[1] >= game[3]


def test_hold_copy_exists_in_each_app_language() -> None:
    titles = {strings_for(code).grab_hold_title for code in ("en", "uk", "ru")}
    bodies = {strings_for(code).grab_hold_body for code in ("en", "uk", "ru")}
    assert len(titles) == 3
    assert len(bodies) == 3
    assert all(titles)
    assert all(bodies)
