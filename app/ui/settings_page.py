"""The settings page: which engine reads the receipts, and how.

The one thing this page must never do is show the API key back to the user's
screen in full, or write it into a log. It is displayed masked (`****1234`); the
entry box is empty until something is typed, and typing nothing leaves the stored
key alone.
"""

from __future__ import annotations

import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from .. import settings_store
from ..db import DATA_DIR
from ..extract import engine_status
from ..extract.claude_vision import PRICING
from .theme import Button, Card, entry, field_label

ENGINES = (
    ("Auto — Claude vision, then offline OCR", "auto"),
    ("Claude vision only", "claude"),
    ("Offline OCR only (built into Windows)", "windows"),
    ("Offline OCR only (Tesseract)", "tesseract"),
    ("Manual entry only (no scanning)", "manual"),
)
# "" means let the engine choose, which prefers an English recogniser.
AUTO_LANGUAGE = "Automatic (prefer English)"
EFFORTS = ("low", "medium", "high", "xhigh", "max")
MODELS = ("claude-opus-5", "claude-sonnet-5", "claude-haiku-4-5")


class SettingsPage:
    def __init__(self, parent: tk.Misc, win) -> None:
        self.win = win
        self.theme = win.theme
        self.frame = tk.Frame(parent, bg=self.theme["BG"])
        self._build()

    def _build(self) -> None:
        theme = self.theme
        columns = tk.Frame(self.frame, bg=theme["BG"])
        columns.pack(fill="both", expand=True, padx=12, pady=12)

        left = Card(columns, theme)
        left.pack(side="left", fill="both", expand=True, padx=(0, 10))
        tk.Label(left, text="Recognition", bg=theme["CARD"], fg=theme["FG"],
                 font=theme.font(11, "bold"), anchor="w").pack(fill="x", padx=16,
                                                                pady=(14, 8))
        body = tk.Frame(left, bg=theme["CARD"])
        body.pack(fill="x", padx=16)

        self.engine = tk.StringVar()
        holder = self._row(body, "Engine")
        ttk.Combobox(holder, textvariable=self.engine, state="readonly", width=38,
                     values=[label for label, _ in ENGINES]).pack(anchor="w")

        self.api_key = tk.StringVar()
        holder = self._row(body, "Anthropic API key")
        self.key_entry = entry(holder, theme, width=40, textvariable=self.api_key,
                               show="•")
        self.key_entry.pack(anchor="w")
        self.key_hint = tk.Label(body, text="", bg=theme["CARD"], fg=theme["DIM"],
                                 font=theme.font(8), anchor="w", justify="left",
                                 wraplength=theme.px(420))
        self.key_hint.pack(fill="x", pady=(0, 8))

        self.model = tk.StringVar()
        holder = self._row(body, "Model")
        ttk.Combobox(holder, textvariable=self.model, width=38,
                     values=list(MODELS)).pack(anchor="w")

        self.effort = tk.StringVar()
        holder = self._row(body, "Effort")
        ttk.Combobox(holder, textvariable=self.effort, state="readonly", width=38,
                     values=list(EFFORTS)).pack(anchor="w")

        self.base_url = tk.StringVar()
        holder = self._row(body, "Base URL (optional, for a proxy)")
        entry(holder, theme, width=40, textvariable=self.base_url).pack(anchor="w")

        self.ocr_language = tk.StringVar()
        holder = self._row(body, "Offline OCR language")
        ttk.Combobox(holder, textvariable=self.ocr_language, state="readonly",
                     width=38, values=[AUTO_LANGUAGE, *_ocr_languages()]).pack(anchor="w")

        self.tesseract = tk.StringVar()
        holder = self._row(body, "Tesseract executable (optional)")
        inner = tk.Frame(holder, bg=theme["CARD"])
        inner.pack(fill="x")
        entry(inner, theme, width=34, textvariable=self.tesseract).pack(side="left")
        Button(inner, theme, "Browse…", self._browse_tesseract).pack(side="left", padx=6)

        self.auto_confirm = tk.BooleanVar()
        check = ttk.Checkbutton(
            body, variable=self.auto_confirm,
            text="Auto-confirm receipts that pass every arithmetic check")
        check.pack(anchor="w", pady=(8, 0))
        tk.Label(body, text="Off by default: a reading whose numbers add up can "
                            "still have the wrong merchant or category.",
                 bg=theme["CARD"], fg=theme["DIM"], font=theme.font(8),
                 anchor="w", justify="left",
                 wraplength=theme.px(420)).pack(fill="x")

        buttons = tk.Frame(left, bg=theme["CARD"])
        buttons.pack(fill="x", padx=16, pady=14)
        Button(buttons, theme, "Save settings", self.save, kind="primary").pack(side="left")
        Button(buttons, theme, "Clear stored key", self.clear_key,
               kind="danger").pack(side="left", padx=6)
        self.saved = tk.Label(buttons, text="", bg=theme["CARD"], fg=theme["GOOD"],
                              font=theme.font(9))
        self.saved.pack(side="left", padx=10)

        right = tk.Frame(columns, bg=theme["BG"])
        right.pack(side="left", fill="both", expand=True)

        status_card = Card(right, theme)
        status_card.pack(fill="x")
        tk.Label(status_card, text="Engine status", bg=theme["CARD"], fg=theme["FG"],
                 font=theme.font(11, "bold"), anchor="w").pack(fill="x", padx=16,
                                                                pady=(14, 6))
        self.status_body = tk.Frame(status_card, bg=theme["CARD"])
        self.status_body.pack(fill="x", padx=16, pady=(0, 14))

        cost_card = Card(right, theme)
        cost_card.pack(fill="x", pady=(10, 0))
        tk.Label(cost_card, text="What a scan costs", bg=theme["CARD"], fg=theme["FG"],
                 font=theme.font(11, "bold"), anchor="w").pack(fill="x", padx=16,
                                                                pady=(14, 6))
        tk.Label(cost_card, text=_cost_text(), bg=theme["CARD"], fg=theme["MUTED"],
                 font=theme.font(9), anchor="w", justify="left",
                 wraplength=theme.px(380)).pack(fill="x", padx=16, pady=(0, 14))

        about_card = Card(right, theme)
        about_card.pack(fill="both", expand=True, pady=(10, 0))
        tk.Label(about_card, text="This computer", bg=theme["CARD"], fg=theme["FG"],
                 font=theme.font(11, "bold"), anchor="w").pack(fill="x", padx=16,
                                                                pady=(14, 6))
        self.about = tk.Label(about_card, text="", bg=theme["CARD"], fg=theme["MUTED"],
                              font=theme.font(9), anchor="w", justify="left",
                              wraplength=theme.px(380))
        self.about.pack(fill="x", padx=16)
        folder_row = tk.Frame(about_card, bg=theme["CARD"])
        folder_row.pack(fill="x", padx=16, pady=14)
        Button(folder_row, theme, "Open data folder",
               self.win.open_data_folder).pack(side="left")
        Button(folder_row, theme, "Open log file",
               self.win.open_log).pack(side="left", padx=6)

    def _row(self, parent: tk.Frame, label: str) -> tk.Frame:
        """A labelled row. Returns the frame the control must be created *in*.

        Creating the control elsewhere and packing it here with ``in_=`` looks
        like it works and does not: Tk stacks a widget behind any frame that is
        not its parent, so the control renders invisibly under the card.
        """
        holder = tk.Frame(parent, bg=self.theme["CARD"])
        holder.pack(fill="x", pady=(0, 6))
        field_label(holder, self.theme, label).pack(anchor="w")
        return holder

    # ------------------------------------------------------------ refresh --

    def refresh(self) -> None:
        values = settings_store.public_view()
        self.engine.set(_label_for(ENGINES, values.get("engine", "auto")))
        self.model.set(values.get("model", "claude-opus-5"))
        self.effort.set(values.get("effort", "medium"))
        self.base_url.set(values.get("anthropic_base_url", ""))
        self.ocr_language.set(values.get("ocr_language", "") or AUTO_LANGUAGE)
        self.tesseract.set(values.get("tesseract_cmd", ""))
        self.auto_confirm.set(values.get("auto_confirm_clean", "0") == "1")
        self.api_key.set("")
        masked = values.get("anthropic_api_key") or ""
        self.key_hint.configure(
            text=(f"Stored key: {masked}. Leave this box empty to keep it."
                  if masked else
                  "No key stored yet. Paste one from console.anthropic.com; it is "
                  "kept in this copy's own data folder and never sent anywhere "
                  "except to Anthropic.")
        )

        for child in self.status_body.winfo_children():
            child.destroy()
        for engine in engine_status(settings_store.get_all()):
            row = tk.Frame(self.status_body, bg=self.theme["CARD"])
            row.pack(fill="x", pady=3)
            tone = self.theme["GOOD"] if engine["available"] else self.theme["BAD"]
            tk.Label(row, text="●", bg=self.theme["CARD"], fg=tone,
                     font=self.theme.font(10)).pack(side="left")
            tk.Label(row, text=f" {engine['name']}", bg=self.theme["CARD"],
                     fg=self.theme["FG"], font=self.theme.font(10, "bold")
                     ).pack(side="left")
            tk.Label(row, text=engine["detail"], bg=self.theme["CARD"],
                     fg=self.theme["MUTED"], font=self.theme.font(9),
                     anchor="w", justify="left",
                     wraplength=self.theme.px(320)).pack(
                         side="left", padx=6, fill="x", expand=True)

        self.about.configure(text=(
            f"Bookkeeping {self.win.version}\n\n"
            f"Data folder:\n{DATA_DIR}\n\n"
            "The books, the receipt images and the key all live in that folder. "
            "Copy it (with the program) to move everything to another computer."
        ))

    # ------------------------------------------------------------- actions --

    def _browse_tesseract(self) -> None:
        chosen = filedialog.askopenfilename(
            parent=self.frame, title="Find tesseract.exe",
            filetypes=[("Programs", "*.exe"), ("All files", "*.*")])
        if chosen:
            self.tesseract.set(chosen)

    def save(self) -> None:
        updates = {
            "engine": dict(ENGINES)[self.engine.get()],
            "model": self.model.get().strip() or "claude-opus-5",
            "effort": self.effort.get() if self.effort.get() in EFFORTS else "medium",
            "anthropic_base_url": self.base_url.get().strip(),
            "ocr_language": "" if self.ocr_language.get() == AUTO_LANGUAGE
                            else self.ocr_language.get().strip(),
            "tesseract_cmd": self.tesseract.get().strip(),
            "auto_confirm_clean": "1" if self.auto_confirm.get() else "0",
        }
        typed = self.api_key.get().strip()
        if typed:
            updates["anthropic_api_key"] = typed
        settings_store.save(updates)
        self.refresh()
        self.win.update_engine_pill()
        self.saved.configure(text="Saved.")
        self.frame.after(2500, lambda: self.saved.configure(text=""))

    def clear_key(self) -> None:
        if not messagebox.askyesno(
            "Bookkeeping",
            "Remove the stored API key?\n\nScanning with Claude will stop working "
            "until a key is entered again.",
            parent=self.frame,
        ):
            return
        settings_store.save({"anthropic_api_key": "__clear__"})
        self.refresh()
        self.win.update_engine_pill()


def _label_for(choices: tuple[tuple[str, str], ...], value: str) -> str:
    for label, key in choices:
        if key == value:
            return label
    return choices[0][0]


def _ocr_languages() -> list[str]:
    """Language tags Windows can recognise, e.g. ['en-GB', 'zh-Hans-CN'].

    Which packs are installed is a property of the machine, not of this program,
    so the list is read at build time rather than hard-coded. Any failure here is
    not worth an error dialog: the dropdown simply offers Automatic only.
    """
    try:
        from winrt.windows.media.ocr import OcrEngine  # noqa: PLC0415

        return [lang.language_tag for lang in OcrEngine.available_recognizer_languages]
    except Exception:
        return []


def _cost_text() -> str:
    """What a scan costs, from measured token counts rather than a guess.

    Both shapes are shown because they differ by more than 2x: the reply grows
    with the number of line items, and a full weekly shop is not a coffee.
    """
    lines = ["Per receipt, at list prices — a short receipt (a few items) and a "
             "long one (a 24-line shop, measured):"]
    for model in MODELS:
        rates = PRICING.get(model)
        if not rates:
            continue
        small = 1600 / 1e6 * rates[0] + 400 / 1e6 * rates[1]
        large = 2208 / 1e6 * rates[0] + 1487 / 1e6 * rates[1]
        lines.append(f"   {model}:  ${small:.4f} – ${large:.4f}")
    lines.append("\nEvery scan records what it actually cost; it is shown beside "
                 "the receipt in the review pane.")
    return "\n".join(lines)
