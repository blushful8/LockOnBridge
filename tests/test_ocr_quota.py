from datetime import date

from lockon_bridge.ocr_quota import format_dev_quota, note_rejection, note_success, quota_view


def _ledger(**overrides):
    base = {
        "month": "2026-09",
        "local": {"2": 0, "3": 0},
        "today": "2026-09-26",
        "today_local": {"2": 0, "3": 0},
        "server": {"1": 0, "2": 0, "3": 0},
        "blocked_day": "",
    }
    base.update(overrides)
    return base


def test_free_remaining_uses_month_caps() -> None:
    view = quota_view(
        _ledger(local={"2": 10, "3": 4}, today_local={"2": 3, "3": 1}),
        today=date(2026, 9, 26),
    )
    assert view["plan"] == "free"
    assert view["used2"] == 10
    assert view["left2"] == 24_990
    assert view["used3"] == 4
    assert view["left3"] == 2_496
    assert view["today"] == 4


def test_daily_wall_zeros_both_engines() -> None:
    view = quota_view(
        _ledger(local={"2": 500, "3": 0}, today_local={"2": 500, "3": 0}),
        today=date(2026, 9, 26),
    )
    assert view["left2"] == 24_500
    assert view["left3"] == 2_500
    assert view["day_left"] == 0


def test_pro_caps_when_engine3_exceeds_free() -> None:
    view = quota_view(
        _ledger(local={"2": 0, "3": 3000}, server={"1": 0, "2": 0, "3": 3000}),
        today=date(2026, 9, 26),
    )
    assert view["plan"] == "pro"
    assert view["left3"] == 27_000
    assert view["cap2"] == 300_000
    assert view["day_cap"] == 0


def test_server_yesterday_plus_today_matches_local_month() -> None:
    view = quota_view(
        _ledger(local={"2": 13, "3": 0}, today_local={"2": 3, "3": 0}, server={"1": 0, "2": 10, "3": 0}),
        today=date(2026, 9, 26),
    )
    assert view["used2"] == 13


def test_uk_line_names_remaining_and_used() -> None:
    text = format_dev_quota("uk", quota_view(_ledger(local={"2": 2, "3": 1}), today=date(2026, 9, 26)))
    assert "E2 залишок 24998 з 25000" in text
    assert "використано 2" in text
    assert "E3 залишок 2499 з 2500" in text
    assert "сьогодні 0/500" in text


def test_raw_ocr_keeps_parsed_text_and_extra_overlay() -> None:
    from lockon_bridge.ocr_space import raw_ocr_text

    text = raw_ocr_text(
        {
            "ParsedResults": [
                {
                    "ParsedText": "З преміумом Без преміума\n2 579\n1 406",
                    "TextOverlay": {
                        "Lines": [
                            {"Words": [{"WordText": "З"}, {"WordText": "преміумом"}, {"WordText": "Без"}, {"WordText": "преміума"}]},
                            {"Words": [{"WordText": "2"}, {"WordText": "579"}]},
                            {"Words": [{"WordText": "1"}, {"WordText": "406"}, {"WordText": "9"}]},
                        ]
                    },
                }
            ]
        }
    )
    assert text.startswith("З преміумом Без преміума\n2 579\n1 406")
    assert "TextOverlay:" in text
    assert "1 406 9" in text
    assert "RP " not in text


def test_raw_ocr_does_not_repeat_same_words() -> None:
    from lockon_bridge.ocr_space import raw_ocr_text

    text = raw_ocr_text(
        {
            "ParsedResults": [
                {
                    "ParsedText": "3 преміумом Без преміума\n2 579 1 406\n13 780 8 742",
                    "TextOverlay": {
                        "Lines": [
                            {"Words": [{"WordText": "3"}, {"WordText": "преміумом"}]},
                            {"Words": [{"WordText": "Без"}, {"WordText": "преміума"}]},
                            {"Words": [{"WordText": "2"}, {"WordText": "579"}]},
                            {"Words": [{"WordText": "1"}, {"WordText": "406"}]},
                            {"Words": [{"WordText": "13"}, {"WordText": "780"}]},
                            {"Words": [{"WordText": "8"}, {"WordText": "742"}]},
                        ]
                    },
                }
            ]
        }
    )
    assert text.count("1 406") == 1
    assert "TextOverlay:" not in text
    assert "2 579\n1 406" in text


def test_note_success_and_daily_block(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(
        "lockon_bridge.ocr_quota._usage_path",
        lambda: tmp_path / "ocr_usage.json",
    )
    note_success(2)
    note_success(3)
    view = quota_view()
    assert view["used2"] == 1
    assert view["used3"] == 1
    assert view["today"] == 2
    note_rejection("You may only perform this action upto maximum 500 number of times within 86400 seconds")
    blocked = quota_view()
    assert blocked["day_left"] == 0
    assert blocked["left2"] == 24_999
    assert blocked["left3"] == 2_499
