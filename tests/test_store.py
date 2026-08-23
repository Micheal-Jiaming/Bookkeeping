"""Tests for the service layer -- everything the app does to the books.

Scans are run synchronously here (``scan_now`` rather than ``submit_scan``) and
the recognition engine is replaced with a stub returning a known reading. That
exercises everything downstream of the model -- schema validation, rule
categorisation, arithmetic checks, storage, reports, CSV -- without an API key,
without a network, and without opening a window.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.extract import (  # noqa: E402
    ExtractedItem, ExtractedReceipt, ExtractionError, ExtractionResult,
)
from app.money import to_cents  # noqa: E402
from app.store import ItemEdit, ReceiptEdit, StoreError  # noqa: E402


class StubEngine:
    """An engine with a scripted answer."""

    def __init__(self, receipt: ExtractedReceipt, name: str = "stub"):
        self.name = name
        self.receipt = receipt
        self.calls = 0

    def available(self):
        return True, "stub"

    def extract(self, image_path, categories):
        self.calls += 1
        return ExtractionResult(
            receipt=self.receipt, engine=self.name, model="stub-1",
            raw_response="{}", input_tokens=1500, output_tokens=600,
            cost_usd=0.0225, elapsed_ms=1234,
        )


class FailingEngine:
    name = "failing"

    def available(self):
        return True, "failing"

    def extract(self, image_path, categories):
        raise ExtractionError("no key configured")


def walmart_reading() -> ExtractedReceipt:
    return ExtractedReceipt(
        merchant="Walmart", merchant_raw="Walmart Store #100",
        purchased_at="2026-07-14", currency="USD", subtotal="64.59", tax="3.87",
        total="68.46", payment_method="VISA ****4471", category="Groceries",
        confidence=0.94,
        items=[
            ExtractedItem(description="GV WHL MILK", readable_name="Great Value Whole Milk",
                          sku="007874203912", amount="3.24", category="Groceries"),
            ExtractedItem(description="BANANAS", amount="1.48", category="Groceries"),
            ExtractedItem(description="MARKETSIDE SALAD", amount="4.98", category="Groceries"),
            ExtractedItem(description="GREAT VALUE EGGS", amount="2.86", category="Groceries"),
            ExtractedItem(description="TIDE PODS 42CT", amount="12.97", category="Household"),
            ExtractedItem(description="PAPER TOWELS 6PK", amount="9.44", category="Household"),
            ExtractedItem(description="COLGATE TOOTHPASTE", amount="3.12",
                          category="Personal Care"),
            ExtractedItem(description="DOG FOOD 16LB", amount="18.62", category="Pets"),
            ExtractedItem(description="HDMI CABLE 6FT", amount="9.88", category="Electronics"),
            ExtractedItem(description="MANAGER COUPON", amount="-2.00", is_discount=True,
                          category="Groceries"),
        ],
    )


@pytest.fixture()
def scanned(books, monkeypatch, sample_receipt_png):
    """A receipt imported from the sample image and read by the stub engine."""
    engine = StubEngine(walmart_reading())
    monkeypatch.setattr(books["pipeline"], "build_engines", lambda settings: [engine])
    path, expected = sample_receipt_png
    receipt_id = books["store"].create_from_image(path.read_bytes(), path.name)
    books["pipeline"].scan_now(receipt_id)
    return {"id": receipt_id, "engine": engine, "expected": expected, **books}


def categories(store) -> dict[str, int]:
    return {c["name"]: c["id"] for c in store.list_categories()}


# ----------------------------------------------------------------- seed data


def test_a_fresh_database_has_categories_and_rules(books):
    store = books["store"]
    names = {c["name"] for c in store.list_categories()}
    assert {"Groceries", "Household", "Uncategorized"} <= names
    assert len(store.list_rules()) > 20
    # Uncategorized is withheld from the model's choices on purpose.
    assert "Uncategorized" not in store.model_category_names()


def test_an_existing_database_loses_the_bad_store_brand_rule(books):
    """The migration must reach books that were created before the fix.

    Rules are seeded only on a fresh database, so a user who already had the
    "GREAT VALUE" rule would otherwise keep mis-filing every Great Value
    product for ever.
    """
    db, store = books["db"], books["store"]
    groceries = categories(store)["Groceries"]
    with db.connect() as connection:
        connection.execute(
            "INSERT INTO category_rule (field, match_type, pattern, category_id, "
            "priority, enabled, is_builtin) "
            "VALUES ('description', 'contains', 'GREAT VALUE', ?, 50, 1, 1)",
            (groceries,),
        )
        # A rule the user wrote themselves, which must survive.
        connection.execute(
            "INSERT INTO category_rule (field, match_type, pattern, category_id, "
            "priority, enabled, is_builtin) "
            "VALUES ('description', 'contains', 'GREAT VALUE', ?, 40, 1, 0)",
            (groceries,),
        )
        connection.execute("PRAGMA user_version = 1")

    db.init_db()

    remaining = [r for r in store.list_rules() if r["pattern"] == "GREAT VALUE"]
    assert len(remaining) == 1
    assert remaining[0]["is_builtin"] == 0, "only the built-in copy is removed"


def test_an_empty_set_of_books_reports_nothing(books):
    store = books["store"]
    assert store.status_counts() == {}
    assert store.list_receipts() == (0, [])
    assert store.report_summary()["totals"]["receipts"] == 0


# ------------------------------------------------------------- the scan path


def test_a_scanned_receipt_is_stored_categorised_and_left_for_review(scanned):
    store = scanned["store"]
    receipt = store.get_receipt(scanned["id"])
    expected = scanned["expected"]

    assert scanned["engine"].calls == 1
    assert receipt["status"] == "needs_review", "a clean reading still gets a human check"
    assert receipt["merchant"] == expected["merchant"]
    assert receipt["purchased_at"] == expected["purchased_at"]
    assert receipt["total_cents"] == to_cents(expected["total"])
    assert len(receipt["items"]) == expected["item_count"]
    assert receipt["review_flags"] == [], receipt["review_flags"]
    assert receipt["cost_usd"] == pytest.approx(0.0225)
    assert receipt["items_total_cents"] == to_cents("64.59")

    by_description = {item["description"]: item for item in receipt["items"]}
    # The seeded "MILK" keyword decides this line, not the model's word.
    assert by_description["GV WHL MILK"]["category_name"] == "Groceries"
    assert by_description["GV WHL MILK"]["category_source"] == "rule"
    assert by_description["MANAGER COUPON"]["amount_cents"] == -200
    assert by_description["MANAGER COUPON"]["is_discount"] == 1
    # The header category is the one holding the most money across the lines.
    assert receipt["category_name"] == "Groceries"


def test_an_item_no_rule_matches_keeps_the_models_category(books, monkeypatch,
                                                           sample_receipt_png):
    reading = ExtractedReceipt(
        merchant="Corner Bakery", purchased_at="2026-07-14", subtotal="6.00",
        tax="0.00", total="6.00", confidence=0.9,
        items=[ExtractedItem(description="SOURDOUGH BOULE", amount="6.00",
                             category="Dining")],
    )
    monkeypatch.setattr(books["pipeline"], "build_engines",
                        lambda s: [StubEngine(reading)])
    path, _ = sample_receipt_png
    receipt_id = books["store"].create_from_image(path.read_bytes(), path.name)
    books["pipeline"].scan_now(receipt_id)
    item = books["store"].get_receipt(receipt_id)["items"][0]
    assert (item["category_name"], item["category_source"]) == ("Dining", "model")


def test_a_misread_total_produces_a_review_flag(books, monkeypatch, sample_receipt_png):
    reading = walmart_reading()
    reading.total = "168.46"          # a plausible-looking decimal misread
    reading.subtotal = "164.59"
    monkeypatch.setattr(books["pipeline"], "build_engines",
                        lambda s: [StubEngine(reading)])
    path, _ = sample_receipt_png
    receipt_id = books["store"].create_from_image(path.read_bytes(), path.name)
    books["pipeline"].scan_now(receipt_id)
    receipt = books["store"].get_receipt(receipt_id)
    assert receipt["status"] == "needs_review"
    assert any("off by 100.00" in flag for flag in receipt["review_flags"])


def test_the_second_engine_is_used_when_the_first_cannot_read(books, monkeypatch,
                                                              sample_receipt_png):
    fallback = StubEngine(walmart_reading(), name="fallback")
    monkeypatch.setattr(books["pipeline"], "build_engines",
                        lambda s: [FailingEngine(), fallback])
    path, _ = sample_receipt_png
    receipt_id = books["store"].create_from_image(path.read_bytes(), path.name)
    books["pipeline"].scan_now(receipt_id)
    receipt = books["store"].get_receipt(receipt_id)
    assert receipt["engine"] == "fallback"
    assert any("no key configured" in flag for flag in receipt["review_flags"])


def test_a_scan_that_fails_entirely_keeps_the_receipt_and_the_reason(
    books, monkeypatch, sample_receipt_png
):
    monkeypatch.setattr(books["pipeline"], "build_engines", lambda s: [FailingEngine()])
    path, _ = sample_receipt_png
    receipt_id = books["store"].create_from_image(path.read_bytes(), path.name)
    books["pipeline"].scan_now(receipt_id)
    receipt = books["store"].get_receipt(receipt_id)
    assert receipt["status"] == "failed"
    assert "no key configured" in receipt["error"]
    # The image survives, so a re-scan after fixing the key is possible.
    assert books["store"].image_path(receipt_id) is not None


def test_importing_the_same_image_twice_is_flagged_as_a_duplicate(
    books, monkeypatch, sample_receipt_png
):
    monkeypatch.setattr(books["pipeline"], "build_engines",
                        lambda s: [StubEngine(walmart_reading())])
    path, _ = sample_receipt_png
    store, pipeline = books["store"], books["pipeline"]
    first = store.create_from_image(path.read_bytes(), path.name)
    pipeline.scan_now(first)
    second = store.create_from_image(path.read_bytes(), path.name)
    pipeline.scan_now(second)
    assert any(f"#{first}" in flag
               for flag in store.get_receipt(second)["review_flags"])


def test_auto_confirm_is_opt_in(books, monkeypatch, sample_receipt_png):
    monkeypatch.setattr(books["pipeline"], "build_engines",
                        lambda s: [StubEngine(walmart_reading())])
    books["settings"].save({"auto_confirm_clean": "1"})
    path, _ = sample_receipt_png
    receipt_id = books["store"].create_from_image(path.read_bytes(), path.name)
    books["pipeline"].scan_now(receipt_id)
    assert books["store"].get_receipt(receipt_id)["status"] == "confirmed"


def test_manual_engine_setting_asks_for_hand_entry(books, sample_receipt_png):
    books["settings"].save({"engine": "manual"})
    path, _ = sample_receipt_png
    receipt_id = books["store"].create_from_image(path.read_bytes(), path.name)
    books["pipeline"].scan_now(receipt_id)
    receipt = books["store"].get_receipt(receipt_id)
    assert receipt["status"] == "needs_review"
    assert "by hand" in receipt["error"]


# ------------------------------------------------------------------- imports


def test_a_non_image_file_is_refused_with_a_readable_message(books):
    with pytest.raises(StoreError, match="not a readable image"):
        books["store"].create_from_image(b"just text", "notes.txt")


def test_an_empty_file_is_refused(books):
    with pytest.raises(StoreError, match="empty"):
        books["store"].create_from_image(b"", "nothing.png")


def test_large_photos_are_downscaled_on_the_way_in(books, tmp_path):
    from PIL import Image

    from app.images import MAX_EDGE

    big = tmp_path / "big.png"
    Image.new("RGB", (3000, 4000), "white").save(big)
    receipt_id = books["store"].create_from_image(big.read_bytes(), big.name)
    stored = books["store"].image_path(receipt_id)
    with Image.open(stored) as image:
        assert max(image.size) == MAX_EDGE


# ------------------------------------------------------------ manual editing


def test_hand_entry_round_trips_and_confirms(books):
    store = books["store"]
    receipt_id = store.create_manual()
    assert store.get_receipt(receipt_id)["status"] == "needs_review"
    ids = categories(store)

    saved = store.save_receipt(receipt_id, ReceiptEdit(
        merchant="Corner Store", purchased_at="2026-08-01",
        subtotal_cents=to_cents("10.00"), tax_cents=to_cents("0.60"),
        total_cents=to_cents("10.60"), category_id=ids["Groceries"],
        items=[
            ItemEdit(description="COFFEE BEANS", amount_cents=to_cents("8.00"),
                     category_id=ids["Groceries"]),
            ItemEdit(description="PASTRY", amount_cents=to_cents("2.00"), quantity=2,
                     category_id=ids["Dining"]),
        ],
    ), confirm=True)

    assert saved["status"] == "confirmed"
    assert saved["review_flags"] == []
    assert saved["total_cents"] == 1060
    assert [item["amount_cents"] for item in saved["items"]] == [800, 200]
    assert all(item["category_source"] == "manual" for item in saved["items"])


def test_a_hand_entered_receipt_gets_its_category_from_its_lines(books):
    store = books["store"]
    ids = categories(store)
    receipt_id = store.create_manual()
    saved = store.save_receipt(receipt_id, ReceiptEdit(
        merchant="Corner Store", purchased_at="2026-08-01",
        total_cents=to_cents("30.00"),
        items=[
            ItemEdit(description="BEER", amount_cents=2500, category_id=ids["Dining"]),
            ItemEdit(description="GUM", amount_cents=500, category_id=ids["Groceries"]),
        ],
    ))
    assert saved["category_name"] == "Dining", "the biggest line decides the header"


def test_saving_replaces_the_item_list_rather_than_appending(books):
    store = books["store"]
    receipt_id = store.create_manual()
    base = dict(merchant="S", purchased_at="2026-08-01", total_cents=500)
    store.save_receipt(receipt_id, ReceiptEdit(
        **base, items=[ItemEdit(description="A", amount_cents=500)]))
    saved = store.save_receipt(receipt_id, ReceiptEdit(
        **base, items=[ItemEdit(description="B", amount_cents=500)]))
    assert [item["description"] for item in saved["items"]] == ["B"]


def test_confirming_needs_a_date_and_a_total(books):
    store = books["store"]
    receipt_id = store.create_manual()
    with pytest.raises(StoreError, match="total"):
        store.confirm_receipt(receipt_id)
    store.save_receipt(receipt_id, ReceiptEdit(purchased_at="", total_cents=1000))
    with pytest.raises(StoreError, match="date"):
        store.confirm_receipt(receipt_id)
    store.save_receipt(receipt_id, ReceiptEdit(purchased_at="2026-08-01",
                                               total_cents=1000))
    assert store.confirm_receipt(receipt_id)["status"] == "confirmed"


def test_a_receipt_can_be_confirmed_despite_its_flags(books):
    """Some receipts really do not add up; the flags stay as the record."""
    store = books["store"]
    receipt_id = store.create_manual()
    saved = store.save_receipt(receipt_id, ReceiptEdit(
        purchased_at="2026-08-01", subtotal_cents=5000, total_cents=5000,
        items=[ItemEdit(description="THING", amount_cents=100)],
    ), confirm=True)
    assert saved["status"] == "confirmed"
    assert any("off by" in flag for flag in saved["review_flags"])


def test_deleting_a_receipt_removes_its_image(scanned):
    store = scanned["store"]
    path = store.image_path(scanned["id"])
    assert path is not None and path.exists()
    store.delete_receipt(scanned["id"])
    assert not path.exists()
    with pytest.raises(StoreError):
        store.get_receipt(scanned["id"])


def test_deleting_can_keep_the_image_file(scanned):
    store = scanned["store"]
    path = store.image_path(scanned["id"])
    store.delete_receipt(scanned["id"], keep_image=True)
    assert path.exists()


def test_editing_a_receipt_that_is_gone_says_so_plainly(books):
    with pytest.raises(StoreError, match="no longer in the books"):
        books["store"].save_receipt(4242, ReceiptEdit())


# ------------------------------------------------------------------- listing


def test_listing_filters_by_status_search_and_date(scanned):
    store = scanned["store"]
    assert store.list_receipts(statuses=("needs_review",))[0] == 1
    assert store.list_receipts(statuses=("confirmed",))[0] == 0
    assert store.list_receipts(query="Walmart")[0] == 1
    assert store.list_receipts(query="TIDE")[0] == 1, "searches item names too"
    assert store.list_receipts(query="Costco")[0] == 0
    assert store.list_receipts(date_from="2026-08-01")[0] == 0
    assert store.list_receipts(date_to="2026-08-01")[0] == 1


def test_listing_can_filter_by_category_including_line_items(scanned):
    store = scanned["store"]
    ids = categories(store)
    assert store.list_receipts(category_id=ids["Pets"])[0] == 1, "matches a line item"
    assert store.list_receipts(category_id=ids["Clothing"])[0] == 0


def test_status_counts_track_the_workflow(scanned):
    store = scanned["store"]
    assert store.status_counts() == {"needs_review": 1}
    store.confirm_receipt(scanned["id"])
    assert store.status_counts() == {"confirmed": 1}


# ------------------------------------------------------------ rules and tags


def test_a_new_rule_can_be_backfilled_over_existing_receipts(scanned):
    store = scanned["store"]
    ids = categories(store)
    store.create_rule("HDMI CABLE", ids["Office & Supplies"], priority=10)
    examined, changed = store.apply_rules()
    assert examined >= 10 and changed >= 1
    items = store.get_receipt(scanned["id"])["items"]
    cable = next(item for item in items if item["description"] == "HDMI CABLE 6FT")
    assert cable["category_name"] == "Office & Supplies"


def test_backfill_leaves_manual_choices_and_confirmed_books_alone(scanned):
    store = scanned["store"]
    ids = categories(store)
    receipt = store.get_receipt(scanned["id"])
    items = [
        ItemEdit(
            description=item["description"],
            amount_cents=item["amount_cents"],
            category_id=(ids["Entertainment"] if item["description"] == "BANANAS"
                         else item["category_id"]),
            category_source=("manual" if item["description"] == "BANANAS"
                             else item["category_source"]),
        )
        for item in receipt["items"]
    ]
    store.save_receipt(scanned["id"], ReceiptEdit(
        merchant=receipt["merchant"], purchased_at=receipt["purchased_at"],
        subtotal_cents=receipt["subtotal_cents"], tax_cents=receipt["tax_cents"],
        total_cents=receipt["total_cents"], items=items,
    ), confirm=True)

    store.apply_rules()
    after = store.get_receipt(scanned["id"])["items"]
    bananas = next(item for item in after if item["description"] == "BANANAS")
    assert bananas["category_name"] == "Entertainment"


def test_backfill_does_not_let_a_merchant_rule_overwrite_the_models_choice(
    books, monkeypatch, sample_receipt_png
):
    """The seeded WALMART rule says Groceries; the model said Dining for this
    line. Re-applying rules must leave the model's specific answer alone."""
    reading = ExtractedReceipt(
        merchant="Walmart", purchased_at="2026-07-14", subtotal="6.00", tax="0.00",
        total="6.00", confidence=0.9,
        items=[ExtractedItem(description="SOURDOUGH BOULE", amount="6.00",
                             category="Dining")],
    )
    monkeypatch.setattr(books["pipeline"], "build_engines",
                        lambda s: [StubEngine(reading)])
    path, _ = sample_receipt_png
    store = books["store"]
    receipt_id = store.create_from_image(path.read_bytes(), path.name)
    books["pipeline"].scan_now(receipt_id)
    assert store.get_receipt(receipt_id)["items"][0]["category_source"] == "model"

    store.apply_rules()
    item = store.get_receipt(receipt_id)["items"][0]
    assert (item["category_name"], item["category_source"]) == ("Dining", "model")


def test_a_bad_regex_rule_is_refused_at_the_door(books):
    store = books["store"]
    with pytest.raises(StoreError, match="regular expression"):
        store.create_rule("([unclosed", categories(store)["Groceries"],
                          match_type="regex")


def test_rules_can_be_deleted(books):
    store = books["store"]
    rule_id = store.create_rule("KOMBUCHA", categories(store)["Groceries"])
    store.delete_rule(rule_id)
    assert all(rule["id"] != rule_id for rule in store.list_rules())
    with pytest.raises(StoreError):
        store.delete_rule(rule_id)


def test_categories_can_be_added_and_are_unique(books):
    store = books["store"]
    store.create_category("Hobbies")
    assert "Hobbies" in {c["name"] for c in store.list_categories()}
    with pytest.raises(StoreError, match="already exists"):
        store.create_category("hobbies")
    with pytest.raises(StoreError, match="needs a name"):
        store.create_category("   ")


def test_deleting_a_category_moves_its_lines_to_uncategorized(scanned):
    store = scanned["store"]
    store.delete_category(categories(store)["Pets"])
    items = store.get_receipt(scanned["id"])["items"]
    dog_food = next(item for item in items if item["description"] == "DOG FOOD 16LB")
    assert dog_food["category_name"] == "Uncategorized"


def test_the_fallback_category_cannot_be_deleted(books):
    store = books["store"]
    with pytest.raises(StoreError, match="cannot be deleted"):
        store.delete_category(categories(store)["Uncategorized"])


# ------------------------------------------------------------------- reports


def test_reports_only_count_confirmed_receipts_by_default(scanned):
    store = scanned["store"]
    empty = store.report_summary()
    assert empty["totals"]["receipts"] == 0
    assert empty["pending_review"] == 1

    store.confirm_receipt(scanned["id"])
    report = store.report_summary()
    assert report["totals"]["receipts"] == 1
    assert report["totals"]["spend"] == "68.46"
    # Every dollar of the total is attributed, including the tax that no line
    # item accounts for -- that is what the residual bucket is for.
    assert sum(b["amount_cents"] for b in report["by_category"]) == 6846
    assert any(b["category"] == "Tax & unitemised" for b in report["by_category"])
    assert report["by_month"][0]["month"] == "2026-07"
    assert report["by_merchant"][0]["merchant"] == "Walmart"
    assert report["by_category"][0]["share"] == pytest.approx(
        report["by_category"][0]["amount_cents"] / 6846)


def test_reports_can_include_unreviewed_receipts(scanned):
    store = scanned["store"]
    report = store.report_summary(statuses=("confirmed", "needs_review"))
    assert report["totals"]["receipts"] == 1


def test_report_date_filters_exclude_out_of_range_receipts(scanned):
    store = scanned["store"]
    store.confirm_receipt(scanned["id"])
    inside = store.report_summary(date_from="2026-07-01", date_to="2026-07-31")
    outside = store.report_summary(date_from="2026-01-01", date_to="2026-01-31")
    assert inside["totals"]["receipts"] == 1
    assert outside["totals"]["receipts"] == 0


def test_csv_export_has_one_row_per_line_item(scanned, tmp_path):
    store = scanned["store"]
    store.confirm_receipt(scanned["id"])
    destination = tmp_path / "out" / "items.csv"
    rows = store.export_items_csv(destination)
    assert rows == 10
    text = destination.read_text(encoding="utf-8-sig")
    lines = text.strip().splitlines()
    assert lines[0].startswith("receipt_id,date,merchant")
    assert len(lines) == 11
    assert "GV WHL MILK" in text
    assert "3.24" in text


def test_csv_export_of_nothing_still_writes_a_header(books, tmp_path):
    destination = tmp_path / "items.csv"
    assert books["store"].export_items_csv(destination) == 0
    assert destination.read_text(encoding="utf-8-sig").strip().startswith("receipt_id")


# ------------------------------------------------------------------ settings


def test_the_api_key_is_never_handed_back_in_full(books):
    settings = books["settings"]
    settings.save({"anthropic_api_key": "sk-ant-secret-value-1234"})
    assert settings.public_view()["anthropic_api_key"] == "****1234"
    assert settings.get("anthropic_api_key") == "sk-ant-secret-value-1234"

    # Saving the masked value back must not overwrite the real key.
    settings.save({"anthropic_api_key": "****1234"})
    assert settings.get("anthropic_api_key") == "sk-ant-secret-value-1234"

    settings.save({"anthropic_api_key": "__clear__"})
    assert settings.get("anthropic_api_key") == ""


def test_engine_availability_explains_itself(books):
    from app.extract import engine_status

    engines = {e["name"]: e for e in engine_status(books["settings"].get_all())}
    assert engines["claude"]["available"] is False
    assert "API key" in engines["claude"]["detail"]
