"""Measure how well the reader actually reads, against receipts a human checked.

## Why this exists

Until now this project's accuracy figures lived in prose: "16 of 18 header
fields", "$28.47 unaccounted", "12 of 20 names". Each was true when it was
written, and none of them can be reproduced -- the set of photographs a figure
covered was never recorded beside it, so a later reader cannot tell whether the
number moved because the code changed or because a sixth receipt joined the set.
A figure that cannot be recomputed is a claim, not a measurement.

## Ground truth and baseline are deliberately different things

**Ground truth** is what the paper actually says, established by a human reading
it. It measures *accuracy*, and it is the only thing that can tell you the
reader is wrong.

**A baseline** is what this code produced on some particular day. It measures
*regression*, and being machine-generated it is perfectly capable of enshrining
a mistake as the expected answer.

They live in separate files and are never merged. A harness that quietly
promotes its own last output to truth reports a clean pass forever while
drifting arbitrarily far from the receipt.

## Why invented lines are counted, and not just missing ones

Two earlier attempts to improve the reader cut the unaccounted money sharply --
$44.02 to $15.57, then to $2.77 -- by inventing five and four line items that
are not printed on the paper. Both were rejected. Scored on the money gap alone,
both would have looked like large wins.

So a reading is scored on three numbers and never on one: lines matched, lines
missed, and lines **invented**. A change that fabricates data has to make this
report look worse, or the report is not merely useless but actively rewards the
failure it exists to catch.

## Why the photographs are not in the repository

They are somebody's shopping and their payment method, so `pictures\\` is
gitignored and this harness skips whatever is not on the disk in front of it
rather than failing. The truth file *is* committed -- it holds no card number,
no address and no transaction code -- and each entry carries the SHA-256 of the
photograph it describes, so a truth record can never drift onto a different
image without saying so.
"""

from __future__ import annotations

import json
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.money import to_cents                    # noqa: E402

PHOTO_DIR = ROOT / "pictures"
TRUTH_PATH = ROOT / "tests" / "fixtures" / "receipts_truth.json"
BASELINE_PATH = ROOT / "tests" / "fixtures" / "accuracy_baseline.json"

# The header fields worth scoring. Deliberately not `confidence` or `currency`:
# one is the reader's opinion of itself and the other is inferred, so neither is
# a fact somebody could check off the paper.
HEADER_FIELDS = ("merchant", "purchased_at", "subtotal", "tax", "total")

MONEY_FIELDS = ("subtotal", "tax", "total")


@dataclass(frozen=True)
class Truth:
    """What a human confirmed is printed on one photographed receipt.

    ``header`` holds only the fields somebody actually checked. A key present
    with a value of ``None`` means *verified absent* -- Walmart1 was
    photographed with its top out of frame, so it genuinely has no merchant and
    no date, and a reader that supplies one is wrong. A key that is simply
    missing means nobody has checked that field yet, and it is not scored at
    all. Keeping those two cases apart is the whole reason this is a dict rather
    than a row of optional attributes.
    """

    photo: str
    sha256: str | None = None
    header: dict[str, str | None] = field(default_factory=dict)
    # None means "not transcribed yet", which is not the same as an empty list.
    lines: tuple[tuple[str, int], ...] | None = None
    # Who established this record. Recorded rather than assumed because a truth
    # transcribed by reading the image with another model is not the same
    # evidence as a truth transcribed by a person holding the paper: two
    # readers of the same photograph can be wrong in the same way, and a
    # measurement is only as good as the provenance of what it measures
    # against. Anything not marked `human` is provisional.
    verified_by: str = "unverified"

    @property
    def has_lines(self) -> bool:
        return self.lines is not None


@dataclass
class Score:
    """One reading, measured. Self-checks first, then whatever truth can settle."""

    photo: str

    # Self-checks. These need no ground truth at all, because a receipt states
    # enough about itself to be caught contradicting itself.
    items_read: int = 0
    items_sum_cents: int = 0
    subtotal_cents: int | None = None
    unaccounted_cents: int | None = None
    arithmetic_ok: bool | None = None
    header_present: int = 0

    # Truth-based. A zero here means "nothing was checked", not "nothing was
    # right", so these are only meaningful beside `header_checked` and
    # `truth_lines`.
    truth_lines: bool = False
    header_checked: int = 0
    header_correct: int = 0
    lines_matched: int = 0
    lines_missed: int = 0
    lines_invented: int = 0
    names_exact: int = 0

    notes: list[str] = field(default_factory=list)


def normalise_name(text: str | None) -> str:
    """Upper-case and collapse runs of whitespace. Nothing cleverer than that.

    Fuzzy name comparison is deliberately not offered. A similarity threshold is
    a dial, and any dial fitted to an accuracy metric can be turned until the
    number looks good without the reading having improved.
    """
    return " ".join((text or "").upper().split())


def match_lines(read_lines, truth_lines):
    """Pair the lines that were read against the lines that are printed.

    Pairing is by **amount**, not by name, because OCR reads the amount column
    far more reliably than the description: a good photograph of an Aldi receipt
    returned every amount correctly and only 3 of its 18 names exactly. Matching
    on names would score lines the reader got right as lines it missed.

    Amounts repeat -- the Walmart fixture prints three 5-cent bottle deposits and
    two identical bread lines -- so this counts multiplicities rather than
    comparing sets, and a receipt with two 1.47 lines needs both of them read
    before both are scored.

    Returns ``(matched, missed, invented, names_exact)``.
    """
    read_by_amount: dict[int, list[str]] = defaultdict(list)
    for name, cents in read_lines:
        read_by_amount[cents].append(normalise_name(name))

    truth_by_amount: dict[int, list[str]] = defaultdict(list)
    for name, cents in truth_lines:
        truth_by_amount[cents].append(normalise_name(name))

    matched = missed = invented = names_exact = 0

    for cents in set(read_by_amount) | set(truth_by_amount):
        got, want = read_by_amount[cents], truth_by_amount[cents]
        pairs = min(len(got), len(want))
        matched += pairs
        missed += len(want) - pairs
        invented += len(got) - pairs
        # Within one amount, how many of the paired lines also came back with
        # the right text. Multiset intersection, so two identical bread lines
        # score two only when both were read.
        names_exact += sum((Counter(got) & Counter(want)).values())

    return matched, missed, invented, names_exact


def score_reading(receipt, truth: Truth | None) -> Score:
    """Measure one ``ExtractedReceipt``. Pure: no OCR, no files, no clock."""
    result = Score(photo=truth.photo if truth else "?")

    priced = [(item.description, to_cents(item.amount))
              for item in receipt.items if to_cents(item.amount) is not None]
    result.items_read = len(priced)
    result.items_sum_cents = sum(cents for _name, cents in priced)
    result.subtotal_cents = to_cents(receipt.subtotal)
    result.header_present = sum(
        1 for name in HEADER_FIELDS if getattr(receipt, name, None) is not None)

    if result.subtotal_cents is not None:
        result.unaccounted_cents = result.subtotal_cents - result.items_sum_cents

    tax, total = to_cents(receipt.tax), to_cents(receipt.total)
    if None not in (result.subtotal_cents, tax, total):
        result.arithmetic_ok = (result.subtotal_cents + tax == total)

    if truth is None:
        return result

    for name, expected in truth.header.items():
        got = getattr(receipt, name, None)
        result.header_checked += 1
        # Money is compared as money rather than as text, so that "7.50" and
        # "7.5" agree and a truth file is not made brittle by formatting.
        if name in MONEY_FIELDS:
            same = to_cents(got) == to_cents(expected)
        else:
            same = (got or None) == (expected or None)
        result.header_correct += 1 if same else 0

    if truth.has_lines:
        result.truth_lines = True
        (result.lines_matched, result.lines_missed,
         result.lines_invented, result.names_exact) = match_lines(priced, truth.lines)

    return result


def load_truth(path: Path = TRUTH_PATH) -> dict[str, Truth]:
    """Read the committed ground-truth file, keyed by photograph filename."""
    raw = json.loads(path.read_text(encoding="utf-8"))
    truths: dict[str, Truth] = {}
    for photo, entry in raw.get("receipts", {}).items():
        lines = entry.get("lines")
        truths[photo] = Truth(
            photo=photo,
            sha256=entry.get("sha256"),
            header=entry.get("header", {}),
            lines=None if lines is None
            else tuple((name, to_cents(amount)) for name, amount in lines),
            verified_by=entry.get("verified_by", "unverified"),
        )
    return truths
