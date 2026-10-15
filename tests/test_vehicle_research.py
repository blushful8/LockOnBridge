"""Vehicle-research RP uses the official War Thunder debriefing strings."""

from lockon_bridge.ocr_parse import parse_rewards_from_ocr_text, vehicle_research_amount
from lockon_bridge.wt_debrief_labels import (
    MODIFICATION_RESEARCH,
    VEHICLE_RESEARCH,
    VEHICLE_RESEARCH_BONUS,
)
from lockon_bridge.wt_languages import (
    WT_LANGUAGES,
    easyocr_fallback_lang_list,
    easyocr_lang_list,
)

# The screen the user transcribed. Summary grid is 1855/993 and 11377/6944.
# Vehicle research is 1098. Modification research, sitting just above it, is 993.
_USER_SCREEN = """
3 преміумом
Без преміума
Ваше місце в команді: 9
1 855
993
11 377
6 944
Знищено повітряних цілей
3
670-
Допомога у знищенні противника
2
480
ВСЬОГО
862
6 944
Дослідження модифікацій
993
Дослідження техніки
1 098
Час гри
9.04
Тривалість місії
10.22
Вільні очки досліджень
862
"""


def test_official_ukrainian_string_is_the_game_row():
    assert VEHICLE_RESEARCH["uk"] == "Дослідження техніки"


def test_easyocr_model_follows_every_bridge_language():
    from easyocr.config import all_lang_list

    for lang in WT_LANGUAGES:
        primary = easyocr_lang_list(lang.code)
        fallback = easyocr_fallback_lang_list(lang.code)
        assert primary, lang.code
        assert fallback, lang.code
        assert fallback != primary, lang.code
        for code in primary + fallback:
            assert code in all_lang_list, (lang.code, code)


def test_every_bridge_language_has_an_official_vehicle_row():
    missing = [lang.code for lang in WT_LANGUAGES if lang.code not in VEHICLE_RESEARCH]
    assert missing == []


def test_user_screen_sends_vehicle_research_not_the_summary_grid():
    without = parse_rewards_from_ocr_text(_USER_SCREEN, prefer_premium_rewards=False)
    assert without is not None
    assert without.research_points == 1098
    assert without.silver_lions == 6944

    with_premium = parse_rewards_from_ocr_text(_USER_SCREEN, prefer_premium_rewards=True)
    assert with_premium is not None
    assert with_premium.research_points == 1098
    assert with_premium.silver_lions == 11377


def test_modification_row_above_is_not_the_vehicle_amount():
    text = "Дослідження модифікацій\n993\nДослідження техніки\n1 098\n"
    assert vehicle_research_amount(text, prefer_with=False) == 1098


def test_bonus_row_does_not_count_as_vehicle_research():
    bonus = VEHICLE_RESEARCH_BONUS["uk"]
    vehicle = VEHICLE_RESEARCH["uk"]
    text = f"{bonus}\n500\n{vehicle}\n1 098\n"
    assert vehicle_research_amount(text, prefer_with=False) == 1098
    # English "Vehicle research bonus" starts with the vehicle-research label.
    english = f"{VEHICLE_RESEARCH_BONUS['en']}\n500\n"
    assert vehicle_research_amount(english, prefer_with=False) is None


def test_two_numbers_follow_the_premium_column():
    text = "Vehicle research\n1 855\n1 098\n"
    assert vehicle_research_amount(text, prefer_with=True) == 1855
    assert vehicle_research_amount(text, prefer_with=False) == 1098


def test_missing_vehicle_row_does_not_guess_from_the_grid():
    text = (
        "З преміумом\nБез преміума\nВаше місце в команді: 9\n"
        "1 855\n993\n11 377\n6 944\n"
    )
    assert parse_rewards_from_ocr_text(text) is None


def test_official_label_in_every_bridge_language():
    for lang in WT_LANGUAGES:
        label = VEHICLE_RESEARCH[lang.code]
        mods = MODIFICATION_RESEARCH[lang.code]
        # OCR drops the word joiners and the datamine tab escape.
        visible = label.replace("\\t", "").replace("\u200b", "").replace("\t", "")
        text = f"{mods}\n993\n{visible}\n1 098\nSilver Lions\n6 944\n"
        amount = vehicle_research_amount(text, prefer_with=False)
        assert amount == 1098, (lang.code, label, visible)
        report = parse_rewards_from_ocr_text(text, prefer_premium_rewards=False)
        assert report is not None, lang.code
        assert report.research_points == 1098, lang.code
        assert report.silver_lions == 6944, lang.code
