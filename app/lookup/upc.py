"""Turning the number printed on a receipt into a barcode a database will accept.

A Walmart receipt prints twelve digits beside each line. They look exactly like
a UPC-A barcode and they are not one: Walmart prints the first *eleven* digits
of the UPC and pads the twelfth column with a zero, dropping the check digit
that every product database validates before it will answer.

That single detail decides whether this feature works. Of the eight codes on the
receipt this project is measured against, seven fail UPC-A validation as
printed; the eighth passes only because its true check digit happens to be zero.
Querying the printed number returns "bad request" or "not found" -- which is
indistinguishable from "this product does not exist", so the failure is silent
and looks like the lookup service being useless.

Recomputing the check digit from the first eleven digits fixes all seven.
"""

from __future__ import annotations

# The first digit of a UPC says who assigned the rest of it. Codes in these
# number systems are handed out by the shop, not by GS1: random-weight goods
# (2), a retailer's own internal use (4) and coupons (5 and 9). They are unique
# only inside that chain, so no global database can resolve them and asking is
# pure latency. Bakery bread priced by the loaf is the usual example -- on this
# receipt FRENCH BREAD is 200989000000.
LOCALLY_ASSIGNED = frozenset("2459")


def check_digit(eleven: str) -> str:
    """The twelfth digit of a UPC-A, computed from the first eleven."""
    total = sum(int(d) * (3 if i % 2 == 0 else 1) for i, d in enumerate(eleven))
    return str((10 - total % 10) % 10)


def is_valid(code: str) -> bool:
    """True when ``code`` is twelve digits whose check digit already agrees."""
    return len(code) == 12 and code.isdigit() and check_digit(code[:11]) == code[11]


def barcode_for(printed: str | None) -> str | None:
    """The UPC worth querying for the number printed beside a receipt line.

    A code that already validates is returned untouched, because a receipt from
    another chain may well print the real twelve-digit barcode and rebuilding
    that would corrupt a working code. Anything else is rebuilt from its first
    eleven digits, which is the Walmart case.

    ``None`` for anything unusable -- the wrong length, non-numeric, or in a
    locally assigned number system that no global database can resolve.
    """
    digits = "".join(c for c in (printed or "") if c.isdigit())
    if len(digits) != 12 or digits[0] in LOCALLY_ASSIGNED:
        return None
    return digits if is_valid(digits) else digits[:11] + check_digit(digits[:11])
