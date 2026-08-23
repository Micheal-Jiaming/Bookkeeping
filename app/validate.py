"""Arithmetic and sanity checks on a reading, producing review flags.

The point of this module is that a bad reading should be *loud*. An OCR pass
that turns 8.99 into 3.99 produces perfectly well-formed JSON; the only thing
that catches it is checking that the parts still add up to the printed total.
Every check returns a human-readable message that goes straight into the review
UI, because "items sum to 43.71 but the printed total is 47.09 (off by 3.38)" is
actionable and "confidence: low" is not.
"""

from __future__ import annotations

from datetime import date, datetime

# Receipts legitimately disagree with their own arithmetic by a cent or two
# (per-item rounding, weighted goods). Anything larger is a reading error.
TOLERANCE_CENTS = 5
LOW_CONFIDENCE = 0.6


def check(
    *,
    purchased_at: str | None,
    total_cents: int | None,
    subtotal_cents: int | None,
    tax_cents: int | None,
    tip_cents: int | None,
    items: list[dict],
    confidence: float | None,
    duplicate_of: int | None = None,
) -> list[str]:
    """Return every problem found with this reading; empty means it looks sound."""
    flags: list[str] = []

    if duplicate_of is not None:
        flags.append(
            f"This image is byte-for-byte identical to receipt #{duplicate_of}, "
            "already in the books. Confirm only if it is a genuine second purchase."
        )

    if total_cents is None:
        flags.append("No total was found on the receipt. Enter it by hand.")
    elif total_cents <= 0:
        flags.append(
            f"The total reads {total_cents / 100:.2f}, which is not a positive amount."
        )

    if purchased_at is None:
        flags.append("No purchase date was found. Enter it by hand.")
    else:
        parsed = _parse_date(purchased_at)
        if parsed is None:
            flags.append(f"The date '{purchased_at}' is not a valid YYYY-MM-DD date.")
        elif parsed > date.today():
            flags.append(f"The date {purchased_at} is in the future.")
        elif parsed.year < 2000:
            flags.append(f"The date {purchased_at} looks misread (before 2000).")

    if not items:
        flags.append("No line items were recognised, so the expense cannot be itemised.")

    items_sum = sum(item.get("amount_cents") or 0 for item in items)
    missing_amounts = [
        item for item in items if item.get("amount_cents") is None
    ]
    if missing_amounts:
        flags.append(
            f"{len(missing_amounts)} line item(s) have no amount: "
            + ", ".join(
                (item.get("description") or "(no description)")[:28]
                for item in missing_amounts[:4]
            )
        )

    # The central arithmetic check. Prefer subtotal when it was printed, since
    # comparing items against the total requires guessing whether tax is
    # included in the line prices.
    if items and subtotal_cents is not None:
        delta = items_sum - subtotal_cents
        if abs(delta) > TOLERANCE_CENTS:
            flags.append(
                f"Line items sum to {items_sum / 100:.2f} but the subtotal reads "
                f"{subtotal_cents / 100:.2f} (off by {abs(delta) / 100:.2f})."
            )
    elif items and total_cents is not None:
        expected = items_sum + (tax_cents or 0) + (tip_cents or 0)
        delta = expected - total_cents
        if abs(delta) > TOLERANCE_CENTS:
            flags.append(
                f"Line items plus tax come to {expected / 100:.2f} but the total "
                f"reads {total_cents / 100:.2f} (off by {abs(delta) / 100:.2f})."
            )

    if (
        subtotal_cents is not None
        and total_cents is not None
        and tax_cents is not None
    ):
        delta = subtotal_cents + tax_cents + (tip_cents or 0) - total_cents
        if abs(delta) > TOLERANCE_CENTS:
            flags.append(
                f"Subtotal + tax = {(subtotal_cents + tax_cents + (tip_cents or 0)) / 100:.2f}, "
                f"which does not match the total of {total_cents / 100:.2f}."
            )

    if tax_cents is not None and total_cents:
        # A tax line larger than half the bill is a decimal-point misread.
        if tax_cents > total_cents * 0.5:
            flags.append(
                f"Tax of {tax_cents / 100:.2f} is implausibly large against a total "
                f"of {total_cents / 100:.2f}."
            )

    if confidence is not None and confidence < LOW_CONFIDENCE:
        flags.append(
            f"The engine reported low confidence ({confidence:.2f}) in this reading."
        )

    return flags


def _parse_date(value: str) -> date | None:
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None
