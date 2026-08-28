"""Tests that build the real window and drive it.

Tkinter can be exercised without ``mainloop()``: create the widgets, then call
``update()`` to process the event queue. That is enough to catch the mistakes
that matter here -- a page that fails to build, a widget referring to a colour or
a field that no longer exists, a save path that does not reach the database, a
chart that divides by zero on an empty range.

These are not pixel tests. They answer "does the interface hold together and do
what it says", which is the part that silently breaks when the logic underneath
is refactored.
"""

from __future__ import annotations

import sys
import tkinter as tk
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.money import to_cents  # noqa: E402
from app.store import ItemEdit, ReceiptEdit  # noqa: E402

# Creating and destroying a Tk interpreter over and over in one process
# eventually breaks Tcl itself ("invalid command name tcl_findLibrary"), so the
# suite creates exactly ONE root for the whole session and gives each test its
# own Toplevel to build the window in.
@pytest.fixture(scope="session")
def tk_root():
    try:
        root = tk.Tk()
    except tk.TclError:  # pragma: no cover - no display
        pytest.skip("no display available")
    root.withdraw()
    yield root
    try:
        root.destroy()
    except tk.TclError:
        pass


@pytest.fixture()
def window(tk_root, books, monkeypatch):
    """A real MainWindow over throwaway books, never entering mainloop()."""
    from app.ui.window import MainWindow

    top = tk.Toplevel(tk_root)
    top.geometry("1280x840+30+30")
    # Mapped but fully transparent: an unmapped window never gets real
    # geometry, so canvases would report a width of 1 and the charts -- which
    # deliberately wait for layout -- would never draw.
    try:
        top.attributes("-alpha", 0.0)
    except tk.TclError:  # pragma: no cover - platform without alpha
        top.withdraw()
    win = MainWindow(top)
    top.update()
    try:
        yield win
    finally:
        # Cancel the poll job before tearing the widgets down, or Tk complains
        # about a callback firing after the widget is gone.
        if win._poll_job:
            try:
                top.after_cancel(win._poll_job)
            except tk.TclError:
                pass
        top.destroy()
        tk_root.update()


def pump(window, times: int = 3) -> None:
    for _ in range(times):
        window.root.update()


def pump_until(window, ready, timeout: float = 3.0) -> bool:
    """Run the event loop until `ready()` is true. Needed for after() work.

    time.sleep() does not run Tk's event loop, so anything scheduled with
    after() -- the debounced chart redraw, the scan poll -- only happens if the
    loop is pumped.
    """
    import time

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        window.root.update()
        if ready():
            return True
        time.sleep(0.02)
    return False


# ------------------------------------------------------------------ chrome


def test_the_window_opens_with_a_title_and_a_status_bar(window):
    assert "Bookkeeping" in window.root.title()
    assert window.status_left.cget("text").startswith("0 receipts")


def test_every_page_builds_and_can_be_shown(window):
    for key, _label in [("receipts", ""), ("reports", ""), ("rules", ""),
                        ("settings", "")]:
        window.show(key)
        pump(window)
        assert window.current == key
        assert window.pages[key].frame.winfo_exists()


def test_the_engine_pill_names_the_engines_that_are_ready(window):
    """With no key and no Tesseract, Windows OCR alone should carry the app.

    This test used to assert the pill read "no engine", which was true and was
    the whole problem: a fresh copy could not read a receipt at all. The offline
    Windows engine needs no key and no install, so on any Windows machine with a
    language pack the pill now names it.
    """
    window.update_engine_pill()
    text = window.engine_pill.cget("text")
    assert "no engine" not in text
    assert "windows" in text


def test_switching_theme_rebuilds_the_window_and_is_remembered(window, books):
    start = window.theme.name
    window.cycle_theme()
    pump(window)
    assert window.theme.name != start
    assert books["settings"].get("theme") == window.theme.name
    # The rebuilt tree must still work.
    window.show("reports")
    pump(window)
    assert window.pages["reports"].frame.winfo_exists()


def test_the_status_bar_counts_what_is_in_the_books(window, books):
    books["store"].create_manual()
    window.update_status()
    text = window.status_left.cget("text")
    assert "1 receipt" in text and "1 to review" in text


# ---------------------------------------------------------------- receipts


def test_the_empty_state_is_shown_until_there_is_a_receipt(window):
    window.show("receipts")
    pump(window)
    page = window.pages["receipts"]
    # winfo_manager() rather than winfo_ismapped(): the test root is withdrawn,
    # so nothing in it reports as mapped even when it is placed.
    assert page.empty.winfo_manager() == "place"
    window.add_manual()
    pump(window)
    assert page.empty.winfo_manager() == ""
    assert len(page.tree.get_children()) == 1


def test_adding_by_hand_selects_the_new_receipt_for_review(window):
    window.add_manual()
    pump(window)
    page = window.pages["receipts"]
    assert page.selected is not None
    assert f"#{page.selected}" in page.review.title.cget("text")
    # A blank receipt has today's date filled in and nothing else.
    assert page.review.vars["merchant"].get() == ""
    assert page.review.vars["purchased_at"].get()


def test_editing_the_pane_and_saving_reaches_the_database(window, books):
    window.add_manual()
    pump(window)
    page = window.pages["receipts"]
    review = page.review
    receipt_id = page.selected

    review.vars["merchant"].set("Corner Store")
    review.vars["purchased_at"].set("2026-08-02")
    review.vars["total"].set("12.50")
    review.vars["subtotal"].set("12.50")
    review._add_item_row(review.items_holder, {})
    row = review._item_rows[-1]
    row["description"].set("SANDWICH")
    row["amount"].set("12.50")
    row["combo"].set("Dining")
    pump(window)

    review.save_and_confirm()
    pump(window)

    stored = books["store"].get_receipt(receipt_id)
    assert stored["status"] == "confirmed"
    assert stored["merchant"] == "Corner Store"
    assert stored["total_cents"] == to_cents("12.50")
    assert [(i["description"], i["amount_cents"], i["category_name"])
            for i in stored["items"]] == [("SANDWICH", 1250, "Dining")]
    assert stored["review_flags"] == []


def test_confirming_without_a_date_or_total_is_refused_with_a_dialog(window, books,
                                                                    monkeypatch):
    warned: list[str] = []
    monkeypatch.setattr("app.ui.receipts.messagebox.showwarning",
                        lambda *args, **kwargs: warned.append(args[1]))
    window.add_manual()
    pump(window)
    review = window.pages["receipts"].review
    review.vars["purchased_at"].set("")
    review.save_and_confirm()
    pump(window)
    assert warned and "date and a total" in warned[0]
    assert books["store"].status_counts() == {"needs_review": 1}


def test_the_running_line_total_is_shown_and_flags_a_mismatch(window):
    window.add_manual()
    pump(window)
    review = window.pages["receipts"].review
    review.vars["subtotal"].set("10.00")
    review._add_item_row(review.items_holder, {})
    review._item_rows[-1]["amount"].set("4.00")
    review._update_sum()
    text = review.sum_label.cget("text")
    assert "4.00" in text and "off by 6.00" in text


def test_a_line_can_be_removed_from_the_pane(window):
    window.add_manual()
    pump(window)
    review = window.pages["receipts"].review
    review._add_item_row(review.items_holder, {"description": "A", "amount_cents": 100})
    before = len(review._item_rows)
    # The remove button is the last child of the row frame.
    row = review._item_rows[-1]["frame"]
    row.winfo_children()[-1].invoke()
    pump(window)
    assert len(review._item_rows) == before - 1


def test_deleting_from_the_pane_removes_the_receipt(window, books, monkeypatch):
    monkeypatch.setattr("app.ui.receipts.messagebox.askyesno",
                        lambda *args, **kwargs: True)
    window.add_manual()
    pump(window)
    window.pages["receipts"].review.delete()
    pump(window)
    assert books["store"].status_counts() == {}
    assert window.pages["receipts"].review.receipt is None


def test_the_list_filters_by_status(window, books):
    store = books["store"]
    first = store.create_manual()
    store.save_receipt(first, ReceiptEdit(merchant="Kept", purchased_at="2026-08-01",
                                          total_cents=100), confirm=True)
    store.create_manual()
    page = window.pages["receipts"] if "receipts" in window.pages else None
    window.show("receipts")
    page = window.pages["receipts"]
    page.status_choice.set("Confirmed")
    page.refresh()
    pump(window)
    assert len(page.tree.get_children()) == 1
    page.status_choice.set("All")
    page.refresh()
    assert len(page.tree.get_children()) == 2


def test_searching_narrows_the_list(window, books):
    store = books["store"]
    for name in ("Walmart", "Costco"):
        receipt_id = store.create_manual()
        store.save_receipt(receipt_id, ReceiptEdit(merchant=name,
                                                   purchased_at="2026-08-01",
                                                   total_cents=100))
    window.show("receipts")
    page = window.pages["receipts"]
    page.search.insert(0, "Costco")
    page.refresh()
    pump(window)
    assert len(page.tree.get_children()) == 1


def test_a_receipt_with_an_image_shows_it_in_the_pane(window, books,
                                                      sample_receipt_png):
    path, _ = sample_receipt_png
    receipt_id = books["store"].create_from_image(path.read_bytes(), path.name)
    window.show("receipts")
    page = window.pages["receipts"]
    page.refresh(select=receipt_id)
    pump(window)
    # The photo reference is what keeps the image on screen; losing it blanks it.
    assert page.review._photo is not None
    assert page.review._photo.width() > 100


def test_review_flags_are_rendered_for_a_questionable_receipt(window, books):
    store = books["store"]
    receipt_id = store.create_manual()
    store.save_receipt(receipt_id, ReceiptEdit(
        purchased_at="2026-08-01", subtotal_cents=5000, total_cents=5000,
        items=[ItemEdit(description="THING", amount_cents=100)]))
    window.show("receipts")
    window.pages["receipts"].refresh(select=receipt_id)
    pump(window)
    flags = window.pages["receipts"].review.flags.winfo_children()
    assert flags, "the arithmetic complaint must be visible in the pane"


# ----------------------------------------------------------------- reports


def test_the_reports_page_survives_having_no_data(window):
    window.show("reports")
    page = window.pages["reports"]
    # "Nothing to report" text, drawn without dividing by zero.
    assert pump_until(window, lambda: page.category_canvas.find_all())
    assert page.data["totals"]["receipts"] == 0


def test_the_reports_page_charts_confirmed_spending(window, books):
    store = books["store"]
    ids = {c["name"]: c["id"] for c in store.list_categories()}
    receipt_id = store.create_manual()
    store.save_receipt(receipt_id, ReceiptEdit(
        merchant="Walmart", purchased_at="2026-08-02", subtotal_cents=1000,
        tax_cents=60, total_cents=1060,
        items=[ItemEdit(description="MILK", amount_cents=1000,
                        category_id=ids["Groceries"])],
    ), confirm=True)

    window.show("reports")
    page = window.pages["reports"]
    page.set_range(None)          # all time, so the fixed date is included
    assert pump_until(window, lambda: page.month_canvas.find_all())

    assert page.data["totals"]["receipts"] == 1
    assert page.tile_labels[0][1].cget("text") == "$10.60"
    categories = [b["category"] for b in page.data["by_category"]]
    assert "Groceries" in categories and "Tax & unitemised" in categories
    assert page.category_canvas.find_all(), "bars were drawn"
    assert page.merchant_tree.get_children()
    # A bar must span a useful part of the canvas, not the 40px stub the
    # draw-before-layout bug produced.
    widest = max(page.category_canvas.bbox(item)[2]
                 for item in page.category_canvas.find_all())
    assert widest > page.category_canvas.winfo_width() * 0.5


def test_the_chart_redraws_at_a_new_size(window, books):
    window.show("reports")
    page = window.pages["reports"]
    assert pump_until(window, lambda: page.category_canvas.find_all())
    window.root.geometry("1000x700+30+30")
    assert pump_until(window, lambda: page.category_canvas.winfo_width() < 1200)
    assert page.category_canvas.find_all(), "the chart survives a resize"


# ------------------------------------------------------- categories & rules


def test_the_rules_page_lists_the_seeded_data(window):
    window.show("rules")
    pump(window)
    page = window.pages["rules"]
    assert len(page.category_tree.get_children()) == 15
    assert len(page.rule_tree.get_children()) > 20


def test_a_category_can_be_added_from_the_page(window, books):
    window.show("rules")
    page = window.pages["rules"]
    page.category_name.set("Hobbies")
    page.add_category()
    pump(window)
    assert "Hobbies" in {c["name"] for c in books["store"].list_categories()}
    assert page.category_name.get() == ""


def test_a_rule_can_be_added_and_backfilled_from_the_page(window, books):
    store = books["store"]
    receipt_id = store.create_manual()
    store.save_receipt(receipt_id, ReceiptEdit(
        merchant="Corner Store", purchased_at="2026-08-01", total_cents=500,
        items=[ItemEdit(description="KOMBUCHA 500ML", amount_cents=500,
                        category_source="default")]))

    window.show("rules")
    page = window.pages["rules"]
    page.rule_pattern.set("KOMBUCHA")
    page.rule_category.set("Groceries")
    page.add_rule()
    page.apply_rules()
    pump(window)

    item = store.get_receipt(receipt_id)["items"][0]
    assert item["category_name"] == "Groceries"
    assert "recategorised" in page.apply_result.cget("text")


def test_deleting_a_rule_from_the_page(window, books, monkeypatch):
    window.show("rules")
    page = window.pages["rules"]
    first = page.rule_tree.get_children()[0]
    page.rule_tree.selection_set(first)
    page.delete_rule()
    pump(window)
    assert first not in page.rule_tree.get_children()


# ---------------------------------------------------------------- settings


def test_the_settings_page_shows_the_stored_state(window, books):
    books["settings"].save({"anthropic_api_key": "sk-ant-abcdefgh1234",
                            "model": "claude-sonnet-5"})
    window.show("settings")
    pump(window)
    page = window.pages["settings"]
    assert page.model.get() == "claude-sonnet-5"
    assert page.api_key.get() == "", "the key box starts empty"
    assert "****1234" in page.key_hint.cget("text")


def test_saving_settings_from_the_page_persists_them(window, books):
    window.show("settings")
    page = window.pages["settings"]
    page.effort.set("high")
    page.api_key.set("sk-ant-typed-9876")
    page.auto_confirm.set(True)
    page.save()
    pump(window)
    settings = books["settings"]
    assert settings.get("effort") == "high"
    assert settings.get("anthropic_api_key") == "sk-ant-typed-9876"
    assert settings.get("auto_confirm_clean") == "1"
    assert page.api_key.get() == "", "the box is cleared after saving"


def test_leaving_the_key_box_empty_keeps_the_stored_key(window, books):
    books["settings"].save({"anthropic_api_key": "sk-ant-original"})
    window.show("settings")
    page = window.pages["settings"]
    page.effort.set("low")
    page.save()
    assert books["settings"].get("anthropic_api_key") == "sk-ant-original"


def test_clearing_the_key_from_the_page(window, books, monkeypatch):
    monkeypatch.setattr("app.ui.settings_page.messagebox.askyesno",
                        lambda *args, **kwargs: True)
    books["settings"].save({"anthropic_api_key": "sk-ant-original"})
    window.show("settings")
    window.pages["settings"].clear_key()
    assert books["settings"].get("anthropic_api_key") == ""


def test_the_settings_page_reports_engine_availability(window):
    window.show("settings")
    pump(window)
    rows = window.pages["settings"].status_body.winfo_children()
    text = " ".join(
        child.cget("text") for row in rows for child in row.winfo_children()
    )
    assert "claude" in text and "API key" in text
    assert "tesseract" in text
