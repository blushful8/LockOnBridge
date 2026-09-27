"""OCR process isolation (WinRT / Tesseract worker)."""

import json
from pathlib import Path

from lockon_bridge.ocr_isolate import _json_line, ocr_worker_main


def test_worker_json_line_keeps_ukrainian_on_ascii_pipe():
    line = _json_line({"variants": [["ocrspace:layout/zone", "Ваше місце в команді: 6"]]})
    assert line.isascii()
    restored = json.loads(line)
    assert "місце" in restored["variants"][0][1]


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
