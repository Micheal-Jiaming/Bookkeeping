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
  4. a second launch is refused -- run on a desktop of its own so nothing it
     draws reaches the screen -- and that process exits cleanly
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
import uuid
import time
from pathlib import Path

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32
WM_CLOSE = 0x0010
EnumWindowsProc = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)


class _STARTUPINFOW(ctypes.Structure):
    _fields_ = [("cb", wt.DWORD), ("lpReserved", wt.LPWSTR),
                ("lpDesktop", wt.LPWSTR), ("lpTitle", wt.LPWSTR),
                ("dwX", wt.DWORD), ("dwY", wt.DWORD),
                ("dwXSize", wt.DWORD), ("dwYSize", wt.DWORD),
                ("dwXCountChars", wt.DWORD), ("dwYCountChars", wt.DWORD),
                ("dwFillAttribute", wt.DWORD), ("dwFlags", wt.DWORD),
                ("wShowWindow", wt.WORD), ("cbReserved2", wt.WORD),
                ("lpReserved2", ctypes.POINTER(ctypes.c_byte)),
                ("hStdInput", wt.HANDLE), ("hStdOutput", wt.HANDLE),
                ("hStdError", wt.HANDLE)]


class _PROCESS_INFORMATION(ctypes.Structure):
    _fields_ = [("hProcess", wt.HANDLE), ("hThread", wt.HANDLE),
                ("dwProcessId", wt.DWORD), ("dwThreadId", wt.DWORD)]


# ctypes defaults every foreign function to returning a C int. On 64-bit Windows
# that silently truncates a returned HANDLE to its low 32 bits, and the first
# version of run_unseen failed for exactly that reason: CreateDesktopW succeeded,
# the handle came back mangled, and CreateProcessW refused it. Declaring the
# signatures is not tidiness here, it is the difference between working and not.
user32.CreateDesktopW.restype = wt.HANDLE
user32.CreateDesktopW.argtypes = [wt.LPCWSTR, wt.LPCWSTR, ctypes.c_void_p,
                                  wt.DWORD, wt.DWORD, ctypes.c_void_p]
user32.CloseDesktop.argtypes = [wt.HANDLE]
kernel32.CreateProcessW.restype = wt.BOOL
kernel32.CreateProcessW.argtypes = [
    wt.LPCWSTR, wt.LPWSTR, ctypes.c_void_p, ctypes.c_void_p, wt.BOOL, wt.DWORD,
    ctypes.c_void_p, wt.LPCWSTR, ctypes.POINTER(_STARTUPINFOW),
    ctypes.POINTER(_PROCESS_INFORMATION)]
kernel32.WaitForSingleObject.argtypes = [wt.HANDLE, wt.DWORD]
kernel32.GetExitCodeProcess.argtypes = [wt.HANDLE, ctypes.POINTER(wt.DWORD)]
kernel32.TerminateProcess.argtypes = [wt.HANDLE, wt.UINT]
kernel32.CloseHandle.argtypes = [wt.HANDLE]

DESKTOP_ALL = 0x10000000
user32.EnumDesktopWindows.argtypes = [wt.HANDLE, EnumWindowsProc, wt.LPARAM]


def _dialog_on(desktop, match: str):
    """The first window on ``desktop`` whose title contains ``match``."""
    found: list[int] = []

    def callback(hwnd, _lparam):
        length = user32.GetWindowTextLengthW(hwnd)
        if length:
            title = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, title, length + 1)
            if match.lower() in title.value.lower():
                found.append(hwnd)
                return False
        return True

    user32.EnumDesktopWindows(desktop, EnumWindowsProc(callback), 0)
    return found[0] if found else None


def kill_tree(pid: int) -> None:
    """Kill a process and everything it started.

    Not optional, and not the same as killing the process you launched. A
    one-file PyInstaller build runs the application in a *child*; the parent is
    only the bootloader. Terminating the handle returned by CreateProcessW
    therefore leaves the real application running, orphaned and invisible --
    which happened twice while this file was being written, leaving two
    Bookkeeping processes alive on desktops that had already been destroyed.
    """
    subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"],
                   capture_output=True, check=False)


def run_unseen(exe: Path, cwd: Path, dismiss: str,
               timeout: float = 30.0) -> tuple[int | None, bool]:
    """Run ``exe`` on a desktop of its own; close its ``dismiss`` dialog there.

    The point of the second-launch check is to prove the single-instance guard
    refuses a duplicate. Proving it used to mean letting the refusal dialog
    appear on the real desktop and closing it a second later -- which, during a
    build, looked exactly like an error flashing past, and was taken for one.

    A private desktop is Windows' own answer: the duplicate starts, is refused,
    and paints its dialog normally, somewhere nobody is looking. Nothing under
    test is faked. **The dialog must still be dismissed**, because it is modal
    and the process will wait on it for ever -- an earlier version of this
    function left it up and reported a perfectly good build as broken when the
    timeout fired.

    Returns ``(exit code, whether the dialog appeared)``; the exit code is
    ``None`` if the desktop or process could not be created.
    """
    name = f"bookkeeping-verify-{uuid.uuid4().hex[:8]}"
    desktop = user32.CreateDesktopW(name, None, None, 0, DESKTOP_ALL, None)
    if not desktop:
        return None, False
    try:
        # The struct holds a bare pointer, so the buffer it points at has to
        # outlive the CreateProcessW call. Assigning a Python str to the field
        # instead leaves it dangling.
        desktop_name = ctypes.create_unicode_buffer(name)
        info = _STARTUPINFOW()
        info.cb = ctypes.sizeof(_STARTUPINFOW)
        info.lpDesktop = ctypes.cast(desktop_name, wt.LPWSTR)
        process = _PROCESS_INFORMATION()
        if not kernel32.CreateProcessW(
                None, ctypes.create_unicode_buffer(f'"{exe}"'), None, None,
                False, 0, None, str(cwd), ctypes.byref(info),
                ctypes.byref(process)):
            return None, False
        try:
            deadline = time.monotonic() + timeout
            dialog = None
            while dialog is None and time.monotonic() < deadline:
                dialog = _dialog_on(desktop, dismiss)
                if dialog is None:
                    time.sleep(0.3)
            if dialog is not None:
                user32.PostMessageW(dialog, WM_CLOSE, 0, 0)
            remaining = max(1.0, deadline - time.monotonic())
            if kernel32.WaitForSingleObject(
                    process.hProcess, int(remaining * 1000)) != 0:
                kill_tree(process.dwProcessId)
                return None, dialog is not None
            code = wt.DWORD()
            kernel32.GetExitCodeProcess(process.hProcess, ctypes.byref(code))
            return int(code.value), dialog is not None
        finally:
            kernel32.CloseHandle(process.hThread)
            kernel32.CloseHandle(process.hProcess)
    finally:
        user32.CloseDesktop(desktop)


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

    # The duplicate runs on a desktop of its own, so its refusal dialog never
    # reaches the screen. It is still found and closed there, so this checks the
    # same three things it always did -- refused, dialog shown, exits cleanly --
    # without flashing a red box past the user during every build.
    code, saw_dialog = run_unseen(portable, workspace, "Already running")
    if code is None:
        problems.append("the second launch could not be run to completion")
        print("    FAILED: the duplicate did not run on its own desktop")
    elif not saw_dialog:
        problems.append("a second launch was not refused")
        print("    FAILED: no 'already running' dialog appeared")
    elif code != 0:
        problems.append(f"the refused copy exited {code}, expected 0")
        print(f"    FAILED: the refused copy exited {code}")
    else:
        print("[5] second launch refused off-screen, dialog closed, exited 0")

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
