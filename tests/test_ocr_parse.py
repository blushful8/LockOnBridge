from lockon_bridge.ocr_parse import looks_like_desktop_noise, parse_rewards_from_ocr_text


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
    text = "Reward +2,100 RP and +15 000 SL"
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


def test_parses_latinized_ukrainian_premium_columns():
    # Real OCR dump from UA client (Cyrillic mangled to Latin lookalikes).
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


def test_parses_bare_second_premium_header():
    text = "Micifl npoBaneHa 3 npeMiYMOM 9 596' npeMiYMa 1 088 6 127'"
    report = parse_rewards_from_ocr_text(text)
    assert report is not None
    assert report.research_points == 1088
    assert report.silver_lions == 6127


def test_skips_desktop_noise():
    text = (
        "EPIC GAMES Telegram LockOn Bridge nopr HTTP "
        "Microsoft Malware Protection NVIDIA App"
    )
    assert looks_like_desktop_noise(text)
    assert parse_rewards_from_ocr_text(text) is None


def test_returns_none_without_currency():
    assert parse_rewards_from_ocr_text("Player shot down enemy") is None
