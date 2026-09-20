"""OCR process isolation (WinRT / Tesseract worker)."""

from pathlib import Path

from lockon_bridge.ocr_isolate import ocr_worker_main


def test_ocr_worker_roundtrip_on_fixture(tmp_path, monkeypatch):
    """Worker must OCR a fixture via WinRT/Tesseract and write JSON."""
    fixture = (
        Path(__file__).parent / "fixtures" / "results_unfinished_383_4052.jpg"
    )
    if not fixture.is_file():
        return
    out = tmp_path / "out.json"
    monkeypatch.setenv("LOCKON_OCR_WORKER", "1")
    code = ocr_worker_main(
        image_path=fixture,
        out_path=out,
        roi_only=False,
        lang="uk",
        backend="auto",
    )
    assert code == 0
    assert out.is_file()
    text = out.read_text(encoding="utf-8")
    assert "variants" in text
