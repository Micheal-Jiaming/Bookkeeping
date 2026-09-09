"""Unit tests for the pure logic: money, validation, rules, OCR text parsing."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

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
#
# **The card and transaction identifiers here are invented**, and must stay
# invented: the transaction certificate, the reference number and the card's
# last four digits are the receipt's own record of who paid, and nothing in
# these tests needs their real values -- what is being tested is that a line of
# that *shape* is recognised as summary rather than as a purchase. The real ones
# were transcribed in by hand and reached the public repository before anybody
# noticed; see §11.57.

WALMART_NAMELESS_ITEM = """\
Walmart
WM Supercenter
ST# 01788 OP# 009047 TE# 47 TR# 02197
# ITEMS SOLD 3
TC# 0000 0000 0000 0000 0000
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


def test_a_summary_word_may_not_hide_inside_a_product_name():
    """CASHEWS contains CASH, and was being discarded as a summary line.

    Plain substring matching threw the line away and its money with it -- the
    receipt simply came up short, with nothing to say why. Every short word on
    the summary list has the same trap waiting in it, so the match is on whole
    words (see also 11.22, the same trap in the categorisation rules).
    """
    receipt = parse_receipt_text(
        "Walmart\n"
        "CASHEWS 012345678905 4.99 X\n"
        "SUBTOTAL 4.99\n"
        "CASH TEND 10.00\n"
        "TOTAL 4.99\n")
    assert [(i.description, i.amount) for i in receipt.items] == [("CASHEWS", "4.99")]
    assert receipt.total == "4.99"


def test_the_real_summary_lines_are_still_recognised():
    """The whole-word rule must not cost the lines it was protecting."""
    from app.extract.receipt_text import _is_summary_line

    for line in ("CASH TEND 200.00", "SUBTOTAL 141.94", "T O T A L $65.32",
                 "TC# 0000 0000 0000", "REF # X0000X000000", "AMOUNT DUE 65.32",
                 "MASTERCARD- 0000", "B-TAXABLE @5.500% 0.15", "18 ITEMS"):
        assert _is_summary_line(line), line


def test_a_food_name_is_not_mistaken_for_a_payment_line():
    """The same substring trap as 11.44, one list over, and it cost a line.

    A payment line's amount can disqualify an unnamed item (11.34/11.45).
    Matching the payment words as substrings meant CHICKEN TENDERS matched
    TEND, so its 8.99 disqualified a genuine unnamed item priced the same, and
    the item vanished with no complaint. CASHEWS matches CASH and CARDAMOM
    matches CARD for the same reason.
    """
    receipt = parse_receipt_text(
        "Walmart\n"
        "CHICKEN TENDERS 012345678905 8.99 X\n"
        "LOOSE PRODUCE 8.99 X\n"
        "GV WHL MILK 007874203912 3.24 X\n"
        "SUBTOTAL 21.22\n"
        "TOTAL 21.22\n")
    assert [i.description for i in receipt.items] == [
        "CHICKEN TENDERS", "LOOSE PRODUCE", "GV WHL MILK"]


def test_the_real_payment_lines_are_still_recognised():
    """Whole-word matching must not cost the tender lines it exists to find."""
    from app.extract.receipt_text import _PAYMENT_RE

    for line in ("MCARD TEND 24.81", "CASH TEND 200.00", "CREDIT CARD $ 17.43",
                 "MASTERCARD- 0000 I 1", "VISA TEND 21.91"):
        assert _PAYMENT_RE.search(line.upper()), line


# --- Costco: two tax rates, and a summary block OCR partly destroyed --------
#
# Costco charges two rates on one receipt (Maine: 5.5% general, 8% prepared
# food) and prints a component line for each. Every earlier receipt here had a
# single rate, and the rule that handled Aldi's zero-rate line only guarded
# against a zero displacing a real figure -- its own comment predicted that two
# genuinely non-zero rates would still take the last one. COSTCO1 is that
# receipt, and it did: the tax read 2.29 when 5.15 was charged.

def test_two_non_zero_tax_rates_are_summed_not_overwritten():
    found = parse_receipt_text(
        "SUBTOTAL 188.37\nA 5.500% TAX 2.86\nF 8.00% TAX 2.29\n")
    assert found.tax == "5.15"


def test_a_stated_tax_line_beats_the_rate_breakdown():
    """A receipt that states the tax outright is believed over its components.

    Ordering matters here: the components print *below* the stated line on a
    Costco receipt, so a last-line-wins rule would take them.
    """
    found = parse_receipt_text(
        "TAX 5.15\nA 5.500% TAX 2.86\nF 8.00% TAX 2.29\n")
    assert found.tax == "5.15"


def test_a_zero_rate_component_still_does_not_erase_the_tax():
    """Aldi's case, which the replaced special case existed for."""
    found = parse_receipt_text(
        "SUBTOTAL 65.17\nB-Taxable @5.500% 0.15\nA-Taxable @0.00% 0.00\n")
    assert found.tax == "0.15"


def test_total_tax_is_not_read_as_the_grand_total():
    """OCR drops the second word of "TOTAL TAX 5.15", leaving "TOTAL 5.15".

    Nothing in the words can tell it from a grand total once TAX is gone, so it
    is told apart by arithmetic: it equals the sum of the rate components above
    it. On COSTCO1 this reported a $193.52 purchase as $5.15.
    """
    found = parse_receipt_text(
        "AMOUNT: $193.52\nA 5.500% TAX 2.86\nF 8.00% TAX 2.29\nTOTAL 5.15\n")
    assert found.total == "193.52"


def test_a_total_equal_to_the_tax_survives_without_rate_lines():
    """The guard above must not fire on a receipt that has no breakdown.

    A receipt whose total genuinely equals its tax is absurd but possible; the
    rule requires at least one rate component so ordinary receipts are untouched.
    """
    found = parse_receipt_text("TAX 5.15\nTOTAL 5.15\n")
    assert found.total == "5.15"


def test_costco_approval_amount_supplies_the_total():
    """Costco prints no plain "TOTAL <amount>" the OCR can read on COSTCO1 --
    the grand total line was scribbled out on the paper. The approval block's
    "AMOUNT: $193.52" is the same figure and is now accepted."""
    found = parse_receipt_text("AMOUNT: $193.52\nCHANGE 0.00\n")
    assert found.total == "193.52"


def test_a_misread_visa_tender_line_is_not_a_purchase():
    """Windows OCR reads Costco's "Visa" as "Vise", so the tender line escaped
    the payment words and was counted as an item carrying the grand total --
    $193.52 of nothing, more than the receipt's own subtotal."""
    found = parse_receipt_text(
        "1199652 BUTER CROISS 5.99 F\nVise 193.52\nCHANGE 0.00\n")
    assert [i.description for i in found.items] == ["BUTER CROISS"]


def test_vise_matching_is_whole_word():
    """The cost of the rule above must stay bounded: a product whose name
    merely contains the letters is still a purchase."""
    found = parse_receipt_text("VISEGRIP PLIERS 19.99\n")
    assert [i.description for i in found.items] == ["VISEGRIP PLIERS"]


def test_amount_cents_keeps_the_sign_of_a_small_refund():
    """"-0.15" has a whole part of "-0", and int("-0") is 0 -- multiplying that
    by 100 would drop the minus and turn a refund into a charge."""
    from app.extract.receipt_text import _amount_cents
    assert _amount_cents("-0.15") == -15
    assert _amount_cents("0.15") == 15
    assert _amount_cents("-2.00") == -200


def test_cents_text_keeps_the_sign_of_a_small_refund():
    """The mirror of _amount_cents's trap, on the way back out.

    Python floors, so -15 // 100 is -1 and -15 % 100 is 85: the obvious
    one-liner renders fifteen cents of refund as "-1.85".
    """
    from app.extract.receipt_text import _amount_cents, _cents_text
    assert _cents_text(-15) == "-0.15"
    assert _cents_text(-200) == "-2.00"
    assert _cents_text(0) == "0.00"
    for cents in (-201, -15, -1, 0, 1, 515, 19352):
        assert _amount_cents(_cents_text(cents)) == cents


# ------------------------------------------------- a logo OCR read badly

def test_a_misread_store_logo_is_still_recognised():
    """The Costco photograph reads as "Cosrco" -- one wrong letter in the logo.

    Before this, an exact search found nothing and the merchant fell through to
    the street address printed underneath, which is what a reviewer then saw in
    the Merchant field.
    """
    from app.extract.receipt_text import _find_merchant
    _raw, merchant = _find_merchant(
        ["Cosrco", "455 Anywhere Rd", "S Portland, ME 04074", "SELF-CHECKOUT"])
    assert merchant == "Costco"


def test_a_short_name_is_never_matched_loosely():
    """SHELF is one edit from SHELL, and a shelf is not a filling station.

    This is why the tolerant pass is restricted to names of six characters or
    more: below that, one character of difference is simply a different word.
    """
    from app.extract.receipt_text import _find_merchant
    _raw, merchant = _find_merchant(["SHELF PULL CLEARANCE", "123 Main St"])
    assert merchant != "Shell"


def test_two_characters_wrong_is_matched_only_on_the_same_shape():
    """"Cesrco" is what the app really gets, so two errors have to be reachable.

    The fences are what keep it honest: same length, same first letter, same
    last letter. Market Basket is a supermarket in the same state as the receipt
    behind this code and MARKET is two substitutions from TARGET, so the first
    letter doing the refusing is not a hypothetical.
    """
    from app.extract.receipt_text import _find_merchant, _same_shape

    _raw, merchant = _find_merchant(["Cesrco", "455 Anywhere Rd"])
    assert merchant == "Costco"

    assert _same_shape("CESRCO", "COSTCO")
    assert not _same_shape("MARKET", "TARGET"), "wrong first letter"
    assert not _same_shape("COSTCA", "COSTCO"), "wrong last letter"
    assert not _same_shape("SUBWAY", "SAFEWAY"), "wrong length"
    assert not _same_shape("CXXXCO", "COSTCO"), "three wrong is too many"


def test_the_two_character_pass_stops_above_the_item_lines():
    """The weaker the test, the closer to the top its evidence has to come from."""
    from app.extract.receipt_text import _find_merchant
    lines = ["QUICK STOP", "1 Main St", "Anytown ME",
             "CESRCO BRAND TOWELS 4.99", "MILK 3.99"]
    _raw, merchant = _find_merchant(lines)
    assert merchant == "Quick Stop"


def test_a_supermarket_that_merely_rhymes_is_not_relabelled():
    """The whole first line, not just the fuzzy helper, must refuse this."""
    from app.extract.receipt_text import _find_merchant
    _raw, merchant = _find_merchant(["MARKET BASKET", "1 Main St", "Anytown ME"])
    assert merchant != "Target"


def test_the_tolerant_pass_does_not_reach_the_item_lines():
    """A store name is printed at the top, so only the top is searched loosely.

    Letting it run down the receipt would give a near-miss a fresh chance on
    every line, and the lines are where the unusual words are.
    """
    from app.extract.receipt_text import _find_merchant
    lines = ["QUICK STOP", "1 Main St", "Anytown ME", "OPEN 24 HOURS",
             "CASHIER 04", "REG 2", "COSRCO BRAND TOWELS 4.99"]
    _raw, merchant = _find_merchant(lines)
    assert merchant == "Quick Stop"


def test_a_recognised_shop_is_distinguishable_from_a_guess():
    """The merge step needs to tell "Costco" from a fallback street address."""
    from app.extract.receipt_text import is_known_merchant
    assert is_known_merchant("Costco")
    assert not is_known_merchant("455 Scarborough Downs Rd")
    assert not is_known_merchant(None)


def test_one_edit_counts_substitution_insertion_and_deletion():
    """All three are things OCR does to a stylised logo."""
    from app.extract.receipt_text import _within_one_edit
    assert _within_one_edit("COSTCO", "COSTCO")
    assert _within_one_edit("COSRCO", "COSTCO")     # substituted
    assert _within_one_edit("COSTCOO", "COSTCO")    # inserted
    assert _within_one_edit("COSTC", "COSTCO")      # deleted
    assert not _within_one_edit("CORSCO", "COSTCO")  # two wrong
    assert not _within_one_edit("MARKET", "TARGET")
    assert not _within_one_edit("CO", "COSTCO")


# --------------------- what the user's confirmed receipts found (1.13.1)
#
# Every case below is a line the user's own corrections exposed: they went
# through all six receipts in the application by hand, and the differences
# between what they confirmed and what the engine read are recorded in
# Bookkeeping_record.md section 9.

def test_a_flag_column_printed_left_of_the_item_number():
    """Costco prints a letter in the margin; Walmart and Aldi print it right.

    Nothing had ever begun a line but a digit, so "E 96716 ORG SPINACH" kept
    the flag and the item number inside the description -- which is why that
    photograph returned most of its names verbatim and scored none correct.
    """
    receipt = parse_receipt_text("COSTCO\n\nE 96716 ORG SPINACH 4.69\nTOTAL 4.69\n")
    item = receipt.items[0]
    assert item.description == "ORG SPINACH"
    assert item.sku == "96716"


def test_the_flag_column_is_not_invented_where_there_is_none():
    """Aldi's layout has no left-hand letter, and must parse as it always did."""
    receipt = parse_receipt_text("ALDI\n\n356387 Green Peppers 2.69\nTOTAL 2.69\n")
    item = receipt.items[0]
    assert item.description == "Green Peppers"
    assert item.sku == "356387"


def test_a_word_is_not_mistaken_for_the_flag_column():
    """Only a lone letter is a flag; a real first word stays in the name."""
    receipt = parse_receipt_text("SHOP\n\nORG 12345 SPINACH 4.69\nTOTAL 4.69\n")
    assert receipt.items[0].description.startswith("ORG")


def test_subtotal_survives_losing_a_letter():
    """OCR returns Costco's "SUBTOTAL" as "SUBT TAL", splitting it at the O."""
    receipt = parse_receipt_text(
        "COSTCO\n\nE 96716 ORG SPINACH 4.69\nSUBT TAL 188.37\nTAX 5.15\n"
        "**** TOTAL 193.52\n")
    assert receipt.subtotal == "188.37"
    assert [i.description for i in receipt.items] == ["ORG SPINACH"], (
        "the subtotal line was also being counted as the largest purchase")


def test_a_total_printed_after_its_amount_is_read():
    """Walmart's card slip states it as "24.81 TOTAL PURCHASE".

    On Walmart2 that is the only legible statement of the total: the TOTAL line
    itself came back as "TOT AL 24 . a-I".
    """
    receipt = parse_receipt_text(
        "WALMART\n\nMILK 3.24\nSUBTOTAL 23.52\nTOT AL 24 . a-I\n"
        "24.81 TOTAL PURCHASE\n")
    assert receipt.total == "24.81"


def test_a_leading_amount_is_only_read_on_a_line_that_names_a_field():
    """Otherwise an item priced before its own name becomes the total."""
    receipt = parse_receipt_text("SHOP\n\n9.99 SOMETHING ODD\nTOTAL 12.00\n")
    assert receipt.total == "12.00"


def test_a_stated_tax_that_contradicts_the_breakdown_loses_to_the_arithmetic():
    """The Costco summary reads "TAX 5.16" where the paper says 5.15.

    Both readings sit on the same receipt and nothing in the text says which
    holds the misread digit, so the receipt decides: subtotal plus the summed
    components equals the printed total, and subtotal plus the stated figure is
    a cent over.
    """
    receipt = parse_receipt_text(
        "COSTCO\n\nSUBT TAL 188.37\nTAX 5.16\nA 5.500% TAX 2.86\n"
        "F 8.00% TAX 2.29\n**** TOTAL 193.52\n")
    assert receipt.tax == "5.15"
    assert receipt.subtotal == "188.37"
    assert receipt.total == "193.52"


def test_a_stated_tax_that_reconciles_is_left_alone():
    """The breakdown only wins when the stated figure fails the arithmetic."""
    receipt = parse_receipt_text(
        "SHOP\n\nSUBTOTAL 100.00\nTAX 5.15\nA 5.500% TAX 2.86\n"
        "F 8.00% TAX 2.29\nTOTAL 105.15\n")
    assert receipt.tax == "5.15"


def test_a_stated_tax_stands_when_the_arithmetic_cannot_decide():
    """No subtotal read means no evidence, and no evidence means no override."""
    receipt = parse_receipt_text(
        "SHOP\n\nTAX 5.16\nA 5.500% TAX 2.86\nF 8.00% TAX 2.29\nTOTAL 193.52\n")
    assert receipt.tax == "5.16"


def test_an_aldi_receipt_that_states_its_tax_is_untouched():
    """Aldi prints "B-Taxable 0.15" with no percentage, so there is no
    breakdown to weigh it against and nothing to settle."""
    receipt = parse_receipt_text(
        "ALDI\n\nSUBTOTAL 65.17\nB-Taxable 0.15\nA-Taxable 0.00\n"
        "AMOUNT DUE 65.32\n")
    assert receipt.tax == "0.15"
    assert receipt.total == "65.32"


# ------------------- the receipt's own item count (1.14.0)
#
# Every chain here prints one, in its own way. The subtotal says how much money
# is unaccounted for; this says how many lines to go looking for, which is what
# tells a reviewer when they have finished.

@pytest.mark.parametrize("line, expected", [
    ("ITEMS SOLD 21", 21),          # Walmart
    ("# ITEMS SOLD 3", 3),          # Walmart, with the hash
    ("18 ITEMS", 18),               # Aldi, count first
    ("7 ITEMS", 7),
    # Costco prints "TOTAL NUMBER OF ITEMS SOLD = 16" and OCR destroys both
    # keywords, leaving "NUMBER OF" intact.
    ("TOTAL NUMBER OF 1 EMS sot-c 16", 16),
])
def test_the_printed_item_count_is_read_in_each_chains_dialect(line, expected):
    from app.extract.receipt_text import _find_items_sold
    assert _find_items_sold([line]) == expected


def test_a_bare_sold_line_is_not_trusted_for_a_count():
    """The second Costco pass returns "sold 6" for a line that reads 16.

    A pattern loose enough to catch that would import a wrong count, and a wrong
    count is worse than none: the flag it raises sends the reviewer hunting for
    lines that are not missing.
    """
    from app.extract.receipt_text import _find_items_sold
    assert _find_items_sold(["sold 6"]) is None


def test_no_printed_count_is_not_an_error():
    from app.extract.receipt_text import _find_items_sold
    assert _find_items_sold(["MILK 3.24", "TOTAL 3.24"]) is None
    assert _find_items_sold(["ITEMS SOLD 0"]) is None, "a till that sold nothing"


def test_deposits_and_discounts_are_not_items_sold():
    """Walmart prints 21 sold against 24 lines; the difference is three Maine
    bottle deposits. Verified against all six confirmed receipts, where the
    printed count then agrees exactly."""
    from app.validate import _lines_missing
    items = [{"description": "MILK", "is_discount": 0},
             {"description": "ME DEPOSIT", "is_discount": 0},
             {"description": "MANAGER COUPON", "is_discount": 1}]
    assert _lines_missing(items, 1) is None, "one purchase, one sold"
    assert _lines_missing(items, 3) == 2


def test_reading_more_lines_than_were_sold_is_not_flagged():
    """Real evidence of an invented line, but also what a misread count looks
    like -- and a flag sending a reviewer after a line that does not exist is
    worse than no flag."""
    from app.validate import _lines_missing
    items = [{"description": f"ITEM {i}", "is_discount": 0} for i in range(5)]
    assert _lines_missing(items, 3) is None


def test_the_missing_line_flag_names_the_shortfall():
    flags = check(
        purchased_at="2026-09-06", total_cents=19352, subtotal_cents=18837,
        tax_cents=515, tip_cents=None,
        items=[{"description": f"ITEM {i}", "amount_cents": 100} for i in range(12)],
        confidence=0.9, items_sold=16,
    )
    assert any("at least 4 line(s) are missing" in flag for flag in flags)


def test_a_receipt_with_every_line_read_raises_no_count_flag():
    flags = check(
        purchased_at="2026-08-29", total_cents=1743, subtotal_cents=1743,
        tax_cents=0, tip_cents=None,
        items=[{"description": f"ITEM {i}", "amount_cents": 249} for i in range(7)],
        confidence=0.9, items_sold=7,
    )
    assert not any("items were sold" in flag for flag in flags)


# ---------------- capital O read as zero (1.17.0)
#
# The largest single defect left after RapidOCR landed: five of the fourteen
# wrong item names across the six confirmed receipts are this one confusion, and
# it is not a model problem -- O and 0 are the same ink in a till printer's
# font, so a bigger recogniser meets exactly the same ambiguity. Every reading
# below came off a real photograph.

@pytest.mark.parametrize("line, expected", [
    # A zero inside an otherwise all-capital word.
    ("PR0TEINSUPPL 23.18 X", "PROTEINSUPPL"),
    ("GVC0RNSTARCH 1.92 O", "GVCORNSTARCH"),
    # The unit, where the digit in front of it stops the whole-word rule.
    ("DOVE BW 110Z 5.47 X", "DOVE BW 11OZ"),
    ("AIM TP 5.50Z 0.98 X", "AIM TP 5.5OZ"),
    ("EQJELLUBE80Z 4.94 X", "EQJELLUBE8OZ"),
    # Both rules on one line, which is the common shape.
    ("GV C0RN 160Z 1.28 O", "GV CORN 16OZ"),
])
def test_a_zero_the_printer_meant_as_a_letter_is_put_back(line, expected):
    receipt = parse_receipt_text(f"WALMART\n{line}\nSUBTOTAL 99.99\n")
    assert receipt.items[0].description == expected


@pytest.mark.parametrize("text", [
    "756809105667",         # a barcode standing in for a name, as Walmart2 prints it
    "002200003672",
    "WD40 LUBRICANT",       # a real digit inside a capitalised code
    "CO2 CARTRIDGE",
    "VITAMIN D3",
    "COKE 2L",
    "9218 RED ONIONS",      # a Costco item number
    "Green Onions",         # mixed case: Aldi's layout is left alone
    "100",
    "0",
])
def test_a_digit_that_is_really_a_digit_is_not_turned_into_a_letter(text):
    """The rules are narrow on purpose, and this is the half that matters.

    A missed repair leaves a name slightly wrong and a reviewer can see it. A
    wrong repair invents a plausible name nobody will question -- and on a
    barcode it names a different product entirely -- so anything the rules
    cannot be sure of they decline, including every token carrying a digit
    other than zero.

    Tested on the function rather than through the parser because most of these
    never reach it as a description: the parser lifts a 9-to-14 digit run out as
    the barcode and a leading count out as the quantity long before this runs.
    """
    from app.extract.receipt_text import _repair_letter_o

    assert _repair_letter_o(text) == text


def test_an_item_number_survives_the_line_it_shares_with_a_repair():
    """Costco prints the item number in the description's own column, so the
    repair and the number are the same string until the parser splits them."""
    receipt = parse_receipt_text(
        f"COSTCO\n9218 GVC0RNSTARCH 4.89 O\nSUBTOTAL 4.89\n")
    assert receipt.items[0].sku == "9218"
    assert receipt.items[0].description == "GVCORNSTARCH"


def test_the_repair_cannot_reach_an_amount():
    """It runs on the description once the amount is off the line. Pinned
    because the two are cut from the same string a few lines apart."""
    receipt = parse_receipt_text("WALMART\nGVC0RNSTARCH 10.05 X\nSUBTOTAL 10.05\n")
    assert receipt.items[0].amount == "10.05"
    assert receipt.subtotal == "10.05"


# ---------------- who paid, and with what (1.17.0)
#
# A KFC receipt prints "Cashier: Zackariah" near the top. `_find_payment` used
# plain substring matching, so CASHIER matched CASH and it returned before ever
# reaching "Card Type: Mastercard" at the bottom. Aldi's "Your cashier today was
# Ismail" did the same, so three of the eight photographs recorded a card
# purchase as cash. Third appearance of the trap `_whole_words` exists for.

@pytest.mark.parametrize("line", [
    "Cashier: Zackariah",                 # KFC
    "Cashier:Zackariah",                  # ...as OCR actually returns it
    "Your cashier today was Ismail",      # Aldi
    "CASHEWS 4.99 F",                     # the original 11.9.4 defect
    "CASHBACK AVAILABLE",
])
def test_a_word_that_merely_contains_cash_is_not_a_payment(line):
    from app.extract.receipt_text import _find_payment

    assert _find_payment([line]) is None


def test_the_tender_line_below_a_cashier_name_is_the_one_that_counts():
    """The whole KFC receipt in miniature: the decoy is printed first."""
    from app.extract.receipt_text import _find_payment

    assert _find_payment(["Cashier:Zackariah",
                          "ETender Credit $12.84",
                          "Card Tyne: Mastercard"]) == "CREDIT"


def test_a_receipt_really_paid_in_cash_still_says_so():
    from app.extract.receipt_text import _find_payment

    assert _find_payment(["CASH TEND 200.00", "CHANGE DUE 58.06"]) == "CASH"


# --- the four digits, which must be the card's and not something else --------

def test_the_approval_code_is_not_mistaken_for_the_card():
    """It used to take the last four digits *anywhere* on the line, so the
    approval code at the end was reported as the card. A card number nobody can
    check is the kind of wrong that survives: right shape, no arithmetic to
    contradict it. Only digits sitting against the brand are trusted.

    The card and the approval code are **invented, and must stay invented**
    (section 11.57) -- but they must also stay *different from each other*, or
    this passes for the wrong reason."""
    from app.extract.receipt_text import _find_payment

    assert _find_payment(["MASTERCARD- 0000 I 1 APPR#009999"]) == "MASTERCARD ****0000"


@pytest.mark.parametrize("line, expected", [
    ("MASTERCARD- 0000 I 1", "MASTERCARD ****0000"),
    ("US DEBIT- 0000 I 0", "DEBIT ****0000"),
    ("VISA ****1234", "VISA ****1234"),
    ("MASTERCARD **************0000 PIN", "MASTERCARD ****0000"),
    # An amount printed against the brand is not a card number.
    ("CREDIT 1234.56", "CREDIT"),
    ("MASTERCARD PURCHASE 1234", "MASTERCARD"),
    # Nothing card-shaped after the brand: a brand and no number beats a guess.
    ("Mastercard 65.32", "MASTERCARD"),
    ("Credit Card $ 65.32", "CREDIT"),
    ("Visa Resp: APPROVED", "VISA"),
    ("DEBIT TEND 6.39", "DEBIT"),
])
def test_only_digits_against_the_brand_are_read_as_the_card(line, expected):
    from app.extract.receipt_text import _find_payment

    assert _find_payment([line]) == expected


# ---------------- fast food: the total is named after the counter (1.17.0)
#
# The first restaurant receipt tested here, and it has no line saying TOTAL at
# all. The amount charged sits against CARRY OUT, and the tax is printed
# *above* it rather than below. Only CARRY OUT is confirmed against a real
# photograph; the siblings are the same label in the same slot.

@pytest.mark.parametrize("label", [
    "CARRY OUT", "Carry Out", "CARRY-OUT", "CARRYOUT",
    "TAKE OUT", "DINE IN", "DRIVE THRU", "DRIVE-THROUGH",
])
def test_the_counter_label_carries_the_total(label):
    receipt = parse_receipt_text(
        f"KFC/TB\nBig Box 11.89\nTax 0.95\n{label} $12.84\n")
    assert receipt.total == "12.84"
    assert receipt.tax == "0.95"


def test_a_bag_charged_at_the_counter_is_not_the_total():
    """The whole safety of the rule. The label has to be followed by the money
    with nothing in between -- otherwise a fifty-cent bag overwrites the total
    of the receipt, which is the worst single field to get wrong."""
    receipt = parse_receipt_text(
        "KFC/TB\nBig Box 11.89\nCARRY OUT BAG 0.10\nTOTAL 12.84\n")
    assert receipt.total == "12.84"
    assert ("CARRY OUT BAG", "0.10") in [(i.description, i.amount) for i in receipt.items]


def test_to_go_is_deliberately_not_a_total_label():
    """Two of the commonest short words in English, and it survives the
    space-stripped comparison as TOGO. No photograph here justifies the risk."""
    receipt = parse_receipt_text("KFC/TB\nBig Box 11.89\nTO GO 12.84\n")
    assert receipt.total != "12.84"


def test_the_whole_fast_food_shape_reconciles():
    """Tax above the total, no subtotal line anywhere, and four unpriced
    components listed under the one item that carries a price. Every value here
    is off KFC1.jpg except the card block, which is invented and must stay so.
    """
    receipt = parse_receipt_text(
        "KFC/TB\nRestaurant #G000000\nTicket #0000\n2026-08-28\n"
        "Big Box 11.89\nInd Mash/ Gvy\nInd Pot Wedge\nBiscuit\n"
        "Md Bj MtnDew\nTax 0.95\nCARRY OUT $12.84\n"
        "ETender Credit $12.84\nChange $0.00\n")
    assert receipt.total == "12.84"
    assert receipt.tax == "0.95"
    assert receipt.purchased_at == "2026-08-28"
    # The four components carry no price, and none is invented for them.
    assert [(i.description, i.amount) for i in receipt.items] == [("Big Box", "11.89")]
    assert to_cents(receipt.total) - to_cents(receipt.tax) == to_cents("11.89")


# ---------------- a zero in the tax-flag column (1.17.0)
#
# Walmart flags a non-taxable line with the letter O, and RapidOCR runs it into
# the amount as a digit: "0.05 O" arrives as "0.050", which is not a
# two-decimal amount, so the line was thrown away and its money with it. Three
# receipts lost a bottle deposit this way.
#
# The older engine never hit it, and why is the useful part: Windows OCR returns
# one box per WORD, so the space is rebuilt from geometry and it produced
# "0.05 O" on all eleven deposit lines. RapidOCR returns one box per LINE, so
# the space survives only if the recogniser emits it.

def test_a_zero_in_the_flag_column_is_the_letter_o():
    from app.extract.receipt_text import _TRAILING_AMOUNT

    m = _TRAILING_AMOUNT.search("ME DEPOSIT 000787423909 F 0.050")
    assert m is not None, "the line is discarded entirely without this"
    assert m.group("amount") == "0.05"
    assert m.group("flag") == "0"


def test_the_deposit_survives_and_keeps_its_taxability():
    """`O` means non-taxable at Walmart. Left as a bare 0 the lookup returns
    None -- "the receipt did not say" -- which loses the only statement it made.
    """
    receipt = parse_receipt_text(
        "Walmart\nCOKE 04900050110 F 3.04 X\n"
        "ME DEPOSIT 000787423909 F 0.050\nSUBTOTAL 3.09\n")
    deposits = [i for i in receipt.items if "DEPOSIT" in i.description.upper()]
    assert [(i.amount, i.taxable) for i in deposits] == [("0.05", False)]


def test_the_spaced_form_the_other_engine_produces_still_works():
    from app.extract.receipt_text import _TRAILING_AMOUNT

    m = _TRAILING_AMOUNT.search("ME DEPOSIT 000787423909 F 0.05 O")
    assert (m.group("amount"), m.group("flag")) == ("0.05", "O")


@pytest.mark.parametrize("line", [
    "SOMETHING 123.456",     # only 0 is allowed, so this is still not an amount
    "SOMETHING 1.234",
])
def test_only_a_zero_is_read_as_a_flag_never_another_digit(line):
    from app.extract.receipt_text import _TRAILING_AMOUNT

    assert _TRAILING_AMOUNT.search(line) is None


def test_a_weight_is_still_not_an_amount_with_a_flag():
    """The rule that had to survive: two-letter flags still need their space,
    or "(T) 0.02lb" reads as 0.02 carrying the tax flag "lb"."""
    from app.extract.receipt_text import _TRAILING_AMOUNT

    assert _TRAILING_AMOUNT.search("(T) 0.02lb") is None

