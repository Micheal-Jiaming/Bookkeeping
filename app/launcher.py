"""Desktop entry point: what the .exe actually runs.

Turning a local web app into something that behaves like a normal Windows
program takes four things, and this module is all four:

1. **Pick a writable data directory** before anything imports the database, so
   a portable copy keeps its books beside the .exe (see ``app/paths.py``).
2. **Find a free port, or hand off to a copy that is already running.** Double
   clicking the .exe twice should open a second browser tab, not a second
   server. A ``GET /api/health`` on the port tells the two cases apart.
3. **Open the browser** at the right address.
4. **Exit when the user is done** -- the Quit button, or no heartbeat from any
   open tab for a while (see ``app/runtime.py``). A windowed .exe has no console
   to close, so without this the process would linger invisibly.

It also owns logging. A windowed PyInstaller build has ``sys.stdout is None``,
which makes uvicorn's default logging configuration raise on startup; the fix is
``log_config=None`` plus our own file handler.
"""

from __future__ import annotations

import argparse
import logging
import socket
import sys
import threading
import time
import urllib.error
import urllib.request
import webbrowser
from logging.handlers import RotatingFileHandler
from pathlib import Path

from . import paths
from .runtime import IDLE_TIMEOUT_SECONDS

DEFAULT_PORT = 8765
PORT_SEARCH_LIMIT = 20
HOST = "127.0.0.1"
LOG_FILE_NAME = "bookkeeping.log"

log = logging.getLogger("bookkeeping.launcher")


# --------------------------------------------------------------------------- #
# Argument parsing
# --------------------------------------------------------------------------- #


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="Bookkeeping",
        description="Receipt-image expense tracker. Runs locally; opens in your browser.",
    )
    parser.add_argument(
        "--data-dir",
        metavar="PATH",
        help="Where to keep the database and receipt images "
             "(default: a 'data' folder beside the program).",
    )
    parser.add_argument("--port", type=int, default=DEFAULT_PORT,
                        help=f"Preferred port (default {DEFAULT_PORT}); "
                             "the next free one is used if it is taken.")
    parser.add_argument("--no-browser", action="store_true",
                        help="Do not open a browser window.")
    parser.add_argument("--keep-alive", action="store_true",
                        help="Stay running even when no browser tab is open.")
    parser.add_argument("--idle-timeout", type=float, metavar="SECONDS",
                        help="Seconds without a browser heartbeat before closing "
                             f"(default {int(IDLE_TIMEOUT_SECONDS)}).")
    parser.add_argument("--version", action="store_true", help="Print the version and exit.")
    return parser.parse_args(argv)


# --------------------------------------------------------------------------- #
# Logging
# --------------------------------------------------------------------------- #


def setup_logging(data_dir: Path) -> Path:
    """Log to a rotating file in the data directory, and to the console if there is one."""
    log_path = data_dir / LOG_FILE_NAME
    handlers: list[logging.Handler] = []
    try:
        handlers.append(RotatingFileHandler(
            log_path, maxBytes=512 * 1024, backupCount=2, encoding="utf-8"
        ))
    except OSError:
        pass  # unwritable log is not a reason to refuse to start
    # A windowed build has no stdout at all; adding a StreamHandler on None
    # raises inside logging on the first record.
    if sys.stderr is not None:
        handlers.append(logging.StreamHandler(sys.stderr))
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        handlers=handlers,
        force=True,
    )
    return log_path


# --------------------------------------------------------------------------- #
# Ports and the already-running case
# --------------------------------------------------------------------------- #


def port_is_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            probe.bind((HOST, port))
        except OSError:
            return False
    return True


def existing_instance(port: int, timeout: float = 1.5) -> bool:
    """True if *our* app already answers on this port."""
    try:
        with urllib.request.urlopen(f"http://{HOST}:{port}/api/health", timeout=timeout) as response:
            import json

            return json.load(response).get("app") == "bookkeeping"
    except (urllib.error.URLError, OSError, ValueError):
        return False


def choose_port(preferred: int) -> tuple[int, bool]:
    """Return (port, already_running).

    A busy preferred port that answers as Bookkeeping means a copy is already
    up; anything else busy just means try the next number.
    """
    if not port_is_free(preferred) and existing_instance(preferred):
        return preferred, True
    for candidate in range(preferred, preferred + PORT_SEARCH_LIMIT):
        if port_is_free(candidate):
            return candidate, False
    raise RuntimeError(
        f"No free port between {preferred} and {preferred + PORT_SEARCH_LIMIT - 1}."
    )


# --------------------------------------------------------------------------- #
# Error reporting when there is no console
# --------------------------------------------------------------------------- #


def report_fatal(message: str) -> None:
    """Show a fatal startup error, using a message box when there is no console."""
    log.error(message)
    if sys.stderr is not None:
        print(message, file=sys.stderr)
        return
    try:  # windowed build: a silent exit would be indistinguishable from nothing happening
        import ctypes

        ctypes.windll.user32.MessageBoxW(None, message, "Bookkeeping", 0x10)
    except Exception:
        pass


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    try:
        data_dir = paths.choose_data_dir(args.data_dir)
    except RuntimeError as exc:
        report_fatal(str(exc))
        return 2

    log_path = setup_logging(data_dir)

    # Imported only now: app.db resolves its paths at import time, and it must
    # see the data directory chosen above.
    from .main import app
    from .runtime import runtime

    if args.version:
        print(app.version)
        return 0

    try:
        port, already_running = choose_port(args.port)
    except RuntimeError as exc:
        report_fatal(str(exc))
        return 2

    url = f"http://{HOST}:{port}/"
    if already_running:
        log.info("Bookkeeping is already running on %s; opening a tab instead.", url)
        if not args.no_browser:
            webbrowser.open(url)
        return 0

    import uvicorn

    config = uvicorn.Config(
        app,
        host=HOST,
        port=port,
        log_config=None,      # our own handlers; uvicorn's default needs a real stdout
        log_level="info",
        access_log=False,     # the app's own log lines are the useful ones
        workers=1,
    )
    server = uvicorn.Server(config)

    # Always a desktop process (so the Quit button works); the idle watchdog is
    # what --keep-alive turns off.
    runtime.set_desktop(True)
    if not args.keep_alive:
        runtime.arm(idle_timeout=args.idle_timeout)

    stop_watchdog = threading.Event()

    def request_exit(reason: str) -> None:
        log.info("Shutting down: %s", reason)
        server.should_exit = True

    watchdog = threading.Thread(
        target=runtime.watch, args=(request_exit, stop_watchdog),
        name="lifetime", daemon=True,
    )
    watchdog.start()

    if not args.no_browser:
        # Opened from a thread so a slow default browser cannot delay the server
        # from binding the port it is about to be pointed at.
        threading.Thread(
            target=_open_when_ready, args=(url, port), name="browser", daemon=True
        ).start()

    log.info(
        "Bookkeeping %s starting on %s (data: %s, log: %s, idle exit: %s)",
        app.version, url, data_dir, log_path,
        "off" if args.keep_alive else f"{int(runtime.idle_timeout)}s",
    )
    try:
        server.run()
    except KeyboardInterrupt:
        pass
    finally:
        stop_watchdog.set()
        log.info("Bookkeeping stopped.")
    return 0


def _open_when_ready(url: str, port: int, timeout: float = 15.0) -> None:
    """Wait for the port to answer, then open the browser."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.settimeout(0.4)
            if probe.connect_ex((HOST, port)) == 0:
                webbrowser.open(url)
                return
        time.sleep(0.2)
    log.warning("The server did not come up within %ss; not opening a browser.", timeout)
