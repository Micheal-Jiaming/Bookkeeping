"""Turn the plain text of a receipt into an ``ExtractedReceipt``.

This is the shared half of all three offline engines. RapidOCR, Windows OCR and
Tesseract differ entirely in how they get characters off an image, but once
there are lines of text the problem is the same one: work out which lines are purchases,
which are the summary block, and which are noise.

The rules here hold for most printed retail receipts (Walmart, Target, grocery
chains): a purchased line is a description followed by a trailing amount;
summary lines announce themselves with the words SUBTOTAL / TAX / TOTAL; the
date is the first date-shaped token.

What it does not do: multi-column layouts, handwritten receipts, restaurant
tickets with the amount printed above the item, or any non-Latin script. When it
gets those wrong the reviewer fixes them by hand -- which is exactly why the
review step is not optional.
"""

from __future__ import annotations

import re
from datetime import date

from ..money import to_cents
from .base import ExtractedItem, ExtractedReceipt

# A trailing amount, optionally with the trailing-minus that thermal printers
# use for credits, and optionally followed by a tax flag letter (Walmart prints
# "X", "O", "T", "N", "F"). The flag is matched in either case: Windows OCR
# reads the small capital Walmart prints as a lowercase "x" most of the time,
# and an uppercase-only pattern silently rejected the whole line -- which cost
# 15 of the 20 readable items on the first real receipt.
#
# **Whether there is a space before the flag depends on the reader, not the
# receipt**: for the same printed line Windows OCR returns "5.97 X" and RapidOCR
# returns "5.97X", and requiring the gap dropped that item and its money.
# But the gap cannot simply be made optional either, because a weight line
# "(T) 0.02lb" would then read as the amount 0.02 carrying the tax flag "lb".
#
# The two are separated by length. A *two*-letter flag still needs its space,
# which is what keeps "lb", "kg" and "oz" out; a single letter does not, because
# no unit of weight is one character.
#
# **A lone `0` is accepted in the flag column, and it means the letter O.**
# Walmart flags a non-taxable line with `O`, and the same O-for-zero confusion
# that garbles item names bites here too -- with worse consequences, because the
# amount and the flag run together into `0.050`, which is not a two-decimal
# amount at all, so the whole line is discarded and its money with it.
#
# **Why the older engine never had this problem** is the thing worth recording,
# because the user pointed at it and it identifies the real cause. Windows OCR
# returns one box per *word*, so `0.05` and `O` are separate detections and
# ``rows_to_text`` puts the space back from their geometry -- it produced
# `0.05 O` on all eleven deposit lines across three receipts. RapidOCR returns
# one box per *line*, so the space survives only if the recogniser chose to emit
# it, and on the first deposit line of each receipt it does not. The gap is
# unrecoverable by then, so the parser has to tolerate its absence.
#
# Only `0` is allowed, never another digit, which keeps this narrow: `123.456`
# is still not an amount followed by a flag. And the failure mode if a genuine
# three-decimal figure ever ends in zero is benign -- the amount is still read
# correctly and only the taxability is wrong.
_TRAILING_AMOUNT = re.compile(
    r"(?P<amount>-?\$?\d[\d,]*\.\d{2})\s*(?P<minus>-)?"
    r"(?:\s+(?P<pair>[A-Za-z]\s?[A-Za-z])|\s*(?P<flag>[A-Za-z0]))?\s*$"
)
_LEADING_AMOUNT = re.compile(r"^(?P<amount>-?\$?\d[\d,]*\.\d{2})(?!\d)")
_LEADING_ALPHA = re.compile(r"^[A-Z]+")

# The count of items the till says it rang up. Every chain here prints one, and
# it is the only statement on a receipt about how many purchases there *should*
# be -- the subtotal says how much money is missing, this says how many lines to
# go looking for. On the Costco photograph that is the difference between "off
# by 59.22" and "six lines were not read".
#
# Three shapes, because three chains print it three ways:
_ITEMS_SOLD = (
    # Walmart: "ITEMS SOLD 21", "# ITEMS SOLD 3".
    re.compile(r"\bITEMS?\s+SOLD\b\D{0,4}(\d{1,3})\b", re.IGNORECASE),
    # Aldi: "18 ITEMS", the count first.
    re.compile(r"^\D{0,2}(\d{1,3})\s+ITEMS?\b", re.IGNORECASE),
    # Costco: "TOTAL NUMBER OF ITEMS SOLD = 16", which OCR mangles into
    # "TOTAL NUMBER OF 1 EMS sot-c 16" -- both keywords destroyed, "NUMBER OF"
    # intact. Anchored on the end of the line so it takes the count rather than
    # the wreckage of the word ITEMS.
    re.compile(r"\bNUMBER\s+OF\b.*?(\d{1,3})\s*$", re.IGNORECASE),
)

# **What is deliberately not matched: a bare "SOLD 16".** The second OCR pass of
# the Costco receipt returns "sold 6" for a line that reads 16 on the paper, and
# a pattern loose enough to catch that would import a wrong count -- which is
# worse than no count at all, because the flag it raises tells the reviewer to
# hunt for lines that are not missing. The three patterns above all require a
# surviving keyword, so that line is ignored and the first pass's 16 stands.

# What the flag after the price means. Walmart prints one letter; Aldi prints
# two ("FA", "NB"), and OCR sometimes splits those into "F A", hence the
# optional space in the pattern above.
#
# The Aldi mapping was read off the receipt's own arithmetic rather than
# guessed: on the 18-line receipt the single NB line is a $2.69 pack of paper
# bowls, and the printed "B-Taxable @5.500%" line is $0.15 -- which is
# 2.69 x 0.055. Every other line is FA and contributes nothing to the tax.
_TAX_FLAGS = {
    "X": True, "T": True, "N": False, "O": False,   # Walmart
    "NB": True, "FA": False,                        # Aldi
}


def _tax_flag(pair: str | None, flag: str | None) -> bool | None:
    """Whether the flag beside an amount says the line was taxed.

    ``None`` means the receipt did not say, which is not the same as untaxed.
    A lone ``0`` is translated to ``O``: it is the letter, misread as a digit
    (see ``_TRAILING_AMOUNT``), and without this the lookup quietly returns
    ``None`` and a non-taxable line loses the only statement it made.
    """
    token = (pair or flag or "").upper().replace(" ", "")
    if token == "0":
        token = "O"
    return _TAX_FLAGS.get(token)


_QTY_AT_PRICE = re.compile(r"(?P<qty>\d+(?:\.\d+)?)\s*(?:@|X)\s*\$?(?P<unit>\d[\d,]*\.\d{2})")
_LEADING_QTY = re.compile(r"^(?P<qty>\d{1,3})\s+(?=\D)")
_SKU = re.compile(r"\b(\d{9,14})\b")

# Goods sold by weight print across two lines: the name and barcode on one, with
# no price at all, then the weighing on the next --
#
#     GINGER ROOT   000000004612 0 F
#        0.42 lb @ 1.00 lb / 3.62         1.52 N
#
# Read a line at a time that loses the name: the first line has no amount so it
# is skipped, and the second becomes an item called "0.42 lb @ 1.00 lb / 3.62".
# The money was always right; the description was unusable. The rate reads
# "<weight> lb @ 1 lb /<price per lb>", so the number after the slash is the
# unit price and the one before the unit is the quantity.
_WEIGHED = re.compile(
    r"^\s*(?P<qty>\d+(?:\.\d+)?)\s*(?P<unit>lb|lbs|kg|g|oz)\b.*?/\s*\$?"
    r"(?P<price>\d[\d,]*\.\d{2})", re.IGNORECASE)

# An item with no printed name: the receipt shows its barcode where the
# description would go. Seen on a real receipt as
# "756809105667 756809105660  5.88 X" -- the true UPC beside the truncated item
# number. Such a line has no letters at all, so the "this is a barcode, not a
# purchase" guard below would otherwise reject it, silently losing $5.88.
_BARE_BARCODE = re.compile(r"^\d{9,14}$")

# Aldi prints its item number *before* the description -- "356387 Green
# Peppers" -- where Walmart prints a barcode after it. The number is six digits
# rather than a UPC's twelve, so _SKU never sees it and the digits end up inside
# the item name. Bounded at 4-8 digits so it cannot swallow a real barcode, and
# it only ever runs when no barcode was found, so Walmart's layout is untouched.
# The name after the number may itself begin with a digit -- "24ct Paper Bowl",
# "2% Milk" -- so the test for "is this a name" is that a letter appears
# somewhere in what follows, applied in _find_items rather than in the pattern.
#
# Costco puts a further column to the *left* of the item number: a single letter
# printed in the margin. Walmart and Aldi both print their per-line flag on the
# right, so a line beginning with anything but a digit had never been a
# possibility, and the whole "E 96716 ORG SPINACH" ran into the description --
# item number and all. That is why the Costco photograph scored zero names
# correct while returning most of them verbatim.
#
# Which side the flag is printed on is the thing to check first at any new
# chain. The letter is dropped rather than interpreted: what Costco means by it
# is not stated anywhere on the receipt, and guessing would put a claim in the
# books that nothing supports.
_LEADING_ITEM_NO = re.compile(r"^(?:(?P<flag>[A-Za-z])\s+)?(?P<no>\d{4,8})\s+(?=\S)")

# **Capital O read as zero, which no recognition model can fix.**
#
# Till printers use a condensed font in which `O` and `0` differ by a hairline,
# and the recogniser has no vocabulary for `EQJELLUBE8OZ` to break the tie with.
# It was the largest single defect outstanding when this was written, and the
# rules below closed it: five of the fourteen wrong item names across the six
# receipts confirmed at the time were this one confusion --
#
#     PR0TEINSUPPL     DOVE BW 110Z     AIM TP 5.50Z
#     GVC0RNSTARCH     EQJELLUBE80Z
#
# -- and a bigger model would face exactly the same ambiguous ink. Two rules
# break the tie by knowing what the token is instead of what it looks like.
#
# **The rules are deliberately narrow, because the cost is asymmetric.** A
# missed repair leaves a name slightly wrong, which a reviewer can see and
# correct; a wrong repair corrupts a barcode or a size into something plausible
# that nobody will question. So both refuse anything they cannot be sure of.
#
# One: a zero inside an otherwise all-capital word is an O. Requiring at least
# one letter exempts barcodes and item numbers, and allowing no digit *other*
# than zero exempts every real alphanumeric code -- `WD40` and `CO2` keep their
# digits because the 4 and the 2 disqualify the whole token.
_ZERO_IN_A_WORD = re.compile(r"\b(?=[A-Z0]*[A-Z])[A-Z0]+\b")

# Two: `0Z` straight after a digit is the unit `OZ`. This is what catches the
# codes rule one has to refuse -- `EQJELLUBE80Z` contains an 8, so only its tail
# can be repaired. Run first, so that by the time rule one looks at the token
# the ounces are already letters and it correctly declines to touch the rest.
_OUNCES_AFTER_A_NUMBER = re.compile(r"(?<=\d)0Z\b")

_DATE_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\b(20\d{2})[-/.](\d{1,2})[-/.](\d{1,2})\b"), "ymd"),
    (re.compile(r"\b(\d{1,2})[-/.](\d{1,2})[-/.](\d{4})\b"), "mdy"),
    (re.compile(r"\b(\d{1,2})[-/.](\d{1,2})[-/.](\d{2})\b"), "mdy2"),
]

# Lines that are never purchased items.
_SUMMARY_WORDS = (
    "SUBTOTAL", "SUB TOTAL", "TOTAL", "TAX", "TIP", "GRATUITY", "BALANCE",
    "CHANGE", "CASH", "DEBIT", "CREDIT", "VISA", "MASTERCARD", "AMEX",
    # Walmart prints a cash-rounding adjustment between TOTAL and CHANGE
    # DUE. It carries an amount in the item column and was being counted
    # as a purchase, which put 4 cents of nothing into the books.
    "ROUNDING",
    "DISCOVER", "TEND", "PAYMENT", "AMOUNT DUE", "SAVINGS", "ITEMS SOLD",
    "TC#", "REF #", "APPROVAL", "AUTH", "ACCOUNT", "NETWORK ID", "TERMINAL",
    "THANK YOU", "SURVEY", "www.", "POINTS", "REWARD", "MEMBER", "STORE #",
    "OP#", "TE#", "TR#",
    # "TAXI" is not a cab: Windows OCR reads Walmart's "TAX1" as it, and
    # whole-word matching then stops TAX from covering it, which put $7.50
    # of tax into the item list. Listed explicitly so the repair is visible.
    # The cost is that a genuine taxi fare line would be read as summary --
    # accepted, because this reads shop receipts and drops nothing else.
    "TAXI",
    # Aldi's summary block and card trailer.
    "TAXABLE", "AMOUNT", "ITEMS", "APPROVED", "TRACE", "CASHIER",
    # "VISE" is Windows OCR reading Costco's "Visa" tender line, where the
    # final 'a' comes back as an 'e'. Without it that line carries the grand
    # total in the amount column and is counted as a purchase -- on COSTCO1 it
    # put $193.52 of nothing into the items, more than the receipt's own
    # subtotal. Listed explicitly, like TAXI above, so the repair is visible
    # rather than hidden in a fuzzy match. The cost is that a genuine vise --
    # Costco does sell tools -- would read as summary; accepted on the same
    # grounds as TAXI, and it is a whole-word match so "VISEGRIP" is safe.
    "VISE",
)
# Lines that hand over money, as opposed to the rest of the summary block.
# TEND is here for Walmart's "MCARD TEND" and "CASH TEND".
_PAYMENT_WORDS = ("MASTERCARD", "VISA", "VISE", "AMEX", "DISCOVER", "DEBIT",
                  "CREDIT", "CASH", "TEND", "PAYMENT", "CARD")


def _whole_words(words) -> re.Pattern[str]:
    """Match any of ``words``, but never inside a longer word.

    Both of these lists are short English words that occur inside ordinary
    groceries, and matching them as plain substrings has now cost real line
    items twice: CASHEWS contains CASH, CHICKEN TENDERS contains TEND, CARDAMOM
    contains CARD, Q-TIPS contains TIP. The lookarounds are on letters only, so
    "TC#" and "REF #" still match while "CASHEWS" does not.
    """
    return re.compile(
        r"(?<![A-Z])(?:" + "|".join(re.escape(w.upper()) for w in words)
        + r")(?![A-Z])")


_SUMMARY_RE = _whole_words(_SUMMARY_WORDS)
_PAYMENT_RE = _whole_words(_PAYMENT_WORDS)

_DISCOUNT_WORDS = (
    "COUPON", "DISCOUNT", "ROLLBACK", "MARKDOWN", "PROMO", "VOID", "REFUND",
    "PRICE CUT", "SAVED",
)

# Store names worth recognising, so the merchant field is not simply the first
# OCR line (often a phone number or a slogan).
_KNOWN_MERCHANTS = {
    "WALMART": "Walmart",
    "WAL-MART": "Walmart",
    "SAM'S CLUB": "Sam's Club",
    "TARGET": "Target",
    "COSTCO": "Costco",
    "KROGER": "Kroger",
    "SAFEWAY": "Safeway",
    "TRADER JOE": "Trader Joe's",
    "WHOLE FOODS": "Whole Foods Market",
    "ALDI": "Aldi",
    "PUBLIX": "Publix",
    "CVS": "CVS Pharmacy",
    "WALGREENS": "Walgreens",
    "HOME DEPOT": "The Home Depot",
    "LOWE'S": "Lowe's",
    "BEST BUY": "Best Buy",
    "STARBUCKS": "Starbucks",
    "MCDONALD": "McDonald's",
    "CHIPOTLE": "Chipotle",
    "SHELL": "Shell",
    "CHEVRON": "Chevron",
    "PETCO": "Petco",
    "PETSMART": "PetSmart",
}

_MERCHANT_NAMES = frozenset(_KNOWN_MERCHANTS.values())

# The store name is printed as a logo, and a logo is the hardest thing on a till
# roll for OCR to read: it is large, stylised, and sits on the part of the paper
# that curls. An exact search finds nothing, and the merchant falls through to
# the street address printed beneath it -- which is what the reviewer then sees
# in the Merchant field.
#
# How badly it is misread depends on the size the image happens to be at.
# Measured on the one Costco photograph, the same six letters come back as:
#
#   Cosrco       from the original file (1280px on the long edge)
#   Cesrco       from the copy the app stores, downscaled again by the second
#                OCR pass -- this is what the application really sees
#   nothing      from the stored copy at its own size
#   =WHOLESAZE   at 1.25x, reading the second word of the logo instead
#   =WHOLESALE   at 2.5x, finally correct, but only that word
#
# There is no scale at which "COSTCO" is read correctly, and no two of those
# readings agree, so corroborating one against another does not work either.
#
# Hence two passes of decreasing strictness. The first tolerates one character
# of error, and only for names where a near-miss cannot land on a different
# shop:
#
#   * a single word, so nothing has to be assumed about where it breaks;
#   * at least six characters, because one edit away from a short word is
#     simply another word -- SHELL and SHELF differ by one, and a line reading
#     SHELF must never be filed under Shell;
#   * the whole candidate word within one edit of the whole name, rather than
#     merely containing something like it.
#
# The second is ``_same_shape``, which accepts two wrong characters under much
# tighter conditions. It exists because "Cesrco" is what this application
# actually gets, and one edit does not reach it.
_FUZZY_MERCHANTS = {
    needle: pretty
    for needle, pretty in _KNOWN_MERCHANTS.items()
    if needle.isalpha() and len(needle) >= 6
}

_WORD_RE = re.compile(r"[A-Z]{5,}")

# How far down the receipt each pass is allowed to look. The exact search runs
# over the whole header block; a tolerant one must not, because every extra line
# is another chance for a near-miss to find something that is not a shop at all.
_FUZZY_LINES = 6
_SHAPE_LINES = 3
_SHAPE_MIN_LENGTH = 6
_SHAPE_MAX_WRONG = 2


def is_known_merchant(name: str | None) -> bool:
    """True when ``name`` is a shop this parser recognised, not a guess.

    Callers use this to tell "Costco" -- read off the logo -- apart from the
    street address the fallback returns when the logo is unreadable. The two are
    both non-empty strings and only this distinguishes them.
    """
    return bool(name) and name in _MERCHANT_NAMES


def _within_one_edit(word: str, target: str) -> bool:
    """True when ``word`` differs from ``target`` by at most one character.

    Substitution, insertion and deletion are all counted, because all three are
    things OCR does to a logo: a stylised glyph read as a different letter, a
    speck of dirt read as an extra one, a thin stroke lost altogether.
    """
    if word == target:
        return True
    longer, shorter = (word, target) if len(word) > len(target) else (target, word)
    if len(longer) - len(shorter) > 1:
        return False
    if len(longer) == len(shorter):
        return sum(a != b for a, b in zip(longer, shorter)) == 1
    # One insertion: the two must agree on both sides of a single skipped
    # character in the longer string.
    index = 0
    while index < len(shorter) and longer[index] == shorter[index]:
        index += 1
    return longer[index + 1:] == shorter[index:]


def _same_shape(word: str, target: str) -> bool:
    """True when ``word`` is ``target`` with up to two letters in the middle wrong.

    Deliberately weaker evidence than ``_within_one_edit``, and fenced in four
    ways so that it stays evidence rather than a guess: the two must be the same
    length, start with the same letter, end with the same letter, and differ
    nowhere else by more than two characters. The caller adds a fifth fence by
    only offering it the first few lines, where a shop prints its name.

    Those fences are what keep the real collisions out. MARKET is two
    substitutions from TARGET, and Market Basket is a supermarket in the same
    state as the receipt that motivated this -- but M is not T, so it is
    refused. SUBWAY against SAFEWAY is refused on length.

    **Why a weaker match is acceptable here and nowhere else in this project.**
    Elsewhere -- rebuilding a barcode, expanding an abbreviation -- a wrong
    answer is invisible: it names a product the reviewer has no way to check
    against the paper. The merchant is the opposite. It is one field, at the top
    of the review pane, next to a photograph of the receipt, and it is wrong in
    a way anybody spots and can correct in a second. The cost of being wrong is
    a visible field to fix; the cost of refusing is a street address in the
    Merchant box and every line the shop would have categorised left blank.
    """
    return (len(word) == len(target)
            and len(word) >= _SHAPE_MIN_LENGTH
            and word[0] == target[0]
            and word[-1] == target[-1]
            and sum(a != b for a, b in zip(word, target)) <= _SHAPE_MAX_WRONG)


def _repair_letter_o(description: str) -> str:
    """Put back the capital Os a receipt printer's font turned into zeros.

    Applied to the description only, and only once the amount, barcode, quantity
    and unit price have already been taken off the line -- so there is no path
    from here to a figure in the books. The two rules and why they are shaped
    the way they are is at ``_ZERO_IN_A_WORD``.
    """
    repaired = _OUNCES_AFTER_A_NUMBER.sub("OZ", description)
    return _ZERO_IN_A_WORD.sub(lambda m: m.group().replace("0", "O"), repaired)


def parse_receipt_text(text: str) -> ExtractedReceipt:
    """Turn OCR text into an ``ExtractedReceipt``. Pure function, unit tested."""
    lines = [line.strip() for line in text.splitlines()]
    lines = [line for line in lines if line]

    receipt = ExtractedReceipt(currency="USD")
    receipt.merchant_raw, receipt.merchant = _find_merchant(lines)
    receipt.purchased_at = _find_date(lines)
    receipt.payment_method = _find_payment(lines)
    receipt.items_sold = _find_items_sold(lines)

    summary = _find_summary_amounts(lines)
    receipt.subtotal = summary.get("subtotal")
    receipt.tax = summary.get("tax")
    receipt.tip = summary.get("tip")
    receipt.total = summary.get("total")

    receipt.items = _find_items(lines, summary)
    return receipt


def _find_merchant(lines: list[str]) -> tuple[str | None, str | None]:
    upper_head = [line.upper() for line in lines[:12]]
    for index, line in enumerate(upper_head):
        for needle, pretty in _KNOWN_MERCHANTS.items():
            if needle in line:
                return lines[index], pretty

    # Still nothing, so try again allowing one character of OCR error, and then
    # a third time allowing two under the much tighter conditions in
    # ``_same_shape``. Each pass looks at fewer lines than the one before,
    # because the weaker the test, the closer to the top of the receipt its
    # evidence has to come from.
    for accept, depth in ((_within_one_edit, _FUZZY_LINES),
                          (_same_shape, _SHAPE_LINES)):
        for index, line in enumerate(upper_head[:depth]):
            for word in _WORD_RE.findall(line):
                for needle, pretty in _FUZZY_MERCHANTS.items():
                    if accept(word, needle):
                        return lines[index], pretty

    # Nothing recognised: use the first line that looks like a name rather than
    # an address, phone number or receipt barcode. Summary lines are skipped --
    # a photo that cuts off the store name would otherwise report a merchant of
    # "Items Sold 21", which is worse than admitting we do not know.
    for line in lines[:6]:
        if _is_summary_line(line.upper()):
            continue
        letters = sum(ch.isalpha() for ch in line)
        digits = sum(ch.isdigit() for ch in line)
        if letters >= 3 and digits <= letters:
            cleaned = re.sub(r"\s{2,}", " ", line).strip(" *-#")
            return line, cleaned.title() if cleaned.isupper() else cleaned
    return None, None


def _find_items_sold(lines: list[str]) -> int | None:
    """The number of items the receipt says were rung up, if it says.

    The last such line wins, for the same reason the last total does: a receipt
    that prints the figure twice prints the authoritative one at the bottom.
    Zero is discarded -- a till that sold nothing does not print a receipt, so a
    zero here is a misread rather than a fact.
    """
    found: int | None = None
    for line in lines:
        for pattern in _ITEMS_SOLD:
            match = pattern.search(line)
            if match:
                count = int(match.group(1))
                if count > 0:
                    found = count
                break
    return found


def _find_date(lines: list[str]) -> str | None:
    for line in lines:
        for pattern, order in _DATE_PATTERNS:
            match = pattern.search(line)
            if not match:
                continue
            try:
                if order == "ymd":
                    year, month, day = (int(g) for g in match.groups())
                elif order == "mdy":
                    month, day, year = (int(g) for g in match.groups())
                else:
                    month, day, short_year = (int(g) for g in match.groups())
                    # The POSIX two-digit-year convention: 00-68 is this century
                    # and 69-99 the last. Widened to 79 here because a till roll
                    # is never decades old -- a receipt reading "/70" is far more
                    # likely a misread of a recent year than a purchase in 1970.
                    year = 2000 + short_year if short_year <= 79 else 1900 + short_year
                if month > 12 and day <= 12:
                    # A DD/MM receipt slipped through; swap rather than fail.
                    month, day = day, month
                return date(year, month, day).isoformat()
            except ValueError:
                continue
    return None


# How a receipt names what paid for it. Matched as whole words for the reason
# `_whole_words` sets out, and this function is the third place that trap has
# been found: it used plain `in` until a KFC receipt printed **`Cashier:
# Zackariah`** near the top. `CASHIER` contains `CASH`, it was the first match
# on the page, and the function returned before ever reaching `Card Type:
# Mastercard` at the bottom. Aldi's `Your cashier today was Ismail` did the same
# thing, so three of the eight photographs here recorded a card purchase as
# **cash** -- a confidently wrong value in somebody's books, which is worse than
# leaving the field blank.
_TENDERS = ("VISA", "MASTERCARD", "AMEX", "DISCOVER", "DEBIT", "CREDIT", "CASH")
_TENDER_RE = _whole_words(_TENDERS)

# The last four digits of the card, and *only* when they follow the brand.
#
# This used to take the last four digits anywhere on the line, which on a line
# shaped `MASTERCARD- 0000 I 1 APPR#009999` returned **9999** -- the approval
# code, not the card. (The digits here are invented and must stay invented; see
# section 11.57. The real ones went into a test file and were published.)
# A card number nobody can check is exactly the kind of wrong that survives: it
# looks like the right shape and no arithmetic contradicts it. The
# real digits sit against the brand, so only that position is trusted, and a
# line with nothing there yields a brand and no number rather than a guess.
# The run between the brand and the digits is whatever the terminal masks with
# -- `VISA ****1234`, `MASTERCARD- 0000`, `**************0000` -- so asterisks,
# dashes, hashes and X are bridged, and nothing else is. A letter ends it, which
# is what stops `MASTERCARD PURCHASE 1234` from reading as a card.
#
# The trailing guard rejects a decimal point as well as a digit, so an amount
# printed against the brand (`CREDIT 1234.56`) is not read as its last four.
_CARD_LAST_FOUR = re.compile(r"^[-*#X\s]{0,24}(\d{4})(?![\d.])")


def _find_payment(lines: list[str]) -> str | None:
    """The tender line, taken in printed order.

    Deliberately still first-match-wins rather than preferring a card brand over
    a generic word. On the KFC receipt that means `ETender Credit` is reported
    as `CREDIT` even though `Card Type: Mastercard` is printed four lines below
    -- less specific, but true. Preferring the brand would read the wrong answer
    off any receipt whose footer advertises the cards the shop accepts, and no
    receipt here justifies taking that risk.
    """
    for line in lines:
        upper = line.upper()
        match = _TENDER_RE.search(upper)
        if not match:
            continue
        digits = _CARD_LAST_FOUR.match(upper[match.end():])
        return f"{match.group(0)} ****{digits.group(1)}" if digits else match.group(0)
    return None


def _find_summary_amounts(lines: list[str]) -> dict[str, str]:
    """Pull subtotal/tax/tip/total out of the summary block.

    Checked most-specific first: 'SUBTOTAL' contains 'TOTAL', and 'TOTAL TAX'
    contains both, so naive substring order would mislabel every one of them.
    The last amount on the line is the value; the last matching line wins,
    because receipts print the true total below any per-department subtotals.
    **Tax is the exception**: a receipt may charge several rates and print one
    component line each, so those are summed rather than overwritten, and a
    line stating the tax outright then beats the sum.
    """
    found: dict[str, str] = {}
    # Per-rate tax lines, kept apart from the tax itself. Costco prints
    # "A 5.500% TAX 2.86" and "F 8.00% TAX 2.29" -- two components of one tax,
    # not two candidate answers. Recognised by the percentage, which is what
    # makes a line a rate breakdown rather than a total.
    rate_parts: list[int] = []
    tax_is_stated = False

    for line in lines:
        # The space-stripped form is checked too: Aldi letter-spaces its grand
        # total as "T O T A L", which contains the word only once collapsed.
        upper = line.upper()
        tight = upper.replace(" ", "")
        def says(*words: str) -> bool:
            return any(w in upper or w.replace(" ", "") in tight for w in words)

        # Which field this line names is decided before its amount is looked
        # for, because a line that names one is allowed a second place to keep
        # the figure -- see ``_leading_amount_text``. Order still matters below:
        # SUBTOTAL contains TOTAL, and TOTAL TAX contains both.
        is_subtotal = _says_subtotal(upper, tight)
        is_tax = says("TAX")
        is_tip = says("TIP", "GRATUITY")
        is_total = (says("TOTAL", "AMOUNT D", "AMOUNT:", "BALANCE DUE")
                    or bool(_FULFILMENT_TOTAL.match(upper)))

        amount = _trailing_amount_text(line)
        if amount is None:
            if not (is_subtotal or is_tax or is_tip or is_total):
                continue
            amount = _leading_amount_text(line)
            if amount is None:
                continue
        if is_subtotal:
            found["subtotal"] = amount
        elif is_tax:
            if "%" in line:
                # A component. Summing beats taking the last, which is what the
                # previous rule did: on COSTCO1 that reported 2.29 as the whole
                # tax when the receipt charged 5.15. Aldi's zero-rate line is
                # handled by the same arithmetic -- 0.15 + 0.00 is still 0.15 --
                # so this replaces the special case that only guarded zeroes.
                cents = _amount_cents(amount)
                if cents is not None:
                    rate_parts.append(cents)
                    if not tax_is_stated:
                        found["tax"] = _cents_text(sum(rate_parts))
            else:
                # A line that states a non-zero tax outright beats any
                # breakdown.
                #
                # The zero test is defensive and predates the rate summing: it
                # stops a bare "TAX 0.00" trailer overwriting a figure already
                # found. **No fixture here exercises it**, and the obvious
                # candidate does not: Aldi's "A-Taxable @0.00%" carries a
                # percentage, so it is a rate component handled by the branch
                # above and never reaches this one. It is kept for the receipt
                # that prints a bare zero trailer *after* its real tax, which
                # would otherwise report no tax at all.
                if amount.strip("$ ").lstrip("-") not in ("0.00", "0") or "tax" not in found:
                    found["tax"] = amount
                    tax_is_stated = True
        elif is_tip:
            found["tip"] = amount
        elif is_total:
            # "TOTAL TAX 5.15" arrives from OCR as a bare "TOTAL 5.15" when the
            # second word is dropped, and then reads as the grand total -- which
            # on COSTCO1 reported the receipt's $193.52 purchase as $5.15. It
            # cannot be told apart by its words once TAX is gone, so it is told
            # apart by arithmetic instead: a TOTAL equal to the sum of the rate
            # components just seen is those components' total, not the amount
            # charged. Requires at least one rate line, so an ordinary receipt
            # whose total happens to equal its tax is untouched.
            if rate_parts and _amount_cents(amount) == sum(rate_parts):
                continue
            found["total"] = amount

    _settle_tax(found, rate_parts, tax_is_stated)
    return found


def _settle_tax(found: dict[str, str], rate_parts: list[int], stated: bool) -> None:
    """Choose between a stated tax and a rate breakdown that disagrees with it.

    They can disagree, and on the Costco photograph they do: the summary line is
    read as "TAX 5.16" where the paper says 5.15, while the two rate components
    are read exactly and sum to 5.15. One of the two readings contains a misread
    digit and nothing about the text says which.

    So neither is preferred on principle -- **the receipt decides, by which of
    them makes its own arithmetic add up**. Subtotal plus the summed components
    equals the printed total; subtotal plus the stated figure is a cent over.
    That is evidence, not a tie-break, and it is the same reasoning
    ``merge_readings`` uses to choose between two passes' item lists.

    Nothing happens unless all three of subtotal, total and a breakdown are
    present and the two candidates actually differ, so an ordinary receipt --
    including every Aldi one here, which states its tax and prints no
    percentage -- is untouched.
    """
    if not (rate_parts and stated):
        return
    subtotal = _amount_cents(found.get("subtotal", ""))
    total = _amount_cents(found.get("total", ""))
    current = _amount_cents(found.get("tax", ""))
    summed = sum(rate_parts)
    if None in (subtotal, total) or current == summed:
        return
    if subtotal + summed == total and subtotal + current != total:
        found["tax"] = _cents_text(summed)


def _amount_cents(amount: str) -> int | None:
    """Integer cents for a decimal string, or None if it is not one.

    The sign is taken off the front and reapplied at the end rather than
    carried through ``int(whole)``: "-0.15" has a whole part of "-0", and
    ``int("-0")`` is 0, so multiplying it by 100 loses the minus and turns a
    fifteen-cent refund into a fifteen-cent charge.
    """
    text = amount.strip("$ ")
    negative = text.startswith("-")
    try:
        whole, _, frac = text.lstrip("+-").partition(".")
        cents = int(whole or "0") * 100 + int((frac + "00")[:2])
    except ValueError:
        return None
    return -cents if negative else cents


def _cents_text(cents: int) -> str:
    """Integer cents back to a decimal string, negatives included.

    Not simply ``f"{cents // 100}.{cents % 100:02d}"``. Python floors, so -15
    // 100 is -1 and -15 % 100 is 85, and fifteen cents of refund renders as
    "-1.85" -- the same sign trap ``_amount_cents`` above documents, reappearing
    on the way back out. Reachable through a rate-breakdown line carrying a
    negative amount, which a refunded receipt would print.
    """
    sign = "-" if cents < 0 else ""
    cents = abs(cents)
    return f"{sign}{cents // 100}.{cents % 100:02d}"


def _trailing_amount_text(line: str) -> str | None:
    match = _TRAILING_AMOUNT.search(line.replace("$", " $"))
    if not match:
        return None
    raw = match.group("amount").replace("$", "").replace(",", "")
    if match.group("minus"):
        raw = f"-{raw}"
    return raw


def _leading_amount_text(line: str) -> str | None:
    """The amount at the *start* of a line, for tills that print it there.

    Walmart's card slip states the grand total as "24.81 TOTAL PURCHASE", and on
    one photograph that is the only legible statement of it -- the TOTAL line
    itself came back as "TOT AL 24 . a-I". Only ever consulted for a line that
    already names a summary field, so an item priced before its own name is not
    swept up as a total.
    """
    match = _LEADING_AMOUNT.match(line.replace("$", " $").lstrip())
    if not match:
        return None
    return match.group("amount").replace("$", "").replace(",", "")


def _says_subtotal(upper: str, tight: str) -> bool:
    """Whether this line is the subtotal, tolerating one character of OCR error.

    "SUBTOTAL" comes off the Costco photograph as "SUBT TAL" -- the O lost where
    two glyph clusters meet. That is neither the literal word nor the
    letter-spaced form the space-stripped comparison already covers, so the line
    was read as a purchase of the subtotal's own value: the largest invented
    item on the receipt, and one that made the arithmetic check meaningless.

    Only the run of letters at the start of the line is compared, so nothing
    buried inside a product name can satisfy this, and only one edit is allowed
    -- the same budget, for the same reason, as the merchant logo match above.
    """
    if "SUBTOTAL" in upper or "SUB TOTAL" in upper or "SUBTOTAL" in tight:
        return True
    head = _LEADING_ALPHA.match(tight)
    return bool(head and _within_one_edit(head.group(), "SUBTOTAL"))


# **Fast food labels the total by how you are taking the food away.** A KFC
# receipt has no line saying TOTAL at all: the amount charged sits against
# `CARRY OUT`, with the tax printed *above* it rather than below. Sit-down and
# drive-through prints use the same slot, so the siblings are listed with it --
# only CARRY OUT is confirmed against a photograph here, the rest are the same
# label in the same position and are marked as unverified in section 9.
#
# **Anchored, and the amount must follow the words immediately.** That is the
# whole safety of it: `CARRY OUT $12.84` is the total, while a bag charged as
# `CARRY OUT BAG 0.10` has a word in between and stays a purchase. Matching
# these loosely would let a fifty-cent bag overwrite the total of the receipt.
#
# `TO GO` is deliberately absent. It is two of the commonest short words in
# English, it survives the space-stripped comparison as `TOGO`, and no
# photograph here justifies the risk.
_FULFILMENT_TOTAL = re.compile(
    r"^\s*(?:CARRY\s?-?\s?OUT|TAKE\s?-?\s?OUT|DINE\s?-?\s?IN"
    r"|DRIVE\s?-?\s?(?:THRU|THROUGH))\s*:?\s*(?=\$?\d)")


def _is_summary_line(upper: str) -> bool:
    """Whether a line belongs to the receipt's summary rather than its purchases.

    Matched on whole words, which is not fussiness. Plain substring matching
    made CASHEWS a summary line -- it contains CASH -- so a bag of cashews was
    silently dropped from the receipt and its money with it. Any short word on
    this list has the same problem waiting in it.

    The space-stripped form is checked too, because a till often letter-spaces
    its emphasis: Aldi prints the grand total as "T O T A L", which contains
    the word TOTAL only once the spaces are gone.
    """
    tight = upper.replace(" ", "")
    return bool(_SUMMARY_RE.search(upper) or _SUMMARY_RE.search(tight)
                or _says_subtotal(upper, tight)
                or _FULFILMENT_TOTAL.match(upper))


def _find_items(
    lines: list[str], summary: dict[str, str] | None = None
) -> list[ExtractedItem]:
    items: list[ExtractedItem] = []
    # A name and barcode seen on a line that carried no price. Goods sold by
    # weight print that way, with the money on the line below; holding the name
    # for exactly one line is what lets the two halves be joined back together.
    carried: tuple[str, str | None] | None = None

    # A card brand followed by the amount charged -- "Mastercard  17.43" -- is a
    # payment line, not a purchase, but OCR corrupts the brand name often enough
    # ("Mas*ercard") that the word list cannot be relied on to catch it. What is
    # reliable is the shape: no item number, and an amount equal to the receipt's
    # own total. Such a line is dropped below, but only when other items were
    # found, so a genuine single-item receipt is never emptied.
    payment_amounts = {
        (summary or {}).get(key, "").strip("$ ")
        for key in ("total", "subtotal")
    }
    # Aldi prints the card total twice -- once beside the brand, once as
    # "Credit Card $17.43". OCR mangled the first into "Mas*ercard", which no
    # word list will match, but the second is clean, and the amount is the same.
    #
    # Only *payment* lines contribute. Taking every summary line would mean an
    # unnamed item that happens to cost the same as the tax gets thrown away,
    # which is a real receipt losing a real purchase to a coincidence.
    for line in lines:
        if _PAYMENT_RE.search(line.upper()):
            amount = _trailing_amount_text(line)
            if amount:
                payment_amounts.add(amount.strip("$ "))
    payment_amounts -= {""}
    suspect: list[int] = []

    for line in lines:
        upper = line.upper()
        if _is_summary_line(upper):
            carried = None
            continue
        match = _TRAILING_AMOUNT.search(line.replace("$", " $"))
        if not match:
            carried = _name_without_price(line) or None
            continue
        amount_text = _trailing_amount_text(line)
        if amount_text is None:
            continue

        head = line[: match.start()].strip(" .-*")
        is_discount = any(word in upper for word in _DISCOUNT_WORDS) or amount_text.startswith("-")
        if is_discount and not amount_text.startswith("-"):
            amount_text = f"-{amount_text}"

        sku_match = _SKU.search(head)
        sku = sku_match.group(1) if sku_match else None
        if not sku_match:
            leading = _LEADING_ITEM_NO.match(head)
            if leading and re.search(r"[A-Za-z]", head[leading.end():]):
                sku = leading.group("no")
                head = head[leading.end():].strip(" .-*")
        if sku_match:
            # Walmart prints a second flag between the UPC and the price -- "F"
            # for food, "N" for non-taxable -- which is not part of the item
            # name. Drop it only when it is a lone letter sitting directly after
            # the SKU, so a description that genuinely ends in one ("VITAMIN D")
            # is left alone.
            trailer = head[sku_match.end():].strip(" .-*")
            head = head[: sku_match.start()] + ("" if len(trailer) <= 1 else f" {trailer}")

        quantity: float | None = None
        unit_price: str | None = None
        weighed = _WEIGHED.match(head)
        if weighed and carried:
            # The weighing half of a two-line item: take the name and barcode
            # from the line above and the rate from this one.
            quantity = float(weighed.group("qty"))
            unit_price = weighed.group("price").replace(",", "")
            head, sku = carried[0], sku or carried[1]
        else:
            qty_match = _QTY_AT_PRICE.search(head)
            if qty_match:
                quantity = float(qty_match.group("qty"))
                unit_price = qty_match.group("unit").replace(",", "")
                head = head[: qty_match.start()].strip(" .-*x")
            else:
                lead = _LEADING_QTY.match(head)
                if lead:
                    quantity = float(lead.group("qty"))
                    head = head[lead.end():].strip()
        carried = None

        description = _repair_letter_o(re.sub(r"\s{2,}", " ", head).strip(" .-*"))
        # A "description" of one character or pure punctuation means the regex
        # latched onto a barcode or a phone number, not a purchase. A bare
        # barcode is the exception: some items have no printed name at all, and
        # rejecting those loses real money off the receipt.
        if (len(re.sub(r"[^A-Za-z]", "", description)) < 2
                and not _BARE_BARCODE.match(description)):
            continue
        if to_cents(amount_text) in (None, 0):
            continue

        if sku is None and amount_text.strip("$ ") in payment_amounts:
            suspect.append(len(items))

        items.append(
            ExtractedItem(
                description=description,
                sku=sku,
                quantity=quantity,
                unit_price=unit_price,
                amount=amount_text,
                is_discount=is_discount,
                taxable=_tax_flag(match.group("pair"), match.group("flag")),
            )
        )

    if suspect and len(suspect) < len(items):
        for index in reversed(suspect):
            items.pop(index)
    return items


def _name_without_price(line: str) -> tuple[str, str | None] | None:
    """A line that names an item and gives its barcode, but quotes no price.

    That is the first half of a weighed item. Anything else -- a slogan, an
    address, a line with no barcode at all -- is not worth carrying forward.
    """
    sku_match = _SKU.search(line)
    if not sku_match:
        return None
    head = line[: sku_match.start()].strip(" .-*")
    if len(re.sub(r"[^A-Za-z]", "", head)) < 2:
        return None
    return re.sub(r"\s{2,}", " ", head).strip(" .-*"), sku_match.group(1)
