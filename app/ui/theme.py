"""Colours, fonts and the handful of shared widgets the pages are built from.

Tkinter has no theming worth the name, so -- as in the Pomodoro timer -- a theme
here is a whole palette held in a dict, applied by rebuilding the widgets.

**The accent is the bar colour in the report charts**, so it is not free to be
whatever looks nice. All four palettes here have been checked two ways, and
adding a fifth means running both checks again -- ``tests/test_theme.py`` does
so automatically, for every theme in this table:

1. **Legibility, by WCAG contrast ratio** -- accent on the chart surface >= 3:1,
   body text >= 4.5:1, hint text >= 3:1. Measured on the surface that palette
   actually uses, not a generic white or black.
2. **Confusability with the status colours, by OKLab delta-E >= 15.** Contrast
   ratio is the wrong instrument for this second question and will mislead:
   `#0a4fa8` and `#a8001b` sit at 1.01:1 because they are equally *dark*, while
   being obviously different colours. Distinctness is a hue question.

Accent on chart surface, as measured: dark 4.79:1, light 4.30:1, dracula 5.90:1,
solarized 5.11:1. Nearest accent-to-status distance: 30.7, 29.4, 21.4, 20.1.

Status colours (good / warning / critical) are tuned per palette so they stay
legible on that surface, but they are never reused as a data colour -- which is
the reason for check 2.
"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from .. import i18n

# Segoe UI carries no Chinese glyphs, so the family follows the interface
# language -- see app/i18n.py. Kept as a module constant for the English
# case so nothing that reads FONT directly has to change.
FONT = "Segoe UI"
MONO = "Consolas"

THEMES: dict[str, dict[str, str]] = {
    "dark": dict(
        BG="#0d0d0d",          # window background ("page plane")
        CARD="#1a1a19",        # panels, chart surface
        CARD_ALT="#242422",    # hover / striped rows
        FG="#ffffff",
        MUTED="#c3c2b7",
        DIM="#898781",
        GRID="#2c2c2a",
        AXIS="#383835",
        ACCENT="#3987e5",
        ACCENT_HOVER="#529af0",
        ON_ACCENT="#ffffff",
        GOOD="#0ca30c",
        WARN="#fab219",
        BAD="#d03b3b",
        ENTRY="#101010",
    ),
    "light": dict(
        BG="#f9f9f7",
        CARD="#fcfcfb",
        CARD_ALT="#f0efec",
        FG="#0b0b0b",
        MUTED="#52514e",
        DIM="#898781",
        GRID="#e1e0d9",
        AXIS="#c3c2b7",
        ACCENT="#2a78d6",
        ACCENT_HOVER="#1c5cab",
        ON_ACCENT="#ffffff",
        GOOD="#006300",
        WARN="#b07600",
        BAD="#c02c2c",
        ENTRY="#ffffff",
    ),
    "dracula": dict(
        BG="#1e1f29",
        CARD="#282a36",
        CARD_ALT="#343746",
        FG="#f8f8f2",
        MUTED="#cfcfe0",
        DIM="#8f93a8",
        GRID="#343746",
        AXIS="#4a4d63",
        ACCENT="#bd93f9",
        ACCENT_HOVER="#d0b0ff",
        # Violet is light enough that white button text would be unreadable on
        # it; the page background doubles as the "on accent" ink instead.
        ON_ACCENT="#1e1f29",
        GOOD="#50fa7b",
        WARN="#f1fa8c",
        BAD="#ff6e6e",
        ENTRY="#21222c",
    ),
    "solarized": dict(
        BG="#eee8d5",
        CARD="#fdf6e3",
        CARD_ALT="#e8e1cd",
        FG="#073642",
        MUTED="#4d6b73",
        # Solarized's own base1 (#93a1a1) is the obvious choice here and is
        # wrong: it manages 2.48:1 on this cream card, too faint for the hint
        # text DIM is used for. Darkened until it cleared 3:1.
        DIM="#758787",
        GRID="#ded7c3",
        AXIS="#b9b3a1",
        # Deepened from Solarized's own blue, #268bd2. That one is not a failure
        # -- it reaches 3.41:1 on this cream card and would pass the 3:1 floor --
        # but only just, and a bar of it looks washed out beside the darker text.
        # #1f6f9c is 5.11:1, which is a choice for weight rather than a fix.
        ACCENT="#1f6f9c",
        ACCENT_HOVER="#17587c",
        ON_ACCENT="#ffffff",
        GOOD="#4d7a00",
        WARN="#a35b00",
        BAD="#c0362c",
        ENTRY="#fffbf0",
    ),
}

# The order the Theme button cycles through. Dark themes first, then light, so
# one press never jumps between a bright and a dark window.
THEME_ORDER = ("dark", "dracula", "light", "solarized")
THEME_LABELS = {
    "dark": "Dark",
    "dracula": "Dracula",
    "light": "Light",
    "solarized": "Solarized",
}
DEFAULT_THEME = "dark"


def ui_scale(widget: tk.Misc) -> float:
    """How much bigger everything is than at the 96-DPI baseline.

    Tk sizes *fonts* in points, so they follow the display automatically -- but
    every explicit pixel measurement (Treeview column widths, canvas heights,
    image thumbnails, wrap widths) does not. On this machine's 3840x2160 panel at
    150%, one "design pixel" is 1.5 real ones, and a window sized in raw pixels
    comes out half the size it should be with its content clipped. Every pixel
    number in the interface therefore goes through ``Theme.px``.
    """
    try:
        return max(1.0, widget.winfo_fpixels("1i") / 96.0)
    except tk.TclError:  # pragma: no cover - no display
        return 1.0


class Theme:
    """The active palette, the display scale, and the ttk styling for both."""

    def __init__(self, name: str = DEFAULT_THEME, scale: float = 1.0) -> None:
        self.name = name if name in THEMES else DEFAULT_THEME
        self.c = dict(THEMES[self.name])
        self.scale = max(1.0, float(scale))

    def __getitem__(self, key: str) -> str:
        return self.c[key]

    def px(self, value: float, minimum: int = 1) -> int:
        """A pixel measurement from the 96-DPI design, resized for this display."""
        return max(minimum, int(round(value * self.scale)))

    def font(self, size: int = 10, style: str = "normal") -> tuple:
        return (i18n.font_family(), size, style)

    def mono(self, size: int = 9) -> tuple:
        return (MONO, size)

    def apply_ttk(self, root: tk.Misc) -> None:
        """Style the ttk widgets that cannot be coloured any other way."""
        style = ttk.Style(root)
        # "clam" is the only built-in theme that honours background colours on
        # Treeview and Scrollbar; the Windows native theme ignores them.
        style.theme_use("clam")
        c = self.c

        style.configure(".", background=c["BG"], foreground=c["FG"],
                        fieldbackground=c["ENTRY"], font=self.font(10))
        style.configure("TFrame", background=c["BG"])
        style.configure("Card.TFrame", background=c["CARD"])
        style.configure("TLabel", background=c["BG"], foreground=c["FG"])
        style.configure("Card.TLabel", background=c["CARD"], foreground=c["FG"])
        style.configure("Muted.TLabel", background=c["BG"], foreground=c["MUTED"],
                        font=self.font(9))
        style.configure("CardMuted.TLabel", background=c["CARD"], foreground=c["MUTED"],
                        font=self.font(9))
        style.configure("Heading.TLabel", background=c["CARD"], foreground=c["FG"],
                        font=self.font(11, "bold"))
        style.configure("Value.TLabel", background=c["CARD"], foreground=c["FG"],
                        font=self.font(20, "bold"))

        style.configure("TEntry", fieldbackground=c["ENTRY"], foreground=c["FG"],
                        bordercolor=c["AXIS"], lightcolor=c["AXIS"],
                        darkcolor=c["AXIS"], insertcolor=c["FG"], padding=4)
        style.map("TEntry", fieldbackground=[("disabled", c["CARD_ALT"])],
                  bordercolor=[("focus", c["ACCENT"])])
        style.configure("TCombobox", fieldbackground=c["ENTRY"], background=c["CARD"],
                        foreground=c["FG"], arrowcolor=c["MUTED"], padding=3,
                        bordercolor=c["AXIS"])
        style.map("TCombobox", fieldbackground=[("readonly", c["ENTRY"])],
                  foreground=[("readonly", c["FG"])],
                  bordercolor=[("focus", c["ACCENT"])])
        style.configure("TCheckbutton", background=c["CARD"], foreground=c["FG"],
                        indicatorcolor=c["ENTRY"], focuscolor=c["ACCENT"])
        style.map("TCheckbutton", background=[("active", c["CARD"])],
                  indicatorcolor=[("selected", c["ACCENT"])])

        style.configure("Treeview", background=c["CARD"], fieldbackground=c["CARD"],
                        foreground=c["FG"], bordercolor=c["GRID"], borderwidth=0,
                        rowheight=self.px(25), font=self.font(10))
        style.configure("Treeview.Heading", background=c["CARD_ALT"],
                        foreground=c["MUTED"], font=self.font(9, "bold"),
                        relief="flat", padding=4)
        style.map("Treeview.Heading", background=[("active", c["CARD_ALT"])])
        style.map("Treeview",
                  background=[("selected", c["ACCENT"])],
                  foreground=[("selected", c["ON_ACCENT"])])

        style.configure("TScrollbar", background=c["CARD"], troughcolor=c["BG"],
                        bordercolor=c["BG"], arrowcolor=c["MUTED"],
                        darkcolor=c["CARD_ALT"], lightcolor=c["CARD_ALT"])
        style.configure("TPanedwindow", background=c["BG"])
        style.configure("Sash", background=c["GRID"], gripcount=0, sashthickness=6)
        style.configure("TSeparator", background=c["GRID"])
        style.configure("TNotebook", background=c["BG"], borderwidth=0)
        style.configure("TProgressbar", background=c["ACCENT"], troughcolor=c["GRID"],
                        bordercolor=c["GRID"], lightcolor=c["ACCENT"],
                        darkcolor=c["ACCENT"])


# --------------------------------------------------------------------------- #
# Small widgets. Plain tk (not ttk) wherever a colour has to be exact.
# --------------------------------------------------------------------------- #


class Button(tk.Button):
    """A flat button that actually takes the colours it is given.

    ttk.Button cannot be reliably coloured across Windows themes, and this app
    needs a primary/ghost/danger distinction, so the buttons are plain tk with
    hover handling attached.
    """

    def __init__(self, parent, theme: Theme, text: str, command=None,
                 kind: str = "ghost", on: str | None = None, **kwargs) -> None:
        c = theme.c
        surface = on or c["CARD"]
        if kind == "primary":
            bg, fg, hover = c["ACCENT"], c["ON_ACCENT"], c["ACCENT_HOVER"]
        elif kind == "danger":
            bg, fg, hover = c["BAD"], "#ffffff", c["BAD"]
        else:
            bg, fg, hover = surface, c["FG"], c["CARD_ALT"]
        super().__init__(
            parent, text=text, command=command, bg=bg, fg=fg,
            activebackground=hover, activeforeground=fg,
            font=theme.font(10, "bold" if kind == "primary" else "normal"),
            relief="flat", bd=0, padx=12, pady=6, cursor="hand2",
            highlightthickness=1,
            highlightbackground=c["AXIS"] if kind == "ghost" else bg,
            disabledforeground=c["DIM"], **kwargs,
        )
        self._bg, self._hover = bg, hover
        self.bind("<Enter>", self._enter, add="+")
        self.bind("<Leave>", self._leave, add="+")

    def _enter(self, _event=None) -> None:
        if str(self["state"]) != "disabled":
            self.configure(bg=self._hover)

    def _leave(self, _event=None) -> None:
        self.configure(bg=self._bg)


class Card(tk.Frame):
    """A panel with a hairline border, the app's basic unit of layout."""

    def __init__(self, parent, theme: Theme, **kwargs) -> None:
        super().__init__(parent, bg=theme["CARD"], highlightthickness=1,
                         highlightbackground=theme["GRID"], **kwargs)


class Pill(tk.Label):
    """A small rounded-looking status label (engine state, receipt status)."""

    def __init__(self, parent, theme: Theme, text: str = "", tone: str = "muted",
                 on: str | None = None, **kwargs) -> None:
        colours = {"muted": theme["MUTED"], "good": theme["GOOD"],
                   "warn": theme["WARN"], "bad": theme["BAD"],
                   "accent": theme["ACCENT"]}
        super().__init__(parent, text=text, bg=on or theme["CARD"],
                         fg=colours.get(tone, theme["MUTED"]),
                         font=theme.font(9), padx=8, pady=2, **kwargs)
        self._theme = theme
        self._colours = colours

    def set(self, text: str, tone: str = "muted") -> None:
        self.configure(text=text, fg=self._colours.get(tone, self._theme["MUTED"]))


class ScrollFrame(tk.Frame):
    """A vertically scrollable container.

    Tk has no such widget: it is always a Canvas with an inner Frame, plus the
    two bindings people forget -- resize the inner window to the canvas width,
    and update the scrollregion when the content changes.
    """

    def __init__(self, parent, theme: Theme, **kwargs) -> None:
        super().__init__(parent, bg=theme["CARD"], **kwargs)
        self.canvas = tk.Canvas(self, bg=theme["CARD"], highlightthickness=0, bd=0)
        self.scrollbar = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.body = tk.Frame(self.canvas, bg=theme["CARD"])
        self._window = self.canvas.create_window((0, 0), window=self.body, anchor="nw")

        self.canvas.configure(yscrollcommand=self.scrollbar.set)
        self.canvas.pack(side="left", fill="both", expand=True)
        self.scrollbar.pack(side="right", fill="y")

        self.body.bind("<Configure>", self._on_body)
        self.canvas.bind("<Configure>", self._on_canvas)
        # Wheel scrolling only while the pointer is inside, so nested scrollables
        # do not fight each other.
        self.canvas.bind("<Enter>", lambda _e: self._bind_wheel(True))
        self.canvas.bind("<Leave>", lambda _e: self._bind_wheel(False))

    def _on_body(self, _event=None) -> None:
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def _on_canvas(self, event) -> None:
        self.canvas.itemconfigure(self._window, width=event.width)

    def _bind_wheel(self, active: bool) -> None:
        if active:
            self.canvas.bind_all("<MouseWheel>", self._wheel)
        else:
            self.canvas.unbind_all("<MouseWheel>")

    def _wheel(self, event) -> None:
        if self.canvas.winfo_exists():
            self.canvas.yview_scroll(int(-event.delta / 120), "units")

    def clear(self) -> None:
        for child in self.body.winfo_children():
            child.destroy()

    def to_top(self) -> None:
        self.canvas.yview_moveto(0.0)


def field_label(parent, theme: Theme, text: str, on_card: bool = True) -> tk.Label:
    return tk.Label(parent, text=text, bg=theme["CARD"] if on_card else theme["BG"],
                    fg=theme["MUTED"], font=theme.font(9), anchor="w")


def entry(parent, theme: Theme, width: int = 16, **kwargs) -> tk.Entry:
    """A plain tk.Entry, because ttk.Entry ignores several colours on Windows."""
    return tk.Entry(parent, width=width, bg=theme["ENTRY"], fg=theme["FG"],
                    insertbackground=theme["FG"], relief="flat",
                    highlightthickness=1, highlightbackground=theme["AXIS"],
                    highlightcolor=theme["ACCENT"], font=theme.font(10), **kwargs)
