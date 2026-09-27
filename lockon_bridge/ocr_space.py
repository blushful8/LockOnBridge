"""OCR.space HTTP API — sole cloud OCR backend for LockOn Bridge."""

from __future__ import annotations

import base64
import json
import logging
import os
import urllib.error
import urllib.request
from io import BytesIO
from typing import Any

log = logging.getLogger("lockon_bridge.ocr_space")

_API_URL = "https://api.ocr.space/parse/image"
# Public demo key (rate-limited). Real key lives in a DPAPI blob under LocalAppData.
_DEMO_KEY = "helloworld"
_SECRET_NAME = "ocr_space.bin"
# Extra entropy so a copied blob is useless without this build. Not the API key.
_SECRET_ENTROPY = b"LockOnBridge.ocr-space.v1"


def _secret_path():
    from .paths import data_root

    return data_root() / _SECRET_NAME


def _dpapi(data: bytes, *, protect: bool) -> bytes:
    """Encrypt or decrypt with the current Windows user (LocalAppData only)."""
    import ctypes
    from ctypes import wintypes

    class DATA_BLOB(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    def _blob(buf: bytes) -> tuple[DATA_BLOB, ctypes.Array]:
        raw = ctypes.create_string_buffer(buf, len(buf))
        blob = DATA_BLOB()
        blob.cbData = len(buf)
        blob.pbData = ctypes.cast(raw, ctypes.POINTER(ctypes.c_char))
        return blob, raw

    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    in_blob, _in_keep = _blob(data)
    entropy, _entropy_keep = _blob(_SECRET_ENTROPY)
    out = DATA_BLOB()
    flags = 0x01  # CRYPTPROTECT_UI_FORBIDDEN
    if protect:
        ok = crypt32.CryptProtectData(
            ctypes.byref(in_blob),
            None,
            ctypes.byref(entropy),
            None,
            None,
            flags,
            ctypes.byref(out),
        )
    else:
        ok = crypt32.CryptUnprotectData(
            ctypes.byref(in_blob),
            None,
            ctypes.byref(entropy),
            None,
            None,
            flags,
            ctypes.byref(out),
        )
    if not ok:
        raise OSError("Windows DPAPI refused the OCR key blob")
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        kernel32.LocalFree(out.pbData)


def active_ocr_engine() -> int:
    """Saved choice: 2 (default) or 3. Anything else falls back to 2."""
    try:
        from .settings import load_settings

        engine = int(load_settings().ocr_space_engine)
    except Exception:  # noqa: BLE001
        engine = 2
    return 3 if engine == 3 else 2


def timeout_for_engine(engine: int) -> float:
    """Engine 3 is a heavier model and often answers more slowly."""
    return 120.0 if int(engine) == 3 else 45.0


def store_api_key(key: str) -> None:
    """Persist the OCR.space key encrypted for this Windows user. Never writes git."""
    cleaned = (key or "").strip()
    if not cleaned or cleaned == _DEMO_KEY:
        raise ValueError("refusing to store an empty or demo OCR key")
    path = _secret_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(_dpapi(cleaned.encode("utf-8"), protect=True))
    log.info("OCR.space key stored (DPAPI) at %s", path)


def _load_stored_api_key() -> str:
    path = _secret_path()
    if not path.is_file():
        return ""
    try:
        return _dpapi(path.read_bytes(), protect=False).decode("utf-8").strip()
    except Exception as exc:  # noqa: BLE001
        log.warning("OCR.space key blob unreadable: %s", exc)
        return ""


def resolve_api_key(explicit: str | None = None) -> str:
    """Explicit argument → env → DPAPI blob → demo key. Settings plaintext is ignored."""
    if explicit and explicit.strip():
        return explicit.strip()
    env = (os.environ.get("OCR_SPACE_API_KEY") or "").strip()
    if env:
        return env
    stored = _load_stored_api_key()
    if stored:
        return stored
    return _DEMO_KEY


def prep_png_for_ocr_space(png: bytes) -> bytes:
    """
    Upscale + invert + contrast for WT dark UI.

    OCR.space Engine 2 reads «Зароблено» amounts far more reliably on
    white-on-black inverted crops than on the raw grey hangar look.
    """
    if not png:
        return png
    try:
        from PIL import Image, ImageEnhance, ImageOps
    except ImportError:
        return png
    try:
        im = Image.open(BytesIO(png)).convert("RGB")
    except Exception:  # noqa: BLE001
        return png
    # Upscale small/medium crops so grouped thousands stay separable.
    if max(im.size) < 2400:
        im = im.resize(
            (im.width * 2, im.height * 2),
            Image.Resampling.LANCZOS,
        )
    gray = ImageOps.grayscale(im)
    inv = ImageOps.invert(gray)
    inv = ImageEnhance.Contrast(inv).enhance(2.0)
    out = BytesIO()
    inv.convert("RGB").save(out, format="PNG", optimize=True)
    return out.getvalue()


def ocr_space_png(
    png: bytes,
    *,
    api_key: str | None = None,
    language: str = "auto",
    engine: int | None = None,
    timeout_sec: float | None = None,
    prep: bool = True,
    overlay: bool = False,
) -> str:
    """
    POST a PNG to OCR.space Engine 2 (auto language) and return ParseText.

    Raises ``RuntimeError`` on transport / API errors.
    """
    result = ocr_space_parse(
        png,
        api_key=api_key,
        language=language,
        engine=engine,
        timeout_sec=timeout_sec,
        prep=prep,
        overlay=overlay,
    )
    return result.get("text") or ""


def ocr_space_parse(
    png: bytes,
    *,
    api_key: str | None = None,
    language: str = "auto",
    engine: int | None = None,
    timeout_sec: float | None = None,
    prep: bool = True,
    overlay: bool = False,
) -> dict[str, Any]:
    """
    POST a PNG to OCR.space; return ``{text, lines, raw}``.

    When ``overlay=True``, ``lines`` is a list of ``OverlayLine``-compatible
    dicts (or objects) with word-box layout from TextOverlay.
    """
    if not png:
        return {"text": "", "lines": [], "raw": {}}
    engine = 3 if int(engine if engine is not None else active_ocr_engine()) == 3 else 2
    if timeout_sec is None:
        timeout_sec = timeout_for_engine(engine)
    if prep:
        png = prep_png_for_ocr_space(png)
    key = resolve_api_key(api_key)
    boundary = "----LockOnBridgeOCRSpace"
    b64 = base64.b64encode(png).decode("ascii")
    fields: list[tuple[str, str]] = [
        ("apikey", key),
        ("base64Image", f"data:image/png;base64,{b64}"),
        ("language", language or "auto"),
        ("OCREngine", str(int(engine))),
        ("isOverlayRequired", "true" if overlay else "false"),
        ("detectOrientation", "true"),
        ("scale", "true"),
    ]
    body_parts: list[bytes] = []
    for name, value in fields:
        body_parts.append(
            (
                f"--{boundary}\r\n"
                f'Content-Disposition: form-data; name="{name}"\r\n\r\n'
                f"{value}\r\n"
            ).encode("utf-8")
        )
    body_parts.append(f"--{boundary}--\r\n".encode("ascii"))
    body = b"".join(body_parts)

    req = urllib.request.Request(
        _API_URL,
        data=body,
        method="POST",
        headers={
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "User-Agent": "LockOnBridge/ocr-space",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=max(5.0, timeout_sec)) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:300]
        try:
            from .ocr_quota import note_rejection

            note_rejection(detail)
        except Exception:  # noqa: BLE001
            log.debug("quota rejection note skipped", exc_info=True)
        raise RuntimeError(f"OCR.space HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"OCR.space network error: {exc}") from exc

    try:
        data: dict[str, Any] = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"OCR.space bad JSON: {raw[:200]}") from exc

    if data.get("IsErroredOnProcessing"):
        msg = data.get("ErrorMessage") or data.get("ErrorDetails") or data
        raise RuntimeError(f"OCR.space processing error: {msg}")

    results = data.get("ParsedResults") or []
    texts: list[str] = []
    for item in results:
        if not isinstance(item, dict):
            continue
        t = (item.get("ParsedText") or "").strip()
        if t:
            texts.append(t)
    text = "\n".join(texts).strip()

    lines: list[Any] = []
    if overlay and results and isinstance(results[0], dict):
        for line in (results[0].get("TextOverlay") or {}).get("Lines") or []:
            if not isinstance(line, dict):
                continue
            words = line.get("Words") or []
            if not words:
                continue
            line_text = " ".join(str(w.get("WordText") or "") for w in words).strip()
            if not line_text:
                continue
            try:
                left = min(int(w["Left"]) for w in words)
                top = min(int(w["Top"]) for w in words)
                right = max(int(w["Left"]) + int(w["Width"]) for w in words)
                bottom = max(int(w["Top"]) + int(w["Height"]) for w in words)
            except (KeyError, TypeError, ValueError):
                continue
            # Lazy construct OverlayLine to avoid import cycles at module load.
            try:
                from .layout_ocr import Box, OverlayLine

                lines.append(
                    OverlayLine(text=line_text, box=Box(left, top, right, bottom))
                )
            except Exception:  # noqa: BLE001
                lines.append(
                    {
                        "text": line_text,
                        "box": (left, top, right, bottom),
                    }
                )

    log.info(
        "OCR.space ok chars=%s engine=%s exit=%s overlay_lines=%s",
        len(text),
        engine,
        data.get("OCRExitCode"),
        len(lines),
    )
    try:
        from .ocr_quota import note_success

        note_success(engine)
    except Exception:  # noqa: BLE001
        log.debug("quota note skipped", exc_info=True)
    return {"text": text, "lines": lines, "raw": data}

def ocr_space_variants(
    png: bytes,
    *,
    api_key: str | None = None,
    wt_ui_language: str = "uk",
) -> list[tuple[str, str]]:
    """Return ``[(tag, text), ...]`` for the shared OCR pipeline."""
    del wt_ui_language  # language=auto; engine comes from settings
    engine = active_ocr_engine()
    try:
        text = ocr_space_png(png, api_key=api_key, language="auto", engine=engine)
    except Exception as exc:  # noqa: BLE001
        log.warning("OCR.space failed: %s", exc)
        return []
    if not text:
        return []
    return [("ocrspace:auto", text)]


def _same_words(left: str, right: str) -> bool:
    return left.split() == right.split()


def raw_ocr_text(payload: dict[str, Any] | None) -> str:
    """Unfiltered OCR.space text, once.

    Engine 3 often returns the same words twice: a wrapped ``ParsedText`` and
    the same words split into TextOverlay lines. Keep the line layout when the
    words match. Show both only when overlay adds or drops words.
    """
    if not isinstance(payload, dict):
        return ""
    blocks: list[str] = []
    for item in payload.get("ParsedResults") or []:
        if not isinstance(item, dict):
            continue
        parsed = str(item.get("ParsedText") or "").replace("\r\n", "\n").strip()
        overlay_rows: list[str] = []
        for line in (item.get("TextOverlay") or {}).get("Lines") or []:
            if not isinstance(line, dict):
                continue
            words = [
                str(word.get("WordText") or "").strip()
                for word in (line.get("Words") or [])
                if isinstance(word, dict) and str(word.get("WordText") or "").strip()
            ]
            if words:
                overlay_rows.append(" ".join(words))
        overlay = "\n".join(overlay_rows).strip()
        if overlay and parsed and _same_words(parsed, overlay):
            blocks.append(overlay)
        else:
            if parsed:
                blocks.append(parsed)
            if overlay and overlay != parsed:
                blocks.append("TextOverlay:\n" + overlay)
    return "\n\n".join(blocks).strip()
