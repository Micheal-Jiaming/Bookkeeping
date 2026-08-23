"""Tests for the pieces that only exist because this ships as a portable .exe.

Where the books go, and what happens when the program is started twice -- the two
things most likely to go wrong on someone else's machine, where the .exe may sit
in a read-only folder or be double-clicked twice.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import launcher, paths  # noqa: E402

# ------------------------------------------------------------------- paths


def test_data_dir_sits_beside_the_program_by_default(monkeypatch, tmp_path):
    monkeypatch.delenv(paths.DATA_DIR_ENV, raising=False)
    monkeypatch.setattr(paths, "program_dir", lambda: tmp_path)
    assert paths.default_data_dir() == tmp_path / "data"


def test_an_explicit_data_dir_wins_and_is_published_to_the_app(monkeypatch, tmp_path):
    monkeypatch.delenv(paths.DATA_DIR_ENV, raising=False)
    chosen = paths.choose_data_dir(str(tmp_path / "books"))
    assert chosen == tmp_path / "books"
    assert chosen.is_dir()
    # app/db.py reads this at import time; the launcher must have set it.
    assert paths.default_data_dir() == chosen


def test_an_unwritable_location_falls_back_instead_of_failing(monkeypatch, tmp_path):
    """The .exe in a read-only folder must still keep books somewhere."""
    unwritable = tmp_path / "readonly"
    fallback = tmp_path / "appdata"
    monkeypatch.delenv(paths.DATA_DIR_ENV, raising=False)
    monkeypatch.setattr(
        paths, "data_dir_candidates", lambda explicit=None: [unwritable, fallback])

    real_mkdir = Path.mkdir

    def refuse_the_first(self, *args, **kwargs):
        if self == unwritable:
            raise OSError(13, "Access is denied")
        return real_mkdir(self, *args, **kwargs)

    monkeypatch.setattr(Path, "mkdir", refuse_the_first)
    assert paths.choose_data_dir() == fallback


def test_no_writable_candidate_is_a_clear_error(monkeypatch, tmp_path):
    monkeypatch.setattr(paths, "data_dir_candidates",
                        lambda explicit=None: [tmp_path / "x"])
    monkeypatch.setattr(
        Path, "mkdir", lambda self, *a, **k: (_ for _ in ()).throw(OSError(13, "denied")))
    with pytest.raises(RuntimeError, match="No writable place"):
        paths.choose_data_dir()


def test_bundled_resources_are_looked_for_inside_the_bundle(monkeypatch, tmp_path):
    monkeypatch.setattr(paths, "is_frozen", lambda: True)
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
    assert paths.resource_dir() == tmp_path / "app"


def test_the_source_checkout_finds_its_own_resources():
    # Guards against a refactor that breaks the non-frozen path.
    assert (paths.resource_dir() / "ui" / "window.py").exists()


# -------------------------------------------------------------------- lock


def _release() -> None:
    if launcher._lock_handle is not None:
        launcher._lock_handle.close()
        launcher._lock_handle = None


def test_one_window_per_set_of_books(tmp_path):
    """A second copy pointed at the same books must be refused."""
    assert launcher.acquire_lock(tmp_path) is True
    held = launcher._lock_handle
    try:
        # A second attempt contends for the same byte range and loses.
        assert launcher.acquire_lock(tmp_path) is False
    finally:
        launcher._lock_handle = held
        _release()


def test_two_portable_copies_with_their_own_books_both_run(tmp_path):
    first, second = tmp_path / "usb", tmp_path / "desktop"
    first.mkdir()
    second.mkdir()
    assert launcher.acquire_lock(first) is True
    held = launcher._lock_handle
    try:
        assert launcher.acquire_lock(second) is True
    finally:
        _release()
        if held is not None:
            held.close()


def test_the_lock_is_released_when_the_program_exits(tmp_path):
    assert launcher.acquire_lock(tmp_path) is True
    _release()
    assert launcher.acquire_lock(tmp_path) is True, "a closed handle frees the books"
    _release()


def test_a_folder_that_cannot_hold_a_lock_file_does_not_block_startup(tmp_path):
    """Better to run without the guard than to refuse to open the books."""
    assert launcher.acquire_lock(tmp_path / "does-not-exist") is True
    _release()


# -------------------------------------------------------------------- args


def test_the_command_line_options_do_what_they_say():
    args = launcher.parse_args(["--data-dir", "E:/books", "--allow-second-window"])
    assert args.data_dir == "E:/books"
    assert args.allow_second_window is True
    assert launcher.parse_args([]).data_dir is None
    assert launcher.parse_args([]).allow_second_window is False
    assert launcher.parse_args(["--version"]).version is True


def test_logging_goes_to_a_file_in_the_data_folder(tmp_path):
    log_path = launcher.setup_logging(tmp_path)
    assert log_path == tmp_path / launcher.LOG_FILE_NAME
    import logging

    logging.getLogger("bookkeeping.test").info("hello from the test")
    logging.shutdown()
    assert "hello from the test" in log_path.read_text(encoding="utf-8")
