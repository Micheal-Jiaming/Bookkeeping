"""End-to-end tests over the HTTP API.

Scanning is made synchronous (``sync_scan``) so assertions do not race the
background thread pool, and the recognition engine is replaced with a stub that
returns a known reading. That combination exercises everything downstream of the
model -- schema validation, rule categorisation, arithmetic checks, storage,
reports -- without an API key and without spending anything.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.extract import ExtractedItem, ExtractedReceipt, ExtractionError, ExtractionResult  # noqa: E402


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
            receipt=self.receipt,
            engine=self.name,
            model="stub-1",
            raw_response="{}",
            input_tokens=1500,
            output_tokens=600,
            cost_usd=0.0225,
            elapsed_ms=1234,
        )


class FailingEngine:
    name = "failing"

    def available(self):
        return True, "failing"

    def extract(self, image_path, categories):
        raise ExtractionError("no key configured")


def walmart_reading() -> ExtractedReceipt:
    return ExtractedReceipt(
        merchant="Walmart",
        merchant_raw="Walmart Store #100",
        purchased_at="2026-07-14",
        currency="USD",
        subtotal="64.59",
        tax="3.87",
        total="68.46",
        payment_method="VISA ****4471",
        category="Groceries",
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
def sync_scan(app_modules, monkeypatch):
    """Run scans inline instead of on the thread pool."""
    pipeline = app_modules["pipeline"]

    def immediate(receipt_id: int) -> bool:
        pipeline.scan_now(receipt_id)
        return True

    monkeypatch.setattr(pipeline, "submit_scan", immediate)
    return pipeline


@pytest.fixture()
def stub_engine(app_modules, monkeypatch):
    engine = StubEngine(walmart_reading())
    monkeypatch.setattr(app_modules["pipeline"], "build_engines", lambda settings: [engine])
    return engine


def upload(client, path: Path):
    return client.post(
        "/api/receipts/upload",
        files={"files": (path.name, path.read_bytes(), "image/png")},
    )


# ----------------------------------------------------------------- basic wiring


def test_health_reports_ok(client):
    body = client.get("/api/health").json()
    assert body["ok"] is True
    assert body["version"]


def test_the_index_page_is_served(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "Bookkeeping" in response.text


def test_builtin_categories_and_rules_are_seeded(client):
    categories = client.get("/api/categories").json()["categories"]
    names = {c["name"] for c in categories}
    assert {"Groceries", "Household", "Uncategorized"} <= names
    rules = client.get("/api/rules").json()["rules"]
    assert len(rules) > 20
    # Uncategorized is withheld from the model's choices on purpose.
    assert "Uncategorized" not in client.get("/api/model-categories").json()["categories"]


# --------------------------------------------------------------- the scan path


def test_a_scanned_receipt_is_stored_categorised_and_left_for_review(
    client, sync_scan, stub_engine, sample_receipt_png
):
    path, expected = sample_receipt_png
    response = upload(client, path)
    assert response.status_code == 200
    receipt_id = response.json()["created"][0]["id"]
    assert stub_engine.calls == 1

    receipt = client.get(f"/api/receipts/{receipt_id}").json()
    assert receipt["status"] == "needs_review", "a clean reading still gets a human check"
    assert receipt["merchant"] == expected["merchant"]
    assert receipt["purchased_at"] == expected["purchased_at"]
    assert receipt["total"] == expected["total"]
    assert receipt["total_cents"] == 6846
    assert len(receipt["items"]) == expected["item_count"]
    assert receipt["review_flags"] == [], receipt["review_flags"]
    assert receipt["cost_usd"] == pytest.approx(0.0225)

    by_description = {item["description"]: item for item in receipt["items"]}
    # Rule hit: the seeded "MILK" keyword, not the model's word.
    assert by_description["GV WHL MILK"]["category_name"] == "Groceries"
    assert by_description["GV WHL MILK"]["category_source"] == "rule"
    # The seeded "TOOTHPASTE" keyword, again a rule rather than the model.
    assert by_description["COLGATE TOOTHPASTE"]["category_name"] == "Personal Care"
    assert by_description["COLGATE TOOTHPASTE"]["category_source"] == "rule"
    assert by_description["MANAGER COUPON"]["amount_cents"] == -200
    assert by_description["MANAGER COUPON"]["is_discount"] is True
    # The header category is the one holding the most money across the lines.
    assert receipt["category_name"] == "Groceries"


def test_an_item_no_rule_matches_keeps_the_models_category(
    client, sync_scan, app_modules, monkeypatch, sample_receipt_png
):
    reading = ExtractedReceipt(
        merchant="Corner Bakery", purchased_at="2026-07-14", subtotal="6.00",
        tax="0.00", total="6.00", confidence=0.9,
        items=[ExtractedItem(description="SOURDOUGH BOULE", amount="6.00",
                             category="Dining")],
    )
    monkeypatch.setattr(app_modules["pipeline"], "build_engines",
                        lambda s: [StubEngine(reading)])
    path, _ = sample_receipt_png
    receipt_id = upload(client, path).json()["created"][0]["id"]
    item = client.get(f"/api/receipts/{receipt_id}").json()["items"][0]
    assert (item["category_name"], item["category_source"]) == ("Dining", "model")


def test_a_misread_total_produces_a_review_flag(
    client, sync_scan, app_modules, monkeypatch, sample_receipt_png
):
    reading = walmart_reading()
    reading.total = "168.46"          # a plausible-looking decimal misread
    reading.subtotal = "164.59"
    engine = StubEngine(reading)
    monkeypatch.setattr(app_modules["pipeline"], "build_engines", lambda s: [engine])

    path, _ = sample_receipt_png
    receipt_id = upload(client, path).json()["created"][0]["id"]
    receipt = client.get(f"/api/receipts/{receipt_id}").json()
    assert receipt["status"] == "needs_review"
    assert any("off by 100.00" in flag for flag in receipt["review_flags"])


def test_the_second_engine_is_used_when_the_first_cannot_read(
    client, sync_scan, app_modules, monkeypatch, sample_receipt_png
):
    fallback = StubEngine(walmart_reading(), name="fallback")
    monkeypatch.setattr(
        app_modules["pipeline"], "build_engines", lambda s: [FailingEngine(), fallback]
    )
    path, _ = sample_receipt_png
    receipt_id = upload(client, path).json()["created"][0]["id"]
    receipt = client.get(f"/api/receipts/{receipt_id}").json()
    assert receipt["engine"] == "fallback"
    assert any("no key configured" in flag for flag in receipt["review_flags"])


def test_a_scan_that_fails_entirely_keeps_the_receipt_and_the_reason(
    client, sync_scan, app_modules, monkeypatch, sample_receipt_png
):
    monkeypatch.setattr(app_modules["pipeline"], "build_engines", lambda s: [FailingEngine()])
    path, _ = sample_receipt_png
    receipt_id = upload(client, path).json()["created"][0]["id"]
    receipt = client.get(f"/api/receipts/{receipt_id}").json()
    assert receipt["status"] == "failed"
    assert "no key configured" in receipt["error"]
    # The image is still there, so a re-scan after fixing the key is possible.
    assert client.get(f"/api/receipts/{receipt_id}/image").status_code == 200


def test_uploading_the_same_image_twice_is_flagged_as_a_duplicate(
    client, sync_scan, stub_engine, sample_receipt_png
):
    path, _ = sample_receipt_png
    first = upload(client, path).json()["created"][0]["id"]
    second = upload(client, path).json()["created"][0]["id"]
    flags = client.get(f"/api/receipts/{second}").json()["review_flags"]
    assert any(f"#{first}" in flag for flag in flags)


def test_auto_confirm_is_opt_in(client, sync_scan, stub_engine, sample_receipt_png):
    path, _ = sample_receipt_png
    client.put("/api/settings", json={"auto_confirm_clean": "1"})
    receipt_id = upload(client, path).json()["created"][0]["id"]
    assert client.get(f"/api/receipts/{receipt_id}").json()["status"] == "confirmed"


def test_a_non_image_upload_is_rejected_with_a_readable_message(client):
    response = client.post(
        "/api/receipts/upload", files={"files": ("notes.txt", b"just text", "text/plain")}
    )
    assert response.status_code == 400
    assert "not a readable image" in response.text


def test_large_photos_are_downscaled_on_the_way_in(
    client, sync_scan, stub_engine, app_modules, tmp_path
):
    from PIL import Image

    from app.images import MAX_EDGE

    big = tmp_path / "big.png"
    Image.new("RGB", (3000, 4000), "white").save(big)
    receipt_id = upload(client, big).json()["created"][0]["id"]
    stored = client.get(f"/api/receipts/{receipt_id}").json()["image_path"]
    with Image.open(app_modules["image_dir"] / stored) as image:
        assert max(image.size) == MAX_EDGE


# ------------------------------------------------------------ manual editing


def test_hand_entry_round_trips_and_confirms(client):
    created = client.post("/api/receipts/manual").json()
    receipt_id = created["id"]
    assert created["status"] == "needs_review"

    categories = {c["name"]: c["id"] for c in client.get("/api/categories").json()["categories"]}
    payload = {
        "merchant": "Corner Store",
        "purchased_at": "2026-08-01",
        "currency": "USD",
        "subtotal": "10.00",
        "tax": "0.60",
        "total": "10.60",
        "category_id": categories["Groceries"],
        "items": [
            {"description": "COFFEE BEANS", "amount": "8.00",
             "category_id": categories["Groceries"]},
            {"description": "PASTRY", "amount": "2.00", "quantity": 2,
             "category_id": categories["Dining"]},
        ],
        "confirm": True,
    }
    saved = client.put(f"/api/receipts/{receipt_id}", json=payload).json()
    assert saved["status"] == "confirmed"
    assert saved["review_flags"] == []
    assert saved["total_cents"] == 1060
    assert [item["amount"] for item in saved["items"]] == ["8.00", "2.00"]
    assert all(item["category_source"] == "manual" for item in saved["items"])


def test_a_hand_entered_receipt_gets_its_category_from_its_lines(client):
    receipt_id = client.post("/api/receipts/manual").json()["id"]
    categories = {c["name"]: c["id"] for c in client.get("/api/categories").json()["categories"]}
    saved = client.put(f"/api/receipts/{receipt_id}", json={
        "merchant": "Corner Store", "purchased_at": "2026-08-01", "total": "30.00",
        "items": [
            {"description": "BEER", "amount": "25.00", "category_id": categories["Dining"]},
            {"description": "GUM", "amount": "5.00", "category_id": categories["Groceries"]},
        ],
    }).json()
    assert saved["category_name"] == "Dining", "the biggest line decides the header"


def test_editing_replaces_the_item_list_rather_than_appending(client):
    receipt_id = client.post("/api/receipts/manual").json()["id"]
    base = {"merchant": "S", "purchased_at": "2026-08-01", "total": "5.00"}
    client.put(f"/api/receipts/{receipt_id}",
               json={**base, "items": [{"description": "A", "amount": "5.00"}]})
    saved = client.put(f"/api/receipts/{receipt_id}",
                       json={**base, "items": [{"description": "B", "amount": "5.00"}]}).json()
    assert [item["description"] for item in saved["items"]] == ["B"]


def test_confirm_refuses_a_receipt_with_no_total(client):
    receipt_id = client.post("/api/receipts/manual").json()["id"]
    response = client.post(f"/api/receipts/{receipt_id}/confirm")
    assert response.status_code == 400
    assert "total" in response.text


def test_deleting_a_receipt_removes_its_image(
    client, sync_scan, stub_engine, app_modules, sample_receipt_png
):
    path, _ = sample_receipt_png
    receipt_id = upload(client, path).json()["created"][0]["id"]
    stored = client.get(f"/api/receipts/{receipt_id}").json()["image_path"]
    image_path = app_modules["image_dir"] / stored
    assert image_path.exists()
    assert client.delete(f"/api/receipts/{receipt_id}").status_code == 200
    assert not image_path.exists()
    assert client.get(f"/api/receipts/{receipt_id}").status_code == 404


# -------------------------------------------------------------- rules & reports


def test_a_new_rule_can_be_backfilled_over_existing_receipts(
    client, sync_scan, stub_engine, sample_receipt_png
):
    path, _ = sample_receipt_png
    receipt_id = upload(client, path).json()["created"][0]["id"]
    categories = {c["name"]: c["id"] for c in client.get("/api/categories").json()["categories"]}

    client.post("/api/rules", json={
        "field": "description", "pattern": "HDMI CABLE",
        "category_id": categories["Office & Supplies"], "priority": 10,
    })
    result = client.post("/api/rules/apply").json()
    assert result["changed"] >= 1

    items = client.get(f"/api/receipts/{receipt_id}").json()["items"]
    cable = next(item for item in items if item["description"] == "HDMI CABLE 6FT")
    assert cable["category_name"] == "Office & Supplies"


def test_backfill_leaves_manual_choices_and_confirmed_books_alone(
    client, sync_scan, stub_engine, sample_receipt_png
):
    path, _ = sample_receipt_png
    receipt_id = upload(client, path).json()["created"][0]["id"]
    categories = {c["name"]: c["id"] for c in client.get("/api/categories").json()["categories"]}
    receipt = client.get(f"/api/receipts/{receipt_id}").json()

    # The reviewer overrides one line by hand and confirms the receipt.
    items = [
        {**item,
         "category_id": categories["Entertainment"] if item["description"] == "BANANAS"
         else item["category_id"]}
        for item in receipt["items"]
    ]
    client.put(f"/api/receipts/{receipt_id}", json={
        "merchant": receipt["merchant"], "purchased_at": receipt["purchased_at"],
        "total": receipt["total"], "subtotal": receipt["subtotal"], "tax": receipt["tax"],
        "items": items, "confirm": True,
    })
    client.post("/api/rules/apply")
    after = client.get(f"/api/receipts/{receipt_id}").json()["items"]
    bananas = next(item for item in after if item["description"] == "BANANAS")
    assert bananas["category_name"] == "Entertainment"


def test_reports_only_count_confirmed_receipts_by_default(
    client, sync_scan, stub_engine, sample_receipt_png
):
    path, _ = sample_receipt_png
    receipt_id = upload(client, path).json()["created"][0]["id"]

    empty = client.get("/api/reports/summary").json()
    assert empty["totals"]["receipts"] == 0
    assert empty["pending_review"] == 1

    client.post(f"/api/receipts/{receipt_id}/confirm")
    report = client.get("/api/reports/summary").json()
    assert report["totals"]["receipts"] == 1
    assert report["totals"]["spend"] == "68.46"

    # Every dollar of the total is attributed, including the tax that no line
    # item accounts for -- that is what the residual bucket is for.
    assert sum(b["amount_cents"] for b in report["by_category"]) == 6846
    assert any(b["category"] == "Tax & unitemised" for b in report["by_category"])
    assert report["by_month"][0]["month"] == "2026-07"
    assert report["by_merchant"][0]["merchant"] == "Walmart"


def test_report_date_filters_exclude_out_of_range_receipts(
    client, sync_scan, stub_engine, sample_receipt_png
):
    path, _ = sample_receipt_png
    receipt_id = upload(client, path).json()["created"][0]["id"]
    client.post(f"/api/receipts/{receipt_id}/confirm")
    inside = client.get("/api/reports/summary?date_from=2026-07-01&date_to=2026-07-31").json()
    outside = client.get("/api/reports/summary?date_from=2026-01-01&date_to=2026-01-31").json()
    assert inside["totals"]["receipts"] == 1
    assert outside["totals"]["receipts"] == 0


def test_csv_export_has_one_row_per_line_item(
    client, sync_scan, stub_engine, sample_receipt_png
):
    path, _ = sample_receipt_png
    receipt_id = upload(client, path).json()["created"][0]["id"]
    client.post(f"/api/receipts/{receipt_id}/confirm")
    response = client.get("/api/export/items.csv")
    assert response.status_code == 200
    lines = response.text.strip().splitlines()
    assert lines[0].startswith("receipt_id,date,merchant")
    assert len(lines) == 11  # header + 10 items
    assert "GV WHL MILK" in response.text


def test_listing_filters_by_status_search_and_date(
    client, sync_scan, stub_engine, sample_receipt_png
):
    path, _ = sample_receipt_png
    upload(client, path)
    assert client.get("/api/receipts?status=needs_review").json()["total"] == 1
    assert client.get("/api/receipts?status=confirmed").json()["total"] == 0
    assert client.get("/api/receipts?q=Walmart").json()["total"] == 1
    assert client.get("/api/receipts?q=TIDE").json()["total"] == 1, "searches item names too"
    assert client.get("/api/receipts?q=Costco").json()["total"] == 0
    assert client.get("/api/receipts?date_from=2026-08-01").json()["total"] == 0


# ---------------------------------------------------------------- settings


def test_the_api_key_is_never_echoed_back(client):
    client.put("/api/settings", json={"anthropic_api_key": "sk-ant-secret-value-1234"})
    shown = client.get("/api/settings").json()["anthropic_api_key"]
    assert shown == "****1234"
    assert "secret" not in shown

    from app import settings_store
    assert settings_store.get("anthropic_api_key") == "sk-ant-secret-value-1234"

    # Saving the masked value back must not overwrite the real key.
    client.put("/api/settings", json={"anthropic_api_key": "****1234"})
    assert settings_store.get("anthropic_api_key") == "sk-ant-secret-value-1234"

    client.put("/api/settings", json={"anthropic_api_key": "__clear__"})
    assert settings_store.get("anthropic_api_key") == ""


def test_invalid_settings_are_rejected(client):
    assert client.put("/api/settings", json={"effort": "turbo"}).status_code == 400
    assert client.put("/api/settings", json={"engine": "magic"}).status_code == 400


def test_engine_status_explains_why_an_engine_is_unavailable(client):
    engines = client.get("/api/engines").json()["engines"]
    claude = next(engine for engine in engines if engine["name"] == "claude")
    assert claude["available"] is False
    assert "API key" in claude["detail"]


def test_a_bad_regex_rule_is_refused_at_the_door(client):
    categories = {c["name"]: c["id"] for c in client.get("/api/categories").json()["categories"]}
    response = client.post("/api/rules", json={
        "match_type": "regex", "pattern": "([unclosed",
        "category_id": categories["Groceries"], "field": "description",
    })
    assert response.status_code == 400
    assert "regular expression" in response.text


def test_deleting_a_category_moves_its_lines_to_uncategorized(
    client, sync_scan, stub_engine, sample_receipt_png
):
    path, _ = sample_receipt_png
    receipt_id = upload(client, path).json()["created"][0]["id"]
    categories = {c["name"]: c["id"] for c in client.get("/api/categories").json()["categories"]}
    assert client.delete(f"/api/categories/{categories['Pets']}").status_code == 200
    items = client.get(f"/api/receipts/{receipt_id}").json()["items"]
    dog_food = next(item for item in items if item["description"] == "DOG FOOD 16LB")
    assert dog_food["category_name"] == "Uncategorized"


def test_the_fallback_category_cannot_be_deleted(client):
    categories = {c["name"]: c["id"] for c in client.get("/api/categories").json()["categories"]}
    response = client.delete(f"/api/categories/{categories['Uncategorized']}")
    assert response.status_code == 400
