"""Where things live, in source checkouts and inside the frozen .exe alike.

Two different questions, and conflating them is the classic PyInstaller bug:

* **Bundled resources** (the static UI files) are read-only and live wherever
  the one-file bootloader unpacked them -- ``sys._MEIPASS``, a fresh temp
  directory on every launch. Never write there; it disappears on exit.
* **The user's data** (database, receipt images, log) must live somewhere
  persistent *outside* the bundle. For a portable app that means beside the
  .exe, so copying the .exe and its ``data`` folder to a USB stick moves the
  whole installation.

The fallback matters: an .exe run from ``C:\\Program Files`` or a read-only
share cannot write beside itself, so the data directory falls back to
``%LOCALAPPDATA%\\Bookkeeping``. Resolution never happens silently at import
time -- the launcher decides once, creates the directory, and passes the answer
to the rest of the app through ``BOOKKEEPING_DATA``.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

DATA_DIR_ENV = "BOOKKEEPING_DATA"


def is_frozen() -> bool:
    """True when running from a PyInstaller-built executable."""
    return bool(getattr(sys, "frozen", False))


def program_dir() -> Path:
    """The folder the program was started from.

    Frozen: the directory containing the .exe -- the anchor for portable data.
    Source: the project root (the parent of the ``app`` package).
    """
    if is_frozen():
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def resource_dir() -> Path:
    """The directory holding bundled read-only resources (``app/`` contents)."""
    if is_frozen():
        base = getattr(sys, "_MEIPASS", None)
        if base:
            return Path(base) / "app"
    return Path(__file__).resolve().parent


def static_dir() -> Path:
    return resource_dir() / "static"


def default_data_dir() -> Path:
    """The data directory the app will use unless the launcher says otherwise."""
    configured = os.environ.get(DATA_DIR_ENV)
    if configured:
        return Path(configured).expanduser().resolve()
    return program_dir() / "data"


def data_dir_candidates(explicit: str | None = None) -> list[Path]:
    """Every place the data directory might go, best first."""
    candidates: list[Path] = []
    if explicit:
        candidates.append(Path(explicit).expanduser())
    configured = os.environ.get(DATA_DIR_ENV)
    if configured:
        candidates.append(Path(configured).expanduser())
    candidates.append(program_dir() / "data")
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        candidates.append(Path(local_app_data) / "Bookkeeping" / "data")
    candidates.append(Path.home() / ".bookkeeping" / "data")
    return candidates


def choose_data_dir(explicit: str | None = None) -> Path:
    """Create and return the first data directory that is actually writable.

    Called once by the launcher. ``os.access`` is not trusted on Windows (it
    ignores ACLs and returns True for directories the process cannot really
    write), so each candidate is tested by writing a real file.
    """
    problems: list[str] = []
    for candidate in data_dir_candidates(explicit):
        try:
            candidate.mkdir(parents=True, exist_ok=True)
            probe = candidate / ".write-test"
            probe.write_text("ok", encoding="utf-8")
            probe.unlink()
        except OSError as exc:
            problems.append(f"{candidate}: {exc.strerror or exc}")
            continue
        os.environ[DATA_DIR_ENV] = str(candidate)
        return candidate
    raise RuntimeError(
        "No writable place to keep the books. Tried:\n  " + "\n  ".join(problems)
    )
