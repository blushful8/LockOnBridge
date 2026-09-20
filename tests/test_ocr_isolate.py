"""OCR process isolation + RapidOCR main-process ban."""

import os

from PIL import Image

from lockon_bridge.rapid_ocr import rapidocr_available, rapidocr_digits_text


def test_rapidocr_blocked_in_main_process(monkeypatch):
    monkeypatch.delenv("LOCKON_ALLOW_RAPIDOCR", raising=False)
    assert rapidocr_available() is False
    img = Image.new("RGB", (64, 32), (255, 255, 255))
    assert rapidocr_digits_text(img) == ""


def test_rapidocr_status_ban(monkeypatch, tmp_path):
    monkeypatch.setenv("LOCKON_ALLOW_RAPIDOCR", "1")
    monkeypatch.setattr(
        "lockon_bridge.rapid_ocr._status_path",
        lambda: tmp_path / "rapidocr_status.json",
    )
    from lockon_bridge.rapid_ocr import write_rapidocr_status

    write_rapidocr_status(ok=False, detail="probe exit 3221225477")
    assert rapidocr_available() is False


def test_ocr_worker_roundtrip_on_fixture(tmp_path, monkeypatch):
    """Worker must OCR a fixture without needing the main-process RapidOCR ban lifted."""
    from pathlib import Path

    from lockon_bridge.ocr_isolate import ocr_worker_main

    fixture = (
        Path(__file__).parent / "fixtures" / "results_unfinished_383_4052.jpg"
    )
    if not fixture.is_file():
        return
    out = tmp_path / "out.json"
    # Worker process path — allow RapidOCR flag but status ban may skip it.
    monkeypatch.setenv("LOCKON_ALLOW_RAPIDOCR", "1")
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
