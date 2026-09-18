from lockon_bridge.ocr_parse import parse_rewards_from_ocr_text


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


def test_returns_none_without_currency():
    assert parse_rewards_from_ocr_text("Player shot down enemy") is None
