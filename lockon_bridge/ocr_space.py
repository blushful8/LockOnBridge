"""OCR.space HTTP API — sole cloud OCR backend for LockOn Bridge."""

from __future__ import annotations

import base64
import json
import logging
import os
import threading
import urllib.error
import urllib.request
from io import BytesIO
from typing import Any

log = logging.getLogger("lockon_bridge.ocr_space")

_API_URL = "https://api.ocr.space/parse/image"
# Public demo key (rate-limited). Real keys live in a DPAPI blob under LocalAppData.
_DEMO_KEY = "helloworld"
# Legacy single-key blob (plain string after DPAPI) — kept for older Bridge builds.
_SECRET_NAME = "ocr_space.bin"
# Multi-key pool (JSON after DPAPI). New Bridge prefers this file.
_POOL_SECRET_NAME = "ocr_space_keys.bin"
# Extra entropy so a copied blob is useless without this build. Not the API key.
_SECRET_ENTROPY = b"LockOnBridge.ocr-space.v1"
_store_lock = threading.RLock()


def _secret_path():
    from .paths import data_root

    return data_root() / _SECRET_NAME


def _pool_secret_path():
    from .paths import data_root

    return data_root() / _POOL_SECRET_NAME


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
    """OCR.space Engine 3. Engine 2 is not used."""
    return 3


def timeout_for_engine(engine: int) -> float:
    """Engine 3 is a heavier model and often answers more slowly."""
    return 120.0 if int(engine) == 3 else 45.0


def _mask_key(key: str) -> str:
    cleaned = (key or "").strip()
    if cleaned == _DEMO_KEY:
        return "helloworld"
    if len(cleaned) <= 8:
        return "(set)"
    return f"{cleaned[:4]}…{cleaned[-4:]}"


def _normalize_keys(keys: list[str] | tuple[str, ...] | None) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for raw in keys or ():
        key = (raw or "").strip()
        if not key or key == _DEMO_KEY or key in seen:
            continue
        seen.add(key)
        out.append(key)
    return out


def _decode_store_blob(path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        plain = _dpapi(path.read_bytes(), protect=False).decode("utf-8").strip()
    except Exception as exc:  # noqa: BLE001
        log.warning("OCR.space key blob unreadable (%s): %s", path.name, exc)
        return None
    if not plain:
        return None
    if plain.startswith("{"):
        try:
            raw = json.loads(plain)
        except json.JSONDecodeError:
            raw = None
        if isinstance(raw, dict) and raw.get("keys") is not None:
            keys = _normalize_keys(
                [str(item) for item in (raw.get("keys") or []) if str(item).strip()]
            )
            preferred = int(raw.get("preferred") or 0)
            if keys:
                preferred = max(0, min(preferred, len(keys) - 1))
            else:
                preferred = 0
            return {"keys": keys, "preferred": preferred}
        return None
    # Legacy v1 / sticky mirror: whole blob is one API key string.
    keys = _normalize_keys([plain])
    return {"keys": keys, "preferred": 0} if keys else None


def _read_key_store() -> dict[str, Any]:
    """Return ``{keys: [...], preferred: int}`` from pool blob, else legacy blob."""
    pool = _decode_store_blob(_pool_secret_path())
    if pool and pool["keys"]:
        return pool
    legacy = _decode_store_blob(_secret_path())
    if legacy and legacy["keys"]:
        return legacy
    return {"keys": [], "preferred": 0}


def _write_key_store(keys: list[str], preferred: int = 0) -> None:
    cleaned = _normalize_keys(keys)
    if not cleaned:
        raise ValueError("refusing to store an empty OCR key list")
    preferred = max(0, min(int(preferred), len(cleaned) - 1))
    payload = {"v": 2, "keys": cleaned, "preferred": preferred}
    pool_path = _pool_secret_path()
    pool_path.parent.mkdir(parents=True, exist_ok=True)
    pool_path.write_bytes(
        _dpapi(json.dumps(payload, ensure_ascii=False).encode("utf-8"), protect=True)
    )
    # Mirror the sticky key as a plain legacy blob so older installed Bridges
    # still authenticate instead of posting a JSON string as apikey.
    sticky = cleaned[preferred]
    _secret_path().write_bytes(_dpapi(sticky.encode("utf-8"), protect=True))
    log.info(
        "OCR.space keys stored (DPAPI) count=%s preferred=%s pool=%s legacy=%s",
        len(cleaned),
        _mask_key(sticky),
        pool_path.name,
        _secret_path().name,
    )


def store_api_key(key: str) -> None:
    """Persist one OCR.space key (replaces the whole pool). Prefer ``store_api_keys``."""
    store_api_keys([key])


def store_api_keys(keys: list[str] | tuple[str, ...], *, preferred: int = 0) -> None:
    """Replace the encrypted OCR.space key pool. Never writes git."""
    with _store_lock:
        _write_key_store(list(keys), preferred=preferred)


def add_api_keys(keys: list[str] | tuple[str, ...]) -> list[str]:
    """Merge extra keys into the pool (deduped). Returns the resulting list."""
    with _store_lock:
        current = _read_key_store()
        merged = _normalize_keys(list(current["keys"]) + list(keys))
        preferred = int(current.get("preferred") or 0)
        if current["keys"]:
            # Keep sticky preferred by key identity when possible.
            old_pref = current["keys"][preferred] if preferred < len(current["keys"]) else ""
            if old_pref in merged:
                preferred = merged.index(old_pref)
            else:
                preferred = 0
        _write_key_store(merged, preferred=preferred)
        return list(merged)


def load_api_keys() -> list[str]:
    """Personal OCR.space keys from DPAPI (never includes the demo key)."""
    with _store_lock:
        return list(_read_key_store()["keys"])


def preferred_api_key() -> str:
    with _store_lock:
        store = _read_key_store()
        keys = store["keys"]
        if not keys:
            return ""
        idx = int(store.get("preferred") or 0)
        return keys[max(0, min(idx, len(keys) - 1))]


def remember_preferred_api_key(key: str) -> None:
    """Stick to a key that just succeeded until it starts failing."""
    cleaned = (key or "").strip()
    if not cleaned or cleaned == _DEMO_KEY:
        return
    with _store_lock:
        store = _read_key_store()
        keys = list(store["keys"])
        if cleaned not in keys:
            return
        idx = keys.index(cleaned)
        if idx == int(store.get("preferred") or 0):
            return
        _write_key_store(keys, preferred=idx)
        log.info("OCR.space sticky key → %s", _mask_key(cleaned))


def _env_api_keys() -> list[str]:
    raw = (os.environ.get("OCR_SPACE_API_KEY") or "").strip()
    if not raw:
        return []
    # Comma / semicolon / whitespace separated lists are accepted.
    parts = [part.strip() for part in raw.replace(";", ",").replace("\n", ",").split(",")]
    return _normalize_keys(parts)


def resolve_api_keys(explicit: str | None = None) -> list[str]:
    """
    Ordered OCR.space key pool for one request.

    Preferred personal key first, then the rest. Demo ``helloworld`` is used
    only when no personal keys exist.
    """
    if explicit and explicit.strip():
        return [explicit.strip()]
    env_keys = _env_api_keys()
    stored = load_api_keys()
    # Env wins as an override pool when set; otherwise DPAPI pool.
    pool = env_keys or stored
    if not pool:
        return [_DEMO_KEY]
    preferred = preferred_api_key()
    if preferred and preferred in pool:
        return [preferred] + [key for key in pool if key != preferred]
    return list(pool)


def resolve_api_key(explicit: str | None = None) -> str:
    """First key from ``resolve_api_keys`` (compat for status / quota helpers)."""
    keys = resolve_api_keys(explicit)
    return keys[0] if keys else _DEMO_KEY


def personal_key_count() -> int:
    return len(load_api_keys())


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


def _ocr_space_parse_once(
    png: bytes,
    *,
    api_key: str,
    language: str,
    engine: int,
    timeout_sec: float,
    overlay: bool,
) -> dict[str, Any]:
    """Single-key OCR.space POST. ``png`` must already be prepped."""
    boundary = "----LockOnBridgeOCRSpace"
    b64 = base64.b64encode(png).decode("ascii")
    fields: list[tuple[str, str]] = [
        ("apikey", api_key),
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
        "OCR.space ok key=%s chars=%s engine=%s exit=%s overlay_lines=%s",
        _mask_key(api_key),
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

    Tries the sticky preferred key first, then every other personal key.
    EasyOCR callers must only fall back after this raises (all keys failed).
    When ``overlay=True``, ``lines`` is a list of ``OverlayLine``-compatible
    dicts (or objects) with word-box layout from TextOverlay.
    """
    if not png:
        return {"text": "", "lines": [], "raw": {}}
    engine = 3
    if timeout_sec is None:
        timeout_sec = timeout_for_engine(engine)
    if prep:
        png = prep_png_for_ocr_space(png)

    keys = resolve_api_keys(api_key)
    errors: list[str] = []
    for index, key in enumerate(keys):
        try:
            result = _ocr_space_parse_once(
                png,
                api_key=key,
                language=language,
                engine=engine,
                timeout_sec=timeout_sec,
                overlay=overlay,
            )
        except Exception as exc:  # noqa: BLE001
            msg = str(exc)
            errors.append(f"{_mask_key(key)}: {msg}")
            log.warning(
                "OCR.space key %s failed (%s/%s): %s",
                _mask_key(key),
                index + 1,
                len(keys),
                msg,
            )
            continue
        if key != _DEMO_KEY:
            remember_preferred_api_key(key)
        return result

    detail = " | ".join(errors) if errors else "no keys"
    try:
        from .alert_mail import notify_failure

        notify_failure(
            "ocr-space-all-keys-failed",
            f"Tried {len(keys)} OCR.space key(s); all failed.\n{detail}",
        )
    except Exception:  # noqa: BLE001
        log.debug("alert mail hook skipped", exc_info=True)
    raise RuntimeError(
        f"OCR.space all {len(keys)} key(s) failed: {detail}"
    )

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
