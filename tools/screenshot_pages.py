"""Open the window, screenshot every page, close it.

The reason this exists: a native window cannot be inspected the way a web page
can. Reading the widget tree tells you the interface *holds together* (that is
what tests/test_ui.py is for) but not that it *looks* right -- and several real
faults in this project were only ever visible in a picture: charts drawn before
layout, a whole page of controls rendering behind their own card, columns
truncated because a pixel width was not scaled for the display.

    py tools\\screenshot_pages.py --out shots --data-dir C:\\temp\\demo-books
    py tools\\screenshot_pages.py --out shots --theme light

Two things worth knowing before changing this:

* ``time.sleep`` does not run Tk's event loop, so anything scheduled with
  ``after()`` -- the debounced chart redraw above all -- will not have happened.
  Pump the loop instead; ``settle()`` below does.
* The window is raised and made topmost first, because ``ImageGrab`` captures
  the screen, not the window: whatever is in front ends up in the picture.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
import tkinter as tk
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

PAGES = ("receipts", "reports", "rules", "settings")


def settle(root: tk.Misc, seconds: float = 1.2) -> None:
    """Run the event loop for a while, so after() work actually happens."""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        root.update()
        time.sleep(0.02)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, default=Path("shots"))
    parser.add_argument("--data-dir", type=Path,
                        help="Books to open (default: the app's usual data folder).")
    # Deliberately not an argparse `choices=` list. Reading the theme names
    # requires importing app.ui, which imports app.db, which resolves the data
    # folder at import time -- and a parser is built before --data-dir has been
    # read, so doing it there silently points the whole run at the default books.
    # The name is validated below instead, once the environment is set up.
    parser.add_argument("--theme",
                        help="Force a theme instead of using the saved one.")
    parser.add_argument("--pages", default=",".join(PAGES),
                        help=f"Comma-separated subset of {','.join(PAGES)}.")
    args = parser.parse_args()

    if args.data_dir:
        args.data_dir.mkdir(parents=True, exist_ok=True)
        os.environ["BOOKKEEPING_DATA"] = str(args.data_dir.resolve())
    args.out.mkdir(parents=True, exist_ok=True)

    try:
        from PIL import ImageGrab
    except ImportError:
        print("Pillow is required: pip install pillow")
        return 2

    from app.db import init_db
    from app.ui.theme import THEME_ORDER
    from app.ui.window import MainWindow, _enable_dpi_awareness

    if args.theme and args.theme not in THEME_ORDER:
        print(f"Unknown theme {args.theme!r}. Available: {', '.join(THEME_ORDER)}")
        return 2

    _enable_dpi_awareness()
    init_db()
    root = tk.Tk()
    window = MainWindow(root)
    if args.theme:
        window.set_theme(args.theme)
    root.update()

    wanted = [page for page in args.pages.split(",") if page in PAGES]
    written: list[Path] = []

    def shoot(name: str) -> None:
        settle(root)
        x, y = root.winfo_rootx(), root.winfo_rooty()
        width, height = root.winfo_width(), root.winfo_height()
        # Reach above and around the client area to include the title bar and
        # the window border, which is where the app's own icon and title show.
        box = (max(0, x - 10), max(0, y - 46), x + width + 10, y + height + 12)
        path = args.out / f"{name}.png"
        ImageGrab.grab(bbox=box).save(path)
        written.append(path)
        print(f"wrote {path}")

    def sequence() -> None:
        root.lift()
        root.attributes("-topmost", True)
        root.update()
        for index, page in enumerate(wanted, start=1):
            window.show(page)
            if page == "reports":
                window.pages["reports"].set_range(None)   # all time
            elif page == "receipts":
                rows = window.pages["receipts"].tree.get_children()
                if rows:   # select the newest, so the review pane is populated
                    window.pages["receipts"].refresh(select=int(rows[0]))
            shoot(f"{index}-{page}")
        root.after(150, root.destroy)

    root.after(700, sequence)
    root.mainloop()
    print(f"\n{len(written)} screenshot(s) in {args.out.resolve()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
