"""Offline parse checks for user-supplied results screenshots (OCR text dumps)."""

from __future__ import annotations

from lockon_bridge.layout_ocr import Box, OverlayLine, select_structured_text
from lockon_bridge.ocr_parse import parse_rewards_from_ocr_text

# Column-split OCR.space dumps observed on tests/fixtures/results/* (demo key).
_FIXTURE_TEXTS: list[tuple[str, int, int]] = [
    (
        # fail_rp555_sl2040.jpg
        "Всього\nДоспідження\nЗ преміум\n11109\nБез преміума\n5559\n2 040\n148\n596",
        555,
        2040,
    ),
    (
        # win_rp1473_sl11834.png
        "З преміумом\n2 946\n18 739\nБез преміуму\n1 473\n11 834\nВсього\n1 473\n11 834",
        1473,
        11834,
    ),
    (
        # abort_rp383_sl4052.jpg
        "Попередні результати\nЗ преміумом\n766\n6 195\nБез преміуму\n3839\n4 052\nВсього\n383\n4 052",
        383,
        4052,
    ),
    (
        # win_rp1078_sl5233.jpg
        "З преміумом\nБез преміуму\n1078\n5233\nВсього\n1078\n5233",
        1078,
        5233,
    ),
    (
        # win_rp1811_sl5671.jpg — Total line alone (no without-premium header)
        "Всього 1 811 5 671\nДослідження модифікацій 1 811",
        1811,
        5671,
    ),
]


def test_fixture_column_split_texts():
    for text, rp, sl in _FIXTURE_TEXTS:
        report = parse_rewards_from_ocr_text(text)
        assert report is not None, text[:60]
        assert report.research_points == rp, (text[:40], report.research_points, rp)
        assert report.silver_lions == sl, (text[:40], report.silver_lions, sl)


def test_overlay_select_keeps_without_premium_amounts():
    lines = [
        OverlayLine("З преміум", Box(10, 10, 80, 30)),
        OverlayLine("11109", Box(200, 10, 260, 30)),
        OverlayLine("Без преміума", Box(10, 40, 120, 60)),
        OverlayLine("5559", Box(200, 40, 250, 60)),
        OverlayLine("2 040", Box(200, 65, 260, 85)),
        OverlayLine("Всього", Box(10, 200, 80, 220)),
        OverlayLine("555", Box(200, 200, 240, 220)),
        OverlayLine("2040", Box(200, 225, 250, 245)),
    ]
    text = select_structured_text("noise", lines)
    report = parse_rewards_from_ocr_text(text)
    assert report is not None
    assert report.research_points == 555
    assert report.silver_lions == 2040
