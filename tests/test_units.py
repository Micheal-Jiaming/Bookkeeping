"""Unit tests for the pure logic: money, validation, rules, OCR text parsing."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.categorize import Rule, match_rules, resolve_category  # noqa: E402
from app.extract.tesseract_ocr import parse_receipt_text  # noqa: E402
from app.money import find_amounts, from_cents, to_cents  # noqa: E402
from app.validate import check  # noqa: E402

# --------------------------------------------------------------------- money


def test_to_cents_handles_the_shapes_receipts_actually_print():
    assert to_cents("12.34") == 1234
    assert to_cents("$12.34") == 1234
    assert to_cents("1,234.56") == 123456
    assert to_cents("-2.00") == -200
    assert to_cents("(2.00)") == -200
    assert to_cents("3.5") == 350
    assert to_cents(12.34) == 1234
    assert to_cents(12) == 1200  # a bare int means dollars


def test_to_cents_distinguishes_absent_from_zero():
    assert to_cents(None) is None
    assert to_cents("") is None
    assert to_cents("n/a") is None
    assert to_cents("0.00") == 0


def test_from_cents_round_trips():
    for text in ("0.00", "0.07", "12.34", "-12.34", "1234.05"):
        assert from_cents(to_cents(text)) == text


def test_cent_arithmetic_does_not_drift():
    # The float version of this sum is 0.30000000000000004.
    assert to_cents("0.10") + to_cents("0.20") == to_cents("0.30")


def test_find_amounts_reads_a_line_of_ocr_text():
    assert find_amounts("SUBTOTAL 64.59 TAX 3.87 TOTAL 68.46") == [6459, 387, 6846]


# ---------------------------------------------------------------- validation


def _items(*amounts: str) -> list[dict]:
    return [{"description": f"item{i}", "amount_cents": to_cents(a)}
            for i, a in enumerate(amounts)]


def test_clean_receipt_has_no_flags():
    flags = check(
        purchased_at="2026-07-14",
        total_cents=to_cents("68.46"),
        subtotal_cents=to_cents("64.59"),
        tax_cents=to_cents("3.87"),
        tip_cents=None,
        items=_items("3.24", "1.48", "4.98", "2.86", "12.97", "9.44", "3.12", "18.62", "9.88", "-2.00"),
        confidence=0.95,
    )
    assert flags == []


def test_items_that_do_not_match_the_subtotal_are_flagged_with_the_delta():
    flags = check(
        purchased_at="2026-07-14",
        total_cents=to_cents("68.46"),
        subtotal_cents=to_cents("64.59"),
        tax_cents=to_cents("3.87"),
        tip_cents=None,
        items=_items("3.24", "1.48"),  # a misread that dropped most lines
        confidence=0.9,
    )
    assert any("off by 59.87" in flag for flag in flags)


def test_a_cent_of_rounding_is_tolerated():
    flags = check(
        purchased_at="2026-07-14", total_cents=1001, subtotal_cents=1000,
        tax_cents=0, tip_cents=None, items=_items("10.01"), confidence=0.9,
    )
    assert flags == []


def test_missing_date_and_total_are_both_reported():
    flags = check(
        purchased_at=None, total_cents=None, subtotal_cents=None, tax_cents=None,
        tip_cents=None, items=[], confidence=0.9,
    )
    assert any("total" in flag.lower() for flag in flags)
    assert any("date" in flag.lower() for flag in flags)
    assert any("line item" in flag.lower() for flag in flags)


def test_future_dates_and_low_confidence_are_flagged():
    flags = check(
        purchased_at="2099-01-01", total_cents=1000, subtotal_cents=1000,
        tax_cents=0, tip_cents=None, items=_items("10.00"), confidence=0.2,
    )
    assert any("future" in flag for flag in flags)
    assert any("low confidence" in flag for flag in flags)


def test_duplicate_upload_is_surfaced():
    flags = check(
        purchased_at="2026-07-14", total_cents=1000, subtotal_cents=1000,
        tax_cents=0, tip_cents=None, items=_items("10.00"), confidence=0.9,
        duplicate_of=7,
    )
    assert any("#7" in flag for flag in flags)


def test_implausible_tax_is_flagged():
    flags = check(
        purchased_at="2026-07-14", total_cents=to_cents("10.00"),
        subtotal_cents=to_cents("9.00"), tax_cents=to_cents("9.00"),
        tip_cents=None, items=_items("9.00"), confidence=0.9,
    )
    assert any("implausibly large" in flag for flag in flags)


# ------------------------------------------------------------- categorisation

RULES = [
    Rule(1, "description", "contains", "GREAT VALUE", 10, 50),
    Rule(2, "description", "regex", r"^BANANA(S)?$", 11, 60),
    Rule(3, "merchant", "contains", "WALMART", 12, 200),
]
INDEX = {"groceries": 10, "produce": 11, "household": 12, "uncategorized": 99}


def test_lower_priority_number_wins():
    category_id, rule_id = match_rules(RULES, "GREAT VALUE EGGS")
    assert (category_id, rule_id) == (10, 1)


def test_regex_rules_work_and_are_case_insensitive():
    assert match_rules(RULES, "bananas")[0] == 11


def test_a_broken_regex_is_ignored_rather_than_fatal():
    broken = [Rule(9, "description", "regex", "([unclosed", 10, 1), *RULES]
    assert match_rules(broken, "GREAT VALUE EGGS")[0] == 10


def test_rules_outrank_the_models_suggestion():
    category_id, source = resolve_category(
        RULES, INDEX, description="GREAT VALUE EGGS", merchant="Walmart",
        model_suggestion="Household",
    )
    assert (category_id, source) == (10, "rule")


def test_the_model_suggestion_is_used_when_no_rule_matches():
    category_id, source = resolve_category(
        RULES, INDEX, description="ARTISAN SOURDOUGH", merchant="Corner Bakery",
        model_suggestion="Groceries",
    )
    assert (category_id, source) == (10, "model")


def test_an_invented_category_name_falls_back_to_uncategorized():
    category_id, source = resolve_category(
        RULES, INDEX, description="MYSTERY ITEM", merchant="Corner Shop",
        model_suggestion="Interstellar Travel",
    )
    assert (category_id, source) == (99, "default")


def test_merchant_rules_catch_what_item_rules_miss():
    category_id, source = resolve_category(
        RULES, INDEX, description="UNKNOWN THING", merchant="WALMART #1234",
        model_suggestion=None,
    )
    assert (category_id, source) == (12, "merchant")


def test_the_model_beats_a_blanket_merchant_rule():
    """A specific per-item judgement must not be overwritten by "everything at
    this shop is Groceries" -- the bug found while testing the frozen build."""
    category_id, source = resolve_category(
        RULES, INDEX, description="SOURDOUGH BOULE", merchant="WALMART #1234",
        model_suggestion="Produce",
    )
    assert (category_id, source) == (11, "model")


def test_a_merchant_rule_still_wins_over_nothing_at_all():
    category_id, source = resolve_category(
        RULES, INDEX, description="SOURDOUGH BOULE", merchant="WALMART #1234",
        model_suggestion="Not A Real Category",
    )
    assert (category_id, source) == (12, "merchant")


# ------------------------------------------------------- OCR text parsing

WALMART_OCR = """\
Walmart
Save money. Live better.
(479) 273-4000
ST# 00100 OP# 000912 TE# 44 TR# 07321

GV WHL MILK 007874203912 3.24 X
BANANAS 000000004011 1.48 O
MARKETSIDE SALAD 068113106422 4.98 O
TIDE PODS 42CT 003700091783 12.97 X
MANAGER COUPON -2.00

SUBTOTAL 20.67
TAX 1 6.000 % 1.24
TOTAL 21.91
VISA TEND 21.91
ITEMS SOLD 4
07/14/26                    19:42:08
Thank you for shopping with us
"""


def test_parses_merchant_date_and_summary_amounts():
    receipt = parse_receipt_text(WALMART_OCR)
    assert receipt.merchant == "Walmart"
    assert receipt.purchased_at == "2026-07-14"
    assert receipt.subtotal == "20.67"
    assert receipt.tax == "1.24"
    assert receipt.total == "21.91"
    assert receipt.payment_method == "VISA"


def test_summary_lines_are_not_mistaken_for_items():
    receipt = parse_receipt_text(WALMART_OCR)
    descriptions = [item.description for item in receipt.items]
    assert descriptions == [
        "GV WHL MILK", "BANANAS", "MARKETSIDE SALAD", "TIDE PODS 42CT", "MANAGER COUPON",
    ]
    assert not any("TOTAL" in d or "TAX" in d for d in descriptions)


def test_item_amounts_skus_and_discounts_are_read():
    receipt = parse_receipt_text(WALMART_OCR)
    milk = receipt.items[0]
    assert milk.amount == "3.24"
    assert milk.sku == "007874203912"
    assert milk.taxable is True
    coupon = receipt.items[-1]
    assert coupon.amount == "-2.00"
    assert coupon.is_discount is True


def test_quantity_at_unit_price_is_split_out():
    receipt = parse_receipt_text("STORE\n\nAPPLES 3 @ 1.50 4.50\nTOTAL 4.50\n")
    item = receipt.items[0]
    assert item.description == "APPLES"
    assert item.quantity == 3.0
    assert item.unit_price == "1.50"
    assert item.amount == "4.50"


def test_two_digit_years_resolve_to_this_century():
    assert parse_receipt_text("SHOP\n01/02/26\nTOTAL 1.00\n").purchased_at == "2026-01-02"


def test_iso_dates_are_read_as_printed():
    assert parse_receipt_text("SHOP\n2026-03-04\nTOTAL 1.00\n").purchased_at == "2026-03-04"
