"""The Tkinter desktop interface.

Layout of this package:

    theme.py          palette, fonts, ttk styling, the shared small widgets
    window.py         the window itself: chrome, menus, navigation, poll loop
    receipts.py       receipt list + the review pane (the main workspace)
    reports.py        stat tiles, category and month charts, tables
    rules.py          categories and keyword rules
    settings_page.py  recognition engine settings

Every page is a class with a ``frame`` attribute and a ``refresh()`` method; the
window packs and refreshes them, and knows nothing else about them.
"""

from .window import run

__all__ = ["run"]
