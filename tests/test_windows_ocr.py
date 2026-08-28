"""The offline Windows OCR engine, and the layout work that makes it usable.

The fixture these tests run against is the word list Windows OCR really
returned for the real Walmart receipt in ``test_real_receipt.py`` -- the same
photo that, before this engine existed, produced four red flags and nothing
else. Storing the words and their boxes rather than the photograph keeps the
test fast, deterministic, and runnable on a machine with no OCR language pack.

The ground truth for what the receipt actually says lives in
``test_real_receipt.py``; this file checks how much of it survives the trip
through OCR, and guards the specific repairs that were needed to get there.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.extract.receipt_text import parse_receipt_text
from app.extract.windows_ocr import (
    Word,
    WindowsOcrExtractor,
    group_rows,
    repair_amounts,
    rows_to_text,
)

FIXTURE = Path(__file__).parent / "fixtures" / "walmart_ocr_words.json"


@pytest.fixture(scope="module")
def words() -> list[Word]:
    raw = json.loads(FIXTURE.read_text(encoding="utf-8"))
    return [Word(w["t"], w["x"], w["y"], w["w"], w["h"]) for w in raw]


@pytest.fixture(scope="module")
def receipt(words):
    return parse_receipt_text(rows_to_text(group_rows(words)))


# ----------------------------------------------------- row reconstruction --


def test_words_are_regrouped_into_printed_rows(words):
    """The point of the whole module: descriptions and amounts back together.

    Windows OCR returns these words grouped into a column of names followed by
    a column of prices, so its own ``result.text`` never puts an item beside
    what it cost. Re-grouping by vertical position does.
    """
    rows = rows_to_text(group_rows(words)).splitlines()
    assert "BEDINABAG 840021403470 29.72 x" in rows
    assert "SUBTOTAL 141.94" in rows
    assert "TOTAL 149.44" in rows


def test_rows_come_out_in_top_to_bottom_printed_order(words):
    rows = group_rows(words)
    tops = [min(word.y for word in row) for row in rows]
    assert tops == sorted(tops)


def test_words_in_a_row_come_out_left_to_right(words):
    for row in group_rows(words):
        lefts = [word.x for word in row]
        assert lefts == sorted(lefts)


def test_a_row_is_grouped_by_text_size_not_by_a_pixel_count():
    """Doubling the capture distance must not change the grouping.

    The tolerance is a fraction of the median word height for exactly this
    reason -- a fixed pixel tolerance would merge every row of a photo taken
    from further away.
    """
    small = [Word("A", 0, 0, 10, 10), Word("B", 90, 2, 10, 10),
             Word("C", 0, 40, 10, 10)]
    large = [Word(w.text, w.x * 4, w.y * 4, w.width * 4, w.height * 4) for w in small]
    assert [[w.text for w in row] for row in group_rows(small)] == [["A", "B"], ["C"]]
    assert [[w.text for w in row] for row in group_rows(large)] == [["A", "B"], ["C"]]


def test_grouping_survives_a_receipt_that_is_not_perfectly_level():
    """A row drifting downwards across the page still reads as one row."""
    drifting = [Word(str(i), i * 100, i * 3, 20, 20) for i in range(6)]
    assert len(group_rows(drifting)) == 1


def test_no_words_means_no_rows():
    assert group_rows([]) == []


# --------------------------------------------------------- amount repairs --


@pytest.mark.parametrize("raw, fixed", [
    # The decimal point ends a glyph cluster, so the amount arrives split.
    ("COKE 049000050110 F 3. 04 x", "COKE 049000050110 F 3.04 x"),
    ("TIDE PODS 57 030772259580 17. 94 x", "TIDE PODS 57 030772259580 17.94 x"),
    # A leading zero read as the letter o, which then blocks the rejoin.
    ("AIM TP 5.50Z 063200000930 o. 98 X", "AIM TP 5.50Z 063200000930 0.98 X"),
    # Walmart's "O" tax flag read as a digit, which looked like a second number.
    ("ME DEPOSIT 000787423909 F 0.05 0", "ME DEPOSIT 000787423909 F 0.05 O"),
])
def test_the_three_ways_windows_ocr_mangles_a_price_are_repaired(raw, fixed):
    assert repair_amounts(raw) == fixed


def test_repairs_never_touch_the_item_description():
    """Character-confusion repair is gated to the amount column on purpose.

    'O' is a letter in half the product names on a Walmart receipt; a global
    O->0 substitution would turn GV TOASTED O into GV TOASTED 0 and DOVE into
    D0VE.
    """
    line = "DOVE BW 11OZ 011111064940 5.47 x"
    assert repair_amounts(line) == line


def test_a_bare_number_is_not_mistaken_for_a_split_price():
    # "ITEMS SOLD 21" has no decimal point, so nothing should be joined to it.
    assert repair_amounts("ITEMS SOLD 21") == "ITEMS SOLD 21"


# ---------------------------------------------- what the real receipt gives --


def test_the_printed_totals_are_read_exactly(receipt):
    """These three are the numbers the books actually depend on."""
    assert receipt.subtotal == "141.94"
    assert receipt.tax == "7.50"
    assert receipt.total == "149.44"


def test_the_payment_method_is_recognised(receipt):
    assert receipt.payment_method == "CASH"


def test_a_cropped_photo_reports_no_merchant_rather_than_inventing_one(receipt):
    """The top of this photo is missing, so there is no store name to read.

    Before the summary-line guard the fallback latched onto the first line with
    letters in it and reported the merchant as 'Items Sold 21'. Saying nothing
    is the correct answer here.
    """
    assert receipt.merchant is None
    assert receipt.purchased_at is None


def test_most_of_the_line_items_survive_offline_ocr(receipt):
    """20 of the 24 printed lines, which is what this engine is worth.

    Not 24: OCR loses the description on two lines, drops the leading digit of
    one amount and misreads another. Those are real losses, they leave the
    arithmetic short, and the review pane flags them -- which is the designed
    behaviour, not a bug to paper over by guessing.
    """
    assert len(receipt.items) == 20
    assert [item.description for item in receipt.items][:4] == [
        "BEDINABAG", "ME DEPOSIT", "GV IG SP", "ME DEPOSIT"]


def test_the_line_items_that_are_read_have_the_right_amounts(receipt):
    found = {(item.description, item.amount) for item in receipt.items}
    for description, amount in [
        ("BEDINABAG", "29.72"), ("GV TWIST MOP", "10.88"), ("COKE", "3.04"),
        ("CLX PLNGR", "17.76"), ("PROTEINSUPPL", "23.18"),
        ("TIDE PODS 57", "17.94"), ("GAIN", "0.97"), ("AIM TP 5.50Z", "0.98"),
    ]:
        assert (description, amount) in found


def test_the_upc_is_kept_out_of_the_description(receipt):
    """A 12-digit UPC beside every item must not end up in its name."""
    for item in receipt.items:
        assert not any(part.isdigit() and len(part) >= 9
                       for part in item.description.split())
    assert {item.sku for item in receipt.items} >= {"840021403470", "049000050110"}


def test_the_shortfall_is_reported_rather_than_hidden(receipt):
    """What OCR could not read must show up as missing money, not as zero.

    The items add up to 131.61 against a printed subtotal of 141.94. That gap
    is the whole reason this engine's reading always goes to review.
    """
    total = sum(float(item.amount) for item in receipt.items)
    assert round(total, 2) == 131.61
    assert float(receipt.subtotal) - total > 10


def test_the_reading_is_never_confident_enough_to_auto_confirm(receipt):
    from app.extract.windows_ocr import MAX_REPORTED_CONFIDENCE, _confidence

    assert _confidence(receipt) <= MAX_REPORTED_CONFIDENCE


# ------------------------------------------------------------ the engine --


def test_the_engine_reports_itself_available_on_this_machine():
    """Skipped rather than failed on a machine with no OCR language pack."""
    ok, detail = WindowsOcrExtractor().available()
    if not ok:
        pytest.skip(f"no Windows OCR here: {detail}")
    assert "Windows OCR" in detail


def test_an_unknown_language_is_refused_with_an_actionable_message():
    ok, detail = WindowsOcrExtractor(language="xx-XX").available()
    assert not ok
    assert "language pack" in detail
