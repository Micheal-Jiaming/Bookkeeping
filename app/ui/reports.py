"""The reports page: where the money went.

The charts are drawn by hand on a Tk canvas, and they follow the same
data-visualisation rules the browser version did, because the rules are about
reading, not about the toolkit:

* Each chart shows **one measure**, so it uses **one hue** -- the accent. The row
  label (category) or the axis (month) carries identity, so there is no legend
  and no per-category colour cycling.
* Bars are anchored to the baseline, have rounded data-ends, and keep a 2px gap
  of surface between neighbours.
* Values are direct-labelled rather than hidden behind a hover, and every chart
  is backed by a table with the same numbers.
"""

from __future__ import annotations

import tkinter as tk
from datetime import date, timedelta
from tkinter import ttk

from .. import store
from ..i18n import t
from ..money import from_cents
from .theme import Button, Card, entry, field_label

RANGES = (("30 days", 30), ("90 days", 90), ("1 year", 365), ("All time", None))
INCLUDE = (("Confirmed only", ("confirmed",)),
           ("Confirmed + unreviewed", ("confirmed", "needs_review")))

# All in 96-DPI design pixels; every use goes through theme.px.
BAR_HEIGHT = 15
BAR_GAP = 9
LABEL_WIDTH = 132
VALUE_WIDTH = 78
CHART_PAD = 12


class ReportsPage:
    def __init__(self, parent: tk.Misc, win) -> None:
        self.win = win
        self.theme = win.theme
        self.frame = tk.Frame(parent, bg=self.theme["BG"])
        self.data: dict | None = None
        self._redraw_job: str | None = None
        self._range = 90
        self._build()

    # -------------------------------------------------------------- build --

    def _build(self) -> None:
        theme = self.theme
        bar = tk.Frame(self.frame, bg=theme["BG"])
        bar.pack(fill="x", padx=12, pady=(12, 8))

        # Packed first so it keeps its space when the window is narrow; pack
        # order, not side, decides who gets squeezed.
        Button(bar, theme, t("Export CSV…"), self.win.export_csv,
               on=theme["BG"]).pack(side="right")

        self.range_buttons: dict[object, tk.Button] = {}
        for label, days in RANGES:
            button = Button(bar, theme, t(label), lambda d=days: self.set_range(d),
                            on=theme["BG"])
            button.pack(side="left", padx=(0, 5))
            self.range_buttons[days] = button

        holder = tk.Frame(bar, bg=theme["BG"])
        holder.pack(side="left", padx=(16, 0))
        field_label(holder, theme, t("From / to (YYYY-MM-DD)"), on_card=False).pack(anchor="w")
        row = tk.Frame(holder, bg=theme["BG"])
        row.pack()
        self.from_var = tk.StringVar()
        self.to_var = tk.StringVar()
        entry(row, theme, width=11, textvariable=self.from_var).pack(side="left")
        entry(row, theme, width=11, textvariable=self.to_var).pack(side="left", padx=4)
        Button(row, theme, t("Apply"), self.refresh, on=theme["BG"]).pack(side="left")

        self.include = tk.StringVar(value=INCLUDE[0][0])
        include_box = ttk.Combobox(bar, textvariable=self.include, state="readonly",
                                   width=22, values=[label for label, _ in INCLUDE])
        include_box.pack(side="left", padx=(16, 0))
        include_box.bind("<<ComboboxSelected>>", lambda _e: self.refresh())

        self.scroll_holder = tk.Frame(self.frame, bg=theme["BG"])
        self.scroll_holder.pack(fill="both", expand=True, padx=12, pady=(0, 12))

        self.tiles = tk.Frame(self.scroll_holder, bg=theme["BG"])
        self.tiles.pack(fill="x")
        self.tile_labels: list[tuple[tk.Label, tk.Label, tk.Label]] = []
        for _ in range(4):
            card = Card(self.tiles, theme)
            card.pack(side="left", fill="both", expand=True, padx=(0, 10))
            label = tk.Label(card, text="", bg=theme["CARD"], fg=theme["MUTED"],
                             font=theme.font(9), anchor="w")
            label.pack(fill="x", padx=14, pady=(12, 0))
            value = tk.Label(card, text="—", bg=theme["CARD"], fg=theme["FG"],
                             font=theme.font(19, "bold"), anchor="w")
            value.pack(fill="x", padx=14)
            sub = tk.Label(card, text="", bg=theme["CARD"], fg=theme["DIM"],
                           font=theme.font(9), anchor="w")
            sub.pack(fill="x", padx=14, pady=(0, 12))
            self.tile_labels.append((label, value, sub))

        charts = tk.Frame(self.scroll_holder, bg=theme["BG"])
        charts.pack(fill="both", expand=True, pady=(12, 0))

        left = Card(charts, theme)
        left.pack(side="left", fill="both", expand=True, padx=(0, 10))
        tk.Label(left, text=t("Spend by category"), bg=theme["CARD"], fg=theme["FG"],
                 font=theme.font(10, "bold"), anchor="w").pack(fill="x", padx=14,
                                                               pady=(12, 4))
        self.category_canvas = tk.Canvas(left, bg=theme["CARD"], highlightthickness=0,
                                         bd=0, height=theme.px(240))
        self.category_canvas.pack(fill="both", expand=True, padx=8, pady=(0, 10))
        self.category_canvas.bind("<Configure>", self._queue_redraw)

        right = tk.Frame(charts, bg=theme["BG"])
        right.pack(side="left", fill="both", expand=True)

        month_card = Card(right, theme)
        month_card.pack(fill="both", expand=True)
        tk.Label(month_card, text=t("Spend by month"), bg=theme["CARD"], fg=theme["FG"],
                 font=theme.font(10, "bold"), anchor="w").pack(fill="x", padx=14,
                                                               pady=(12, 4))
        self.month_canvas = tk.Canvas(month_card, bg=theme["CARD"], highlightthickness=0,
                                      bd=0, height=theme.px(180))
        self.month_canvas.pack(fill="both", expand=True, padx=8, pady=(0, 10))
        self.month_canvas.bind("<Configure>", self._queue_redraw)

        merchant_card = Card(right, theme)
        merchant_card.pack(fill="both", expand=True, pady=(10, 0))
        tk.Label(merchant_card, text=t("Top merchants"), bg=theme["CARD"], fg=theme["FG"],
                 font=theme.font(10, "bold"), anchor="w").pack(fill="x", padx=14,
                                                               pady=(12, 4))
        self.merchant_tree = ttk.Treeview(
            merchant_card, columns=("merchant", "receipts", "amount"),
            show="headings", height=6, selectmode="none")
        for key, label, width, anchor in (("merchant", t("Merchant"), 140, "w"),
                                          ("receipts", t("Receipts"), 62, "e"),
                                          ("amount", t("Amount"), 76, "e")):
            self.merchant_tree.heading(key, text=label)
            self.merchant_tree.column(key, width=theme.px(width), anchor=anchor,
                                      minwidth=theme.px(50))
        self.merchant_tree.pack(fill="both", expand=True, padx=8, pady=(0, 10))

        self.note = tk.Label(self.scroll_holder, text="", bg=theme["BG"],
                             fg=theme["DIM"], font=theme.font(9), anchor="w")
        self.note.pack(fill="x", pady=(8, 0))

    # ------------------------------------------------------------ refresh --

    def set_range(self, days: int | None) -> None:
        self._range = days
        if days is None:
            self.from_var.set("")
            self.to_var.set("")
        else:
            today = date.today()
            self.from_var.set((today - timedelta(days=days)).isoformat())
            self.to_var.set(today.isoformat())
        self.refresh()

    def refresh(self) -> None:
        theme = self.theme
        for days, button in self.range_buttons.items():
            selected = days == self._range
            button.configure(bg=theme["ACCENT"] if selected else theme["BG"],
                             fg=theme["ON_ACCENT"] if selected else theme["FG"])
            button._bg = theme["ACCENT"] if selected else theme["BG"]

        if not self.from_var.get() and not self.to_var.get() and self._range:
            today = date.today()
            self.from_var.set((today - timedelta(days=self._range)).isoformat())
            self.to_var.set(today.isoformat())

        statuses = dict(INCLUDE)[self.include.get()]
        self.data = store.report_summary(
            date_from=self.from_var.get().strip() or None,
            date_to=self.to_var.get().strip() or None,
            statuses=statuses,
        )
        self._render_tiles()
        self._render_merchants()
        self._draw()

    def _render_tiles(self) -> None:
        assert self.data is not None
        totals = self.data["totals"]
        pending = self.data["pending_review"]
        values = (
            (t("Total spend"), f"${totals['spend'] or '0.00'}",
             f"{totals['receipts']} receipt(s)"),
            (t("Average receipt"), f"${from_cents(totals['average_cents'])}",
             f"{totals['items']} line items"),
            (t("Tax paid"), f"${totals['tax'] or '0.00'}", t("in this period")),
            (t("Awaiting review"), str(pending),
             t("not counted here") if pending else t("nothing pending")),
        )
        for (label, value, sub), (label_w, value_w, sub_w) in zip(values, self.tile_labels):
            label_w.configure(text=label)
            value_w.configure(text=value)
            sub_w.configure(text=sub)

    def _render_merchants(self) -> None:
        assert self.data is not None
        self.merchant_tree.delete(*self.merchant_tree.get_children())
        for bucket in self.data["by_merchant"]:
            self.merchant_tree.insert("", "end", values=(
                bucket["merchant"], bucket["receipts"], f"${bucket['amount']}"))

    # ------------------------------------------------------------- drawing --

    def _queue_redraw(self, _event=None) -> None:
        # Tk fires <Configure> for every pixel of a window drag; redrawing on
        # each one makes resizing crawl.
        if self._redraw_job:
            self.frame.after_cancel(self._redraw_job)
        self._redraw_job = self.frame.after(60, self._draw)

    def _draw(self) -> None:
        self._redraw_job = None
        if self.data is None:
            return
        # Before Tk's first layout pass a canvas reports a width of 1, and
        # drawing against that produced 40-pixel bars with their value labels off
        # the left edge, and a month chart drawn entirely above the visible area.
        # Wait for real geometry rather than draw nonsense.
        if self.category_canvas.winfo_width() <= 1 or self.month_canvas.winfo_width() <= 1:
            self._redraw_job = self.frame.after(50, self._draw)
            return
        self._draw_categories()
        self._draw_months()
        totals = self.data["totals"]
        self.note.configure(text=(
            "Category figures come from line items, which exclude tax; the "
            "difference between a receipt's total and its lines is shown as "
            "“Tax & unitemised”, so the categories add up to the "
            f"${totals['spend'] or '0.00'} actually spent."
        ))

    def _draw_categories(self) -> None:
        canvas = self.category_canvas
        canvas.delete("all")
        theme = self.theme
        buckets = self.data["by_category"] if self.data else []
        width = canvas.winfo_width()
        if not buckets:
            canvas.create_text(width / 2, 40, text=t("Nothing confirmed in this range yet."),
                               fill=theme["DIM"], font=theme.font(10))
            return

        pad = theme.px(CHART_PAD)
        bar_height = theme.px(BAR_HEIGHT)
        gap = theme.px(BAR_GAP)
        # The label column takes a share of the width rather than a fixed number,
        # so a long category name still fits in a narrow window.
        label_width = int(min(theme.px(LABEL_WIDTH), max(theme.px(70), width * 0.30)))
        value_width = theme.px(VALUE_WIDTH)

        needed = pad * 2 + len(buckets) * (bar_height + gap)
        canvas.configure(height=max(theme.px(150), needed))
        largest = max(abs(b["amount_cents"]) for b in buckets) or 1
        track_left = pad + label_width
        track_right = max(track_left + theme.px(40), width - pad - value_width)
        font = theme.font(9)

        y = pad
        for bucket in buckets:
            middle = y + bar_height / 2
            # Truncated rather than wrapped: a wrapped label overlapped the row
            # below it, which read as a rendering fault.
            canvas.create_text(pad, middle, anchor="w", font=font, fill=theme["MUTED"],
                               text=_fit(canvas, bucket["category"], font,
                                         label_width - theme.px(8)))
            canvas.create_rectangle(track_left, y, track_right, y + bar_height,
                                    fill=theme["GRID"], outline="")
            span = (track_right - track_left) * (abs(bucket["amount_cents"]) / largest)
            _rounded_bar(canvas, track_left, y, track_left + max(2, span),
                         y + bar_height, theme["ACCENT"], radius=theme.px(4))
            canvas.create_text(track_right + theme.px(8), middle, anchor="w",
                               text=f"${bucket['amount']}", fill=theme["FG"], font=font)
            y += bar_height + gap

    def _draw_months(self) -> None:
        canvas = self.month_canvas
        canvas.delete("all")
        theme = self.theme
        months = self.data["by_month"] if self.data else []
        width = canvas.winfo_width()
        height = canvas.winfo_height()
        if not months:
            canvas.create_text(width / 2, height / 2, text=t("No months to show yet."),
                               fill=theme["DIM"], font=theme.font(10))
            return

        pad = theme.px(CHART_PAD)
        baseline = height - theme.px(22)
        top = theme.px(24)
        largest = max(m["amount_cents"] for m in months) or 1
        slot = (width - pad * 2) / len(months)
        bar_width = min(theme.px(52), max(theme.px(8), slot - theme.px(6)))

        canvas.create_line(pad, baseline, width - pad, baseline, fill=theme["AXIS"])
        for index, month in enumerate(months):
            centre = pad + slot * (index + 0.5)
            span = (baseline - top) * (month["amount_cents"] / largest)
            x0, x1 = centre - bar_width / 2, centre + bar_width / 2
            _rounded_bar(canvas, x0, baseline - max(2, span), x1, baseline,
                         theme["ACCENT"], vertical=True, radius=theme.px(4))
            canvas.create_text(centre, baseline + theme.px(11),
                               text=month["month"][2:], fill=theme["DIM"],
                               font=theme.font(8))
            # Direct-label the largest column only: a number on every bar is
            # noise, and the tallest is the one people look for.
            if month["amount_cents"] == largest:
                canvas.create_text(centre, baseline - max(2, span) - theme.px(9),
                                   text=f"${month['amount']}", fill=theme["FG"],
                                   font=theme.font(8, "bold"))


def _fit(canvas: tk.Canvas, text: str, font: tuple, limit: int) -> str:
    """Shorten text with an ellipsis until it fits `limit` pixels."""
    import tkinter.font as tkfont

    measure = tkfont.Font(root=canvas, font=font).measure
    if measure(text) <= limit:
        return text
    shortened = text
    while shortened and measure(shortened + "…") > limit:
        shortened = shortened[:-1]
    return (shortened + "…") if shortened else ""


def _rounded_bar(canvas: tk.Canvas, x0: float, y0: float, x1: float, y1: float,
                 colour: str, vertical: bool = False, radius: int = 4) -> None:
    """A bar with its data-end rounded and its baseline end square.

    Tk has no rounded rectangle, so this is a rectangle plus a half-disc drawn
    at the far end -- which is exactly the shape the chart spec asks for: the end
    that carries the value is rounded, the end anchored to the baseline is not.
    """
    if vertical:
        span = y1 - y0
        r = min(radius, max(0, span / 2), (x1 - x0) / 2)
        canvas.create_rectangle(x0, y0 + r, x1, y1, fill=colour, outline="")
        if r > 0:
            canvas.create_arc(x0, y0, x1, y0 + 2 * r, start=0, extent=180,
                              fill=colour, outline="", style="pieslice")
    else:
        span = x1 - x0
        r = min(radius, max(0, span / 2), (y1 - y0) / 2)
        canvas.create_rectangle(x0, y0, x1 - r, y1, fill=colour, outline="")
        if r > 0:
            canvas.create_arc(x1 - 2 * r, y0, x1, y1, start=270, extent=180,
                              fill=colour, outline="", style="pieslice")
