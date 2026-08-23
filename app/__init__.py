"""Bookkeeping -- a local receipt-recognition expense tracker.

Layout:
    app/main.py        HTTP API and static UI
    app/pipeline.py    upload -> scan -> validate -> categorise orchestration
    app/extract/       recognition engines behind one interface
    app/categorize.py  rules-then-model category assignment
    app/validate.py    arithmetic checks that produce review flags
    app/db.py          SQLite schema and seed data
    app/money.py       integer-cent money handling

See Bookkeeping.md in the project root for the full design and history.
"""

__all__ = ["main", "pipeline"]
