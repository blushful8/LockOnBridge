"""Unit tests for locale-agnostic Messages clipboard battle parse."""

from __future__ import annotations

from lockon_bridge.battle_msg_parse import (
    detect_battle_outcome,
    detect_outcome_from_frame,
    looks_like_battle_msg_clipboard,
    parse_battle_msg_clipboard,
)

# Representative UK paste: Total (3-amount) wins over Earned (2-amount).
_UK_PASTE = """\
Детальний звіт
Сесія: 744a9c1e2f00
Перемога

Зароблено: 8742 СЛ, 1445 ВОД
Всього: 7535 СЛ, 1445 ВОД, 1806 ОД

Активність
Натисніть Ctrl+C щоб скопіювати звіт до буфера обміну
"""

_EN_PASTE = """\
Detailed report
Session: abcdef123456
Victory

Earned: 8 742 SL, 1 445 Free RP
Total: 7 535 SL, 1 445 Free RP, 1 806 RP
"""

_RU_PASTE = """\
Подробный отчёт
Сессия: deadbeef00
Победа

Заработано: 8742 СЛ, 1445 ВОД
Всего: 7535 СЛ, 1445 ВОД, 1806 ОД
"""

# Real user dump (defeat Vietnam): net Total SL+ВОД; third ОД is module research.
_USER_DEFEAT_FOOTER = """\
Поразка в [Панування] В'єтнам місій!

Знищення авіації                       6    2359 СЛ     306 ОД
    5:34     P-63C-5(Франція)    Стандарт (12.7мм)    Ju.87D-3                     117 очків місії                             — (повна нагорода)               720 СЛ    53 + (Підсилювач)27 = 80 ОД
    11:18    P-63C-5(Франція)    Стандарт (12.7мм)    Tempest Mk.V (Vickers P)      10 очків місії    Іншим гравцем (без нагороди)     0 СЛ      0 ОД

Зароблено: 11849 СЛ, 3012 ВОД
Активність: 93%
Автоматичний ремонт усієї техніки: -676 СЛ
Автоматична закупівля боєприпасів та «Поповнення екіпажу»: -260 СЛ

Сесія: 745ebb9000e9fcf
Всього: 10913 СЛ, 3012 ВОД, 4657 ОД
"""


def test_uk_total_not_earned() -> None:
    report = parse_battle_msg_clipboard(_UK_PASTE)
    assert report is not None
    assert report.silver_lions == 7535
    assert report.research_points == 1445
    assert report.source == "clipboard-msg"
    assert report.confidence >= 0.95
    # Cyrillic outcome is not used — undecided without frame color / latin.
    assert report.outcome == "undecided"
    # Total (3-amount) means finished detail — not provisional.
    assert report.provisional is False
    assert report.session_id == "744a9c1e2f00"


def test_user_dump_total_sl_free_rp() -> None:
    report = parse_battle_msg_clipboard(_USER_DEFEAT_FOOTER)
    assert report is not None
    assert report.silver_lions == 10913
    assert report.research_points == 3012
    assert report.session_id == "745ebb9000e9fcf"
    # Must not pick earned 11849 or module RP 4657.
    assert report.silver_lions != 11849
    assert report.research_points != 4657


def test_zero_amounts_allowed() -> None:
    text = """\
Session hex below
abcdef12345678
Total: 0 SL, 0 Free RP, 0 RP
"""
    report = parse_battle_msg_clipboard(text)
    assert report is not None
    assert report.silver_lions == 0
    assert report.research_points == 0
    assert report.session_id == "abcdef12345678"


def test_two_amount_footer_when_no_total() -> None:
    text = """\
abcdef12345678
Earned: 1200 SL, 300 Free RP
"""
    report = parse_battle_msg_clipboard(text)
    assert report is not None
    assert report.silver_lions == 1200
    assert report.research_points == 300


def test_bare_hex_session_without_label() -> None:
    text = """\
Some header
745ebb9000e9fcf
10913 3012 4657
"""
    report = parse_battle_msg_clipboard(text)
    assert report is not None
    assert report.session_id == "745ebb9000e9fcf"
    assert report.silver_lions == 10913
    assert report.research_points == 3012


def test_provisional_without_latin_outcome() -> None:
    """2-amount Earned only (no Total) + no outcome → provisional."""
    text = """\
Детальний звіт
Сесія: abcdef123456

Зароблено: 1200 СЛ, 300 ВОД
"""
    report = parse_battle_msg_clipboard(text)
    assert report is not None
    assert report.outcome == "undecided"
    assert report.provisional is True
    assert report.session_id == "abcdef123456"
    assert report.silver_lions == 1200
    assert report.research_points == 300


def test_total_finished_not_provisional() -> None:
    text = """\
Сесія: abcdef123456
Всього: 1000 СЛ, 300 ВОД, 400 ОД
"""
    report = parse_battle_msg_clipboard(text)
    assert report is not None
    assert report.outcome == "undecided"
    assert report.provisional is False
    assert report.silver_lions == 1000
    assert report.research_points == 300


def test_latin_outcome_soft() -> None:
    assert detect_battle_outcome("Victory in mission") == "victory"
    assert detect_battle_outcome("Defeat somewhere") == "defeat"
    assert detect_battle_outcome("Mopaska y micii") == "undecided"
    assert detect_battle_outcome("Поразка в місії") == "undecided"


def test_outcome_from_frame_color() -> None:
    from PIL import Image

    def _paint_badge(img: Image.Image, color: tuple[int, int, int]) -> None:
        w, h = img.size
        # Same ROI fractions as detect_outcome_from_frame.
        x0, y0 = int(w * 0.22), int(h * 0.12)
        x1, y1 = int(w * 0.72), int(h * 0.28)
        for x in range(x0, x1):
            for y in range(y0, y1):
                img.putpixel((x, y), color)

    green = Image.new("RGB", (800, 600), (40, 40, 40))
    _paint_badge(green, (40, 200, 50))
    assert detect_outcome_from_frame(green) == "victory"

    red = Image.new("RGB", (800, 600), (40, 40, 40))
    _paint_badge(red, (210, 45, 40))
    assert detect_outcome_from_frame(red) == "defeat"

    gray = Image.new("RGB", (800, 600), (50, 50, 50))
    assert detect_outcome_from_frame(gray) == "undecided"


def test_en_total() -> None:
    report = parse_battle_msg_clipboard(_EN_PASTE)
    assert report is not None
    assert report.silver_lions == 7535
    assert report.research_points == 1445
    assert report.outcome == "victory"
    assert report.provisional is False


def test_ru_total() -> None:
    report = parse_battle_msg_clipboard(_RU_PASTE)
    assert report is not None
    assert report.silver_lions == 7535
    assert report.research_points == 1445


def test_total_only_accepted() -> None:
    text = "deadbeef00aabb\nВсього: 7535 СЛ, 1445 ВОД, 1806 ОД\n"
    report = parse_battle_msg_clipboard(text)
    assert report is not None
    assert report.silver_lions == 7535
    assert report.research_points == 1445


def test_ocr_mangled_total_line() -> None:
    """Panel OCR paints junk on Total: @, %, trailing icon digit."""
    text = """
Cecia: 744dbc000099c89
Bcboro: 7 535@, 14459, 1 8069
"""
    report = parse_battle_msg_clipboard(text)
    assert report is not None
    assert report.silver_lions == 7535
    assert report.research_points == 1445
    assert report.session_id == "744dbc000099c89"


def test_five_digit_sl_not_unglued() -> None:
    """Real 5-digit SL (earned/total) must not lose a trailing digit."""
    text = """
deadbeef00aabb
Всього: 10913 СЛ, 3012 ВОД, 4657 ОД
"""
    report = parse_battle_msg_clipboard(text)
    assert report is not None
    assert report.silver_lions == 10913
    assert report.research_points == 3012


def test_session_hex_digits_not_currency() -> None:
    text = """
Сесія: 745ebb9000e9fcf
Всього: 10913 СЛ, 3012 ВОД, 4657 ОД
"""
    report = parse_battle_msg_clipboard(text)
    assert report is not None
    assert report.silver_lions == 10913
    assert report.research_points == 3012
    assert report.session_id == "745ebb9000e9fcf"
    assert report.silver_lions != 745


def test_total_line_glued_ctrl_c_junk() -> None:
    """OCR often glues the Ctrl+C hint onto the Total line → 4+ amounts."""
    text = """
Cecia: 745ebb9000e9fcf
Bcboro: 10 913, 3 012%, 4 657% Hatucuitb Ctrl+C ana Toro, Wo6 ckoniioBaTH 6010
"""
    report = parse_battle_msg_clipboard(text)
    assert report is not None
    assert report.silver_lions == 10913
    assert report.research_points == 3012
    assert report.session_id == "745ebb9000e9fcf"
