"""Hiding the personal details a real receipt carries, for display only.

A shop receipt is not anonymous. A Costco receipt prints the member's number on
every copy; most card receipts print a masked account number; some print a
loyalty ID. None of that is needed to keep books, but all of it ends up on
screen once the receipt has been read -- so showing somebody the app, or
screenshotting it, hands over more than was intended.

``mask`` is the whole feature: replace every digit with an asterisk, keep every
letter. ``VISA ****4471`` becomes ``VISA ********``, which still says the card
was a Visa while saying nothing about which one. That is deliberately not a
blanket blackout, because a field that reads only ``********`` tells the owner
nothing either, and a masking option people turn off to get their work done
protects nobody.

**Nothing here may touch what is stored.** Masking is a property of the display
and of nothing else. The trap is specific and was live in this code before the
feature existed: the review pane's entry boxes are the same widgets the save
path reads back, so rendering a mask into one and then saving would write the
asterisks into the database and destroy the value. The rule is that a masked
field is shown read-only and its true value is passed through the save
untouched -- see ``ReceiptsPage._collect``.
"""

from __future__ import annotations

import re

from . import settings_store

# Fields whose stored value is masked when the option is on. Kept as a named
# set rather than checked inline so that adding a field is one edit here, and
# so a reader can see the complete list of what the option covers.
SENSITIVE_FIELDS = frozenset({"payment_method"})

_DIGIT = re.compile(r"\d")


def mask(text: str | None) -> str:
    """Every digit replaced by an asterisk; letters and punctuation kept."""
    return _DIGIT.sub("*", text or "")


def masking_on() -> bool:
    """Whether the option is currently on.

    Defaults to on. A privacy control that has to be discovered and switched on
    protects only the people who already knew to look for it, and the cost of
    the default being wrong is asymmetric: a hidden card number is an
    inconvenience, a shown one is a disclosure.
    """
    return settings_store.get("mask_sensitive", "1") == "1"


def toggle() -> bool:
    """Flip the option and return its new state."""
    new = not masking_on()
    settings_store.save({"mask_sensitive": "1" if new else "0"})
    return new


def apply(field: str, value: str | None) -> str:
    """The display form of ``value`` for ``field``."""
    if field in SENSITIVE_FIELDS and masking_on():
        return mask(value)
    return value or ""
