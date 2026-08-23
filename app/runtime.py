"""Process lifetime for the desktop build.

A browser-UI desktop app has a problem a console app does not: the window the
user closes is a browser tab, not the program. The windowed .exe has no console
to close, so without help the server would sit in the background forever and the
only way to stop it would be Task Manager.

Two mechanisms solve that, both routed through this module so the HTTP layer and
the launcher do not have to know about each other:

* **Quit** -- the UI's Quit button calls ``POST /api/quit``, which asks the
  server to stop immediately.
* **Heartbeat** -- the open page pings ``POST /api/ping`` every few seconds. If
  no ping arrives for ``IDLE_TIMEOUT_SECONDS`` the app assumes the last tab is
  gone and exits. A scan in flight suppresses this, so closing the tab while a
  receipt is being read does not throw the reading away.

In a source checkout (``run.bat``, ``uvicorn`` directly, the tests) nothing calls
``arm()``, the watchdog never runs, and the server behaves like an ordinary
long-running process.
"""

from __future__ import annotations

import threading
import time

# Generous enough to survive a browser being slow, a laptop suspending briefly,
# or the user reading a long report without touching anything.
IDLE_TIMEOUT_SECONDS = 90.0
PING_INTERVAL_SECONDS = 10.0  # what the front end is told to use
_CHECK_INTERVAL_SECONDS = 5.0


class Runtime:
    """Shared, thread-safe process state."""

    def __init__(self, idle_timeout: float = IDLE_TIMEOUT_SECONDS) -> None:
        self._lock = threading.Lock()
        self._last_ping = time.monotonic()
        self._shutdown = False
        self._armed = False
        self._desktop = False
        self._busy = 0
        self._idle_timeout = idle_timeout

    # -- heartbeat ------------------------------------------------------- #

    def ping(self) -> None:
        with self._lock:
            self._last_ping = time.monotonic()

    def seconds_since_ping(self) -> float:
        with self._lock:
            return time.monotonic() - self._last_ping

    # -- "a scan is running, do not exit" -------------------------------- #

    def begin_work(self) -> None:
        with self._lock:
            self._busy += 1
            self._last_ping = time.monotonic()

    def end_work(self) -> None:
        with self._lock:
            self._busy = max(0, self._busy - 1)
            self._last_ping = time.monotonic()

    @property
    def busy(self) -> bool:
        with self._lock:
            return self._busy > 0

    # -- shutdown -------------------------------------------------------- #

    def request_shutdown(self) -> None:
        with self._lock:
            self._shutdown = True

    @property
    def shutdown_requested(self) -> bool:
        with self._lock:
            return self._shutdown

    @property
    def armed(self) -> bool:
        with self._lock:
            return self._armed

    @property
    def idle_timeout(self) -> float:
        with self._lock:
            return self._idle_timeout

    @property
    def desktop(self) -> bool:
        """Whether this process is the desktop app, and so may be told to quit.

        Independent of ``armed``: ``--keep-alive`` turns the idle watchdog off,
        but the Quit button must still work -- a windowed .exe has no console to
        close, so removing both would leave Task Manager as the only way out.
        """
        with self._lock:
            return self._desktop

    def set_desktop(self, value: bool = True) -> None:
        with self._lock:
            self._desktop = value

    def arm(self, idle_timeout: float | None = None) -> None:
        """Enable the idle watchdog. Called only by the desktop launcher."""
        with self._lock:
            self._armed = True
            if idle_timeout is not None:
                self._idle_timeout = idle_timeout
            self._last_ping = time.monotonic()

    def watch(
        self, on_exit, stop_event: threading.Event,
        check_interval: float = _CHECK_INTERVAL_SECONDS,
    ) -> None:
        """Poll until shutdown is requested or the UI has gone quiet.

        Runs in a daemon thread. ``on_exit`` is called exactly once.
        """
        while not stop_event.wait(check_interval):
            if self.shutdown_requested:
                on_exit("quit requested from the user interface")
                return
            if not self.armed:
                continue
            if self.busy:
                continue
            idle = self.seconds_since_ping()
            if idle > self.idle_timeout:
                on_exit(f"no browser heartbeat for {idle:.0f}s")
                return


runtime = Runtime()
