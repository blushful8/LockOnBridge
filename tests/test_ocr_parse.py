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
    Research Points 1 250
    Silver Lions 8 400
    Total
    """
    report = parse_rewards_from_ocr_text(text)
    assert report is not None
    assert report.research_points == 1250
    assert report.silver_lions == 8400
    assert report.outcome == "victory"


def test_parses_inline_labels():
    text = "Reward +2,100 Research Points and +15 000 Silver Lions"
    report = parse_rewards_from_ocr_text(text)
    assert report is not None
    assert report.research_points == 2100
    assert report.silver_lions == 15000


def test_parses_stacked_labels():
    text = """
    Research Points
    3 420
    Silver Lions
    12 800
    """
    report = parse_rewards_from_ocr_text(text)
    assert report is not None
    assert report.research_points == 3420
    assert report.silver_lions == 12800


def test_parses_russian_labels():
    text = "Очки исследований 890\nСеребряные львы 4500\nПобеда"
    report = parse_rewards_from_ocr_text(text)
    assert report is not None
    assert report.research_points == 890
    assert report.silver_lions == 4500
    assert report.outcome == "victory"


def test_parses_total_pair():
    text = "Session\nTotal 2 100  7 500\nContinue"
    report = parse_rewards_from_ocr_text(text)
    assert report is not None
    assert report.research_points == 2100
    assert report.silver_lions == 7500


def test_parses_mangled_5b_without_premium_row():
    """Real OCR: «Без преміума» → «5B npeMiyxa»; must not take with-premium 2927/23574."""
    text = (
        "Haropona 3a yuacTb B Micii: *34% , Baue Micue B KOMaHAi: 3 "
        "3 npeMiYMOM 2 927' 23 574' 3HhUeHO "
        "5B npeMiyxa 1 708 15 902' nponycTMT Bcboro 1 708 15 902'"
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
        "11:36 13:02 1 288' 1064' 1 637' 416 15 902' 1 708"
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
        "Всього 1 708 15 902"
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
        "60hOBhX 3aBnaHHA 604 1 708' 15 902' 1 708'"
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
        "Всього 1 708 15 902"
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
        "nocninxeHHR M0A14$iKauii"
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
        "672 336 4 608 2 912"
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
        "Всього 1 779 1 783 12 446 1 909 1 276"
    )
    report = parse_rewards_from_ocr_text(text)
    assert report is not None
    assert report.research_points == 1909
    assert report.silver_lions == 12446


def test_choose_best_prefers_full_totals_over_partial():
    partial = "3HnueH0 noBiTpRHX 5 143 2 388 3 109"
    full = (
        "Sea npexiyxa 1 788 12 446 Всього 1 779 1 783 12 446 1 788 1 909 1 276"
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
        "Всього 1 128 7 804"
    )
    report = parse_rewards_from_ocr_text(text)
    assert report is not None
    assert report.research_points == 1128
    assert report.silver_lions == 7804
    assert report.outcome == "defeat"


def test_parses_mangled_column_major_from_real_ocr():
    text = (
        "Ваше в 5 днагорода за участь в Micii: , 3 npeMiYM0M без npeMiYHa "
        "2 108' 1128' 11 221' 7804 знищено Всього 1 128' 7 804'"
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
        "nporpec nocninxeHb"
    )
    report = parse_rewards_from_ocr_text(text)
    assert report is not None
    assert report.research_points == 1088
    assert report.silver_lions == 6016
    assert report.outcome == "defeat"


def test_parses_victory_boost_line():
    text = "Haropona 3a nepeMory: +100% , +47%' 1 148 2 732'"
    report = parse_rewards_from_ocr_text(text)
    assert report is not None
    assert report.research_points == 1148
    assert report.silver_lions == 2732
    assert report.outcome == "victory"


def test_parses_bare_second_premium_header():
    text = "Micifl npoBaneHa 3 npeMiYMOM 9 596' npeMiYMa 1 088 6 127'"
    report = parse_rewards_from_ocr_text(text)
    assert report is not None
    assert report.research_points == 1088
    assert report.silver_lions == 6127


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
    good = "Without premium 1 088 6 016"
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
