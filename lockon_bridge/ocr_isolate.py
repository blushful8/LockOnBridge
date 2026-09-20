"""Run heavy OCR in an isolated child so a native crash cannot kill the Bridge UI."""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

from PIL import Image

from .crashguard import breadcrumb
from .paths import log_dir

log = logging.getLogger("lockon_bridge.ocr_isolate")

_WORKER_ENV = "LOCKON_OCR_WORKER"


def _unlink_quiet(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


def _worker_cmd(*, loop: bool = False) -> list[str]:
    flag = "--ocr-worker-loop" if loop else "--ocr-worker"
    if getattr(sys, "frozen", False):
        return [sys.executable, flag]
    return [sys.executable, "-m", "lockon_bridge", flag]


def _creationflags() -> int:
    if sys.platform == "win32":
        return int(getattr(subprocess, "CREATE_NO_WINDOW", 0))
    return 0


def _parse_variants_payload(payload: dict[str, Any]) -> list[tuple[str, str]]:
    variants_raw = payload.get("variants")
    if not isinstance(variants_raw, list):
        return []
    out: list[tuple[str, str]] = []
    for item in variants_raw:
        if not isinstance(item, (list, tuple)) or len(item) < 2:
            continue
        out.append((str(item[0]), str(item[1])))
    return out


def _calib_hint_from_payload(payload: dict[str, Any]) -> dict[str, Any] | None:
    raw = payload.get("calib_hint")
    if not isinstance(raw, dict):
        return None
    try:
        pair_index = int(raw["pair_index"])
        prefer_with = bool(raw["prefer_with"])
    except (KeyError, TypeError, ValueError):
        return None
    ceiling = raw.get("premium_ceiling")
    hint: dict[str, Any] = {
        "pair_index": pair_index,
        "prefer_with": prefer_with,
    }
    if (
        isinstance(ceiling, (list, tuple))
        and len(ceiling) >= 2
        and ceiling[0] is not None
        and ceiling[1] is not None
    ):
        hint["premium_ceiling"] = [int(ceiling[0]), int(ceiling[1])]
    return hint


def run_ocr_worker_on_image(
    image: Image.Image,
    *,
    roi_only: bool = False,
    wt_ui_language: str | None = None,
    backend: str | None = None,
    timeout: float = 120.0,
    pair_hint: int | None = None,
    prefer_with: bool | None = None,
    premium_ceiling: tuple[int, int] | None = None,
    skip_expensive_fallback: bool = False,
) -> tuple[list[tuple[str, str]], dict[str, Any] | None]:
    """
    OCR ``image`` in a one-shot child process (WinRT / Tesseract).

    Returns ``(variants, calib_hint)``. On worker crash/timeout returns ``([], None)``.
    """
    log_dir().mkdir(parents=True, exist_ok=True)
    frame_path = log_dir() / "ocr_worker_frame.png"
    result_path = log_dir() / "ocr_worker_result.json"
    try:
        image.save(frame_path, format="PNG")
    except OSError as exc:
        log.warning("OCR worker frame save failed: %s", exc)
        return [], None

    if result_path.is_file():
        try:
            result_path.unlink()
        except OSError:
            pass

    env = os.environ.copy()
    env[_WORKER_ENV] = "1"
    cmd = _worker_cmd(loop=False) + [
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
    if pair_hint is not None:
        cmd.extend(["--pair-hint", str(int(pair_hint))])
    if prefer_with is not None:
        cmd.extend(["--prefer-with", "1" if prefer_with else "0"])
    if premium_ceiling is not None:
        cmd.extend(
            [
                "--premium-ceiling",
                f"{int(premium_ceiling[0])},{int(premium_ceiling[1])}",
            ]
        )
    if skip_expensive_fallback:
        cmd.append("--skip-expensive-fallback")

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
            creationflags=_creationflags(),
            capture_output=True,
        )
    except subprocess.TimeoutExpired:
        log.error("OCR worker timed out after %.0fs", timeout)
        breadcrumb("ocr-worker timeout")
        _unlink_quiet(frame_path)
        return [], None
    except Exception as exc:  # noqa: BLE001
        log.error("OCR worker spawn failed: %s", exc)
        breadcrumb(f"ocr-worker spawn-error {exc}")
        _unlink_quiet(frame_path)
        return [], None

    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or b"")[:600]
        log.error(
            "OCR worker crashed/exit=%s stderr=%s",
            proc.returncode,
            err,
        )
        breadcrumb(f"ocr-worker exit={proc.returncode}")
        try:
            from .crashguard import write_crash_report

            write_crash_report(
                "ocr-worker",
                f"exit={proc.returncode}\nstderr={err!r}\n",
            )
        except Exception:  # noqa: BLE001
            pass
        _unlink_quiet(frame_path)
        return [], None

    if not result_path.is_file():
        log.error("OCR worker finished but result file missing")
        breadcrumb("ocr-worker missing-result")
        _unlink_quiet(frame_path)
        return [], None

    try:
        payload = json.loads(result_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        log.error("OCR worker result unreadable: %s", exc)
        _unlink_quiet(frame_path)
        return [], None

    if not isinstance(payload, dict):
        _unlink_quiet(frame_path)
        return [], None
    out = _parse_variants_payload(payload)
    hint = _calib_hint_from_payload(payload)
    log.info("OCR worker ok — %s variant(s)", len(out))
    breadcrumb(f"ocr-worker ok variants={len(out)}")
    # Drop the full-frame dump after OCR — privacy (never leave desktop/WT frame around).
    _unlink_quiet(frame_path)
    return out, hint


class PersistentOcrWorker:
    """Long-lived OCR child for a capture burst (avoids cold spawn per frame)."""

    def __init__(self, *, timeout: float = 120.0) -> None:
        self._timeout = float(timeout)
        self._proc: subprocess.Popen[str] | None = None
        self._stderr_file = None
        self._lock = threading.Lock()
        self._req_id = 0

    @property
    def alive(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def start(self) -> None:
        with self._lock:
            self._start_unlocked()

    def _start_unlocked(self) -> None:
        self._close_unlocked(join=False)
        log_dir().mkdir(parents=True, exist_ok=True)
        stderr_path = log_dir() / "ocr_worker_stderr.log"
        self._stderr_file = open(stderr_path, "a", encoding="utf-8")  # noqa: SIM115
        env = os.environ.copy()
        env[_WORKER_ENV] = "1"
        cmd = _worker_cmd(loop=True)
        breadcrumb("ocr-worker-loop start")
        log.info("OCR worker loop starting")
        self._proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=self._stderr_file,
            env=env,
            creationflags=_creationflags(),
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )
        ready = self._readline_unlocked(timeout=60.0)
        if not ready or not ready.get("ready"):
            log.error("OCR worker loop failed to become ready: %r", ready)
            self._close_unlocked(join=False)
            raise RuntimeError("OCR worker loop not ready")
        log.info("OCR worker loop ready")
        breadcrumb("ocr-worker-loop ready")

    def process(
        self,
        image: Image.Image,
        *,
        roi_only: bool = False,
        wt_ui_language: str | None = None,
        backend: str | None = None,
        pair_hint: int | None = None,
        prefer_with: bool | None = None,
        premium_ceiling: tuple[int, int] | None = None,
        skip_expensive_fallback: bool = False,
    ) -> tuple[list[tuple[str, str]], dict[str, Any] | None]:
        with self._lock:
            try:
                if not self.alive:
                    self._start_unlocked()
                return self._process_unlocked(
                    image,
                    roi_only=roi_only,
                    wt_ui_language=wt_ui_language,
                    backend=backend,
                    pair_hint=pair_hint,
                    prefer_with=prefer_with,
                    premium_ceiling=premium_ceiling,
                    skip_expensive_fallback=skip_expensive_fallback,
                )
            except Exception as exc:  # noqa: BLE001
                log.warning("OCR worker loop request failed: %s — respawn once", exc)
                breadcrumb(f"ocr-worker-loop fail {exc}")
                try:
                    self._start_unlocked()
                    return self._process_unlocked(
                        image,
                        roi_only=roi_only,
                        wt_ui_language=wt_ui_language,
                        backend=backend,
                        pair_hint=pair_hint,
                        prefer_with=prefer_with,
                        premium_ceiling=premium_ceiling,
                        skip_expensive_fallback=skip_expensive_fallback,
                    )
                except Exception as exc2:  # noqa: BLE001
                    log.error("OCR worker loop respawn failed: %s", exc2)
                    self._close_unlocked(join=False)
                    return [], None

    def _process_unlocked(
        self,
        image: Image.Image,
        *,
        roi_only: bool,
        wt_ui_language: str | None,
        backend: str | None,
        pair_hint: int | None,
        prefer_with: bool | None,
        premium_ceiling: tuple[int, int] | None,
        skip_expensive_fallback: bool,
    ) -> tuple[list[tuple[str, str]], dict[str, Any] | None]:
        assert self._proc is not None and self._proc.stdin and self._proc.stdout
        frame_path = log_dir() / "ocr_worker_frame.png"
        result_path = log_dir() / "ocr_worker_result.json"
        image.save(frame_path, format="PNG")
        if result_path.is_file():
            try:
                result_path.unlink()
            except OSError:
                pass
        self._req_id += 1
        req: dict[str, Any] = {
            "cmd": "ocr",
            "id": self._req_id,
            "image": str(frame_path),
            "out": str(result_path),
            "roi_only": bool(roi_only),
            "skip_expensive_fallback": bool(skip_expensive_fallback),
        }
        if wt_ui_language:
            req["lang"] = wt_ui_language
        if backend:
            req["backend"] = backend
        if pair_hint is not None:
            req["pair_hint"] = int(pair_hint)
        if prefer_with is not None:
            req["prefer_with"] = bool(prefer_with)
        if premium_ceiling is not None:
            req["premium_ceiling"] = [int(premium_ceiling[0]), int(premium_ceiling[1])]

        breadcrumb(f"ocr-worker-loop req id={self._req_id} roi_only={roi_only}")
        self._proc.stdin.write(json.dumps(req, ensure_ascii=False) + "\n")
        self._proc.stdin.flush()
        payload = self._readline_unlocked(timeout=self._timeout)
        if not payload:
            raise RuntimeError("OCR worker loop empty response")
        if payload.get("ok") is False:
            raise RuntimeError(payload.get("error") or "OCR worker loop error")
        out = _parse_variants_payload(payload)
        hint = _calib_hint_from_payload(payload)
        log.info("OCR worker loop ok — %s variant(s)", len(out))
        breadcrumb(f"ocr-worker-loop ok variants={len(out)}")
        _unlink_quiet(frame_path)
        return out, hint

    def _readline_unlocked(self, *, timeout: float) -> dict[str, Any] | None:
        assert self._proc is not None and self._proc.stdout
        deadline = time.monotonic() + timeout
        # Blocking readline with a watchdog thread is awkward on Windows; poll
        # process liveness and use a short select-less wait via thread.
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(f"OCR worker loop timed out after {timeout:.0f}s")
            box: list[str | None] = [None]
            err: list[BaseException] = []

            def _reader() -> None:
                try:
                    box[0] = (
                        self._proc.stdout.readline()
                        if self._proc and self._proc.stdout
                        else ""
                    )
                except BaseException as exc:  # noqa: BLE001
                    err.append(exc)

            t = threading.Thread(target=_reader, name="ocr-worker-readline", daemon=True)
            t.start()
            while t.is_alive():
                rem = deadline - time.monotonic()
                if rem <= 0:
                    raise TimeoutError(f"OCR worker loop timed out after {timeout:.0f}s")
                if self._proc.poll() is not None:
                    t.join(timeout=0.2)
                    raise RuntimeError(
                        f"OCR worker loop exited early code={self._proc.returncode}"
                    )
                t.join(timeout=min(0.25, rem))
            if err:
                raise err[0]
            line = (box[0] or "").strip()
            if not line:
                return None
            # Skip non-JSON noise (logging must not land on stdout, but be resilient).
            if not line.startswith("{"):
                continue
            try:
                data = json.loads(line)
            except json.JSONDecodeError as exc:
                raise RuntimeError(f"OCR worker loop bad JSON: {line[:200]!r}") from exc
            return data if isinstance(data, dict) else None

    def close(self) -> None:
        with self._lock:
            self._close_unlocked(join=True)

    def _close_unlocked(self, *, join: bool) -> None:
        proc = self._proc
        self._proc = None
        if proc is not None:
            try:
                if proc.poll() is None and proc.stdin:
                    proc.stdin.write(json.dumps({"cmd": "quit"}) + "\n")
                    proc.stdin.flush()
            except Exception:  # noqa: BLE001
                pass
            try:
                if join:
                    proc.wait(timeout=5)
                elif proc.poll() is None:
                    proc.kill()
            except Exception:  # noqa: BLE001
                try:
                    proc.kill()
                except Exception:  # noqa: BLE001
                    pass
            try:
                if proc.stdin:
                    proc.stdin.close()
            except Exception:  # noqa: BLE001
                pass
            try:
                if proc.stdout:
                    proc.stdout.close()
            except Exception:  # noqa: BLE001
                pass
        if self._stderr_file is not None:
            try:
                self._stderr_file.close()
            except Exception:  # noqa: BLE001
                pass
            self._stderr_file = None
        breadcrumb("ocr-worker-loop closed")


def _run_ocr_on_image_file(
    *,
    image_path: Path,
    out_path: Path,
    roi_only: bool,
    lang: str | None,
    backend: str | None,
    pair_hint: int | None,
    prefer_with: bool | None,
    premium_ceiling: tuple[int, int] | None,
    skip_expensive_fallback: bool,
) -> dict[str, Any]:
    """Shared one-shot / loop body: load PNG → ROI (+ optional panel) → payload."""
    from .capture import _crop_results_rois, _png_from_image, ocr_png_variants
    from .ocr_parse import choose_best_report, parse_rewards_from_ocr_text
    from .roi_rewards import extract_roi_reward_variants, last_calib_hit_hint
    from .settings import load_settings

    image = Image.open(image_path).convert("RGB")
    settings = load_settings()
    use_lang = lang or settings.wt_ui_language or settings.language
    use_backend = backend or getattr(settings, "ocr_backend", "auto") or "auto"
    use_prefer = (
        bool(prefer_with)
        if prefer_with is not None
        else bool(settings.has_premium_account)
    )

    breadcrumb("ocr-worker extract_roi")
    variants: list[tuple[str, str]] = []
    try:
        variants.extend(
            extract_roi_reward_variants(
                image,
                prefer_with=use_prefer,
                pair_hint=pair_hint,
                premium_ceiling=premium_ceiling,
                skip_expensive_fallback=skip_expensive_fallback,
            )
        )
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
        if skip_expensive_fallback:
            log.info("OCR worker: skip panel engines (expensive fallback disabled)")
            breadcrumb("ocr-worker skip-panel")
        else:
            breadcrumb("ocr-worker panel engines")
            for roi_tag, crop in _crop_results_rois(image):
                png = _png_from_image(crop)
                for eng_tag, text in ocr_png_variants(
                    png, wt_ui_language=use_lang, backend=use_backend
                ):
                    variants.append((f"{eng_tag}/{roi_tag}", text))

    payload: dict[str, Any] = {
        "ok": True,
        "variants": [[tag, text] for tag, text in variants],
        "roi_confident": bool(roi_confident),
    }
    if roi_best is not None:
        payload["rp"] = roi_best[1].research_points
        payload["sl"] = roi_best[1].silver_lions
        payload["confidence"] = roi_best[1].confidence
    hint = last_calib_hit_hint()
    if hint is not None:
        calib: dict[str, Any] = {
            "prefer_with": hint.prefer_with,
            "pair_index": hint.pair_index,
        }
        if hint.premium_ceiling is not None:
            calib["premium_ceiling"] = [
                int(hint.premium_ceiling[0]),
                int(hint.premium_ceiling[1]),
            ]
        payload["calib_hint"] = calib

    tmp = out_path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    tmp.replace(out_path)
    breadcrumb(f"ocr-worker done variants={len(variants)}")
    return payload


def ocr_worker_main(
    *,
    image_path: Path,
    out_path: Path,
    roi_only: bool,
    lang: str | None,
    backend: str | None,
    pair_hint: int | None = None,
    prefer_with: bool | None = None,
    premium_ceiling: tuple[int, int] | None = None,
    skip_expensive_fallback: bool = False,
) -> int:
    """Child entry: load PNG, run ROI (+ optional panel) OCR, write JSON."""
    os.environ[_WORKER_ENV] = "1"
    from .crashguard import breadcrumb, install_crash_guard

    install_crash_guard()
    breadcrumb(f"ocr-worker begin image={image_path}")

    try:
        _run_ocr_on_image_file(
            image_path=image_path,
            out_path=out_path,
            roi_only=roi_only,
            lang=lang,
            backend=backend,
            pair_hint=pair_hint,
            prefer_with=prefer_with,
            premium_ceiling=premium_ceiling,
            skip_expensive_fallback=skip_expensive_fallback,
        )
    except OSError as exc:
        out_path.write_text(
            json.dumps({"ok": False, "error": str(exc), "variants": []}),
            encoding="utf-8",
        )
        return 2
    return 0


def ocr_worker_loop_main() -> int:
    """Long-lived child: JSON lines on stdin, JSON lines on stdout."""
    os.environ[_WORKER_ENV] = "1"
    from .crashguard import breadcrumb, install_crash_guard

    install_crash_guard()
    breadcrumb("ocr-worker-loop begin")
    # Signal ready after imports/crash guard so the parent can skip cold wait later.
    sys.stdout.write(json.dumps({"ready": True}) + "\n")
    sys.stdout.flush()

    for raw in sys.stdin:
        line = (raw or "").strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except json.JSONDecodeError:
            sys.stdout.write(
                json.dumps({"ok": False, "error": "bad json", "variants": []}) + "\n"
            )
            sys.stdout.flush()
            continue
        if not isinstance(req, dict):
            continue
        cmd = str(req.get("cmd") or "")
        if cmd == "quit":
            breadcrumb("ocr-worker-loop quit")
            break
        if cmd != "ocr":
            sys.stdout.write(
                json.dumps({"ok": False, "error": f"unknown cmd {cmd}", "variants": []})
                + "\n"
            )
            sys.stdout.flush()
            continue
        try:
            image_path = Path(str(req["image"]))
            out_path = Path(str(req["out"]))
            ceiling_raw = req.get("premium_ceiling")
            ceiling: tuple[int, int] | None = None
            if (
                isinstance(ceiling_raw, (list, tuple))
                and len(ceiling_raw) >= 2
                and ceiling_raw[0] is not None
                and ceiling_raw[1] is not None
            ):
                ceiling = (int(ceiling_raw[0]), int(ceiling_raw[1]))
            prefer_raw = req.get("prefer_with", None)
            prefer: bool | None
            if prefer_raw is None:
                prefer = None
            else:
                prefer = bool(prefer_raw)
            pair_raw = req.get("pair_hint", None)
            pair_hint = int(pair_raw) if pair_raw is not None else None
            payload = _run_ocr_on_image_file(
                image_path=image_path,
                out_path=out_path,
                roi_only=bool(req.get("roi_only")),
                lang=req.get("lang"),
                backend=req.get("backend"),
                pair_hint=pair_hint,
                prefer_with=prefer,
                premium_ceiling=ceiling,
                skip_expensive_fallback=bool(req.get("skip_expensive_fallback")),
            )
            payload["id"] = req.get("id")
            sys.stdout.write(json.dumps(payload, ensure_ascii=False) + "\n")
            sys.stdout.flush()
        except Exception as exc:  # noqa: BLE001
            log.exception("ocr-worker-loop request failed")
            sys.stdout.write(
                json.dumps(
                    {
                        "ok": False,
                        "error": str(exc),
                        "variants": [],
                        "id": req.get("id"),
                    }
                )
                + "\n"
            )
            sys.stdout.flush()
    return 0
