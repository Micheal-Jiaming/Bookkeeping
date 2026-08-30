"""Unit tests for the pure logic: money, validation, rules, OCR text parsing."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.categorize import Rule, match_rules, resolve_category  # noqa: E402
from app.extract.receipt_text import parse_receipt_text  # noqa: E402
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


def test_a_lowercase_tax_flag_still_reads_as_an_item():
    """OCR reads Walmart's small-capital X as a lowercase x most of the time.

    While the trailing-flag pattern accepted uppercase only, the amount failed
    to match at the end of the line and the entire item was silently discarded
    -- 15 of the 20 readable lines on the first real receipt were lost this way.
    """
    receipt = parse_receipt_text("SHOP\nBEDINABAG 840021403470 29.72 x\nTOTAL 29.72\n")
    assert [(i.description, i.amount) for i in receipt.items] == [("BEDINABAG", "29.72")]
    assert receipt.items[0].taxable is True


def test_the_class_flag_after_the_upc_is_not_part_of_the_item_name():
    """Walmart prints a second flag between the UPC and the price."""
    receipt = parse_receipt_text("SHOP\nFRENCH BREAD 200989000000 F 1.47 N\nTOTAL 1.47\n")
    assert receipt.items[0].description == "FRENCH BREAD"


def test_a_name_that_really_ends_in_one_letter_is_left_alone():
    """The dangling-flag rule must not eat the D off a vitamin."""
    receipt = parse_receipt_text("SHOP\nVITAMIN D 012345678901 8.99 X\nTOTAL 8.99\n")
    assert receipt.items[0].description == "VITAMIN D"


# --- two more real Walmart receipts, 2026-08-29, Scarborough ME -------------
#
# These are transcriptions of the printed text rather than OCR output, so they
# test the parser and not the recognition stage. Both exposed a defect the
# original receipt did not: see 11.29 and 11.30.

WALMART_NAMELESS_ITEM = """\
Walmart
WM Supercenter
ST# 01788 OP# 009047 TE# 47 TR# 02197
# ITEMS SOLD 3
TC# 2596 9185 9980 4500 2582
COTT CLN 12M 036000554800          11.67 X
756809105667 756809105660           5.88 X
HARDWHOOKS   850043215670           5.97 X
                  SUBTOTAL         23.52
      TAX1  5.5000 %                1.29
                     TOTAL         24.81
08/29/26                        15:46:06
"""

WALMART_WEIGHED_ITEM = """\
Walmart
WM Supercenter
ST# 01788 OP# 009047 TE# 47 TR# 02195
# ITEMS SOLD 7
BG ALM UNVAN  194346193890 F        2.54 N
GINGER ROOT   000000004612 0 F
   0.42 lb @ 1.00 lb / 3.62         1.52 N
GVCORNSTARCH  078742002830 F        1.92 N
                  SUBTOTAL          5.98
      TAX1  5.5000 %                0.00
                     TOTAL          5.98
08/29/26                        15:44:38
"""


def test_an_item_printed_with_no_name_is_still_counted():
    """Losing this line loses $5.88 off a $23.52 receipt.

    Some items have no name on the receipt at all -- the till prints the
    barcode where the description would go. The guard that rejects a
    "description" with no letters used to drop the whole line, and the only
    symptom was a subtotal that would not reconcile.
    """
    receipt = parse_receipt_text(WALMART_NAMELESS_ITEM)
    amounts = [item.amount for item in receipt.items]
    assert amounts == ["11.67", "5.88", "5.97"]
    assert sum(to_cents(a) for a in amounts) == to_cents(receipt.subtotal)

    nameless = receipt.items[1]
    assert nameless.sku == "756809105667"
    assert nameless.description.isdigit(), "the barcode stands in for a name"


def test_a_phone_number_is_still_not_an_item():
    """The guard that change relaxed must still reject what it was built for."""
    receipt = parse_receipt_text(
        "Walmart\n(479) 273-4000\n207-885-5567 Mgr. KYLE\nTOTAL 5.00\n")
    assert receipt.items == []


def test_a_weighed_item_keeps_its_name_from_the_line_above():
    """Goods sold by weight print their name and their price on separate lines.

    Parsed a line at a time the money is right but the name is not: the item
    came out called "0.42 lb @ 1.00 lb / 3.62".
    """
    receipt = parse_receipt_text(WALMART_WEIGHED_ITEM)
    descriptions = [item.description for item in receipt.items]
    assert descriptions == ["BG ALM UNVAN", "GINGER ROOT", "GVCORNSTARCH"]

    ginger = receipt.items[1]
    assert ginger.amount == "1.52"
    assert ginger.quantity == 0.42
    assert ginger.unit_price == "3.62"
    assert ginger.sku == "000000004612"
    assert ginger.taxable is False


def test_a_carried_name_is_not_attached_to_an_unrelated_later_line():
    """The name is held for one line only, or it leaks onto the next item."""
    receipt = parse_receipt_text(
        "Walmart\n"
        "GINGER ROOT   000000004612 0 F\n"
        "COKE          049000050110          3.04 X\n"
        "TOTAL 3.04\n")
    assert [i.description for i in receipt.items] == ["COKE"]


# --- Aldi, a second chain with a different layout --------------------------
#
# Aldi differs from Walmart in every structural way that matters: the item
# number is printed before the name rather than after, the tax flag is two
# letters, weighed goods put the price on the first line and the weighing on
# the next (the opposite of Walmart), and the grand total is letter-spaced.
# Transcribed from the receipt of 2026-08-21, shortened to five lines.

ALDI = """\
ALDI
Store #163
1100 Brighton Avenue
Portland, ME
343415 24ct Paper Bowl        2.69 NB
356387 Green Peppers          2.69 FA
385448 Sourdough Loaf         3.49 F A
356508 Broccoli Crowns        3.66 FA
   1.75 lb x  2.09/lb
341876 Red Grapes LRW         3.20 FA
   (G) 2.50lb -   (T) 0.02lb
   (N) 2.48 lb x  1.29/lb
Mas*ercard                   15.73
SUBTOTAL                     15.73
B-Taxable @5.500%             0.15
A-Taxable @0.00%              0.00
AMOUNT D                     15.88
T O T A L                  $ 15.88
5 ITEMS
08/21/26 10:14
"""


def test_a_two_letter_tax_flag_does_not_discard_the_line():
    """Aldi flags every line "FA" or "NB", and OCR sometimes splits them.

    The amount pattern allowed a single flag letter, so no Aldi line matched at
    all and a whole receipt read as zero items.
    """
    receipt = parse_receipt_text(ALDI)
    assert [i.amount for i in receipt.items] == ["2.69", "2.69", "3.49", "3.66", "3.20"]
    assert sum(to_cents(i.amount) for i in receipt.items) == to_cents(receipt.subtotal)


def test_the_aldi_tax_flags_are_understood():
    """NB is taxable and FA is not -- confirmed by the receipt's own arithmetic."""
    receipt = parse_receipt_text(ALDI)
    by_name = {i.description: i for i in receipt.items}
    assert by_name["24ct Paper Bowl"].taxable is True          # NB
    assert by_name["Green Peppers"].taxable is False           # FA
    assert by_name["Sourdough Loaf"].taxable is False          # "F A", split by OCR


def test_an_item_number_printed_before_the_name_becomes_the_sku():
    receipt = parse_receipt_text(ALDI)
    assert [i.sku for i in receipt.items] == [
        "343415", "356387", "385448", "356508", "341876"]
    assert [i.description for i in receipt.items] == [
        "24ct Paper Bowl", "Green Peppers", "Sourdough Loaf",
        "Broccoli Crowns", "Red Grapes LRW"]


def test_a_weight_line_is_not_an_item():
    """"(T) 0.02lb" must not parse as the amount 0.02 with the tax flag "lb"."""
    receipt = parse_receipt_text(ALDI)
    assert not any("lb" in (i.description or "") for i in receipt.items)
    assert "0.02" not in [i.amount for i in receipt.items]


def test_a_zero_rate_does_not_erase_the_tax_that_was_found():
    """Aldi prints one line per tax band, and the zero band prints last."""
    assert parse_receipt_text(ALDI).tax == "0.15"


def test_a_clipped_amount_due_still_gives_the_total():
    """OCR reads "AMOUNT DUE" as "AMOUNT D" often enough to matter."""
    assert parse_receipt_text(ALDI).total == "15.88"


def test_a_payment_line_is_not_counted_as_a_purchase():
    """OCR mangled "Mastercard" to "Mas*ercard", which no word list catches.

    What gives it away is the shape: no item number, and an amount equal to the
    receipt's own subtotal.
    """
    assert "Mas*ercard" not in [i.description for i in parse_receipt_text(ALDI).items]


def test_a_single_item_receipt_is_never_emptied_by_that_rule():
    """A lone item legitimately equals the total; dropping it would be worse."""
    receipt = parse_receipt_text(
        "CORNER SHOP\nBread 3.50\nTOTAL 3.50\n")
    assert [i.description for i in receipt.items] == ["Bread"]


def test_a_cash_rounding_line_is_not_a_purchase():
    """Walmart prints ROUNDING between TOTAL and CHANGE DUE, with an amount.

    It reached the item list and put four cents of nothing into the books --
    small, but it is money the receipt never spent, and it broke the one check
    that says whether a reading hangs together.
    """
    receipt = parse_receipt_text(
        "Walmart\n"
        "GV WHL MILK 007874203912 3.24 X\n"
        "SUBTOTAL 3.24\n"
        "TOTAL 3.42\n"
        "CASH TEND 5.00\n"
        "ROUNDING 0.04\n"
        "CHANGE DUE 1.58\n")
    assert [i.description for i in receipt.items] == ["GV WHL MILK"]
