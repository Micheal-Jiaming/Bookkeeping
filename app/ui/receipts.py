"""The receipts page: the list on the left, the review pane on the right.

This is where the work happens, so it is worth saying what the review pane is
*for*. A reading -- by the vision model or by OCR -- is a proposal, not a fact. The
pane puts the stored image next to every field that was extracted, shows the
arithmetic complaints in plain words, and lets every value be corrected before
the receipt is confirmed. Nothing reaches the reports until someone presses
"Save & confirm".
"""

from __future__ import annotations

import logging
import os
import sys
import tkinter as tk
from tkinter import messagebox, ttk

from .. import pipeline, store
from ..money import from_cents, to_cents
from .theme import Button, Card, Pill, ScrollFrame, entry, field_label

log = logging.getLogger("bookkeeping.ui.receipts")

STATUS_FILTERS = (
    ("All", None),
    ("Needs review", ("needs_review",)),
    ("Confirmed", ("confirmed",)),
    ("Scanning", ("scanning", "uploaded")),
    ("Failed", ("failed",)),
)
STATUS_LABEL = {
    "uploaded": "queued", "scanning": "scanning…", "needs_review": "needs review",
    "confirmed": "confirmed", "failed": "failed",
}
STATUS_TONE = {
    "uploaded": "accent", "scanning": "accent", "needs_review": "muted",
    "confirmed": "good", "failed": "bad",
}
# In 96-DPI design pixels; scaled per display before use. Kept modest on purpose:
# a taller preview pushes the line-item editor off the bottom of the pane, and
# the image can always be opened full size with a click.
IMAGE_MAX = (260, 320)


class ReceiptsPage:
    def __init__(self, parent: tk.Misc, win) -> None:
        self.win = win
        self.theme = win.theme
        self.selected: int | None = None
        self.frame = tk.Frame(parent, bg=self.theme["BG"])
        self._row_ids: list[int] = []
        self._build()

    # -------------------------------------------------------------- build --

    def _build(self) -> None:
        theme = self.theme
        bar = tk.Frame(self.frame, bg=theme["BG"])
        bar.pack(fill="x", padx=12, pady=(12, 8))

        Button(bar, theme, "＋ Add receipt images", self.win.add_images,
               kind="primary", on=theme["BG"]).pack(side="left")
        Button(bar, theme, "Paste image", self.win.paste_image,
               on=theme["BG"]).pack(side="left", padx=6)
        Button(bar, theme, "Add by hand", self.win.add_manual,
               on=theme["BG"]).pack(side="left")

        tk.Label(bar, text="Show", bg=theme["BG"], fg=theme["MUTED"],
                 font=theme.font(9)).pack(side="left", padx=(18, 4))
        self.status_choice = tk.StringVar(value=STATUS_FILTERS[0][0])
        status_box = ttk.Combobox(bar, textvariable=self.status_choice, width=13,
                                  state="readonly",
                                  values=[label for label, _ in STATUS_FILTERS])
        status_box.pack(side="left")
        status_box.bind("<<ComboboxSelected>>", lambda _e: self.refresh())

        tk.Label(bar, text="Search", bg=theme["BG"], fg=theme["MUTED"],
                 font=theme.font(9)).pack(side="left", padx=(14, 4))
        self.search = entry(bar, theme, width=26)
        self.search.pack(side="left")
        self.search.bind("<Return>", lambda _e: self.refresh())
        self.search.bind("<KeyRelease>", self._search_soon)
        self._search_job: str | None = None

        split = ttk.Panedwindow(self.frame, orient="horizontal")
        split.pack(fill="both", expand=True, padx=12, pady=(0, 12))

        list_card = Card(split, theme)
        split.add(list_card, weight=4)
        columns = ("date", "merchant", "category", "items", "total", "status")
        self.tree = ttk.Treeview(list_card, columns=columns, show="headings",
                                 selectmode="browse")
        # Widths are design pixels: a Treeview column does not scale itself, so
        # on a 150% display these must be scaled or every column truncates.
        headings = (("date", "Date", 84), ("merchant", "Merchant", 130),
                    ("category", "Category", 96), ("items", "Items", 44),
                    ("total", "Total", 68), ("status", "Status", 88))
        for key, label, width in headings:
            self.tree.heading(key, text=label)
            anchor = "e" if key in ("items", "total") else "w"
            self.tree.column(key, width=theme.px(width), anchor=anchor,
                             minwidth=theme.px(40), stretch=key == "merchant")
        scroll = ttk.Scrollbar(list_card, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.pack(side="left", fill="both", expand=True, padx=(1, 0), pady=1)
        scroll.pack(side="right", fill="y", pady=1)
        self.tree.bind("<<TreeviewSelect>>", self._on_select)
        self.tree.bind("<Double-1>", lambda _e: self.review.open_image())
        self.tree.bind("<Delete>", lambda _e: self.review.delete())
        self.tree.tag_configure("failed", foreground=theme["BAD"])
        self.tree.tag_configure("confirmed", foreground=theme["MUTED"])

        self.empty = tk.Label(
            list_card, bg=theme["CARD"], fg=theme["DIM"], font=theme.font(10),
            text="No receipts yet.\n\nUse “Add receipt images” to scan one,\n"
                 "or “Add by hand” to type one in.", justify="center")

        self.review = ReviewPane(split, self)
        split.add(self.review.frame, weight=5)

    def _search_soon(self, _event=None) -> None:
        if self._search_job:
            self.frame.after_cancel(self._search_job)
        self._search_job = self.frame.after(250, self.refresh)

    # ------------------------------------------------------------- refresh --

    def refresh(self, select: int | None = None, keep_selection: bool = False) -> None:
        wanted = select if select is not None else (
            self.selected if keep_selection or self.selected else None
        )
        statuses = dict(STATUS_FILTERS)[self.status_choice.get()]
        query = self.search.get().strip() or None
        _total, rows = store.list_receipts(statuses=statuses, query=query)

        # Rebuilding the whole tree is fine at this scale (hundreds of rows) and
        # avoids a diffing bug class; the selection is restored by id below.
        self.tree.delete(*self.tree.get_children())
        self._row_ids = []
        for row in rows:
            tags = [row["status"]] if row["status"] in ("failed", "confirmed") else []
            self.tree.insert(
                "", "end", iid=str(row["id"]), tags=tags,
                values=(
                    row["purchased_at"] or "—",
                    row["merchant"] or row["original_name"] or "(unread)",
                    row["category_name"] or "—",
                    row["item_count"] or 0,
                    from_cents(row["total_cents"]) or "—",
                    STATUS_LABEL.get(row["status"], row["status"]),
                ))
            self._row_ids.append(row["id"])

        if rows:
            self.empty.place_forget()
        else:
            self.empty.place(relx=0.5, rely=0.4, anchor="center")

        if wanted in self._row_ids:
            self.tree.selection_set(str(wanted))
            self.tree.see(str(wanted))
            self.review.load(wanted)
        elif self._row_ids and select is None and not keep_selection:
            self.review.clear()
            self.selected = None
        elif not self._row_ids:
            self.review.clear()
            self.selected = None

    def _on_select(self, _event=None) -> None:
        chosen = self.tree.selection()
        if not chosen:
            return
        receipt_id = int(chosen[0])
        if receipt_id != self.selected:
            self.review.load(receipt_id)

    def after_change(self, select: int | None = None) -> None:
        """Called by the review pane once it has changed something."""
        self.refresh(select=select)
        self.win.update_status()


class ReviewPane:
    """The editor for one receipt."""

    def __init__(self, parent: tk.Misc, page: ReceiptsPage) -> None:
        self.page = page
        self.win = page.win
        self.theme = page.theme
        self.receipt: dict | None = None
        self._photo = None          # ImageTk reference; dropping it blanks the image
        self._item_rows: list[dict] = []
        self._categories: list[dict] = []
        self._raw_shown = False

        theme = self.theme
        self.frame = Card(parent, theme)

        head = tk.Frame(self.frame, bg=theme["CARD"])
        head.pack(fill="x", padx=14, pady=(12, 0))
        self.title = tk.Label(head, text="No receipt selected", bg=theme["CARD"],
                              fg=theme["FG"], font=theme.font(12, "bold"), anchor="w")
        self.title.pack(side="left")
        self.status_pill = Pill(head, theme, "")
        self.status_pill.pack(side="right")
        self.meta = tk.Label(self.frame, text="", bg=theme["CARD"], fg=theme["DIM"],
                             font=theme.font(9), anchor="w", justify="left")
        self.meta.pack(fill="x", padx=14, pady=(1, 6))

        self.flags = tk.Frame(self.frame, bg=theme["CARD"])
        self.flags.pack(fill="x", padx=14)

        self.scroll = ScrollFrame(self.frame, theme)
        self.scroll.pack(fill="both", expand=True, padx=1, pady=6)
        self.body = self.scroll.body

        self.actions = tk.Frame(self.frame, bg=theme["CARD"])
        self.actions.pack(fill="x", padx=14, pady=(0, 12))
        self.buttons: dict[str, tk.Button] = {}
        for key, label, kind, command in (
            ("confirm", "Save & confirm", "primary", self.save_and_confirm),
            ("save", "Save draft", "ghost", self.save_draft),
            ("rescan", "Re-scan", "ghost", self.rescan),
            ("raw", "Output", "ghost", self.toggle_raw),
            ("delete", "Delete", "danger", self.delete),
        ):
            button = Button(self.actions, theme, label, command, kind=kind)
            button.pack(side="right" if key == "delete" else "left", padx=(0, 6))
            self.buttons[key] = button
        self.sum_label = tk.Label(self.actions, text="", bg=theme["CARD"],
                                  fg=theme["MUTED"], font=theme.font(9))
        self.sum_label.pack(side="right", padx=10)

        self.clear()

    # --------------------------------------------------------------- state --

    def clear(self) -> None:
        self.receipt = None
        self.title.configure(text="No receipt selected")
        self.status_pill.set("")
        self.meta.configure(text="Choose a receipt on the left, or add one.")
        for child in self.flags.winfo_children():
            child.destroy()
        self.scroll.clear()
        self.sum_label.configure(text="")
        for button in self.buttons.values():
            button.configure(state="disabled")

    def load(self, receipt_id: int) -> None:
        try:
            self.receipt = store.get_receipt(receipt_id)
        except store.StoreError as exc:
            # The receipt can vanish underneath the pane -- deleted in another
            # copy of the app, or a database replaced on disk.
            self.clear()
            self.page.refresh()
            self.win.set_activity(str(exc))
            return
        self.page.selected = receipt_id
        self._categories = store.list_categories()
        self._raw_shown = False
        self._render()

    def _render(self) -> None:
        receipt = self.receipt
        assert receipt is not None
        theme = self.theme
        scanning = receipt["status"] in ("scanning", "uploaded")

        self.title.configure(text=f"Receipt #{receipt['id']}")
        self.status_pill.set(STATUS_LABEL.get(receipt["status"], receipt["status"]),
                             STATUS_TONE.get(receipt["status"], "muted"))
        self.meta.configure(text=self._meta_text(receipt))

        for child in self.flags.winfo_children():
            child.destroy()
        if receipt["error"]:
            self._flag(receipt["error"], bad=True)
        for message in receipt["review_flags"]:
            self._flag(message)

        self.scroll.clear()
        self._item_rows = []
        columns = tk.Frame(self.body, bg=theme["CARD"])
        columns.pack(fill="x", padx=13, pady=(4, 0))
        self._build_image(columns, receipt)
        self._build_fields(columns, receipt)
        self._build_items(self.body, receipt)
        self.scroll.to_top()

        for key, button in self.buttons.items():
            state = "disabled" if scanning else "normal"
            if key == "rescan" and not receipt["image_path"]:
                state = "disabled"
            if key == "raw" and not (receipt["raw_response"] or receipt["raw_text"]):
                state = "disabled"
            button.configure(state=state)
        self._update_sum()

    def _meta_text(self, receipt: dict) -> str:
        bits = []
        if receipt["engine"]:
            bits.append(f"{receipt['engine']}"
                        + (f" · {receipt['model']}" if receipt["model"] else ""))
        if receipt["confidence"] is not None:
            bits.append(f"confidence {receipt['confidence']:.2f}")
        if receipt["extract_ms"]:
            bits.append(f"{receipt['extract_ms'] / 1000:.1f}s")
        if receipt["cost_usd"]:
            bits.append(f"${receipt['cost_usd']:.4f}")
        if receipt["input_tokens"]:
            bits.append(f"{receipt['input_tokens']}+{receipt['output_tokens']} tokens")
        return "  ·  ".join(bits) or "not scanned"

    def _flag(self, message: str, bad: bool = False) -> None:
        theme = self.theme
        row = tk.Frame(self.flags, bg=theme["CARD"])
        row.pack(fill="x", pady=2)
        tk.Label(row, text="!", bg=theme["BAD"] if bad else theme["WARN"],
                 fg="#ffffff" if bad else "#1a1a19", font=theme.font(9, "bold"),
                 width=2).pack(side="left", padx=(0, 8), ipady=1)
        tk.Label(row, text=message, bg=theme["CARD"], fg=theme["FG"],
                 font=theme.font(9), anchor="w", justify="left",
                 wraplength=theme.px(620)).pack(side="left", fill="x", expand=True)

    # -------------------------------------------------------------- pieces --

    def _build_image(self, parent: tk.Frame, receipt: dict) -> None:
        theme = self.theme
        holder = tk.Frame(parent, bg=theme["CARD"])
        holder.pack(side="left", anchor="n", padx=(0, 16))
        path = store.image_path(receipt["id"])
        if path is None:
            tk.Label(holder, text="No image\n(entered by hand)", bg=theme["CARD_ALT"],
                     fg=theme["DIM"], font=theme.font(9), width=24, height=8,
                     justify="center").pack()
            return
        try:
            from PIL import Image, ImageTk

            with Image.open(path) as image:
                image = image.copy()
            image.thumbnail((theme.px(IMAGE_MAX[0]), theme.px(IMAGE_MAX[1])),
                            Image.LANCZOS)
            self._photo = ImageTk.PhotoImage(image)
        except Exception as exc:
            log.warning("Could not show the image for receipt %s: %s", receipt["id"], exc)
            tk.Label(holder, text="Image could not be shown", bg=theme["CARD_ALT"],
                     fg=theme["BAD"], font=theme.font(9)).pack()
            return
        label = tk.Label(holder, image=self._photo, bg=theme["CARD"], cursor="hand2",
                         highlightthickness=1, highlightbackground=theme["GRID"])
        label.pack()
        label.bind("<Button-1>", lambda _e: self.open_image())
        tk.Label(holder, text="click to open full size", bg=theme["CARD"],
                 fg=theme["DIM"], font=theme.font(8)).pack(pady=(3, 0))

    def _build_fields(self, parent: tk.Frame, receipt: dict) -> None:
        theme = self.theme
        grid = tk.Frame(parent, bg=theme["CARD"])
        grid.pack(side="left", fill="both", expand=True)
        self.vars: dict[str, tk.Variable] = {}

        def text_field(row: int, column: int, key: str, label: str, value: str,
                       width: int = 18) -> None:
            cell = tk.Frame(grid, bg=theme["CARD"])
            cell.grid(row=row, column=column, sticky="ew", padx=(0, 12), pady=3)
            field_label(cell, theme, label).pack(fill="x")
            variable = tk.StringVar(value=value or "")
            box = entry(cell, theme, width=width, textvariable=variable)
            box.pack(fill="x")
            if key in ("subtotal", "tax", "tip", "total"):
                box.bind("<KeyRelease>", lambda _e: self._update_sum())
            self.vars[key] = variable

        text_field(0, 0, "merchant", "Merchant", receipt["merchant"] or "", 22)
        text_field(0, 1, "purchased_at", "Date (YYYY-MM-DD)", receipt["purchased_at"] or "")
        text_field(1, 0, "payment_method", "Payment", receipt["payment_method"] or "", 22)
        text_field(1, 1, "currency", "Currency", receipt["currency"] or "USD", 8)
        text_field(2, 0, "subtotal", "Subtotal", from_cents(receipt["subtotal_cents"]), 12)
        text_field(2, 1, "tax", "Tax", from_cents(receipt["tax_cents"]), 12)
        text_field(3, 0, "tip", "Tip", from_cents(receipt["tip_cents"]), 12)
        text_field(3, 1, "total", "Total", from_cents(receipt["total_cents"]), 12)

        cell = tk.Frame(grid, bg=theme["CARD"])
        cell.grid(row=4, column=0, columnspan=2, sticky="ew", pady=3)
        field_label(cell, theme, "Category for the whole receipt").pack(fill="x")
        self.category_box = self._category_combo(cell, receipt["category_id"])
        self.category_box.pack(fill="x")

        cell = tk.Frame(grid, bg=theme["CARD"])
        cell.grid(row=5, column=0, columnspan=2, sticky="ew", pady=3)
        field_label(cell, theme, "Notes").pack(fill="x")
        self.notes = tk.Text(cell, height=2, bg=theme["ENTRY"], fg=theme["FG"],
                             insertbackground=theme["FG"], relief="flat",
                             highlightthickness=1, highlightbackground=theme["AXIS"],
                             font=theme.font(9), wrap="word")
        self.notes.insert("1.0", receipt["notes"] or "")
        self.notes.pack(fill="x")

        grid.columnconfigure(0, weight=1)
        grid.columnconfigure(1, weight=1)

    def _category_combo(self, parent: tk.Misc, category_id: int | None) -> ttk.Combobox:
        names = ["—"] + [category["name"] for category in self._categories]
        current = "—"
        for category in self._categories:
            if category["id"] == category_id:
                current = category["name"]
        box = ttk.Combobox(parent, values=names, state="readonly", width=18)
        box.set(current)
        return box

    def _category_id(self, box: ttk.Combobox) -> int | None:
        chosen = box.get()
        for category in self._categories:
            if category["name"] == chosen:
                return category["id"]
        return None

    def _build_items(self, parent: tk.Frame, receipt: dict) -> None:
        theme = self.theme
        block = tk.Frame(parent, bg=theme["CARD"])
        block.pack(fill="both", expand=True, padx=13, pady=(12, 8))

        header = tk.Frame(block, bg=theme["CARD"])
        header.pack(fill="x")
        tk.Label(header, text="Line items", bg=theme["CARD"], fg=theme["FG"],
                 font=theme.font(10, "bold")).pack(side="left")
        Button(header, theme, "＋ Add line",
               lambda: self._add_item_row(self.items_holder, {})).pack(side="right")

        titles = tk.Frame(block, bg=theme["CARD"])
        titles.pack(fill="x", pady=(6, 2))
        for text, width in (("Item", 30), ("Qty", 5), ("Amount", 9), ("Category", 18)):
            tk.Label(titles, text=text, bg=theme["CARD"], fg=theme["MUTED"],
                     font=theme.font(8, "bold"), width=width, anchor="w").pack(side="left")

        self.items_holder = tk.Frame(block, bg=theme["CARD"])
        self.items_holder.pack(fill="both", expand=True)
        for item in receipt["items"]:
            self._add_item_row(self.items_holder, item)

    def _add_item_row(self, parent: tk.Frame, item: dict) -> None:
        theme = self.theme
        row = tk.Frame(parent, bg=theme["CARD"])
        row.pack(fill="x", pady=1)
        line = tk.Frame(row, bg=theme["CARD"])
        line.pack(fill="x")

        description = tk.StringVar(value=item.get("description", ""))
        quantity = tk.StringVar(
            value="" if item.get("quantity") in (None, "") else _trim_number(item["quantity"]))
        amount = tk.StringVar(value=from_cents(item.get("amount_cents")))

        entry(line, theme, width=30, textvariable=description).pack(side="left", padx=(0, 3))
        entry(line, theme, width=5, textvariable=quantity).pack(side="left", padx=(0, 3))
        amount_box = entry(line, theme, width=9, textvariable=amount)
        amount_box.pack(side="left", padx=(0, 3))
        amount_box.bind("<KeyRelease>", lambda _e: self._update_sum())
        combo = self._category_combo(line, item.get("category_id"))
        combo.configure(width=18)
        combo.pack(side="left", padx=(0, 3))

        source = item.get("category_source") or "manual"
        tk.Label(line, text={"rule": "rule", "model": "model", "merchant": "shop",
                             "manual": "you", "default": "—"}.get(source, source),
                 bg=theme["CARD"], fg=theme["DIM"], font=theme.font(8),
                 width=5).pack(side="left")

        # The printed name stays in the editable field because it is what the
        # receipt actually says. The expansion goes underneath it, where it
        # answers "what *is* EQJELLUBE8OZ?" without overwriting the evidence.
        # "from barcode" is not decoration. The name came from a catalogue,
        # keyed on a number the OCR read off a photograph, and one misread digit
        # produces a confidently wrong product -- a toothpaste on the test
        # receipt came back as an Audi cylinder head gasket. Saying where the
        # name came from lets a reviewer weigh it instead of trusting it.
        readable = (item.get("raw_description") or "").strip()
        if readable:
            tk.Label(row, text=f"from barcode: {readable}", bg=theme["CARD"],
                     fg=theme["DIM"], font=theme.font(8),
                     anchor="w").pack(fill="x", padx=(6, 0))

        record = {
            "frame": row, "description": description, "quantity": quantity,
            "amount": amount, "combo": combo, "source": source,
            "unit_price_cents": item.get("unit_price_cents"),
            "sku": item.get("sku"), "raw_description": item.get("raw_description"),
            "is_discount": bool(item.get("is_discount")),
            "taxable": item.get("taxable"),
            "original_category_id": item.get("category_id"),
        }

        def remove() -> None:
            row.destroy()
            self._item_rows.remove(record)
            self._update_sum()

        Button(row, theme, "✕", remove).pack(side="left")
        self._item_rows.append(record)
        self._update_sum()

    # ------------------------------------------------------------- actions --

    def _update_sum(self) -> None:
        total_cents = 0
        for record in self._item_rows:
            total_cents += to_cents(record["amount"].get()) or 0
        header_total = to_cents(self.vars["total"].get()) if getattr(self, "vars", None) else None
        text = f"Lines: {from_cents(total_cents)}"
        subtotal = to_cents(self.vars.get("subtotal").get()) if getattr(self, "vars", None) else None
        reference = subtotal if subtotal is not None else header_total
        if reference is not None:
            delta = total_cents - reference
            if abs(delta) > 5:
                text += f"   (off by {from_cents(abs(delta))})"
        self.sum_label.configure(text=text)

    def _collect(self) -> store.ReceiptEdit:
        items = []
        for record in self._item_rows:
            chosen_id = self._category_id(record["combo"])
            # Only a category the reviewer actually changed becomes "manual";
            # otherwise a rule backfill would be locked out of every line that
            # was merely looked at.
            source = record["source"]
            if chosen_id != record["original_category_id"]:
                source = "manual"
            quantity_text = record["quantity"].get().strip()
            try:
                quantity = float(quantity_text) if quantity_text else None
            except ValueError:
                quantity = None
            items.append(store.ItemEdit(
                description=record["description"].get(),
                quantity=quantity,
                unit_price_cents=record["unit_price_cents"],
                amount_cents=to_cents(record["amount"].get()),
                category_id=chosen_id,
                category_source=source,
                raw_description=record["raw_description"],
                sku=record["sku"],
                is_discount=record["is_discount"],
                taxable=record["taxable"],
            ))
        return store.ReceiptEdit(
            merchant=self.vars["merchant"].get(),
            purchased_at=self.vars["purchased_at"].get(),
            currency=self.vars["currency"].get() or "USD",
            subtotal_cents=to_cents(self.vars["subtotal"].get()),
            tax_cents=to_cents(self.vars["tax"].get()),
            tip_cents=to_cents(self.vars["tip"].get()),
            total_cents=to_cents(self.vars["total"].get()),
            payment_method=self.vars["payment_method"].get() or None,
            category_id=self._category_id(self.category_box),
            notes=self.notes.get("1.0", "end").strip() or None,
            items=items,
        )

    def _save(self, confirm: bool) -> None:
        if self.receipt is None:
            return
        receipt_id = self.receipt["id"]
        try:
            store.save_receipt(receipt_id, self._collect(), confirm=confirm)
        except store.StoreError as exc:
            messagebox.showerror("Bookkeeping", str(exc), parent=self.frame)
            return
        self.page.after_change(select=receipt_id)
        self.win.set_activity(
            f"Receipt #{receipt_id} {'confirmed' if confirm else 'saved as a draft'}")

    def save_draft(self) -> None:
        self._save(confirm=False)

    def save_and_confirm(self) -> None:
        if self.receipt is None:
            return
        edit = self._collect()
        if edit.total_cents is None or not (edit.purchased_at or "").strip():
            messagebox.showwarning(
                "Bookkeeping",
                "A confirmed receipt needs at least a date and a total.\n\n"
                "Fill those in (they are on the right of the image) and try again.",
                parent=self.frame)
            return
        self._save(confirm=True)

    def rescan(self) -> None:
        if self.receipt is None:
            return
        receipt_id = self.receipt["id"]
        if not pipeline.submit_scan(receipt_id):
            messagebox.showinfo("Bookkeeping", "That receipt is already being read.",
                                parent=self.frame)
            return
        self.win.set_activity(f"Re-scanning receipt #{receipt_id}…")
        self.page.refresh(select=receipt_id)
        self.win._schedule_poll(immediate=True)

    def delete(self) -> None:
        if self.receipt is None:
            return
        receipt_id = self.receipt["id"]
        if not messagebox.askyesno(
            "Bookkeeping",
            f"Delete receipt #{receipt_id} and its image?\n\nThis cannot be undone.",
            parent=self.frame,
        ):
            return
        try:
            store.delete_receipt(receipt_id)
        except store.StoreError as exc:
            messagebox.showerror("Bookkeeping", str(exc), parent=self.frame)
            return
        self.clear()
        self.page.selected = None
        self.page.after_change()
        self.win.set_activity(f"Receipt #{receipt_id} deleted")

    def open_image(self) -> None:
        if self.receipt is None:
            return
        path = store.image_path(self.receipt["id"])
        if path is None:
            return
        try:
            if sys.platform == "win32":
                os.startfile(str(path))  # noqa: S606 - the user's own image
        except OSError as exc:
            messagebox.showerror("Bookkeeping", f"Could not open the image:\n{exc}",
                                 parent=self.frame)

    def toggle_raw(self) -> None:
        """Show exactly what the engine returned -- the audit trail for a reading."""
        if self.receipt is None:
            return
        text = self.receipt["raw_response"] or self.receipt["raw_text"] or ""
        window = tk.Toplevel(self.frame)
        window.title(f"Engine output — receipt #{self.receipt['id']}")
        window.configure(bg=self.theme["CARD"])
        window.geometry("640x520")
        box = tk.Text(window, bg=self.theme["ENTRY"], fg=self.theme["FG"],
                      font=self.theme.mono(9), relief="flat", wrap="none",
                      insertbackground=self.theme["FG"])
        scroll_y = ttk.Scrollbar(window, orient="vertical", command=box.yview)
        box.configure(yscrollcommand=scroll_y.set)
        box.insert("1.0", text)
        box.configure(state="disabled")
        scroll_y.pack(side="right", fill="y")
        box.pack(side="left", fill="both", expand=True, padx=1, pady=1)


def _trim_number(value: float) -> str:
    """2.0 -> '2', 1.5 -> '1.5'. Quantities look wrong with a trailing .0."""
    text = f"{value:.3f}".rstrip("0").rstrip(".")
    return text or "0"
