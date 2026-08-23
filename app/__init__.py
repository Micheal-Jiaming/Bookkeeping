"""Bookkeeping -- a receipt-recognition expense tracker for Windows.

Layout:
    app/launcher.py    startup: data directory, logging, single-instance lock
    app/ui/            the Tkinter interface (window, pages, theme)
    app/store.py       everything done to the books, as plain function calls
    app/pipeline.py    scan orchestration: image -> reading -> categorised rows
    app/extract/       recognition engines behind one interface
    app/categorize.py  the category precedence chain
    app/validate.py    arithmetic checks that produce review flags
    app/db.py          SQLite schema and seed data
    app/paths.py       where things live, frozen (.exe) or from source
    app/money.py       integer-cent money handling
    app/images.py      upload normalisation

The interface never touches SQL and the store never touches a widget; that
separation is what let the interface be replaced without rewriting the logic.

See Bookkeeping.md in the project root for the full design and history.
"""

__all__ = ["launcher", "pipeline", "store", "ui"]
