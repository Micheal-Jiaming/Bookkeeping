"""Expanding till shorthand offline, for the receipts no barcode can reach.

Every case here comes off the real Costco receipt in ``pictures\\COSTCO1.jpg``.
The photograph itself is not in git -- it is somebody's shopping -- so the
printed names are transcribed here and the amounts and card details are not.

The tests that matter most are the ones asserting what is *not* expanded. A
wrong expansion is shown to the reviewer as though it were the product, so a
plausible guess that happens to be wrong is worse than leaving the shorthand
alone, and these lock in the refusals.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.lookup.shorthand import expand  # noqa: E402


# ------------------------------------------------------------- what expands

@pytest.mark.parametrize("printed, expected", [
    ("ORG SPINACH", "Organic Spinach"),
    ("BUTER CROISS", "Butter Croissants"),
    ("KS COFFEE", "Kirkland Signature Coffee"),
    ("KS CAGE FREE", "Kirkland Signature Cage Free"),
])
def test_the_abbreviations_costco_prints_are_expanded(printed, expected):
    assert expand(printed, "Costco") == expected


def test_a_store_brand_run_into_the_next_word_is_split():
    """The till prints "KSDAILY", with no space, and one token is unreadable."""
    assert expand("KSDAILY 500C", "Costco") == "Kirkland Signature Daily 500C"
    assert expand("KSBLUEDISH", "Costco") == "Kirkland Signature Bluedish"


def test_a_pack_size_is_separated_from_its_number():
    """"500CT" reaches a translator as a word it has never seen; "500 count"
    reaches it as a quantity."""
    assert expand("KS CAL 500CT", "Costco") == "Kirkland Signature Cal 500 count"


def test_a_shouted_word_is_made_readable():
    """The expansion is shown to a person, and the till prints in capitals."""
    assert expand("ORG SPINACH", "Costco") == "Organic Spinach"


# --------------------------------------------------------- what does not

@pytest.mark.parametrize("printed", [
    "SHRIMP", "MUSHROOMS", "FRESH GARLIC", "RED ONIONS", "BEEF STEW",
    "DRUMSTICKS",
])
def test_plain_english_is_left_completely_alone(printed):
    """None rather than a copy: a line that needed no help records no expansion."""
    assert expand(printed, "Costco") is None


@pytest.mark.parametrize("printed, uncertain", [
    ("GP WINGS", "GP"),
    ("KS CAL 500CT", "Cal"),
    ("KSBLUEDISH", "Bluedish"),
    ("KS FISH 400", "400"),
])
def test_the_part_nobody_can_be_sure_of_is_not_guessed(printed, uncertain):
    """GP, CAL and BLUEDISH each have a likely reading and no certain one.

    Chicken wings, calcium, dish soap -- all plausible, none of them printed
    anywhere on the paper. The expansion is displayed as though it were the
    product, so being nearly right about what somebody bought is the failure
    this declines to risk: the uncertain fragment survives as printed, and the
    reviewer decides.
    """
    assert uncertain in (expand(printed, "Costco") or printed)


def test_a_chain_s_own_shorthand_is_not_applied_to_another_chain():
    """"KS" is Kirkland Signature at Costco and two letters anywhere else."""
    assert expand("KS COFFEE", "Walmart") is None
    assert expand("KSDAILY 500C", "") is None


def test_the_chain_neutral_abbreviations_still_apply_anywhere():
    """ORG is organic on any receipt printed in English."""
    assert expand("ORG SPINACH", "Walmart") == "Organic Spinach"


def test_empty_input_is_handled_rather_than_crashing():
    assert expand("", "Costco") is None
    assert expand("   ", "Costco") is None
    assert expand("SHRIMP", None) is None
