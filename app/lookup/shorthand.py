"""Expanding the shorthand a till prints, without asking anybody.

``app/lookup/product_names.py`` turns a receipt line into a product name by
looking its barcode up in a catalogue. That is the better answer where it works,
and at Costco it cannot work at all: Costco prints a five- to seven-digit
*item number* of its own devising -- 96716, 1199652 -- not a UPC, so
``barcode_for`` correctly declines every line and there is nothing to query.

**Costco's own website is not the way round that, and was measured rather than
assumed.** ``costco.com`` serves its home page to a program, then answers both
the catalogue search (``/CatalogSearch?keyword=...``) and any product page with
``403 Forbidden`` -- an Akamai "Access Denied" body, not a missing product.
``search.costco.com``'s query API answers 403 as well. This is the same wall
walmart.com puts up (see the note in ``product_names.py``), and it does not come
down for a different User-Agent, because it is not a User-Agent check.

So the expansion has to come from what is already on the paper. That is less
than a catalogue gives and it is not nothing: most of what makes a Costco line
unreadable is a handful of abbreviations used the same way on every receipt the
chain prints.

**Why bother, when the printed name is right there.** Two things downstream read
the name rather than showing it. ``resolve_category`` searches the expansion as
well as the printed text, and the Chinese translation is made from it -- and a
general-purpose translator handed till shorthand produces confident nonsense.
Measured on the receipt behind this module, translating the printed line against
translating the expansion:

    DRUMSTICKS      鼓槌 (drum sticks)        Chicken Drumsticks     鸡腿
    BUTER CROISS    黄油克罗斯                 Butter Croissants      黄油牛角面包
    KS CAGE FREE    KS 笼子免费 (free of        Kirkland Signature     柯克兰招牌无笼鸡蛋
                    charge)                   Cage Free Eggs

**What may go in the tables below.** Only an abbreviation with one meaning in a
shop, which no ordinary word is spelled like. The rule the rest of this project
works to applies here with more force than usual, because this expansion is
shown to a reviewer as though it were the product: a guess that fails is
harmless, and a guess that succeeds puts somebody else's goods on the line. So
``GP WINGS``, ``KSBLUEDISH`` and ``KS CAL 500CT`` keep their ``GP``,
``BLUEDISH`` and ``CAL`` -- each has a likely reading and none has a certain
one, and being *nearly* right about what a person bought is the failure this
declines to risk.
"""

from __future__ import annotations

import re

# Abbreviations safe on a receipt from any shop. Each is a whole token on the
# printed line and none of them is an English word, so matching them cannot
# swallow a real one.
#
# BUTER is Costco's own spelling, printed that way on the paper -- it is not an
# OCR error being papered over, and it is here because the till really does
# print it.
GENERIC: dict[str, str] = {
    "ORG": "Organic",
    "CROISS": "Croissants",
    "BUTER": "Butter",
}

# Abbreviations that mean this at one chain and nothing reliable anywhere else.
# "KS" is Kirkland Signature on a Costco receipt; on somebody else's receipt it
# is two letters, so it is expanded only once the shop is known.
BY_MERCHANT: dict[str, dict[str, str]] = {
    "Costco": {"KS": "Kirkland Signature"},
}

# A pack size, printed shut against its number: "500CT" is five hundred of them.
# Split rather than translated, because "500CT" reaches a translator as a word
# it has never seen and "500 count" reaches it as a quantity.
_PACK_SIZE = re.compile(r"^(\d+)CT$", re.IGNORECASE)

# A token the till has run together with a store-brand prefix -- "KSDAILY",
# "KSBLUEDISH". Splitting needs a floor on what is left over, or "KS" would be
# shaved off any word that happens to start with those letters; four characters
# is enough that the remainder is a word rather than a fragment.
_MIN_REMAINDER = 4


def _split_prefix(token: str, prefixes: tuple[str, ...]) -> list[str]:
    """``KSDAILY`` -> ``['KS', 'DAILY']``, or the token unchanged."""
    upper = token.upper()
    for prefix in prefixes:
        remainder = upper[len(prefix):]
        if (upper.startswith(prefix) and len(remainder) >= _MIN_REMAINDER
                and remainder.isalpha()):
            return [token[:len(prefix)], token[len(prefix):]]
    return [token]


def _readable(token: str) -> str:
    """Title-case a shouted word, leaving initialisms and numbers alone.

    The till prints in capitals and this name is shown to a reader, so SPINACH
    becomes Spinach. Anything under three letters is left as it is: GP may well
    be a brand's initials, and "Gp" would be a worse guess than leaving it.
    """
    return token.title() if len(token) >= 3 and token.isalpha() else token


def expand(description: str, merchant: str | None = None) -> str | None:
    """A readable version of ``description``, or ``None`` if nothing expanded.

    ``None`` rather than the unchanged text on purpose: the caller stores this
    as the line's expansion, and a line whose printed name needed no help should
    have no expansion recorded against it rather than a copy of itself.
    """
    text = (description or "").strip()
    if not text:
        return None

    chain = BY_MERCHANT.get((merchant or "").strip(), {})
    table = {**GENERIC, **chain}
    prefixes = tuple(chain)

    words: list[str] = []
    changed = False
    for token in text.split():
        pieces = _split_prefix(token, prefixes) if prefixes else [token]
        if len(pieces) > 1:
            changed = True
        for piece in pieces:
            expansion = table.get(piece.upper())
            if expansion:
                words.append(expansion)
                changed = True
                continue
            pack = _PACK_SIZE.match(piece)
            if pack:
                words.append(f"{pack.group(1)} count")
                changed = True
                continue
            words.append(_readable(piece))

    return " ".join(words) if changed else None
