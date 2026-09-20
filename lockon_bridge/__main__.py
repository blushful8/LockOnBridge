from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from pathlib import Path

from . import __version__
from .crashguard import FlushingRotatingFileHandler, breadcrumb, install_crash_guard
from .paths import log_file
from .process_watch import (
    is_war_thunder_running,
    wait_until_war_thunder_starts,
    wait_until_war_thunder_stops,
)
from .runtime import BridgeRuntime, RuntimeConfig
from .settings import load_settings


def _configure_logging(log_path: Path | None, verbose: bool) -> None:
    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(logging.DEBUG if verbose else logging.INFO)
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    # Avoid console spam for frozen GUI / background agent.
    if not getattr(sys, "frozen", False) or verbose:
        stream = logging.StreamHandler(sys.stdout)
        stream.setFormatter(formatter)
        root.addHandler(stream)
    if log_path is not None:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        file_handler = FlushingRotatingFileHandler(
            log_path,
            maxBytes=1_048_576,
            backupCount=5,
            encoding="utf-8",
        )
        file_handler.setFormatter(formatter)
        root.addHandler(file_handler)


def run_auto(config: RuntimeConfig, idle_poll_sec: float) -> int:
    """CLI-only War Thunder linked mode (no UI)."""
    log = logging.getLogger("lockon_bridge")
    log.info("LockOn Bridge v%s — auto mode (War Thunder linked)", __version__)
    runtime = BridgeRuntime(config)
    try:
        while True:
            if not is_war_thunder_running():
                log.info(
                    "War Thunder not running — idle (poll every %.0fs)",
                    idle_poll_sec,
                )
                wait_until_war_thunder_starts(idle_poll_sec=idle_poll_sec)
            log.info("War Thunder detected — starting Bridge")
            runtime.start()
            wait_until_war_thunder_stops(check_sec=2.0)
            runtime.stop()
            time.sleep(1.0)
    except KeyboardInterrupt:
        log.info("stopping…")
        runtime.stop()
        return 0


def run_session(config: RuntimeConfig) -> int:
    log = logging.getLogger("lockon_bridge")
    log.info("LockOn Bridge v%s — session mode (Ctrl+C to stop)", __version__)
    log.info("Phone API: http://<PC_IP>:%s/v1/latest-report", config.port)
    runtime = BridgeRuntime(config)
    runtime.start()
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        log.info("stopping…")
        runtime.stop()
        return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="LockOn Bridge — OCR War Thunder results for the phone app",
    )
    parser.add_argument("--bind", default=None, help="HTTP bind address")
    parser.add_argument("--port", type=int, default=None, help="HTTP port for the phone")
    parser.add_argument("--game-host", default="127.0.0.1")
    parser.add_argument("--game-port", type=int, default=8111)
    parser.add_argument("--frames", type=int, default=5, help="Screenshots after hangar")
    parser.add_argument("--frame-gap", type=float, default=1.2, help="Seconds between frames")
    parser.add_argument(
        "--poll",
        type=float,
        default=2.0,
        help="Hangar phase poll interval while War Thunder is running",
    )
    parser.add_argument(
        "--idle-poll",
        type=float,
        default=None,
        help="How often to check for aces.exe while idle (default 120s)",
    )
    parser.add_argument(
        "--session",
        action="store_true",
        help="Keep Bridge running until Ctrl+C (ignore War Thunder process)",
    )
    parser.add_argument(
        "--background",
        action="store_true",
        help="Autostart mode: tray + agent if enabled; exit immediately if disabled",
    )
    parser.add_argument(
        "--ui",
        action="store_true",
        help="Open the control window (default for the .exe)",
    )
    parser.add_argument(
        "--uninstall",
        action="store_true",
        help="Remove autostart, firewall rule, and local files",
    )
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument(
        "--log-file",
        default=str(log_file()),
        help="Rotating log path (empty string disables file log)",
    )
    parser.add_argument(
        "--self-test",
        action="store_true",
        help="Run offline OCR parser checks (fixtures + bridge.log) — no match needed",
    )
    parser.add_argument(
        "--ocr-once",
        action="store_true",
        help="Capture the screen once, OCR, print RP/SL (open a results screenshot first)",
    )
    parser.add_argument(
        "--ocr-worker",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    parser.add_argument("--image", default=None, help=argparse.SUPPRESS)
    parser.add_argument("--out", default=None, help=argparse.SUPPRESS)
    parser.add_argument("--roi-only", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--lang", default=None, help=argparse.SUPPRESS)
    parser.add_argument("--backend", default=None, help=argparse.SUPPRESS)
    parser.add_argument(
        "--serve-test",
        action="store_true",
        help="Serve a fixed test RP/SL report on the Bridge port (no War Thunder)",
    )
    parser.add_argument("--test-rp", type=int, default=1088, help="RP for --serve-test")
    parser.add_argument("--test-sl", type=int, default=6016, help="SL for --serve-test")
    args = parser.parse_args(argv)

    log_path = Path(args.log_file) if args.log_file else None
    _configure_logging(log_path, verbose=args.verbose)
    install_crash_guard()
    breadcrumb(f"main argv={argv or sys.argv[1:]}")

    if args.uninstall:
        from .autostart import full_uninstall

        full_uninstall()
        return 0

    if args.ocr_worker:
        from .ocr_isolate import ocr_worker_main

        if not args.image or not args.out:
            print("--ocr-worker requires --image and --out", file=sys.stderr)
            return 2
        return ocr_worker_main(
            image_path=Path(args.image),
            out_path=Path(args.out),
            roi_only=bool(args.roi_only),
            lang=args.lang,
            backend=args.backend,
        )

    if args.self_test:
        from .selftest import run_self_test

        return run_self_test()

    if args.ocr_once:
        os.environ["LOCKON_OCR_WORKER"] = "1"
        from .selftest import run_ocr_once_cli

        return run_ocr_once_cli()

    if args.serve_test:
        from .selftest import run_serve_test
        from .settings import load_settings

        settings = load_settings()
        port = args.port if args.port is not None else settings.port
        bind = args.bind if args.bind is not None else settings.bind
        return run_serve_test(
            bind=bind,
            port=port,
            research_points=args.test_rp,
            silver_lions=args.test_sl,
        )

    # Default for frozen exe: UI. For python -m: keep CLI unless --ui/--background.
    want_ui = args.ui or args.background or getattr(sys, "frozen", False)
    if want_ui and not args.session:
        from .dpi import enable_windows_dpi_awareness

        enable_windows_dpi_awareness()
        from .app_ui import run_ui

        return run_ui(start_hidden=bool(args.background))

    settings = load_settings()
    port = args.port if args.port is not None else settings.port
    bind = args.bind if args.bind is not None else settings.bind
    idle = args.idle_poll if args.idle_poll is not None else settings.idle_poll_sec

    config = RuntimeConfig(
        bind=bind,
        port=port,
        game_host=args.game_host,
        game_port=args.game_port,
        frames=args.frames,
        frame_gap=args.frame_gap,
        poll_sec=args.poll,
        poll_hangar_sec=args.poll,
        poll_battle_sec=max(args.poll, 2.5),
    )
    if args.session:
        return run_session(config)
    return run_auto(config, idle_poll_sec=idle)


if __name__ == "__main__":
    sys.exit(main())
