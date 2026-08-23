"""The one real receipt this project has ever been tested against.

A Walmart receipt photographed by the user on 2026-08-23: 24 printed lines, real
store abbreviations (`GV TWIST MOP`, `HS SH CLS8.5`, `EQJELLUBE8OZ`), three
5-cent bottle deposits, two identical `FRENCH BREAD` lines, and a header that is
out of frame so the store name and date are genuinely missing.

The transcription below is trustworthy because the receipt checks itself four
ways, and all four hold (see ``test_the_transcription_is_self_consistent``):

    items sum                   == printed subtotal 141.94
    subtotal + tax              == printed total    149.44
    cash - total + rounding     == printed change     50.60
    printed lines - 3 deposits  == "# ITEMS SOLD 21"

A single misread digit would break at least one of them, so this fixture is
worth defending. What it exercises that synthetic data cannot: whether the seeded
rules survive contact with how a real shop actually prints its item names. The
first run found that they did not -- see the GREAT VALUE test at the end.

No personal data: the crop contains no name, address or card number, and the
transaction code is deliberately not reproduced here.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.extract import ExtractedItem, ExtractedReceipt, ExtractionResult  # noqa: E402
from app.money import to_cents  # noqa: E402

# printed name, sku, amount, tax flag, the model's plain-English expansion,
# the category a competent reader would choose
LINES = [
    ("BEDINABAG",    "840021403470", "29.72", "X", "Bed-in-a-Bag bedding set",      "Household"),
    ("GV 1G SP",     "078742356220", "1.37",  "X", "Great Value 1 gallon spring water", "Groceries"),
    ("ME DEPOSIT",   "000787423909", "0.05",  "O", "Maine bottle deposit",          "Fees & Taxes"),
    ("GV 1G SP",     "078742356220", "1.37",  "X", "Great Value 1 gallon spring water", "Groceries"),
    ("ME DEPOSIT",   "000787423909", "0.05",  "O", "Maine bottle deposit",          "Fees & Taxes"),
    ("GV TWIST MOP", "078742352910", "10.88", "X", "Great Value twist mop",         "Household"),
    ("COKE",         "049000050110", "3.04",  "X", "Coca-Cola",                     "Groceries"),
    ("ME DEPOSIT",   "000787423909", "0.05",  "O", "Maine bottle deposit",          "Fees & Taxes"),
    ("HANGERS",      "802404007800", "2.98",  "X", "Clothes hangers",               "Household"),
    ("HS SH CLS8.5", "037000949590", "3.97",  "X", "Head & Shoulders Classic Clean shampoo 8.5oz", "Personal Care"),
    ("DOVE BW 11OZ", "011111064940", "5.47",  "X", "Dove body wash 11oz",           "Personal Care"),
    ("AIM TP 5.5OZ", "033200000930", "0.98",  "X", "Aim toothpaste 5.5oz",          "Personal Care"),
    ("GV TOASTED O", "194346525620", "2.47",  "N", "Great Value Toasted Oats cereal", "Groceries"),
    ("DWN EZS 22Z",  "030772224910", "3.83",  "X", "Dawn EZ-Squeeze dish soap 22oz", "Household"),
    ("CLX PLNGR",    "070982051940", "17.76", "X", "Clorox toilet plunger",         "Household"),
    ("GV AMMONIA",   "078742276610", "2.94",  "X", "Great Value ammonia cleaner",   "Household"),
    ("FRENCH BREAD", "200989000000", "1.47",  "N", "French bread loaf",             "Groceries"),
    ("FRENCH BREAD", "200989000000", "1.47",  "N", "French bread loaf",             "Groceries"),
    ("PROTEINSUPPL", "660726503370", "23.18", "X", "Protein supplement",            "Health & Pharmacy"),
    ("GV HD SPGE 4", "078742364220", "2.18",  "X", "Great Value heavy duty sponges, 4 pack", "Household"),
    ("TIDE PODS 57", "030772259580", "17.94", "X", "Tide Pods laundry detergent, 57 count", "Household"),
    ("ST BUCKET",    "073149120830", "2.86",  "X", "Storage bucket",                "Household"),
    ("GAIN",         "037000976140", "0.97",  "X", "Gain laundry detergent",        "Household"),
    ("EQJELLUBE8OZ", "194346600820", "4.94",  "X", "Equate lubricating jelly 8oz",  "Health & Pharmacy"),
]
SUBTOTAL, TAX, TOTAL = "141.94", "7.50", "149.44"
CASH, ROUNDING, CHANGE = "200.00", "0.04", "50.60"
ITEMS_SOLD = 21          # the receipt's own count: 24 lines minus 3 deposits


def reading() -> ExtractedReceipt:
    """The receipt as the vision model reports it, per the system prompt.

    Merchant and date are ``None`` on purpose: the top of the receipt is not in
    the photo, and the prompt says never to guess a store name or a date that is
    not visible.
    """
    return ExtractedReceipt(
        merchant=None, merchant_raw=None, purchased_at=None, currency="USD",
        subtotal=SUBTOTAL, tax=TAX, tip=None, total=TOTAL,
        payment_method="CASH", category=None, confidence=0.88,
        notes="The top of the receipt is not in frame: the store name, address, "
              "date and time are missing. The items and totals are legible.",
        items=[
            ExtractedItem(
                description=name, readable_name=readable, sku=sku, amount=amount,
                taxable={"X": True, "N": False, "O": False}.get(flag),
                category=category,
            )
            for name, sku, amount, flag, readable, category in LINES
        ],
    )


class RealEngine:
    """Replays the reading above in place of an API call."""

    name = "claude"

    def available(self):
        return True, "replaying a real receipt"

    def extract(self, image_path, categories):
        return ExtractionResult(
            receipt=reading(), engine="claude", model="claude-opus-5",
            raw_response="{}",
            # Measured on this receipt: 24 items with expansions is a much
            # longer reply than the "a few hundred output tokens" a small
            # receipt produces.
            input_tokens=2208, output_tokens=1487, cost_usd=0.048215,
            elapsed_ms=18400,
        )


@pytest.fixture()
def scanned(books, monkeypatch, sample_receipt_png):
    monkeypatch.setattr(books["pipeline"], "build_engines", lambda s: [RealEngine()])
    path, _ = sample_receipt_png     # stands in for the photo's pixels
    receipt_id = books["store"].create_from_image(path.read_bytes(), "walmart.jpg")
    books["pipeline"].scan_now(receipt_id)
    return receipt_id, books["store"]


# ------------------------------------------------------- the fixture itself


def test_the_transcription_is_self_consistent():
    """The four checks the receipt performs on itself. If one of these ever
    fails, the transcription was edited wrongly -- not the code."""
    items = sum(to_cents(amount) for _, _, amount, _, _, _ in LINES)
    assert items == to_cents(SUBTOTAL)
    assert to_cents(SUBTOTAL) + to_cents(TAX) == to_cents(TOTAL)
    assert to_cents(CASH) - to_cents(TOTAL) + to_cents(ROUNDING) == to_cents(CHANGE)
    deposits = sum(1 for _, _, amount, _, _, _ in LINES if amount == "0.05")
    assert len(LINES) - deposits == ITEMS_SOLD


# ------------------------------------------------------------- the pipeline


def test_the_whole_receipt_is_stored_correctly(scanned):
    receipt_id, store = scanned
    receipt = store.get_receipt(receipt_id)

    assert receipt["status"] == "needs_review"
    assert len(receipt["items"]) == 24
    assert receipt["subtotal_cents"] == to_cents(SUBTOTAL)
    assert receipt["tax_cents"] == to_cents(TAX)
    assert receipt["total_cents"] == to_cents(TOTAL)
    assert receipt["items_total_cents"] == to_cents(SUBTOTAL)
    assert receipt["payment_method"] == "CASH"
    assert receipt["cost_usd"] == pytest.approx(0.048215)


def test_the_only_complaint_is_the_missing_date(scanned):
    """A self-consistent receipt must not be nagged about its arithmetic -- and a
    receipt with no visible date must not be allowed to pass as complete."""
    receipt_id, store = scanned
    flags = store.get_receipt(receipt_id)["review_flags"]
    assert len(flags) == 1, flags
    assert "date" in flags[0].lower()


def test_two_identical_lines_are_both_kept(scanned):
    """FRENCH BREAD appears twice at 1.47. Collapsing duplicates would lose
    1.47 and break the subtotal."""
    receipt_id, store = scanned
    items = store.get_receipt(receipt_id)["items"]
    bread = [i for i in items if i["description"] == "FRENCH BREAD"]
    assert len(bread) == 2
    assert all(i["amount_cents"] == 147 for i in bread)


def test_the_bottle_deposits_are_kept_as_lines(scanned):
    receipt_id, store = scanned
    items = store.get_receipt(receipt_id)["items"]
    deposits = [i for i in items if i["description"] == "ME DEPOSIT"]
    assert len(deposits) == 3
    assert all(i["amount_cents"] == 5 for i in deposits)
    assert all(i["category_name"] == "Fees & Taxes" for i in deposits)


def test_the_printed_tax_flags_are_kept(scanned):
    """`X` means taxable, `N`/`O` do not -- worth keeping for anyone checking a
    sales-tax figure later."""
    receipt_id, store = scanned
    items = {i["description"]: i for i in store.get_receipt(receipt_id)["items"]}
    assert items["TIDE PODS 57"]["taxable"] == 1
    assert items["FRENCH BREAD"]["taxable"] == 0
    assert items["ME DEPOSIT"]["taxable"] == 0


def test_the_header_category_follows_the_money(scanned):
    """Household is the biggest share of this basket, so it is the receipt's
    category even though no rule or model suggestion set one."""
    receipt_id, store = scanned
    assert store.get_receipt(receipt_id)["category_name"] == "Household"


# --------------------------------------------------------- categorisation


def _by_name(store, receipt_id) -> dict[str, dict]:
    return {i["description"]: i for i in store.get_receipt(receipt_id)["items"]}


def test_store_brand_products_are_not_all_groceries(scanned):
    """The bug this receipt found.

    A seeded `GREAT VALUE` rule used to file every Great Value product as
    Groceries -- including a mop, a bottle of ammonia and a pack of sponges,
    16.00 of this basket in the wrong category. It was a brand, not a category.
    """
    receipt_id, store = scanned
    items = _by_name(store, receipt_id)
    assert items["GV TWIST MOP"]["category_name"] == "Household"
    assert items["GV AMMONIA"]["category_name"] == "Household"
    assert items["GV HD SPGE 4"]["category_name"] == "Household"
    # …while the Great Value products that really are food stay food.
    assert items["GV TOASTED O"]["category_name"] == "Groceries"
    assert items["GV 1G SP"]["category_name"] == "Groceries"

    assert not any(
        rule["pattern"] == "GREAT VALUE" and rule["is_builtin"]
        for rule in store.list_rules()
    ), "a store brand must not be seeded as a category rule"


def test_the_plain_english_expansion_earns_its_keep(scanned):
    """Every one of these printed names is opaque; each is categorised by a
    keyword rule that only matches because the model expanded the abbreviation."""
    receipt_id, store = scanned
    items = _by_name(store, receipt_id)
    for printed, expected in (("HS SH CLS8.5", "Personal Care"),   # …shampoo
                              ("AIM TP 5.5OZ", "Personal Care"),   # …toothpaste
                              ("DWN EZS 22Z", "Household"),        # …dish soap
                              ("GAIN", "Household"),               # …detergent
                              ("ME DEPOSIT", "Fees & Taxes")):     # bottle deposit
        assert items[printed]["category_name"] == expected, printed
        assert items[printed]["category_source"] == "rule", printed


def test_every_line_lands_in_the_category_a_person_would_choose(scanned):
    """The end-to-end quality measure: the app's answer against a human's, for
    all 24 lines. Failures here are the interesting kind."""
    receipt_id, store = scanned
    items = _by_name(store, receipt_id)
    wrong = [
        (printed, expected, items[printed]["category_name"])
        for printed, _sku, _amount, _flag, _readable, expected in LINES
        if items[printed]["category_name"] != expected
    ]
    assert not wrong, f"{len(wrong)} of {len(LINES)} lines mis-categorised: {wrong}"


def test_nothing_is_left_uncategorised(scanned):
    receipt_id, store = scanned
    items = store.get_receipt(receipt_id)["items"]
    assert not [i for i in items if i["category_name"] == "Uncategorized"]


# ---------------------------------------------------------------- reports


def test_the_report_attributes_every_cent(scanned):
    receipt_id, store = scanned
    store.save_receipt(
        receipt_id,
        store.ReceiptEdit(
            merchant="Walmart", purchased_at="2026-08-20", currency="USD",
            subtotal_cents=to_cents(SUBTOTAL), tax_cents=to_cents(TAX),
            total_cents=to_cents(TOTAL), payment_method="CASH",
            items=[
                store.ItemEdit(description=i["description"],
                               amount_cents=i["amount_cents"],
                               category_id=i["category_id"],
                               category_source=i["category_source"])
                for i in store.get_receipt(receipt_id)["items"]
            ],
        ),
        confirm=True,
    )
    report = store.report_summary(date_from="2026-01-01")
    assert report["totals"]["spend"] == TOTAL
    assert report["totals"]["items"] == 24
    # Category buckets, including the tax residual, must equal the money spent.
    assert sum(b["amount_cents"] for b in report["by_category"]) == to_cents(TOTAL)
    biggest = report["by_category"][0]
    assert biggest["category"] == "Household"
    residual = next(b for b in report["by_category"]
                    if b["category"] == "Tax & unitemised")
    assert residual["amount_cents"] == to_cents(TAX)
