"""War Thunder UI languages ↔ Windows OCR / Tesseract codes."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class WtLanguage:
    code: str  # short id used in settings
    label_en: str
    label_uk: str
    # Preferred Windows.Media.Ocr tags (may be empty / unsupported).
    windows_tags: tuple[str, ...]
    # Tesseract traineddata codes (ISO 639-2 / tessdata names).
    tesseract_langs: tuple[str, ...]


# Official WT FAQ languages + common Steam extras we already parse.
WT_LANGUAGES: tuple[WtLanguage, ...] = (
    WtLanguage("en", "English", "Англійська", ("en-US", "en-GB"), ("eng",)),
    WtLanguage("uk", "Ukrainian", "Українська", ("ru",), ("ukr", "rus", "eng")),  # no Win uk OCR
    WtLanguage("ru", "Russian", "Російська", ("ru",), ("rus", "eng")),
    WtLanguage("de", "German", "Німецька", ("de-DE",), ("deu", "eng")),
    WtLanguage("fr", "French", "Французька", ("fr-FR",), ("fra", "eng")),
    WtLanguage("es", "Spanish", "Іспанська", ("es-ES",), ("spa", "eng")),
    WtLanguage("it", "Italian", "Італійська", ("it-IT",), ("ita", "eng")),
    WtLanguage("pl", "Polish", "Польська", ("pl-PL",), ("pol", "eng")),
    WtLanguage("pt", "Portuguese", "Португальська", ("pt-BR",), ("por", "eng")),
    WtLanguage("cs", "Czech", "Чеська", ("cs-CZ",), ("ces", "eng")),
    WtLanguage("tr", "Turkish", "Турецька", ("tr-TR",), ("tur", "eng")),
    WtLanguage("ja", "Japanese", "Японська", ("ja", "ja-JP"), ("jpn", "eng")),
    WtLanguage("ko", "Korean", "Корейська", ("ko", "ko-KR"), ("kor", "eng")),
    WtLanguage("zh", "Chinese", "Китайська", ("zh-Hans", "zh-CN", "zh-Hant", "zh-TW"), ("chi_sim", "chi_tra", "eng")),
    WtLanguage("hu", "Hungarian", "Угорська", ("hu-HU",), ("hun", "eng")),
    WtLanguage("ro", "Romanian", "Румунська", ("ro-RO",), ("ron", "eng")),
    WtLanguage("be", "Belarusian", "Білоруська", ("ru",), ("bel", "rus", "eng")),
    WtLanguage("sr", "Serbian", "Сербська", ("sr-Cyrl-RS",), ("srp", "srp_latn", "eng")),
)

_BY_CODE = {lang.code: lang for lang in WT_LANGUAGES}


def get_wt_language(code: str) -> WtLanguage:
    key = (code or "en").strip().lower()
    if key.startswith("zh"):
        key = "zh"
    return _BY_CODE.get(key, _BY_CODE["en"])


def wt_language_codes() -> list[str]:
    return [lang.code for lang in WT_LANGUAGES]


# Official Apache-2.0 tessdata_fast (smaller / good enough for UI text).
TESSDATA_FAST_BASE = (
    "https://github.com/tesseract-ocr/tessdata_fast/raw/main/{lang}.traineddata"
)

# All unique tess langs we may need for WT.
ALL_TESS_LANGS: tuple[str, ...] = tuple(
    dict.fromkeys(code for lang in WT_LANGUAGES for code in lang.tesseract_langs)
)
