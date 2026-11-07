import pytest

from lockon_bridge.lang_labels import LANGUAGE_FIXTURES, MANGLED_LANGUAGE_FIXTURES
from lockon_bridge.ocr_parse import (
    choose_best_report,
    looks_like_desktop_noise,
    parse_rewards_from_ocr_text,
)


def test_parses_english_total_block():
    text = """
    Mission results
    Victory
    Vehicle research 1 250
    Silver Lions 8 400
    Total
    """
    report = parse_rewards_from_ocr_text(text)
    assert report is not None
    assert report.research_points == 1250
    assert report.silver_lions == 8400
    assert report.outcome == "victory"


def test_parses_inline_labels():
    text = "Vehicle research 2 100\nSilver Lions 15 000"
    report = parse_rewards_from_ocr_text(text)
    assert report is not None
    assert report.research_points == 2100
    assert report.silver_lions == 15000


def test_parses_stacked_labels():
    text = """
    Vehicle research
    3 420
    Silver Lions
    12 800
    """
    report = parse_rewards_from_ocr_text(text)
    assert report is not None
    assert report.research_points == 3420
    assert report.silver_lions == 12800


def test_parses_russian_labels():
    text = "Исследование техники 890\nСеребряные львы 4500\nПобеда"
    report = parse_rewards_from_ocr_text(text)
    assert report is not None
    assert report.research_points == 890
    assert report.silver_lions == 4500
    assert report.outcome == "victory"


def test_parses_total_pair():
    text = "Session\nTotal 2 100  7 500\nИсследование техники\n2 100\nContinue"
    report = parse_rewards_from_ocr_text(text)
    assert report is not None
    assert report.research_points == 2100
    assert report.silver_lions == 7500


def test_parses_mangled_5b_without_premium_row():
    """Real OCR: «Без преміума» → «5B npeMiyxa»; must not take with-premium 2927/23574."""
    text = (
        "Haropona 3a yuacTb B Micii: *34% , Baue Micue B KOMaHAi: 3 "
        "3 npeMiYMOM 2 927' 23 574' 3HhUeHO "
        "5B npeMiyxa 1 708 15 902' nponycTMT Bcboro 1 708 15 902'\n"
        "Дослідження техніки\n1 708"
    )
    report = parse_rewards_from_ocr_text(text)
    assert report is not None
    assert report.research_points == 1708
    assert report.silver_lions == 15902


def test_parses_without_row_before_activity_noise():
    """Full-window OCR: without totals then activity times/detail columns (was 58604/1708)."""
    text = (
        "Haropona 3a yuacTb B Micii: *34% , Baue Micue B KOMaHAi: 3 "
        "3 npeMiYMOM 2 927' 23 574' 3HhUeHO nosiTp9Hhx uinei "
        "5B npeMiyxa 1 708 15 902' nponycTMT 6 8 3 6 41 2:58 604 1 708 "
        "11:36 13:02 1 288' 1064' 1 637' 416 15 902' 1 708\n"
        "Дослідження техніки\n1 708"
    )
    report = parse_rewards_from_ocr_text(text)
    assert report is not None
    assert report.research_points == 1708
    assert report.silver_lions == 15902


def test_parses_ua_results_screen_with_cyrillic_z_premium():
    """Real UA screen: «Без З npeMiyM0M 2927 1708 23574 15902» → without 1708/15902."""
    text = (
        "Ваше місце в команді: 3 Місія провалена "
        "Без З npeMiyM0M 2 927' 1 708 23 574 15 902' "
        "Всього 1 708 15 902\nДослідження техніки\n1 708"
    )
    report = parse_rewards_from_ocr_text(text)
    assert report is not None
    assert report.research_points == 1708
    assert report.silver_lions == 15902
    assert report.outcome == "defeat"


def test_rejects_swapped_premium_junk_sixty():
    """en-US OCR lost with-RP and glued «60» from «бойових» → must not return 15902/60."""
    text = (
        "MiciR npoBaneHa Ee3 npeMiyxa 3 npeMiyM0M 1 708 23 574 15 902' "
        "60hOBhX 3aBnaHHA 604 1 708' 15 902' 1 708'\nДослідження техніки\n1 708"
    )
    report = parse_rewards_from_ocr_text(text)
    assert report is not None
    assert report.research_points == 1708
    assert report.silver_lions == 15902


def test_parses_sl_columns_swapped_by_ocr():
    """RU OCR: RP column then SL column with with/without SL order flipped."""
    text = (
        "Місія провалена Ваше місце в команді: 3 "
        "З npeMiyM0M 2 927' 1 708 15 902' 23 574 "
        "Всього 1 708 15 902\nДослідження техніки\n1 708"
    )
    report = parse_rewards_from_ocr_text(text)
    assert report is not None
    assert report.research_points == 1708
    assert report.silver_lions == 15902


def test_parses_short_arcade_without_premium_grid():
    """Real UA screen: place 13 then 672/336/4608/2912 → without 336/2912 (not 4608/29128)."""
    text = (
        "MiciR npoBaneHa ApaAHi 60i "
        "3 npeMiyM0M Sea npexiyxa Baue Micue B KOMaHAi: 13 "
        "672 336 4 608 2 912 "
        "nocqrHeHHA Bcsoro 748 2 912 336 336 336 "
        "nocninxeHHR M0A14$iKauii\nДослідження техніки\n336"
    )
    report = parse_rewards_from_ocr_text(text)
    assert report is not None
    assert report.research_points == 336
    assert report.silver_lions == 2912


def test_amount_tokenizer_does_not_glue_three_groups():
    from lockon_bridge.ocr_parse import _amounts_in

    # Left part may be 1–2 digits + thousands; three RP-sized cells stay separate.
    assert _amounts_in("672 336 4 608 2 912") == [672, 336, 4608, 2912]
    assert _amounts_in("1 788 12 446") == [1788, 12446]


def test_team_place_stripped_before_premium_grid():
    text = (
        "3 npeMiyM0M Sea npexiyxa Baue Micue B KOMaHAi: 13 "
        "672 336 4 608 2 912\nДослідження техніки\n336"
    )
    report = parse_rewards_from_ocr_text(text)
    assert report is not None
    assert report.research_points == 336
    assert report.silver_lions == 2912


def test_vsego_ignores_combat_sl_pair_as_totals():
    """Air-kills SL + fatal SL must not beat the real Всього pair."""
    text = (
        "Bawe Micue B KOMaHAi: 3 3 npeMiyM0M 3064 18 926 "
        "3HnueH0 2 388 3 109 критичні 389 "
        "Всього 1 779 1 783 12 446 1 909 1 276\nДослідження техніки\n1 909"
    )
    report = parse_rewards_from_ocr_text(text)
    assert report is not None
    assert report.research_points == 1909
    assert report.silver_lions == 12446


def test_choose_best_prefers_full_totals_over_partial():
    partial = "3HnueH0 noBiTpRHX 5 143 2 388 3 109"
    full = (
        "Sea npexiyxa 1 788 12 446 Всього 1 779 1 783 12 446 1 788 1 909 1 276\n"
        "Дослідження техніки\n1 788"
    )
    best = choose_best_report(
        [
            (partial, parse_rewards_from_ocr_text(partial)),
            (full, parse_rewards_from_ocr_text(full)),
        ]
    )
    assert best is not None
    assert best[1].research_points == 1788
    assert best[1].silver_lions == 12446


def test_parses_column_major_premium_table():
    """UA results: labels then RP column then SL column (2108/1128/11221/7804)."""
    text = (
        "Місія провалена Ваше місце в команді: 5 "
        "З преміумом Без преміуму 2 108 1 128 11 221 7 804 "
        "Всього 1 128 7 804\nДослідження техніки\n1 128"
    )
    report = parse_rewards_from_ocr_text(text)
    assert report is not None
    assert report.research_points == 1128
    assert report.silver_lions == 7804
    assert report.outcome == "defeat"


def test_parses_mangled_column_major_from_real_ocr():
    text = (
        "Ваше в 5 днагорода за участь в Micii: , 3 npeMiYM0M без npeMiYHa "
        "2 108' 1128' 11 221' 7804 знищено Всього 1 128' 7 804'\nДослідження техніки\n1 128"
    )
    report = parse_rewards_from_ocr_text(text)
    assert report is not None
    assert report.research_points == 1128
    assert report.silver_lions == 7804


def test_parses_latinized_ukrainian_premium_columns():
    text = (
        "Bepciq 2.59.0.13 Micifl npoBaneHa APGAHi 60i, "
        "Haropona 3a yuacTb B Micii: +34% , +20%' "
        "3 npeMiYM0M 9 426' be3 npeMiYMa 1 088 6 016' "
        "nporpec nocninxeHb\nДослідження техніки\n1 088"
    )
    report = parse_rewards_from_ocr_text(text)
    assert report is not None
    assert report.research_points == 1088
    assert report.silver_lions == 6016
    assert report.outcome == "defeat"


def test_parses_victory_boost_line():
    text = "Haropona 3a nepeMory: +100% , +47%' 1 148 2 732'\nДослідження техніки\n1 148"
    report = parse_rewards_from_ocr_text(text)
    assert report is not None
    assert report.research_points == 1148
    assert report.silver_lions == 2732
    assert report.outcome == "victory"


def test_parses_bare_second_premium_header():
    text = "Micifl npoBaneHa 3 npeMiYMOM 9 596' npeMiYMa 1 088 6 127'\nДослідження техніки\n1 088"
    report = parse_rewards_from_ocr_text(text)
    assert report is not None
    assert report.research_points == 1088
    assert report.silver_lions == 6127


def test_alone_with_premium_not_banked_as_without():
    """Phone has premium OFF — alone «З преміумом RP SL» must not publish."""
    text = "З преміумом 436 4172"
    assert parse_rewards_from_ocr_text(text, prefer_premium_rewards=False) is None
    report = parse_rewards_from_ocr_text(text, prefer_premium_rewards=True)
    assert report is not None
    assert (report.research_points, report.silver_lions) == (436, 4172)
    best = choose_best_report(
        [(text, parse_rewards_from_ocr_text(text, prefer_premium_rewards=False))],
        prefer_premium_rewards=False,
    )
    assert best is None


def test_single_column_onscreen_header_without_premium_setting():
    """
    Real EasyOCR dump (2026-11-06 shot burst): no with/without premium columns.

    Screen shows RP 12 542 / SL 36 288. Settings may still say without-premium —
    bank the visible totals anyway when the vehicle-research amount is missing.
    """
    text = (
        "12 542\n"
        "Ваше місце в команді: 3\n"
        "36 288\n"
        "Знищено повітряних цілей\n"
        "3\n"
        "Критичні ушкодження\n"
        "Ушкодження\n"
        "1\n"
        "Посадки\n"
        "ЗЛЬОТИ\n"
        "2\n"
        "Час активності\n"
        "8.33\n"
        "Активність\n"
        "89%\n"
        "Бонус навичок\n"
        "Нагорода за перемогу\n"
        "9 193\n"
        "ВсЬОГо\n"
        "12 542\n"
        "Дослідження модифікацій\n"
        "Дослідження техніки\n"
        "Час гри\n"
        "14.22\n"
        "Тривалість мicii\n"
        "14.38\n"
        "Вільні очки досЛідЖень\n"
        "Ембрсоаг\n"
        "FrпRппппҐаE\n"
    )
    for prefer in (False, True, None):
        report = parse_rewards_from_ocr_text(text, prefer_premium_rewards=prefer)
        assert report is not None, prefer
        assert report.research_points == 12542, prefer
        assert report.silver_lions == 36288, prefer
        assert report.confidence >= 0.7


def test_single_column_vehicle_research_beats_header_totals():
    """
    2026-11-06 21:45 battle: header/mods/Всього = 9 478, but «Дослідження техніки» = 948.

    Bridge must bank vehicle-research RP (efficiency penalty), not the summary total.
    """
    text = (
        "9 478\n"
        "Ваше місце в команді: 1\n"
        "35 683\n"
        "Знищено повітряних цілей\n"
        "2\n"
        "Фатальні пошкодження\n"
        "2\n"
        "Критичні ушкодження\n"
        "2\n"
        "Ушкодження\n"
        "5\n"
        "Посадки\n"
        "1\n"
        "Зльоти\n"
        "2\n"
        "Час активності\n"
        "2.13\n"
        "Активність\n"
        "83%\n"
        "Бонус навичок\n"
        "1 164\n"
        "Нагорода за участь в місії\n"
        "6 513\n"
        "Досягнення\n"
        "200\n"
        "Всього\n"
        "9 478\n"
        "35 683\n"
        "Дослідження модифікацій\n"
        "9 478\n"
        "Дослідження техніки\n"
        "948\n"
        "Час гри\n"
        "16.21\n"
        "Тривалість місії\n"
        "16.48\n"
        "Вільні очки досліджень\n"
        "8 314\n"
    )
    for prefer in (False, True):
        report = parse_rewards_from_ocr_text(text, prefer_premium_rewards=prefer)
        assert report is not None, prefer
        assert report.research_points == 948, prefer
        assert report.silver_lions == 35683, prefer


def test_single_column_vehicle_research_english_and_german():
    """Same layout in EN/DE — official Gaijin strings, not UA-only heuristics."""
    en = (
        "9 478\nYour place in the team: 1\n35 683\n"
        "Air targets destroyed\n2\nTotal\n9 478\n35 683\n"
        "Modifications research\n9 478\nVehicle research\n948\n"
        "Convertible RP\n8 314\n"
    )
    de = (
        "9 478\nPlatz im Team: 1\n35 683\n"
        "Gesamt\n9 478\n35 683\n"
        "Modifikationsforschung\n9 478\nFahrzeugforschung\n948\n"
        "Konvertierbare FP\n8 314\n"
    )
    for text in (en, de):
        report = parse_rewards_from_ocr_text(text, prefer_premium_rewards=False)
        assert report is not None, text[:40]
        assert report.research_points == 948
        assert report.silver_lions == 35683


def test_wide_parse_zone_keeps_vehicle_research_amount_on_shot6():
    """Crop must include the 948 RP cell that the old 0.58 right edge clipped."""
    from pathlib import Path

    from PIL import Image

    from lockon_bridge.roi_calib import default_parse_zone

    shot = (
        Path.home()
        / "AppData"
        / "Local"
        / "LockOnBridge"
        / "captures"
        / "20261106_214540_shot6.png"
    )
    if not shot.is_file():
        pytest.skip("shot6 capture missing")
    image = Image.open(shot)
    w, h = image.size
    zone = default_parse_zone()
    # Vehicle-research amount sits near ~0.60–0.65 of client width on 2560px.
    assert int(zone.right * w) >= int(0.66 * w)
    crop = image.crop(
        (
            int(zone.left * w),
            int(zone.top * h),
            int(zone.right * w),
            int(zone.bottom * h),
        )
    )
    assert crop.width > int(0.50 * w)


def test_rejects_scoreboard_kd_as_rewards():
    """Real battle-end OCR: participation boost + team board ratios, no premium totals."""
    text = (
        "города за участь в Micii: +34% ' , +20%9 з 5 4 2 0.954 0.954 0.854 0.645 "
        "1.27 1 1 2052 1891 1531 1451 1385 1299 1252 1083 — 1040 997 867 576 406 "
        "ЧКИ:0, ЗОНИ:0 Очки: 3640, Зон и: О"
    )
    assert parse_rewards_from_ocr_text(text) is None
    latin = (
        "ropona 3a yuacTb B Micii: +34% T, +20%• 3 5 4 2 0.954 0.954 0.854 0.645 "
        "1.27 1 1 2052 1891 1531 1451 1385 1299 1252 1083 1040 997"
    )
    assert parse_rewards_from_ocr_text(latin) is None


def test_rejects_leading_zero_ratio_amounts():
    assert parse_rewards_from_ocr_text("Haropona 3a yuac: +34% , +20% 0.954 0.954") is None


def test_skips_desktop_noise():
    text = (
        "EPIC GAMES Telegram LockOn Bridge nopr HTTP "
        "Microsoft Malware Protection NVIDIA App"
    )
    assert looks_like_desktop_noise(text)
    assert parse_rewards_from_ocr_text(text) is None


def test_returns_none_without_currency():
    assert parse_rewards_from_ocr_text("Player shot down enemy") is None


def test_all_language_fixtures_parse():
    for lang, labels in LANGUAGE_FIXTURES.items():
        premium = (
            f"{labels['victory']}\n"
            f"{labels['with']} 9 999\n"
            f"{labels['without']} 1 250 8 400\n"
        )
        report = parse_rewards_from_ocr_text(premium)
        assert report is not None, lang
        assert report.research_points == 1250, lang
        assert report.silver_lions == 8400, lang

        labeled = f"{labels['rp']} 2 100\n{labels['sl']} 7 500\n"
        report2 = parse_rewards_from_ocr_text(labeled)
        assert report2 is not None, lang
        assert report2.research_points == 2100, lang
        assert report2.silver_lions == 7500, lang


def test_mangled_non_en_ru_fixtures_parse():
    """Wrong OCR pack strips diacritics / Latinizes — same failure class as UA."""
    for lang, labels in MANGLED_LANGUAGE_FIXTURES.items():
        premium = (
            f"{labels['victory']}\n"
            f"{labels['with']} 9 999\n"
            f"{labels['without']} 1 250 8 400\n"
        )
        report = parse_rewards_from_ocr_text(premium)
        assert report is not None, f"mangled premium {lang}"
        assert report.research_points == 1250, lang
        assert report.silver_lions == 8400, lang

        labeled = f"{labels['rp']} 2 100\n{labels['sl']} 7 500\n"
        report2 = parse_rewards_from_ocr_text(labeled)
        assert report2 is not None, f"mangled labels {lang}: {labeled!r}"
        assert report2.research_points == 2100, lang
        assert report2.silver_lions == 7500, lang


def test_choose_best_ignores_bad_engine():
    bad = "Chinese OCR garbage 银 1 2 3 random"
    good = "Without premium 1 088 6 016\nVehicle research\n1 088"
    chinese_only = "有高级账号 9 999 无高级账号 1 250 8 400"
    best = choose_best_report(
        [
            (bad, parse_rewards_from_ocr_text(bad)),
            (good, parse_rewards_from_ocr_text(good)),
            (chinese_only, parse_rewards_from_ocr_text(chinese_only)),
        ]
    )
    assert best is not None
    _text, report = best
    # Either the latinized UA without-premium or Chinese fixture — both valid pairs.
    assert report.research_points in (1088, 1250)
    assert report.silver_lions in (6016, 8400)


def test_ocrspace_garbled_earned_line():
    """OCR.space often mangles «Зароблено» and glues icon digits onto amounts."""
    text = "Бонус навичок\n3apooneH0:11 8490,3 0129\nАктивність: 93%\nДослідження техніки\n3 012"
    report = parse_rewards_from_ocr_text(text)
    assert report is not None
    assert report.silver_lions == 11849
    assert report.research_points == 3012
    assert report.source == "ocrspace"
    assert report.confidence >= 0.9


def test_select_structured_prefers_earned_overlay():
    from lockon_bridge.layout_ocr import Box, OverlayLine, select_structured_text

    lines = [
        OverlayLine("Бонус навичок", Box(0, 0, 10, 10)),
        OverlayLine("Зароблено: 11 849, 3 012", Box(0, 20, 200, 40)),
        OverlayLine("Активність: 93%", Box(0, 50, 100, 60)),
    ]
    text = select_structured_text("noise", lines)
    assert "Зароблено" in text
    assert "11 849" in text


def test_engine3_team_place_grid_without_premium():
    text = (
        "3 преміумом\n"
        "Без преміума\n"
        "Ваше місце в команді: 9\n"
        "3 238\n"
        "1 811\n"
        "8 808\n"
        "5 671\n"
        "Дослідження техніки\n"
        "3 238\n"
        "1 811\n"
    )
    report = parse_rewards_from_ocr_text(text, prefer_premium_rewards=False)
    assert report is not None
    assert report.research_points == 1811
    assert report.silver_lions == 5671
    assert report.source == "ocrspace"
    assert report.confidence >= 0.9


def test_engine3_team_place_grid_with_premium():
    text = (
        "3 преміумом\n"
        "Без преміума\n"
        "Ваше місце в команді: 9\n"
        "3 238\n"
        "1 811\n"
        "8 808\n"
        "5 671\n"
        "Дослідження техніки\n"
        "3 238\n"
        "1 811\n"
    )
    report = parse_rewards_from_ocr_text(text, prefer_premium_rewards=True)
    assert report is not None
    assert report.research_points == 3238
    assert report.silver_lions == 8808


def test_four_amount_lines_without_place_are_the_grid_not_two_rp_columns():
    """Mid-animation zone text has no «місце» line yet. Do not swap the RP cells."""
    text = "Без преміума\n1 263\n559\n3748\n2313\nДослідження техніки\n559\n"
    report = parse_rewards_from_ocr_text(text, prefer_premium_rewards=False)
    assert report is not None
    assert report.research_points == 559
    assert report.silver_lions == 2313
    assert report.source == "ocrspace"
    assert report.confidence >= 0.9


def test_short_grid_and_trailing_dash_still_make_a_pair():
    text = "Без преміума\n40\n22\n302-\n202\nДослідження техніки\n22\n"
    report = parse_rewards_from_ocr_text(text, prefer_premium_rewards=False)
    assert report is not None
    assert report.research_points == 22
    assert report.silver_lions == 202


def test_phone_preview_uses_latest_report_fields():
    pytest.importorskip("tkinter")
    from lockon_bridge.ocr_parse import parse_rewards_from_ocr_text
    from lockon_bridge.parse_zone_ui import _phone_view

    text = (
        "3 преміумом\nБез преміума\nВаше місце в команді: 9\n"
        "3 238\n1 811\n8 808\n5 671\nДослідження техніки\n1 811\n"
    )
    report = parse_rewards_from_ocr_text(text, prefer_premium_rewards=False)
    view = _phone_view(report, premium=False)
    assert "RP 1811" in view
    assert "SL 5671" in view
    assert '"researchPoints": 1811' in view
    assert '"silverLions": 5671' in view

