"""The application window: chrome, navigation, menus and the scan poll loop.

Threading rule that shapes this file: **Tk widgets may only be touched from the
thread that created them.** Scans run in a worker pool
(``app/pipeline.py``), so nothing in a worker is allowed to call back into the
interface. Instead a worker writes its result to the database, and the window
polls with ``after()`` while any scan is in flight. That is why there are no
locks or queues here -- the database is the hand-off point.
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
import tkinter as tk
from tkinter import filedialog, messagebox
from pathlib import Path

from .. import paths, pipeline, settings_store, store
from ..db import DATA_DIR, init_db
from ..extract import engine_status
from .theme import THEME_ORDER, Button, Pill, Theme, ui_scale

log = logging.getLogger("bookkeeping.ui")

APP_NAME = "Bookkeeping"
# In 96-DPI design pixels; scaled for the actual display by Theme.px.
MIN_SIZE = (960, 620)
DEFAULT_SIZE = (1240, 800)
POLL_MS = 900

PAGES = (
    ("receipts", "Receipts"),
    ("reports", "Reports"),
    ("rules", "Categories & rules"),
    ("settings", "Settings"),
)


def version() -> str:
    for candidate in (paths.resource_dir() / "VERSION",
                      paths.program_dir() / "VERSION"):
        try:
            text = candidate.read_text(encoding="utf-8").strip()
        except OSError:
            continue
        if text:
            return text
    return "0.0.0"


class MainWindow:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.version = version()
        self.theme = Theme(settings_store.get("theme", "dark"), ui_scale(root))
        self.pages: dict[str, object] = {}
        self.current = settings_store.get("last_page", "receipts")
        if self.current not in dict(PAGES):
            self.current = "receipts"
        self._poll_job: str | None = None

        root.title(f"{APP_NAME} {self.version}")
        root.minsize(self.theme.px(MIN_SIZE[0]), self.theme.px(MIN_SIZE[1]))
        self._restore_geometry()
        try:
            root.iconbitmap(str(paths.resource_dir() / "icon.ico"))
        except tk.TclError:
            pass  # the icon is cosmetic; run without it if it is missing
        root.protocol("WM_DELETE_WINDOW", self.close)

        self._build()
        self._bind_keys()
        self.show(self.current)
        # Fill the status bar now rather than leaving it blank until the first
        # poll tick fires.
        self.update_status()
        self._schedule_poll(immediate=True)

    # ------------------------------------------------------------- chrome --

    def _build(self) -> None:
        c = self.theme.c
        self.root.configure(bg=c["BG"])
        self.theme.apply_ttk(self.root)
        self._build_menu()

        self.header = tk.Frame(self.root, bg=c["CARD"], height=self.theme.px(48))
        self.header.pack(side="top", fill="x")
        self.header.pack_propagate(False)

        brand = tk.Frame(self.header, bg=c["CARD"])
        brand.pack(side="left", padx=(14, 18))
        size = self.theme.px(16)
        mark = tk.Canvas(brand, width=size, height=size, bg=c["CARD"],
                         highlightthickness=0)
        mark.create_rectangle(0, 0, size - 1, size - 1, fill=c["ACCENT"], outline="")
        mark.create_rectangle(size * 0.27, size * 0.2, size * 0.72, size * 0.78,
                              fill=c["CARD"], outline="")
        mark.pack(side="left", pady=self.theme.px(14))
        tk.Label(brand, text=APP_NAME, bg=c["CARD"], fg=c["FG"],
                 font=self.theme.font(11, "bold")).pack(side="left", padx=8)

        self.nav_buttons: dict[str, tk.Button] = {}
        nav = tk.Frame(self.header, bg=c["CARD"])
        nav.pack(side="left")
        for key, label in PAGES:
            button = tk.Button(
                nav, text=label, command=lambda k=key: self.show(k),
                bg=c["CARD"], fg=c["MUTED"], activebackground=c["CARD_ALT"],
                activeforeground=c["FG"], relief="flat", bd=0, padx=13, pady=7,
                font=self.theme.font(10), cursor="hand2", highlightthickness=0,
            )
            button.pack(side="left", padx=1)
            self.nav_buttons[key] = button

        right = tk.Frame(self.header, bg=c["CARD"])
        right.pack(side="right", padx=12)
        self.engine_pill = Pill(right, self.theme, "engine…")
        self.engine_pill.pack(side="left", padx=(0, 8))
        Button(right, self.theme, "Theme", self.cycle_theme).pack(side="left")

        self.body = tk.Frame(self.root, bg=c["BG"])
        self.body.pack(side="top", fill="both", expand=True)

        self.status = tk.Frame(self.root, bg=c["CARD"], height=self.theme.px(26))
        self.status.pack(side="bottom", fill="x")
        self.status.pack_propagate(False)
        self.status_left = tk.Label(self.status, text="", bg=c["CARD"], fg=c["MUTED"],
                                    font=self.theme.font(9), anchor="w")
        self.status_left.pack(side="left", padx=12)
        self.status_right = tk.Label(self.status, text="", bg=c["CARD"], fg=c["DIM"],
                                     font=self.theme.font(9), anchor="e")
        self.status_right.pack(side="right", padx=12)

    def _build_menu(self) -> None:
        c = self.theme.c
        menu_kwargs = dict(bg=c["CARD"], fg=c["FG"], activebackground=c["ACCENT"],
                           activeforeground=c["ON_ACCENT"], bd=0,
                           font=self.theme.font(10))
        bar = tk.Menu(self.root, **menu_kwargs)

        file_menu = tk.Menu(bar, tearoff=0, **menu_kwargs)
        file_menu.add_command(label="Add receipt images…   Ctrl+O",
                              command=self.add_images)
        file_menu.add_command(label="Paste image from clipboard   Ctrl+V",
                              command=self.paste_image)
        file_menu.add_command(label="Add receipt by hand   Ctrl+N",
                              command=self.add_manual)
        file_menu.add_separator()
        file_menu.add_command(label="Export line items to CSV…", command=self.export_csv)
        file_menu.add_separator()
        file_menu.add_command(label="Exit   Ctrl+Q", command=self.close)
        bar.add_cascade(label="File", menu=file_menu)

        view_menu = tk.Menu(bar, tearoff=0, **menu_kwargs)
        for index, (key, label) in enumerate(PAGES, start=1):
            view_menu.add_command(label=f"{label}   Ctrl+{index}",
                                  command=lambda k=key: self.show(k))
        view_menu.add_separator()
        view_menu.add_command(label="Switch light / dark", command=self.cycle_theme)
        view_menu.add_command(label="Refresh   F5", command=self.refresh)
        bar.add_cascade(label="View", menu=view_menu)

        help_menu = tk.Menu(bar, tearoff=0, **menu_kwargs)
        help_menu.add_command(label="Open data folder", command=self.open_data_folder)
        help_menu.add_command(label="Open log file", command=self.open_log)
        help_menu.add_separator()
        help_menu.add_command(label=f"About {APP_NAME}", command=self.about)
        bar.add_cascade(label="Help", menu=help_menu)

        self.root.configure(menu=bar)

    def _bind_keys(self) -> None:
        self.root.bind("<Control-o>", lambda _e: self.add_images())
        self.root.bind("<Control-v>", lambda _e: self.paste_image())
        self.root.bind("<Control-n>", lambda _e: self.add_manual())
        self.root.bind("<Control-q>", lambda _e: self.close())
        self.root.bind("<F5>", lambda _e: self.refresh())
        for index, (key, _label) in enumerate(PAGES, start=1):
            self.root.bind(f"<Control-Key-{index}>", lambda _e, k=key: self.show(k))

    # -------------------------------------------------------------- pages --

    def show(self, key: str) -> None:
        if key not in dict(PAGES):
            return
        self.current = key
        for name, button in self.nav_buttons.items():
            selected = name == key
            button.configure(
                bg=self.theme["ACCENT"] if selected else self.theme["CARD"],
                fg=self.theme["ON_ACCENT"] if selected else self.theme["MUTED"],
                font=self.theme.font(10, "bold" if selected else "normal"),
            )
        page = self.pages.get(key)
        if page is None:
            page = self._create_page(key)
            self.pages[key] = page
        for other in self.pages.values():
            other.frame.pack_forget()
        page.frame.pack(fill="both", expand=True)
        page.refresh()
        settings_store.save({"last_page": key})

    def _create_page(self, key: str):
        # Imported here to keep the import graph acyclic: every page imports the
        # window's helpers, not the other way round.
        from .receipts import ReceiptsPage
        from .reports import ReportsPage
        from .rules import RulesPage
        from .settings_page import SettingsPage

        return {
            "receipts": ReceiptsPage,
            "reports": ReportsPage,
            "rules": RulesPage,
            "settings": SettingsPage,
        }[key](self.body, self)

    def refresh(self) -> None:
        page = self.pages.get(self.current)
        if page is not None:
            page.refresh()
        self.update_status()

    # ------------------------------------------------------------ actions --

    def add_images(self) -> None:
        filenames = filedialog.askopenfilenames(
            parent=self.root,
            title="Choose receipt images",
            filetypes=[("Images", "*.png *.jpg *.jpeg *.webp *.gif *.bmp"),
                       ("All files", "*.*")],
        )
        if not filenames:
            return
        created, problems = [], []
        for name in filenames:
            try:
                created.append(store.create_from_image(Path(name).read_bytes(),
                                                       Path(name).name))
            except (store.StoreError, OSError) as exc:
                problems.append(str(exc))
        self._after_import(created, problems)

    def paste_image(self) -> None:
        """Take a screenshot of a receipt straight from the clipboard."""
        try:
            from PIL import ImageGrab
        except ImportError:  # pragma: no cover - Pillow is a hard dependency
            return
        try:
            grabbed = ImageGrab.grabclipboard()
        except Exception as exc:  # clipboard access can fail transiently
            messagebox.showerror(APP_NAME, f"Could not read the clipboard: {exc}",
                                 parent=self.root)
            return
        if grabbed is None:
            messagebox.showinfo(
                APP_NAME,
                "The clipboard has no image in it.\n\n"
                "Copy a receipt photo or take a screenshot (Win+Shift+S) first.",
                parent=self.root)
            return
        if isinstance(grabbed, list):  # a file was copied in Explorer, not an image
            created, problems = [], []
            for name in grabbed:
                try:
                    created.append(store.create_from_image(Path(name).read_bytes(),
                                                           Path(name).name))
                except (store.StoreError, OSError) as exc:
                    problems.append(str(exc))
            self._after_import(created, problems)
            return

        import io

        buffer = io.BytesIO()
        grabbed.save(buffer, format="PNG")
        try:
            receipt_id = store.create_from_image(buffer.getvalue(), "clipboard.png")
        except store.StoreError as exc:
            messagebox.showerror(APP_NAME, str(exc), parent=self.root)
            return
        self._after_import([receipt_id], [])

    def add_manual(self) -> None:
        receipt_id = store.create_manual()
        self.show("receipts")
        page = self.pages["receipts"]
        page.refresh(select=receipt_id)
        self.update_status()

    def _after_import(self, created: list[int], problems: list[str]) -> None:
        for receipt_id in created:
            pipeline.submit_scan(receipt_id)
        if problems:
            messagebox.showwarning(
                APP_NAME,
                f"{len(created)} image(s) added.\n\nNot added:\n• "
                + "\n• ".join(problems[:6]),
                parent=self.root)
        if created:
            self.show("receipts")
            self.pages["receipts"].refresh(select=created[0])
            self.set_activity(f"Scanning {len(created)} receipt(s)…")
        self._schedule_poll(immediate=True)
        self.update_status()

    def export_csv(self) -> None:
        destination = filedialog.asksaveasfilename(
            parent=self.root, title="Export line items",
            defaultextension=".csv", initialfile="bookkeeping-items.csv",
            filetypes=[("CSV", "*.csv")],
        )
        if not destination:
            return
        try:
            rows = store.export_items_csv(Path(destination))
        except OSError as exc:
            messagebox.showerror(APP_NAME, f"Could not write the file: {exc}",
                                 parent=self.root)
            return
        messagebox.showinfo(
            APP_NAME,
            f"Wrote {rows} line item(s) from confirmed receipts to\n{destination}",
            parent=self.root)

    def open_data_folder(self) -> None:
        self._reveal(DATA_DIR)

    def open_log(self) -> None:
        log_path = DATA_DIR / "bookkeeping.log"
        if not log_path.exists():
            messagebox.showinfo(APP_NAME, "There is no log file yet.", parent=self.root)
            return
        self._reveal(log_path)

    def _reveal(self, target: Path) -> None:
        try:
            if sys.platform == "win32":
                os.startfile(str(target))  # noqa: S606 - opening the user's own folder
            else:  # pragma: no cover - this build is Windows only
                subprocess.Popen(["xdg-open", str(target)])
        except OSError as exc:
            messagebox.showerror(APP_NAME, f"Could not open {target}:\n{exc}",
                                 parent=self.root)

    def about(self) -> None:
        engines = ", ".join(
            f"{e['name']} ({'ready' if e['available'] else 'unavailable'})"
            for e in engine_status(settings_store.get_all())
        )
        messagebox.showinfo(
            f"About {APP_NAME}",
            f"{APP_NAME} {self.version}\n\n"
            "Reads receipt photos and keeps the expenses in order.\n\n"
            f"Data folder:\n{DATA_DIR}\n\n"
            f"Recognition: {engines}\n\n"
            "Everything stays on this computer, apart from the receipt image "
            "sent to the Anthropic API when the Claude engine runs.",
            parent=self.root)

    def cycle_theme(self) -> None:
        index = THEME_ORDER.index(self.theme.name)
        self.set_theme(THEME_ORDER[(index + 1) % len(THEME_ORDER)])

    def set_theme(self, name: str) -> None:
        settings_store.save({"theme": name})
        self.theme = Theme(name, ui_scale(self.root))
        # Rebuilding is the only reliable way to re-colour a Tk tree: widgets
        # hold their colours as instance options, not as inherited style.
        geometry = self.root.geometry()
        for child in self.root.winfo_children():
            child.destroy()
        self.pages.clear()
        self._build()
        self.root.geometry(geometry)
        self.show(self.current)
        self.update_status()

    # ------------------------------------------------------------ polling --

    def _schedule_poll(self, immediate: bool = False) -> None:
        if self._poll_job is not None:
            try:
                self.root.after_cancel(self._poll_job)
            except tk.TclError:
                pass
        delay = 60 if immediate else POLL_MS
        self._poll_job = self.root.after(delay, self._poll)

    def _poll(self) -> None:
        """Refresh while scans are running, then fall quiet."""
        self._poll_job = None
        counts = store.status_counts()
        busy = pipeline.busy() or counts.get("scanning", 0) or counts.get("uploaded", 0)
        self.update_status(counts)
        if busy:
            page = self.pages.get("receipts")
            if page is not None and self.current == "receipts":
                page.refresh(keep_selection=True)
            self._schedule_poll()
        else:
            self.set_activity("")

    def update_status(self, counts: dict[str, int] | None = None) -> None:
        counts = store.status_counts() if counts is None else counts
        total = sum(counts.values())
        parts = [f"{total} receipt{'' if total == 1 else 's'}"]
        if counts.get("needs_review"):
            parts.append(f"{counts['needs_review']} to review")
        if counts.get("failed"):
            parts.append(f"{counts['failed']} failed")
        if counts.get("confirmed"):
            parts.append(f"{counts['confirmed']} confirmed")
        scanning = counts.get("scanning", 0) + counts.get("uploaded", 0)
        if scanning:
            parts.append(f"{scanning} scanning")
        self.status_left.configure(text="  ·  ".join(parts))
        self.update_engine_pill()

    def set_activity(self, text: str) -> None:
        self.status_right.configure(text=text)

    def update_engine_pill(self) -> None:
        engines = engine_status(settings_store.get_all())
        ready = [e["name"] for e in engines if e["available"]]
        if ready:
            self.engine_pill.set(" + ".join(ready), "good")
        else:
            self.engine_pill.set("no engine — see Settings", "bad")

    # -------------------------------------------------------------- close --

    def close(self) -> None:
        if pipeline.busy() and not messagebox.askokcancel(
            APP_NAME, "A receipt is still being read. Close anyway?",
            parent=self.root
        ):
            return
        try:
            settings_store.save({"window_geometry": self.root.geometry()})
        except Exception:  # never block closing over a remembered size
            log.debug("Could not save the window geometry", exc_info=True)
        pipeline.shutdown()
        self.root.destroy()

    def _restore_geometry(self) -> None:
        saved = settings_store.get("window_geometry", "")
        if saved and _geometry_is_on_screen(self.root, saved):
            self.root.geometry(saved)
            return
        width = self.theme.px(DEFAULT_SIZE[0])
        height = self.theme.px(DEFAULT_SIZE[1])
        screen_w = self.root.winfo_screenwidth()
        screen_h = self.root.winfo_screenheight()
        width = min(width, int(screen_w * 0.92))
        height = min(height, int(screen_h * 0.88))
        x = max(0, (screen_w - width) // 2)
        y = max(0, (screen_h - height) // 3)
        self.root.geometry(f"{width}x{height}+{x}+{y}")


def _geometry_is_on_screen(root: tk.Misc, geometry: str) -> bool:
    """Reject a remembered position that would open off-screen.

    Unplugging the second monitor the app was last used on would otherwise leave
    the window invisible at, say, +2400+300 with no way to get it back.
    """
    try:
        size, x, y = geometry.replace("-", "+-").split("+")[0], None, None
        parts = geometry.split("+")
        size = parts[0]
        x, y = int(parts[1]), int(parts[2])
        width, height = (int(value) for value in size.split("x"))
    except (ValueError, IndexError):
        return False
    if width < 640 or height < 420:
        return False
    return (-20 <= x <= root.winfo_screenwidth() - 200
            and -20 <= y <= root.winfo_screenheight() - 150)


def run(argv: list[str] | None = None) -> int:
    """Create the window and enter the Tk event loop."""
    _enable_dpi_awareness()
    init_db()
    _log_engines()
    root = tk.Tk()
    # Tk's own font scaling is already derived from the display DPI once the
    # process is DPI-aware, so it is deliberately left alone here; only explicit
    # pixel measurements are scaled (see theme.ui_scale).
    MainWindow(root)
    root.mainloop()
    return 0


def _log_engines() -> None:
    """Record which engines this copy can actually use, and why not.

    Worth a line in the log on every start: when a portable copy misbehaves on
    somebody else's machine, the first question is always which engines it
    found there, and this answers it without asking them to open Settings. It is
    also how a frozen build is checked -- whether PyInstaller really bundled the
    Windows OCR bindings cannot be told from the source tree.
    """
    try:
        for engine in engine_status(settings_store.get_all()):
            log.info("Engine %-9s %s — %s", engine["name"],
                     "ready" if engine["available"] else "unavailable",
                     engine["detail"])
    except Exception:  # diagnostics must never stop the window from opening
        log.exception("Could not determine engine availability")


def _enable_dpi_awareness() -> None:
    """Ask Windows not to bitmap-stretch the window (which looks blurry)."""
    if sys.platform != "win32":
        return
    try:
        import ctypes

        ctypes.windll.shcore.SetProcessDpiAwareness(1)  # system DPI aware
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass
