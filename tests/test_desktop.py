"""Tests for the pieces that only exist because of the .exe build.

These cover the logic that decides *where the books go* and *when the process
should stop* — the two things most likely to go wrong on someone else's machine,
where the .exe may sit in a read-only folder or be launched twice.
"""

from __future__ import annotations

import socket
import sys
import threading
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import launcher, paths  # noqa: E402
from app.runtime import Runtime  # noqa: E402

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
        paths, "data_dir_candidates", lambda explicit=None: [unwritable, fallback]
    )

    real_mkdir = Path.mkdir

    def refuse_the_first(self, *args, **kwargs):
        if self == unwritable:
            raise OSError(13, "Access is denied")
        return real_mkdir(self, *args, **kwargs)

    monkeypatch.setattr(Path, "mkdir", refuse_the_first)
    assert paths.choose_data_dir() == fallback


def test_no_writable_candidate_is_a_clear_error(monkeypatch, tmp_path):
    monkeypatch.setattr(paths, "data_dir_candidates", lambda explicit=None: [tmp_path / "x"])
    monkeypatch.setattr(
        Path, "mkdir", lambda self, *a, **k: (_ for _ in ()).throw(OSError(13, "denied"))
    )
    with pytest.raises(RuntimeError, match="No writable place"):
        paths.choose_data_dir()


def test_bundled_resources_are_looked_for_inside_the_bundle(monkeypatch, tmp_path):
    monkeypatch.setattr(paths, "is_frozen", lambda: True)
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
    assert paths.static_dir() == tmp_path / "app" / "static"


def test_the_source_checkout_finds_its_own_static_files():
    # Guards against a refactor that breaks the non-frozen path.
    assert (paths.static_dir() / "index.html").exists()


# -------------------------------------------------------------------- ports


def test_a_free_port_is_reported_free_and_a_bound_one_is_not():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as taken:
        taken.bind((launcher.HOST, 0))
        taken.listen(1)
        port = taken.getsockname()[1]
        assert launcher.port_is_free(port) is False
    assert launcher.port_is_free(port) is True


def test_a_port_taken_by_something_else_is_skipped(monkeypatch):
    """A busy port that is not Bookkeeping means "try the next one", not "hand off"."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as taken:
        taken.bind((launcher.HOST, 0))
        taken.listen(1)
        port = taken.getsockname()[1]
        monkeypatch.setattr(launcher, "existing_instance", lambda p, timeout=1.5: False)
        chosen, already_running = launcher.choose_port(port)
        assert already_running is False
        assert chosen != port


def test_a_port_answering_as_bookkeeping_means_hand_off(monkeypatch):
    monkeypatch.setattr(launcher, "port_is_free", lambda port: False)
    monkeypatch.setattr(launcher, "existing_instance", lambda port, timeout=1.5: True)
    assert launcher.choose_port(8765) == (8765, True)


def test_no_free_port_at_all_is_an_error(monkeypatch):
    monkeypatch.setattr(launcher, "port_is_free", lambda port: False)
    monkeypatch.setattr(launcher, "existing_instance", lambda port, timeout=1.5: False)
    with pytest.raises(RuntimeError, match="No free port"):
        launcher.choose_port(8765)


def test_existing_instance_ignores_a_stranger_on_the_port():
    """Something else answering HTTP on the port must not be mistaken for us."""
    from http.server import BaseHTTPRequestHandler, HTTPServer

    class Stranger(BaseHTTPRequestHandler):
        def do_GET(self):
            body = b'{"app": "something-else"}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            return

    server = HTTPServer((launcher.HOST, 0), Stranger)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        assert launcher.existing_instance(server.server_address[1]) is False
    finally:
        server.shutdown()
        server.server_close()


def test_nothing_listening_is_not_an_existing_instance():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind((launcher.HOST, 0))
        free_port = probe.getsockname()[1]
    assert launcher.existing_instance(free_port, timeout=0.5) is False


def test_the_command_line_options_do_what_they_say():
    args = launcher.parse_args(["--port", "9000", "--no-browser", "--keep-alive",
                               "--data-dir", "E:/books"])
    assert (args.port, args.no_browser, args.keep_alive, args.data_dir) == (
        9000, True, True, "E:/books"
    )
    assert launcher.parse_args([]).port == launcher.DEFAULT_PORT
    assert launcher.parse_args(["--idle-timeout", "5"]).idle_timeout == 5.0
    assert launcher.parse_args([]).idle_timeout is None, "None means use the default"


def test_arming_with_an_explicit_timeout_overrides_the_default():
    runtime = Runtime()
    assert runtime.idle_timeout > 1
    runtime.arm(idle_timeout=7.5)
    assert runtime.idle_timeout == 7.5
    runtime.arm(idle_timeout=None)
    assert runtime.idle_timeout == 7.5, "None must not reset an explicit value"


# ------------------------------------------------------------------ lifetime


def _watch(runtime: Runtime):
    """Run the watchdog with a short poll interval and return what it decided."""
    reasons: list[str] = []
    stop = threading.Event()
    thread = threading.Thread(
        target=runtime.watch, args=(reasons.append, stop), kwargs={"check_interval": 0.01}
    )
    thread.start()
    thread.join(timeout=2.0)
    stop.set()
    thread.join(timeout=1.0)
    return reasons


def test_a_quit_request_stops_the_app():
    runtime = Runtime()
    runtime.request_shutdown()
    reasons = _watch(runtime)
    assert reasons and "quit requested" in reasons[0]


def test_silence_from_the_browser_stops_the_app_once_armed():
    runtime = Runtime()
    runtime.arm(idle_timeout=0.05)
    time.sleep(0.08)
    reasons = _watch(runtime)
    assert reasons and "heartbeat" in reasons[0]


def test_a_running_scan_keeps_the_app_alive():
    runtime = Runtime()
    runtime.arm(idle_timeout=0.05)
    runtime.begin_work()          # a receipt is being read right now
    time.sleep(0.08)
    assert _watch(runtime) == [], "the app must not exit mid-scan"
    runtime.end_work()
    assert runtime.busy is False


def test_an_unarmed_runtime_never_exits_on_its_own():
    """Started from source (run.bat / uvicorn), it is an ordinary server."""
    runtime = Runtime()
    time.sleep(0.08)
    assert _watch(runtime) == []


def test_a_heartbeat_resets_the_idle_clock():
    runtime = Runtime()
    runtime.arm(idle_timeout=0.05)
    time.sleep(0.05)
    runtime.ping()
    assert runtime.seconds_since_ping() < 0.02


def test_work_counting_survives_overlapping_scans():
    runtime = Runtime()
    runtime.begin_work()
    runtime.begin_work()
    runtime.end_work()
    assert runtime.busy is True
    runtime.end_work()
    assert runtime.busy is False
    runtime.end_work()  # an extra release must not push the count negative
    assert runtime.busy is False


# ----------------------------------------------------------- HTTP endpoints


def test_health_identifies_the_app_and_reports_where_the_books_are(client):
    body = client.get("/api/health").json()
    assert body["app"] == "bookkeeping"
    assert body["desktop"] is False, "not the desktop build under test"
    assert body["data_dir"]
    assert body["ping_interval"] > 0


def test_ping_is_accepted_even_when_the_watchdog_is_off(client):
    assert client.post("/api/ping").json()["ok"] is True


def test_quit_says_so_plainly_when_not_running_as_the_desktop_app(client):
    body = client.post("/api/quit").json()
    assert body["stopping"] is False
    assert "desktop app" in body["detail"]


def test_quit_stops_the_desktop_build(client, monkeypatch):
    from app.runtime import runtime

    monkeypatch.setattr(type(runtime), "desktop", property(lambda self: True))
    assert client.post("/api/quit").json()["stopping"] is True
    assert runtime.shutdown_requested is True
    runtime._shutdown = False  # leave the shared singleton as it was found


def test_quit_works_even_with_the_idle_watchdog_disabled():
    """--keep-alive turns off the watchdog, not the Quit button: a windowed .exe
    with neither would only be stoppable from Task Manager."""
    runtime = Runtime()
    runtime.set_desktop(True)          # what the launcher always does
    assert runtime.desktop is True
    assert runtime.armed is False      # what --keep-alive means
    runtime.request_shutdown()
    reasons = _watch(runtime)
    assert reasons and "quit requested" in reasons[0]
