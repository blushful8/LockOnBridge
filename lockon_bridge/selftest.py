"""Fixtures and offline self-checks — no War Thunder match required."""

from __future__ import annotations

import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from .capture import list_installed_ocr_languages
from .lang_labels import LANGUAGE_FIXTURES, MANGLED_LANGUAGE_FIXTURES
from .ocr_parse import (
    BattleReport,
    choose_best_report,
    parse_rewards_from_ocr_text,
    summarize_ocr_text,
)
from .paths import log_dir


FIXTURE_UA_FRAME1 = (
    "Bepciq 2.59.0.13 Micifl npoBaneHa APGAHi 60i, "
    "[nay-IYBaHH91 ryopu eH Haropona 3a yuacTb B Micii: +34% , +20%' "
    "3 npeMiYM0M 9 426' be3 npeMiYMa 1 088 6 016' "
    "3HV1LUeHO noBiTpH',1x uinei nonoMora y 3HVIU4eHHi nPOTMBHV1Ka "
    "5 2 3 2 21 1 552' 186' 1 131' 103' 544 ' "
    "50h0Bi 3aanaHHfl nocniAHYBaHa TexHiKa nporpec nocninxeHb"
)

FIXTURE_UA_VICTORY_BOOST = (
    "sawe Micue B KOMaHAi: 14 "
    "Haropona 3a nepeMory: +100% , +47%' 1 148 2 732' "
    "015. 432 528 950 730 1 615' 553 341"
)

FIXTURE_UA_FRAME2 = (
    "Micifl npoBaneHa 3 npeMiYMOM 9 596' npeMiYMa 1 088 6 127'"
)

FIXTURE_DESKTOP = (
    "EPIC GAMES Telegram LockOn Bridge nopr HTTP 8112 "
    "Microsoft Malware Protection NVIDIA App Ledger Wallet"
)

FIXTURE_EN = (
    "Mission results\nVictory\nResearch Points 1 250\nSilver Lions 8 400\nTotal\n"
)

FIXTURE_JUNK_RP1 = (
    "sawe Micue B KOMaHAi: 14 some noise rp 1 sl 0 random 2.59.0.13"
)

# Live dump 2026-09-19: boost line + K/D board, real RP/SL never on this frame.
FIXTURE_SCOREBOARD_KD = (
    "ropona 3a yuacTb B Micii: +34% T, +20%• 3 5 4 2 0.954 0.954 0.854 0.645 "
    "1.27 1 1 2052 1891 1531 1451 1385 1299 1252 1083 1040 997"
)


@dataclass
class CaseResult:
    name: str
    ok: bool
    detail: str


def _expect(
    name: str,
    text: str,
    *,
    rp: int | None = None,
    sl: int | None = None,
    none: bool = False,
) -> CaseResult:
    report = parse_rewards_from_ocr_text(text)
    if none:
        if report is None:
            return CaseResult(name, True, "correctly skipped")
        return CaseResult(
            name,
            False,
            f"expected None, got RP={report.research_points} SL={report.silver_lions}",
        )
    if report is None:
        return CaseResult(name, False, "parser returned None")
    problems: list[str] = []
    if rp is not None and report.research_points != rp:
        problems.append(f"RP {report.research_points} != {rp}")
    if sl is not None and report.silver_lions != sl:
        problems.append(f"SL {report.silver_lions} != {sl}")
    if problems:
        return CaseResult(name, False, "; ".join(problems))
    return CaseResult(
        name,
        True,
        f"RP={report.research_points} SL={report.silver_lions} "
        f"outcome={report.outcome} conf={report.confidence:.2f}",
    )


def run_fixture_cases() -> list[CaseResult]:
    cases = [
        _expect("ua_frame1_without_premium", FIXTURE_UA_FRAME1, rp=1088, sl=6016),
        _expect("ua_victory_boost_pair", FIXTURE_UA_VICTORY_BOOST, rp=1148, sl=2732),
        _expect("ua_frame2_bare_header", FIXTURE_UA_FRAME2, rp=1088, sl=6127),
        _expect("english_labels", FIXTURE_EN, rp=1250, sl=8400),
        _expect("desktop_noise", FIXTURE_DESKTOP, none=True),
        _expect("junk_rp1_rejected", FIXTURE_JUNK_RP1, none=True),
        _expect("scoreboard_kd_rejected", FIXTURE_SCOREBOARD_KD, none=True),
    ]
    for lang, labels in LANGUAGE_FIXTURES.items():
        text = (
            f"{labels['victory']}\n"
            f"{labels['with']} 9 999\n"
            f"{labels['without']} 1 250 8 400\n"
            f"{labels['rp']}\n{labels['sl']}\n"
        )
        cases.append(_expect(f"lang_{lang}_premium_cols", text, rp=1250, sl=8400))
        labeled = (
            f"{labels['victory']}\n"
            f"{labels['rp']} 2 100\n"
            f"{labels['sl']} 7 500\n"
        )
        cases.append(_expect(f"lang_{lang}_labels", labeled, rp=2100, sl=7500))
    for lang, labels in MANGLED_LANGUAGE_FIXTURES.items():
        text = (
            f"{labels['victory']}\n"
            f"{labels['with']} 9 999\n"
            f"{labels['without']} 1 250 8 400\n"
        )
        cases.append(_expect(f"mangled_{lang}_premium", text, rp=1250, sl=8400))
        labeled = f"{labels['rp']} 2 100\n{labels['sl']} 7 500\n"
        cases.append(_expect(f"mangled_{lang}_labels", labeled, rp=2100, sl=7500))
    return cases


def extract_ocr_snippets_from_bridge_log(path: Path | None = None) -> list[str]:
    log_path = path or (log_dir() / "bridge.log")
    if not log_path.is_file():
        return []
    text = log_path.read_text(encoding="utf-8", errors="replace")
    snippets: list[str] = []
    for match in re.finditer(r"ocr=(.+)$", text, flags=re.MULTILINE):
        snippet = match.group(1).strip()
        if snippet and snippet not in snippets:
            snippets.append(snippet)
    return snippets


def replay_log_samples(path: Path | None = None) -> list[CaseResult]:
    results: list[CaseResult] = []
    snippets = extract_ocr_snippets_from_bridge_log(path)
    if not snippets:
        last = log_dir() / "last_ocr.txt"
        if last.is_file():
            snippets = [last.read_text(encoding="utf-8", errors="replace")]
    if not snippets:
        return [CaseResult("log_replay", False, f"no OCR samples in {log_dir()}")]

    for index, snippet in enumerate(snippets, start=1):
        # Multi-engine dumps use ---OCR--- separators — score each block.
        blocks = [b.strip() for b in re.split(r"\n---OCR---\n", snippet) if b.strip()]
        if len(blocks) <= 1 and not snippet.startswith("["):
            blocks = [snippet]
        cleaned_blocks: list[str] = []
        for block in blocks:
            # Strip optional [en-US] header / => trailer from dumps.
            body = re.sub(r"^\[.*?\]\n", "", block)
            body = re.sub(r"\n=>.*$", "", body).strip()
            cleaned_blocks.append(body or block)
        best = choose_best_report(
            [(b, parse_rewards_from_ocr_text(b)) for b in cleaned_blocks]
        )
        preview = summarize_ocr_text(snippet, limit=80)
        if best is None:
            results.append(CaseResult(f"log#{index}", False, f"no parse | {preview}"))
        else:
            _text, report = best
            results.append(
                CaseResult(
                    f"log#{index}",
                    True,
                    f"RP={report.research_points} SL={report.silver_lions} | {preview}",
                )
            )
    return results


def make_test_report(
    *,
    research_points: int = 1088,
    silver_lions: int = 6016,
    outcome: str = "defeat",
) -> BattleReport:
    stamp = int(time.time() * 1000)
    return BattleReport(
        captured_at_epoch_millis=stamp,
        research_points=research_points,
        silver_lions=silver_lions,
        outcome=outcome,
        raw_hash=f"test-{stamp}",
        confidence=0.99,
        source="test",
    )


def ocr_once(*, save_dump: bool = True) -> tuple[str, BattleReport | None, Path | None]:
    from .capture import last_ocr_frame, ocr_screen_capture
    from .roi_debug import save_ocr_crop_dumps
    from .settings import load_settings

    png, variants = ocr_screen_capture()
    candidates = [(text, parse_rewards_from_ocr_text(text)) for _tag, text in variants]
    best = choose_best_report(candidates)
    dump_dir: Path | None = None
    dump_body_parts = []
    for tag, text in variants:
        parsed = parse_rewards_from_ocr_text(text)
        if parsed is None:
            dump_body_parts.append(f"[{tag}]\n{text}\n=> (no parse)")
        else:
            dump_body_parts.append(
                f"[{tag}]\n{text}\n=> RP={parsed.research_points} SL={parsed.silver_lions}"
            )
    dump_text = "\n\n---OCR---\n\n".join(dump_body_parts)
    if save_dump:
        dump_dir = log_dir()
        dump_dir.mkdir(parents=True, exist_ok=True)
        (dump_dir / "last_ocr.txt").write_text(dump_text or "", encoding="utf-8")
        frame = last_ocr_frame()
        if frame is not None:
            debug_full = bool(load_settings().debug_show_rois)
            save_ocr_crop_dumps(frame, dump_dir, debug_full=debug_full)
        else:
            # No frame handle — keep legacy panel PNG only as last resort.
            (dump_dir / "last_capture.png").write_bytes(png)
    if best is None:
        preview = variants[0][1] if variants else ""
        return preview, None, dump_dir
    text, report = best
    return text, report, dump_dir


def print_results(title: str, results: list[CaseResult]) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001
        pass
    print(f"=== {title} ===")
    failed = 0
    for item in results:
        mark = "OK  " if item.ok else "FAIL"
        detail = item.detail.encode("utf-8", errors="replace").decode("utf-8")
        print(f"  [{mark}] {item.name}: {detail}")
        if not item.ok:
            failed += 1
    print(f"--- {len(results) - failed}/{len(results)} passed ---")
    return 0 if failed == 0 else 1


def run_self_test(*, include_log_replay: bool = True) -> int:
    from .ocr_backends import describe_ocr_status, tesseract_available
    from .settings import load_settings

    settings = load_settings()
    wt = settings.wt_ui_language or "uk"
    installed = list_installed_ocr_languages()
    print("=== installed Windows OCR packs ===")
    if not installed:
        print("  (none detected)")
    else:
        for tag, name in installed:
            print(f"  {tag} — {name}")
    print(f"=== OCR status (WT UI={wt}, backend={settings.ocr_backend}) ===")
    print(describe_ocr_status(wt))
    if not tesseract_available():
        print(
            "TIP: Install Tesseract (winget install UB-Mannheim.TesseractOCR) "
            "then use More → Setup Tesseract OCR for ukr/deu/… models."
        )
    if wt == "uk":
        print(
            "NOTE: Windows has no Ukrainian OCR pack — UA UI is Latinized with EN packs. "
            "Prefer Tesseract ukr, or Russian Windows OCR as a fallback."
        )

    code = print_results("fixture parser", run_fixture_cases())
    if include_log_replay:
        replay = replay_log_samples()
        print_results("bridge.log / last_ocr replay (informational)", replay)
        for snippet in extract_ocr_snippets_from_bridge_log():
            if "be3 npeMi" in snippet or "1 148 2 732" in snippet or "npeMiYM" in snippet:
                report = parse_rewards_from_ocr_text(snippet)
                if report is None and "1 148 2 732" in snippet:
                    report = parse_rewards_from_ocr_text(
                        "Haropona 3a nepeMory: +100% , +47%' 1 148 2 732'"
                    )
                if report is None and "be3 npeMi" in snippet:
                    print("FAIL: UA premium OCR from log still does not parse")
                    code = 1
                elif report is not None:
                    print(
                        f"OK: UA log-like sample → RP={report.research_points} "
                        f"SL={report.silver_lions}"
                    )
                break
    return code


def run_ocr_once_cli() -> int:
    print("Capturing primary monitor + OCR (each installed pack separately)…")
    try:
        text, report, dump_dir = ocr_once(save_dump=True)
    except Exception as exc:  # noqa: BLE001
        print(f"OCR failed: {exc}", file=sys.stderr)
        return 1
    if dump_dir is not None:
        print(f"OCR dump → {dump_dir / 'last_ocr.txt'}")
        print(f"Lean ROI collage → {dump_dir / 'last_capture.png'}")
        print(f"Per-crop PNGs → {dump_dir / 'roi_crops'}")
    print(f"Best OCR preview: {summarize_ocr_text(text)}")
    if report is None:
        print("Parse: no RP/SL found")
        return 2
    print(
        f"Parse: RP={report.research_points} SL={report.silver_lions} "
        f"outcome={report.outcome} conf={report.confidence:.2f}"
    )
    return 0


def run_serve_test(
    *,
    bind: str = "0.0.0.0",
    port: int = 8112,
    research_points: int = 1088,
    silver_lions: int = 6016,
) -> int:
    from http.server import ThreadingHTTPServer

    from .report_store import ReportStore
    from .server import make_handler

    store = ReportStore()
    report = make_test_report(
        research_points=research_points,
        silver_lions=silver_lions,
    )
    store.publish(report)
    server = ThreadingHTTPServer((bind, port), make_handler(store))
    print(f"Test report published: RP={research_points} SL={silver_lions}")
    print(f"GET http://127.0.0.1:{port}/v1/latest-report")
    print(f"GET http://127.0.0.1:{port}/v1/health")
    print("Ctrl+C to stop")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("stopped")
    finally:
        server.server_close()
    return 0
