"""Program startup: data directory, logging, single-instance guard, then the window.

Everything here runs before the interface exists, and in an order that matters:

1. **Choose the data directory first.** ``app/db.py`` resolves its paths at import
   time, so the writable location has to be settled and published through
   ``BOOKKEEPING_DATA`` before anything imports it.
2. **Set up logging second**, so a failure in step 3 is recorded.
3. **Take the per-data-directory lock.** Two windows editing one set of books is
   confusing; two portable copies in different folders are perfectly fine, so the
   lock lives in the data directory rather than being global to the machine.
4. Only then build the window.

A windowed build has no console: ``sys.stdout`` is ``None``, printing is
pointless, and an unhandled exception would vanish. Hence the file log and the
message box.
"""

from __future__ import annotations

import argparse
import logging
import sys
import traceback
from logging.handlers import RotatingFileHandler
from pathlib import Path

from . import paths

LOG_FILE_NAME = "bookkeeping.log"
LOCK_FILE_NAME = ".lock"

log = logging.getLogger("bookkeeping.launcher")

# Held open for the life of the process; closing it releases the lock.
_lock_handle = None


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="Bookkeeping",
        description="Reads receipt photos and keeps the expenses in order.",
    )
    parser.add_argument(
        "--data-dir", metavar="PATH",
        help="Where to keep the books, images and log "
             "(default: a 'data' folder beside the program).")
    parser.add_argument("--version", action="store_true",
                        help="Print the version and exit.")
    parser.add_argument("--allow-second-window", action="store_true",
                        help="Skip the single-instance check for this data folder.")
    return parser.parse_args(argv)


def setup_logging(data_dir: Path) -> Path:
    log_path = data_dir / LOG_FILE_NAME
    handlers: list[logging.Handler] = []
    try:
        handlers.append(RotatingFileHandler(
            log_path, maxBytes=512 * 1024, backupCount=2, encoding="utf-8"))
    except OSError:
        pass  # an unwritable log is not a reason to refuse to start
    if sys.stderr is not None:
        handlers.append(logging.StreamHandler(sys.stderr))
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        handlers=handlers, force=True,
    )
    return log_path


def acquire_lock(data_dir: Path) -> bool:
    """Take an exclusive lock on this data folder. False if someone else has it.

    The lock is an OS-level file lock, so it is released even if the program is
    killed -- a PID file would go stale after a crash and lock the user out of
    their own books.
    """
    global _lock_handle
    path = data_dir / LOCK_FILE_NAME
    try:
        handle = path.open("a+b")
    except OSError:
        return True  # cannot create a lock file: better to run than to refuse
    try:
        if sys.platform == "win32":
            import msvcrt

            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:  # pragma: no cover - this build is Windows only
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        return False
    _lock_handle = handle
    return True


def report_fatal(title: str, message: str) -> None:
    """Show a startup failure even when there is no console to print to."""
    log.error("%s: %s", title, message)
    if sys.stderr is not None:
        print(f"{title}: {message}", file=sys.stderr)
    try:
        import ctypes

        ctypes.windll.user32.MessageBoxW(None, message, f"Bookkeeping — {title}", 0x10)
    except Exception:
        pass


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    try:
        data_dir = paths.choose_data_dir(args.data_dir)
    except RuntimeError as exc:
        report_fatal("Cannot start", str(exc))
        return 2

    log_path = setup_logging(data_dir)

    from . import ui
    from .ui.window import version

    if args.version:
        print(version())
        return 0

    if not args.allow_second_window and not acquire_lock(data_dir):
        report_fatal(
            "Already running",
            "Bookkeeping is already open for these books:\n\n"
            f"{data_dir}\n\n"
            "Switch to the window that is already open. (Use "
            "--allow-second-window if you really do want two.)")
        return 0

    log.info("Bookkeeping %s starting (data: %s, log: %s)",
             version(), data_dir, log_path)
    try:
        return ui.run()
    except Exception:
        # A traceback in a windowed build would otherwise be lost completely.
        detail = traceback.format_exc()
        log.error("Unhandled error in the interface:\n%s", detail)
        report_fatal(
            "Unexpected error",
            "Bookkeeping hit an unexpected problem and has to close.\n\n"
            f"{detail.strip().splitlines()[-1]}\n\n"
            f"The full details are in:\n{log_path}")
        return 1
    finally:
        log.info("Bookkeeping stopped.")
