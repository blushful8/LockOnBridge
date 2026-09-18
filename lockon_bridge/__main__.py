from __future__ import annotations

import argparse
import logging
import sys
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path

from . import __version__
from .process_watch import (
    is_war_thunder_running,
    wait_until_war_thunder_starts,
    wait_until_war_thunder_stops,
)
from .runtime import BridgeRuntime, RuntimeConfig


def _configure_logging(log_file: Path | None, verbose: bool) -> None:
    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(logging.DEBUG if verbose else logging.INFO)
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    stream = logging.StreamHandler(sys.stdout)
    stream.setFormatter(formatter)
    root.addHandler(stream)
    if log_file is not None:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(
            log_file,
            maxBytes=512_000,
            backupCount=2,
            encoding="utf-8",
        )
        file_handler.setFormatter(formatter)
        root.addHandler(file_handler)


def _default_log_path() -> Path:
    base = Path.home() / "AppData" / "Local" / "LockOnBridge" / "logs"
    return base / "bridge.log"


def run_auto(config: RuntimeConfig, idle_poll_sec: float) -> int:
    """
    Install-once mode: sleep until War Thunder starts, run Bridge, shut down hard when it exits.
    Idle path only checks for aces.exe — no OCR, no :8111, no HTTP socket.
    """
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
            # Brief pause so a launcher restart does not flap.
            time.sleep(1.0)
    except KeyboardInterrupt:
        log.info("stopping…")
        runtime.stop()
        return 0


def run_session(config: RuntimeConfig) -> int:
    """Manual / debug: Bridge stays up until Ctrl+C (does not follow aces.exe)."""
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
    parser.add_argument("--bind", default="0.0.0.0", help="HTTP bind address")
    parser.add_argument("--port", type=int, default=8112, help="HTTP port for the phone")
    parser.add_argument("--game-host", default="127.0.0.1")
    parser.add_argument("--game-port", type=int, default=8111)
    parser.add_argument("--frames", type=int, default=5, help="Screenshots after hangar")
    parser.add_argument("--frame-gap", type=float, default=1.2, help="Seconds between frames")
    parser.add_argument(
        "--poll",
        type=float,
        default=1.5,
        help="Phase poll interval while War Thunder is running",
    )
    parser.add_argument(
        "--idle-poll",
        type=float,
        default=30.0,
        help="How often to check for aces.exe while idle (default 30s, energy-saving)",
    )
    parser.add_argument(
        "--session",
        action="store_true",
        help="Keep Bridge running until Ctrl+C (ignore War Thunder process)",
    )
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument(
        "--log-file",
        default=str(_default_log_path()),
        help="Rotating log path (empty string disables file log)",
    )
    args = parser.parse_args(argv)

    log_path = Path(args.log_file) if args.log_file else None
    _configure_logging(log_path, verbose=args.verbose)

    config = RuntimeConfig(
        bind=args.bind,
        port=args.port,
        game_host=args.game_host,
        game_port=args.game_port,
        frames=args.frames,
        frame_gap=args.frame_gap,
        poll_sec=args.poll,
    )
    if args.session:
        return run_session(config)
    return run_auto(config, idle_poll_sec=args.idle_poll)


if __name__ == "__main__":
    sys.exit(main())
