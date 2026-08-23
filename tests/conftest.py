"""Test fixtures.

Every test runs against a throwaway database in a temp directory. The module
paths are patched *before* the app modules are imported so that nothing ever
touches the real ``data/bookkeeping.db``.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


@pytest.fixture()
def app_modules(tmp_path, monkeypatch):
    """The app package, rewired to a temporary data directory."""
    from app import db

    data_dir = tmp_path / "data"
    image_dir = data_dir / "images"
    image_dir.mkdir(parents=True)
    monkeypatch.setattr(db, "DATA_DIR", data_dir)
    monkeypatch.setattr(db, "IMAGE_DIR", image_dir)
    monkeypatch.setattr(db, "DB_PATH", data_dir / "test.db")

    from app import main, pipeline, settings_store

    monkeypatch.setattr(pipeline, "IMAGE_DIR", image_dir)
    monkeypatch.setattr(main, "IMAGE_DIR", image_dir)
    db.init_db()

    return {
        "db": db,
        "main": main,
        "pipeline": pipeline,
        "settings": settings_store,
        "image_dir": image_dir,
    }


@pytest.fixture()
def client(app_modules):
    from fastapi.testclient import TestClient

    with TestClient(app_modules["main"].app) as test_client:
        yield test_client


@pytest.fixture()
def sample_receipt_png(tmp_path):
    """A synthetic Walmart receipt image plus its known-correct values."""
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from tools.make_sample_receipt import build, expected

    path = tmp_path / "sample-receipt.png"
    build().save(path)
    return path, expected()
