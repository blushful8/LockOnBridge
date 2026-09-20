"""Unit tests for locale-agnostic Messages clipboard battle parse."""

from __future__ import annotations

from lockon_bridge.battle_msg_parse import (
    looks_like_battle_msg_clipboard,
    parse_battle_msg_clipboard,
)

# Representative UK paste (user sample): earned two-amount vs total three-amount.
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


def test_uk_earned_not_total() -> None:
    report = parse_battle_msg_clipboard(_UK_PASTE)
    assert report is not None
    assert report.silver_lions == 8742
    assert report.research_points == 1445
    assert report.source == "clipboard-msg"
    assert report.confidence >= 0.95
    assert report.outcome == "victory"


def test_en_earned() -> None:
    report = parse_battle_msg_clipboard(_EN_PASTE)
    assert report is not None
    assert report.silver_lions == 8742
    assert report.research_points == 1445


def test_ru_earned() -> None:
    report = parse_battle_msg_clipboard(_RU_PASTE)
    assert report is not None
    assert report.silver_lions == 8742
    assert report.research_points == 1445


def test_total_only_rejected() -> None:
    text = "Всього: 7535 СЛ, 1445 ВОД, 1806 ОД\n"
    assert parse_battle_msg_clipboard(text) is None


def test_looks_like_clipboard() -> None:
    assert looks_like_battle_msg_clipboard(_UK_PASTE)
    assert not looks_like_battle_msg_clipboard("hello world")
