from lockon_bridge.ocr_active import last_ocr_provider, note_ocr_provider, provider_label
from lockon_bridge.settings import BridgeSettings


def test_note_ocr_provider_tracks_fallback() -> None:
    note_ocr_provider("ocrspace", reason="preferred")
    note_ocr_provider("easyocr", reason="fallback")
    state = last_ocr_provider()
    assert state["provider"] == "easyocr"
    assert state["reason"] == "fallback"
    assert provider_label(state["provider"]) == "EasyOCR"


def test_shipping_ocrspace_with_fallback_migrates_to_auto() -> None:
    settings = BridgeSettings.from_dict(
        {"ocr_backend": "ocrspace", "allow_ocr_fallback": True}
    )
    assert settings.ocr_backend == "auto"
    assert settings.allow_ocr_fallback is True


def test_forced_ocrspace_stays_without_fallback() -> None:
    settings = BridgeSettings.from_dict(
        {"ocr_backend": "ocrspace", "allow_ocr_fallback": False}
    )
    assert settings.ocr_backend == "ocrspace"
    assert settings.allow_ocr_fallback is False
