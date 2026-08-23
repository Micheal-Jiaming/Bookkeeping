"""The categories and rules page.

Two panels side by side, because they are two halves of one idea: categories are
the buckets the reports are built from, and rules are the standing instructions
that put lines into them without anyone having to decide twice.

The order rules are applied in is explained on the page itself, not only in the
documentation -- a user who cannot see why "everything at Walmart is Groceries"
did not override the model's "Dining" would reasonably think it was broken.
"""

from __future__ import annotations

import tkinter as tk
from tkinter import messagebox, ttk

from .. import store
from .theme import Button, Card, entry, field_label

FIELD_CHOICES = (("Item name contains", "description"), ("Merchant contains", "merchant"))
MATCH_CHOICES = (("plain text", "contains"), ("regular expression", "regex"))


class RulesPage:
    def __init__(self, parent: tk.Misc, win) -> None:
        self.win = win
        self.theme = win.theme
        self.frame = tk.Frame(parent, bg=self.theme["BG"])
        self.categories: list[dict] = []
        self._build()

    def _build(self) -> None:
        theme = self.theme
        columns = tk.Frame(self.frame, bg=theme["BG"])
        columns.pack(fill="both", expand=True, padx=12, pady=12)

        # ---------------------------------------------------- categories --
        left = Card(columns, theme)
        left.pack(side="left", fill="both", expand=True, padx=(0, 10))
        tk.Label(left, text="Categories", bg=theme["CARD"], fg=theme["FG"],
                 font=theme.font(11, "bold"), anchor="w").pack(fill="x", padx=14,
                                                               pady=(12, 2))
        tk.Label(left, text="The buckets the reports are built from. These names are "
                            "also the choices offered to the recognition model.",
                 bg=theme["CARD"], fg=theme["MUTED"], font=theme.font(9),
                 anchor="w", justify="left",
                 wraplength=theme.px(380)).pack(fill="x", padx=14)

        self.category_tree = ttk.Treeview(left, columns=("name", "lines"),
                                          show="headings", selectmode="browse")
        self.category_tree.heading("name", text="Name")
        self.category_tree.heading("lines", text="Lines")
        self.category_tree.column("name", width=200, anchor="w")
        self.category_tree.column("lines", width=60, anchor="e")
        self.category_tree.pack(fill="both", expand=True, padx=8, pady=8)

        form = tk.Frame(left, bg=theme["CARD"])
        form.pack(fill="x", padx=14, pady=(0, 12))
        field_label(form, theme, "New category").pack(anchor="w")
        row = tk.Frame(form, bg=theme["CARD"])
        row.pack(fill="x")
        self.category_name = tk.StringVar()
        entry(row, theme, width=22, textvariable=self.category_name).pack(side="left")
        Button(row, theme, "Add", self.add_category,
               kind="primary").pack(side="left", padx=6)
        Button(row, theme, "Delete selected", self.delete_category,
               kind="danger").pack(side="right")

        # --------------------------------------------------------- rules --
        right = Card(columns, theme)
        right.pack(side="left", fill="both", expand=True)
        tk.Label(right, text="Keyword rules", bg=theme["CARD"], fg=theme["FG"],
                 font=theme.font(11, "bold"), anchor="w").pack(fill="x", padx=14,
                                                                pady=(12, 2))
        tk.Label(
            right,
            text="Order of precedence: what you set by hand wins, then item-name "
                 "rules, then the model's own suggestion, then merchant rules, "
                 "then Uncategorized. Merchant rules sit below the model on "
                 "purpose — “everything from this shop is Groceries” is a safety "
                 "net, not a better answer than a look at the item. Lower "
                 "priority numbers run first.",
            bg=theme["CARD"], fg=theme["MUTED"], font=theme.font(9), anchor="w",
            justify="left", wraplength=theme.px(420)).pack(fill="x", padx=14)

        self.rule_tree = ttk.Treeview(
            right, columns=("priority", "field", "pattern", "category"),
            show="headings", selectmode="browse")
        for key, label, width, anchor in (("priority", "Pri", 40, "e"),
                                          ("field", "Matches", 80, "w"),
                                          ("pattern", "Pattern", 170, "w"),
                                          ("category", "Category", 120, "w")):
            self.rule_tree.heading(key, text=label)
            self.rule_tree.column(key, width=width, anchor=anchor)
        self.rule_tree.pack(fill="both", expand=True, padx=8, pady=8)

        form = tk.Frame(right, bg=theme["CARD"])
        form.pack(fill="x", padx=14, pady=(0, 12))
        field_label(form, theme, "New rule").pack(anchor="w")
        row = tk.Frame(form, bg=theme["CARD"])
        row.pack(fill="x", pady=(0, 6))
        self.rule_field = tk.StringVar(value=FIELD_CHOICES[0][0])
        ttk.Combobox(row, textvariable=self.rule_field, state="readonly", width=18,
                     values=[label for label, _ in FIELD_CHOICES]).pack(side="left")
        self.rule_pattern = tk.StringVar()
        entry(row, theme, width=18, textvariable=self.rule_pattern).pack(side="left",
                                                                        padx=5)
        self.rule_category = tk.StringVar()
        self.rule_category_box = ttk.Combobox(row, textvariable=self.rule_category,
                                              state="readonly", width=16)
        self.rule_category_box.pack(side="left")

        row2 = tk.Frame(form, bg=theme["CARD"])
        row2.pack(fill="x")
        self.rule_match = tk.StringVar(value=MATCH_CHOICES[0][0])
        ttk.Combobox(row2, textvariable=self.rule_match, state="readonly", width=18,
                     values=[label for label, _ in MATCH_CHOICES]).pack(side="left")
        field_label(row2, theme, "priority").pack(side="left", padx=(8, 3))
        self.rule_priority = tk.StringVar(value="80")
        entry(row2, theme, width=5, textvariable=self.rule_priority).pack(side="left")
        Button(row2, theme, "Add rule", self.add_rule,
               kind="primary").pack(side="left", padx=6)
        Button(row2, theme, "Delete selected", self.delete_rule,
               kind="danger").pack(side="right")

        apply_row = tk.Frame(right, bg=theme["CARD"])
        apply_row.pack(fill="x", padx=14, pady=(0, 12))
        Button(apply_row, theme, "Re-apply rules to unconfirmed receipts",
               self.apply_rules).pack(side="left")
        self.apply_result = tk.Label(apply_row, text="", bg=theme["CARD"],
                                     fg=theme["MUTED"], font=theme.font(9))
        self.apply_result.pack(side="left", padx=10)

    # ------------------------------------------------------------ refresh --

    def refresh(self) -> None:
        self.categories = store.list_categories()
        self.category_tree.delete(*self.category_tree.get_children())
        for category in self.categories:
            self.category_tree.insert("", "end", iid=str(category["id"]),
                                      values=(category["name"], category["item_count"]))

        names = [category["name"] for category in self.categories]
        self.rule_category_box.configure(values=names)
        if self.rule_category.get() not in names:
            self.rule_category.set(names[0] if names else "")

        self.rule_tree.delete(*self.rule_tree.get_children())
        for rule in store.list_rules():
            self.rule_tree.insert("", "end", iid=str(rule["id"]), values=(
                rule["priority"],
                "item" if rule["field"] == "description" else "merchant",
                rule["pattern"] + ("  (regex)" if rule["match_type"] == "regex" else ""),
                rule["category_name"],
            ))

    # ------------------------------------------------------------ actions --

    def _category_id(self, name: str) -> int | None:
        for category in self.categories:
            if category["name"] == name:
                return category["id"]
        return None

    def add_category(self) -> None:
        try:
            store.create_category(self.category_name.get())
        except store.StoreError as exc:
            messagebox.showerror("Bookkeeping", str(exc), parent=self.frame)
            return
        self.category_name.set("")
        self.refresh()

    def delete_category(self) -> None:
        chosen = self.category_tree.selection()
        if not chosen:
            messagebox.showinfo("Bookkeeping", "Select a category to delete first.",
                                parent=self.frame)
            return
        name = self.category_tree.item(chosen[0], "values")[0]
        if not messagebox.askyesno(
            "Bookkeeping",
            f"Delete the category “{name}”?\n\n"
            "Any line items using it move to Uncategorized.",
            parent=self.frame,
        ):
            return
        try:
            store.delete_category(int(chosen[0]))
        except store.StoreError as exc:
            messagebox.showerror("Bookkeeping", str(exc), parent=self.frame)
            return
        self.refresh()

    def add_rule(self) -> None:
        category_id = self._category_id(self.rule_category.get())
        if category_id is None:
            messagebox.showinfo("Bookkeeping", "Choose a category for the rule.",
                                parent=self.frame)
            return
        try:
            priority = int(self.rule_priority.get())
        except ValueError:
            messagebox.showerror("Bookkeeping", "Priority must be a whole number.",
                                 parent=self.frame)
            return
        try:
            store.create_rule(
                self.rule_pattern.get(), category_id,
                field_name=dict(FIELD_CHOICES)[self.rule_field.get()],
                match_type=dict(MATCH_CHOICES)[self.rule_match.get()],
                priority=priority,
            )
        except store.StoreError as exc:
            messagebox.showerror("Bookkeeping", str(exc), parent=self.frame)
            return
        self.rule_pattern.set("")
        self.refresh()
        self.apply_result.configure(
            text="Added. Use “Re-apply rules” to backfill existing receipts.")

    def delete_rule(self) -> None:
        chosen = self.rule_tree.selection()
        if not chosen:
            messagebox.showinfo("Bookkeeping", "Select a rule to delete first.",
                                parent=self.frame)
            return
        try:
            store.delete_rule(int(chosen[0]))
        except store.StoreError as exc:
            messagebox.showerror("Bookkeeping", str(exc), parent=self.frame)
            return
        self.refresh()

    def apply_rules(self) -> None:
        examined, changed = store.apply_rules()
        self.apply_result.configure(
            text=f"{changed} of {examined} lines recategorised.")
        self.refresh()
        self.win.update_status()
