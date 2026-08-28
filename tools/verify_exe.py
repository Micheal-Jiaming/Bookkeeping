"""Drive the built .exe the way a user would, and report what happened.

The test suite can prove the code is right; only this can prove the *build* is.
Everything it checks has actually been broken at least once: a bundle missing
tkinter started and showed nothing, a bundle missing certifi started and could
not call the API, and a lock file left held would refuse the second launch for
ever.

    py tools\\verify_exe.py
    py tools\\verify_exe.py --exe dist\\Bookkeeping.exe --out shots

Checks, in order:
  1. a copy in an empty folder starts and shows its window
  2. it creates data\\ beside itself (database + log)
  3. a screenshot of the real window is captured
  4. a second launch is refused with an "Already running" dialog, which is
     dismissed, and that process exits
  5. closing the window (the X button) ends the program cleanly
  6. --version works afterwards, proving the lock was released

**The trap this script exists to remember:** a one-file PyInstaller build runs
the application in a *child* process. The parent is only the bootloader that
unpacks it and owns nothing but a hidden window, so looking for the window by the
pid returned by ``Popen`` finds nothing -- which looks exactly like a crash on
startup and is not. Hence ``_descendants``.
"""

from __future__ import annotations

import argparse
import ctypes
import ctypes.wintypes as wt
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

user32 = ctypes.windll.user32
WM_CLOSE = 0x0010
EnumWindowsProc = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)


def _descendants(pid: int) -> set[int]:
    """Every child process id below `pid`, recursively.

    The whole process table is fetched in **one** PowerShell call and the tree
    walked in Python. An earlier version shelled out once per node per poll,
    which cost a second or more each time; under the disk load right after a
    build the polling loop ran so seldom that it missed a dialog that was
    sitting on screen the entire time, and reported a working build as broken.
    A verification tool that cries wolf is worse than no verification tool.
    """
    result = subprocess.run(
        ["powershell", "-NoProfile", "-Command",
         "Get-CimInstance Win32_Process | ForEach-Object "
         "{ \"$($_.ProcessId) $($_.ParentProcessId)\" }"],
        capture_output=True, text=True)

    children: dict[int, list[int]] = {}
    for line in result.stdout.splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[0].isdigit() and parts[1].isdigit():
            children.setdefault(int(parts[1]), []).append(int(parts[0]))

    found: set[int] = set()
    queue = [pid]
    while queue:
        for kid in children.get(queue.pop(), []):
            if kid not in found:
                found.add(kid)
                queue.append(kid)
    return found


def windows_of(pid: int) -> list[tuple[int, str, str]]:
    """Visible top-level windows belonging to a process or any of its children."""
    family = {pid} | _descendants(pid)
    found: list[tuple[int, str, str]] = []

    def callback(hwnd, _lparam):
        owner = wt.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value not in family or not user32.IsWindowVisible(hwnd):
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        title = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, title, length + 1)
        klass = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(hwnd, klass, 256)
        found.append((hwnd, title.value, klass.value))
        return True

    user32.EnumWindows(EnumWindowsProc(callback), 0)
    return found


def wait_for_window(pid: int, match: str, timeout: float = 40.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        for entry in windows_of(pid):
            if match.lower() in entry[1].lower():
                return entry
        time.sleep(0.3)
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--exe", type=Path,
                        default=Path(__file__).resolve().parent.parent / "dist"
                        / "Bookkeeping.exe")
    parser.add_argument("--out", type=Path, help="Where to save the screenshot.")
    parser.add_argument("--keep", action="store_true",
                        help="Leave the temporary portable copy in place.")
    args = parser.parse_args()

    if not args.exe.exists():
        print(f"No such file: {args.exe}\nBuild it first with build.bat")
        return 2

    problems: list[str] = []
    workspace = Path(tempfile.mkdtemp(prefix="bookkeeping-verify-"))
    portable = workspace / "Bookkeeping.exe"
    shutil.copy2(args.exe, portable)
    print(f"[1] {portable.stat().st_size / 1024 / 1024:.1f} MB copied to an empty "
          f"folder\n    {workspace}")

    first = subprocess.Popen([str(portable)], cwd=str(workspace))
    window = wait_for_window(first.pid, "Bookkeeping")
    if window is None:
        problems.append("no window appeared")
        print("    FAILED: no window within 40s")
        first.kill()
        return _report(problems, workspace, args.keep)
    hwnd, title, klass = window
    print(f"[2] window: {title!r} (class {klass})")

    time.sleep(2.0)
    data = workspace / "data"
    for expected in ("bookkeeping.db", "bookkeeping.log"):
        path = data / expected
        if path.exists():
            print(f"[3] data\\{expected}: {path.stat().st_size} bytes")
        else:
            problems.append(f"data\\{expected} was not created")

    if args.out:
        try:
            from PIL import ImageGrab

            args.out.mkdir(parents=True, exist_ok=True)
            rect = wt.RECT()
            user32.GetWindowRect(hwnd, ctypes.byref(rect))
            user32.SetForegroundWindow(hwnd)
            time.sleep(0.8)
            shot = args.out / "exe-window.png"
            ImageGrab.grab(bbox=(rect.left, rect.top, rect.right, rect.bottom)).save(shot)
            print(f"[4] screenshot: {shot}")
        except Exception as exc:
            problems.append(f"screenshot failed: {exc}")

    second = subprocess.Popen([str(portable)], cwd=str(workspace))
    dialog = wait_for_window(second.pid, "Already running", timeout=25.0)
    if dialog is None:
        problems.append("a second launch was not refused")
        second.kill()
        print("    FAILED: no 'already running' dialog")
    else:
        print(f"[5] second launch refused: {dialog[1]!r}")
        user32.PostMessageW(dialog[0], WM_CLOSE, 0, 0)
        try:
            print(f"    it exited with code {second.wait(timeout=20)}")
        except subprocess.TimeoutExpired:
            problems.append("the refused process did not exit")
            second.kill()

    user32.PostMessageW(hwnd, WM_CLOSE, 0, 0)
    try:
        code = first.wait(timeout=25)
        print(f"[6] closing the window exited with code {code}")
        if code != 0:
            problems.append(f"exit code {code}, expected 0")
    except subprocess.TimeoutExpired:
        problems.append("closing the window did not end the program")
        first.kill()

    try:
        result = subprocess.run([str(portable), "--version"], capture_output=True,
                                text=True, cwd=str(workspace), timeout=40)
        version = result.stdout.strip()
        print(f"[7] --version after exit: {version!r} (the lock was released)")
        if not version:
            problems.append("--version printed nothing after the app closed")
    except subprocess.TimeoutExpired:
        problems.append("--version hung after the app closed")

    return _report(problems, workspace, args.keep)


def _report(problems: list[str], workspace: Path, keep: bool) -> int:
    if keep:
        print(f"\nleft in place: {workspace}")
    else:
        shutil.rmtree(workspace, ignore_errors=True)
    print()
    if problems:
        print("PROBLEMS:")
        for problem in problems:
            print("  -", problem)
        return 1
    print("ALL CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
