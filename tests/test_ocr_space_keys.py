from __future__ import annotations

from typing import Any

import pytest

from lockon_bridge import ocr_space


@pytest.fixture()
def key_store(tmp_path, monkeypatch):
    legacy = tmp_path / "ocr_space.bin"
    pool = tmp_path / "ocr_space_keys.bin"

    def fake_dpapi(data: bytes, *, protect: bool) -> bytes:
        if protect:
            return b"ENCRYPTED:" + data
        raw = data
        if raw.startswith(b"ENCRYPTED:"):
            return raw[len(b"ENCRYPTED:") :]
        return raw

    monkeypatch.setattr(ocr_space, "_secret_path", lambda: legacy)
    monkeypatch.setattr(ocr_space, "_pool_secret_path", lambda: pool)
    monkeypatch.setattr(ocr_space, "_dpapi", fake_dpapi)
    monkeypatch.delenv("OCR_SPACE_API_KEY", raising=False)
    return legacy


def test_store_and_resolve_prefers_sticky(key_store) -> None:
    ocr_space.store_api_keys(
        ["K1111111111111111111", "K2222222222222222222", "K3333333333333333333"],
        preferred=1,
    )
    keys = ocr_space.resolve_api_keys()
    assert keys[0] == "K2222222222222222222"
    assert set(keys) == {
        "K1111111111111111111",
        "K2222222222222222222",
        "K3333333333333333333",
    }


def test_add_api_keys_merges_without_dupes(key_store) -> None:
    ocr_space.store_api_keys(["K1111111111111111111"])
    merged = ocr_space.add_api_keys(
        ["K1111111111111111111", "K2222222222222222222", "helloworld", ""]
    )
    assert merged == ["K1111111111111111111", "K2222222222222222222"]


def test_remember_preferred_moves_sticky(key_store) -> None:
    ocr_space.store_api_keys(
        ["K1111111111111111111", "K2222222222222222222"],
        preferred=0,
    )
    ocr_space.remember_preferred_api_key("K2222222222222222222")
    assert ocr_space.resolve_api_keys()[0] == "K2222222222222222222"


def test_parse_rotates_until_success(key_store, monkeypatch) -> None:
    ocr_space.store_api_keys(
        ["K1111111111111111111", "K2222222222222222222", "K3333333333333333333"],
        preferred=0,
    )
    calls: list[str] = []

    def fake_once(
        png: bytes,
        *,
        api_key: str,
        language: str,
        engine: int,
        timeout_sec: float,
        overlay: bool,
    ) -> dict[str, Any]:
        del png, language, engine, timeout_sec, overlay
        calls.append(api_key)
        if api_key != "K3333333333333333333":
            raise RuntimeError("OCR.space HTTP 429: rate limit")
        return {"text": "ok", "lines": [], "raw": {}}

    monkeypatch.setattr(ocr_space, "_ocr_space_parse_once", fake_once)
    monkeypatch.setattr(ocr_space, "prep_png_for_ocr_space", lambda png: png)
    result = ocr_space.ocr_space_parse(b"png-bytes", prep=False)
    assert result["text"] == "ok"
    assert calls == [
        "K1111111111111111111",
        "K2222222222222222222",
        "K3333333333333333333",
    ]
    assert ocr_space.resolve_api_keys()[0] == "K3333333333333333333"


def test_parse_raises_only_after_all_keys_fail(key_store, monkeypatch) -> None:
    ocr_space.store_api_keys(
        ["K1111111111111111111", "K2222222222222222222"],
        preferred=0,
    )

    def fake_once(*_args, **_kwargs):
        raise RuntimeError("OCR.space HTTP 429: rate limit")

    alerts: list[tuple[str, str]] = []

    monkeypatch.setattr(ocr_space, "_ocr_space_parse_once", fake_once)
    monkeypatch.setattr(ocr_space, "prep_png_for_ocr_space", lambda png: png)

    import lockon_bridge.alert_mail as alert_mail

    monkeypatch.setattr(
        alert_mail,
        "notify_failure",
        lambda kind, detail, **_kwargs: alerts.append((kind, detail)),
    )

    with pytest.raises(RuntimeError, match="all 2 key"):
        ocr_space.ocr_space_parse(b"png-bytes", prep=False)
    assert alerts and alerts[0][0] == "ocr-space-all-keys-failed"


def test_demo_key_only_when_pool_empty(key_store) -> None:
    assert ocr_space.resolve_api_keys() == ["helloworld"]
