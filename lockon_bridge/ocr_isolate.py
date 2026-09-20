"""Run heavy OCR in an isolated child so native AV cannot kill the Bridge."""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
from pathlib import Path

from PIL import Image

from .crashguard import breadcrumb
from .paths import log_dir

log = logging.getLogger("lockon_bridge.ocr_isolate")

_WORKER_ENV = "LOCKON_OCR_WORKER"
_ALLOW_RAPID = "LOCKON_ALLOW_RAPIDOCR"


def _worker_cmd() -> list[str]:
    if getattr(sys, "frozen", False):
        return [sys.executable, "--ocr-worker"]
    return [sys.executable, "-m", "lockon_bridge", "--ocr-worker"]


def run_ocr_worker_on_image(
    image: Image.Image,
    *,
    roi_only: bool = False,
    wt_ui_language: str | None = None,
    backend: str | None = None,
    timeout: float = 120.0,
) -> list[tuple[str, str]]:
    """
    OCR ``image`` in a child process (RapidOCR allowed there only).

    Returns ``[(tag, text), ...]``. On worker crash/timeout returns [].
    """
    log_dir().mkdir(parents=True, exist_ok=True)
    frame_path = log_dir() / "ocr_worker_frame.png"
    result_path = log_dir() / "ocr_worker_result.json"
    try:
        image.save(frame_path, format="PNG")
    except OSError as exc:
        log.warning("OCR worker frame save failed: %s", exc)
        return []

    if result_path.is_file():
        try:
            result_path.unlink()
        except OSError:
            pass

    env = os.environ.copy()
    env[_WORKER_ENV] = "1"
    env[_ALLOW_RAPID] = "1"
    cmd = _worker_cmd() + [
        "--image",
        str(frame_path),
        "--out",
        str(result_path),
    ]
    if roi_only:
        cmd.append("--roi-only")
    if wt_ui_language:
        cmd.extend(["--lang", wt_ui_language])
    if backend:
        cmd.extend(["--backend", backend])

    creationflags = 0
    if sys.platform == "win32":
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)

    breadcrumb(f"ocr-worker spawn roi_only={roi_only} size={image.size}")
    log.info(
        "OCR worker spawn size=%sx%s roi_only=%s timeout=%.0fs",
        image.size[0],
        image.size[1],
        roi_only,
        timeout,
    )
    try:
        proc = subprocess.run(
            cmd,
            timeout=timeout,
            env=env,
            creationflags=creationflags,
            capture_output=True,
        )
    except subprocess.TimeoutExpired:
        log.error("OCR worker timed out after %.0fs", timeout)
        breadcrumb("ocr-worker timeout")
        return []
    except Exception as exc:  # noqa: BLE001
        log.error("OCR worker spawn failed: %s", exc)
        breadcrumb(f"ocr-worker spawn-error {exc}")
        return []

    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or b"")[:600]
        log.error(
            "OCR worker crashed/exit=%s stderr=%s",
            proc.returncode,
            err,
        )
        breadcrumb(f"ocr-worker exit={proc.returncode}")
        # ACCESS_VIOLATION (0xC0000005) from onnxruntime — ban RapidOCR so the
        # rest of this burst (and later battles) fall back to WinRT/Tesseract.
        av = proc.returncode in (3221225477, -1073741819)
        if av:
            try:
                from .rapid_ocr import write_rapidocr_status

                write_rapidocr_status(
                    ok=False,
                    detail=f"ocr-worker ACCESS_VIOLATION exit={proc.returncode}",
                )
                log.warning("RapidOCR banned after worker ACCESS_VIOLATION")
            except Exception:  # noqa: BLE001
                pass
        # Leave a mini report next to breadcrumbs.
        try:
            from .crashguard import write_crash_report

            write_crash_report(
                "ocr-worker",
                f"exit={proc.returncode}\nstderr={err!r}\n",
            )
        except Exception:  # noqa: BLE001
            pass
        return []

    if not result_path.is_file():
        log.error("OCR worker finished but result file missing")
        breadcrumb("ocr-worker missing-result")
        return []

    try:
        payload = json.loads(result_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        log.error("OCR worker result unreadable: %s", exc)
        return []

    variants_raw = payload.get("variants") if isinstance(payload, dict) else None
    if not isinstance(variants_raw, list):
        return []
    out: list[tuple[str, str]] = []
    for item in variants_raw:
        if not isinstance(item, (list, tuple)) or len(item) < 2:
            continue
        out.append((str(item[0]), str(item[1])))
    log.info("OCR worker ok — %s variant(s)", len(out))
    breadcrumb(f"ocr-worker ok variants={len(out)}")
    return out


def ocr_worker_main(
    *,
    image_path: Path,
    out_path: Path,
    roi_only: bool,
    lang: str | None,
    backend: str | None,
) -> int:
    """Child entry: load PNG, run ROI (+ optional panel) OCR, write JSON."""
    os.environ[_WORKER_ENV] = "1"
    os.environ[_ALLOW_RAPID] = "1"
    from .crashguard import breadcrumb, install_crash_guard

    install_crash_guard()
    breadcrumb(f"ocr-worker begin image={image_path}")

    try:
        image = Image.open(image_path).convert("RGB")
    except OSError as exc:
        out_path.write_text(
            json.dumps({"ok": False, "error": str(exc), "variants": []}),
            encoding="utf-8",
        )
        return 2

    from .capture import _crop_results_rois, _png_from_image, ocr_png_variants
    from .ocr_parse import choose_best_report, parse_rewards_from_ocr_text
    from .roi_rewards import extract_roi_reward_variants
    from .settings import load_settings

    settings = load_settings()
    use_lang = lang or settings.wt_ui_language or settings.language
    use_backend = backend or getattr(settings, "ocr_backend", "auto") or "auto"

    breadcrumb("ocr-worker extract_roi")
    variants: list[tuple[str, str]] = []
    try:
        variants.extend(extract_roi_reward_variants(image))
    except Exception as exc:  # noqa: BLE001
        log.warning("ROI digit extract failed in worker: %s", exc)

    roi_best = choose_best_report(
        [
            (text, parse_rewards_from_ocr_text(text))
            for tag, text in variants
            if tag.startswith("roi:")
        ]
    )
    roi_confident = roi_best is not None and roi_best[1].confidence >= 0.85
    if not (roi_only or roi_confident):
        breadcrumb("ocr-worker panel engines")
        for roi_tag, crop in _crop_results_rois(image):
            png = _png_from_image(crop)
            for eng_tag, text in ocr_png_variants(
                png, wt_ui_language=use_lang, backend=use_backend
            ):
                variants.append((f"{eng_tag}/{roi_tag}", text))

    payload = {
        "ok": True,
        "variants": [[tag, text] for tag, text in variants],
        "roi_confident": bool(roi_confident),
    }
    if roi_best is not None:
        payload["rp"] = roi_best[1].research_points
        payload["sl"] = roi_best[1].silver_lions
        payload["confidence"] = roi_best[1].confidence

    tmp = out_path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    tmp.replace(out_path)
    breadcrumb(f"ocr-worker done variants={len(variants)}")
    return 0
