"""Test fixtures.

Every test runs against a throwaway database in a temp directory. The module
attributes are patched *after* import (``app.db`` resolves its paths at import
time) so nothing ever touches the real ``data\\bookkeeping.db``.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


@pytest.fixture()
def books(tmp_path, monkeypatch):
    """A fresh, empty set of books. Yields the app modules under test."""
    from app import db

    data_dir = tmp_path / "data"
    image_dir = data_dir / "images"
    image_dir.mkdir(parents=True)
    monkeypatch.setattr(db, "DATA_DIR", data_dir)
    monkeypatch.setattr(db, "IMAGE_DIR", image_dir)
    monkeypatch.setattr(db, "DB_PATH", data_dir / "test.db")

    from app import pipeline, settings_store, store

    # These modules imported IMAGE_DIR by value, so each needs its own patch.
    monkeypatch.setattr(store, "IMAGE_DIR", image_dir)
    monkeypatch.setattr(pipeline, "IMAGE_DIR", image_dir)
    db.init_db()

    return {
        "db": db,
        "store": store,
        "pipeline": pipeline,
        "settings": settings_store,
        "data_dir": data_dir,
        "image_dir": image_dir,
    }


@pytest.fixture()
def sample_receipt_png(tmp_path):
    """A synthetic Walmart receipt image plus its known-correct values."""
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from tools.make_sample_receipt import build, expected

    path = tmp_path / "sample-receipt.png"
    build().save(path)
    return path, expected()
