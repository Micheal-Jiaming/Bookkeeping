"""Money handling.

Every monetary amount in this application is stored and computed as an integer
number of cents. Floats are never used for money: 0.1 + 0.2 != 0.3 in binary
floating point, and a bookkeeping ledger that cannot make its own totals add up
is worthless. Conversion to/from a decimal string happens only at the edges
(parsing extractor output, rendering to the UI, CSV export).
"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

# Matches the money-ish substrings that turn up in OCR text: "$12.34", "12,345.67",
# "-4.00", "3.5", "(2.00)" (parenthesised negative). Currency symbols, thousands
# separators and trailing junk are stripped before conversion.
_NUMBER_RE = re.compile(r"-?\(?\$?\s*\d[\d,]*(?:\.\d{1,2})?\)?")


def to_cents(value: object) -> int | None:
    """Convert a number or money-like string to integer cents.

    Returns None when the value is absent or cannot be read as a number, so the
    caller can distinguish "the receipt had no tip line" from "the tip was $0".
    """
    if value is None or value == "":
        return None
    if isinstance(value, int) and not isinstance(value, bool):
        # Bare ints from an extractor are dollars, not cents.
        return value * 100
    if isinstance(value, float):
        dec = Decimal(str(value))
    else:
        text = str(value).strip()
        negative = text.startswith("(") and text.endswith(")")
        text = text.strip("()")
        text = re.sub(r"[^\d.\-]", "", text)
        if text in ("", "-", ".", "-."):
            return None
        try:
            dec = Decimal(text)
        except InvalidOperation:
            return None
        if negative:
            dec = -dec
    return int((dec * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def from_cents(cents: int | None) -> str:
    """Render cents as a plain decimal string, e.g. -1234 -> '-12.34'."""
    if cents is None:
        return ""
    sign = "-" if cents < 0 else ""
    cents = abs(int(cents))
    return f"{sign}{cents // 100}.{cents % 100:02d}"


def find_amounts(text: str) -> list[int]:
    """Every money-like amount in a blob of OCR text, in order, as cents."""
    out: list[int] = []
    for match in _NUMBER_RE.finditer(text):
        cents = to_cents(match.group(0))
        if cents is not None:
            out.append(cents)
    return out
