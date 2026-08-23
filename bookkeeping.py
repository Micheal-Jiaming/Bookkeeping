"""Executable entry point (this is the script PyInstaller freezes).

Kept to three lines on purpose: everything interesting is in
``app/launcher.py``, which is importable and testable. A PyInstaller entry
script is the one module that cannot be imported without running, so it should
contain no logic.

    py bookkeeping.py            # run from source, same behaviour as the .exe
    py bookkeeping.py --help     # options (data directory, port, no browser)
"""

from __future__ import annotations

import multiprocessing
import sys

from app.launcher import main

if __name__ == "__main__":
    # Harmless here (the server runs single-process) but mandatory insurance in
    # a frozen build: without it, any library that spawns a process would
    # re-execute this script instead, forking the whole app repeatedly.
    multiprocessing.freeze_support()
    sys.exit(main())
