"""Expanding a receipt's shorthand into a name a person would recognise.

A till prints ``CLX PLNGR``, and no amount of local cleverness turns that into
"Clorox Plunger & Toilet Brush with Carry Caddy" -- the words simply are not in
the string. The expansion has to come from somewhere that knows the product, and
the receipt already carries the key: the barcode number beside each line.

Two free sources answer, and they are complementary rather than redundant:

* **Open Food Facts** -- an open database, no key, no quota. Food and drink only,
  so it resolves the groceries and nothing else.
* **UPCitemdb** -- a commercial catalogue with a free tier that needs no key and
  allows about a hundred lookups a day per address. It covers the household and
  personal-care items Open Food Facts has never heard of.

Measured on the receipt in ``tests/test_real_receipt.py``, twenty distinct lines:

* **12 resolve** with both services answering, split roughly evenly between them.
* **8 resolve** with UPCitemdb's daily allowance spent, Open Food Facts alone.
* Two of the misses can never resolve: a bottle deposit and bakery bread carry
  codes the shop assigned itself (see ``upc.LOCALLY_ASSIGNED``), so the ceiling
  for this receipt is eighteen rather than twenty.

The expansion also feeds category matching, which is where it pays for itself
twice: correct categories on that receipt go from 14/20 to 17/20, because
``HS SH CLS8.5`` and ``AIM TP 5.5OZ`` only reach Personal Care once something
says "shampoo" and "toothpaste". No line was categorised worse.

**Walmart's own website is not one of the sources, and cannot be.** Requests to
walmart.com from a program are answered with a bot-check page titled "Robot or
human?" rather than the product, whatever headers are sent. Their catalogue is
reachable only through the Walmart affiliate/marketplace API, which needs an
approved developer account and a signed key. The UPC route gets the same names
without pretending to be a browser.

Nothing here is allowed to fail loudly. The application's whole premise is that
it reads a receipt with no key, no account and no network; an expansion is an
improvement on a line that already works without it, so every error path ends in
"no name" rather than an exception.
"""

from __future__ import annotations

import json
import logging
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass

from .upc import barcode_for

log = logging.getLogger("bookkeeping.lookup")

# Open Food Facts asks callers to identify themselves rather than pose as a
# browser, and rate-limits anonymous traffic harder. Saying who we are is also
# the honest thing to do when using somebody's free service.
USER_AGENT = "Bookkeeping/1.5 (personal receipt manager; non-commercial)"

REQUEST_TIMEOUT = 6.0   # seconds per HTTP request
BATCH_BUDGET = 60.0     # seconds for a whole receipt, after which we stop asking

# Minimum gap between two requests to the same host, and the least decorative
# constant in this file. Both numbers were arrived at by being punished:
#
#   * Four concurrent requests -- the obvious way to write this -- made *both*
#     services answer 429 within a dozen calls. A receipt that resolves twelve
#     names resolved six.
#   * Serial requests at 0.7s still drew 429s from Open Food Facts partway
#     through a twenty-line receipt.
#   * Serial requests at 1.2s resolved all twelve, which is where this sits.
#
# Pacing is per host, so asking one service costs nothing against the other's
# allowance. A twenty-line receipt takes roughly half a minute the first time
# and nothing at all afterwards, because every answer is cached.
HOST_INTERVAL = {
    "world.openfoodfacts.org": 1.2,
    "api.upcitemdb.com": 1.2,
}
DEFAULT_INTERVAL = 1.2

# A name long enough to be a catalogue dump rather than a product name. The
# worst offenders run to ingredient lists; anything past this is truncated on a
# word boundary so the review pane stays readable.
MAX_NAME = 90


class Unavailable(Exception):
    """A source could not answer at all -- so nothing about it may be cached."""


class RateLimited(Unavailable):
    """A source's free quota is spent; there is no point asking it again today."""


@dataclass(frozen=True)
class Found:
    """A resolved name and which source knew it, for display and for the log."""

    name: str
    source: str


_pace_lock = threading.Lock()
_last_call: dict[str, float] = {}


def _pace(host: str) -> None:
    """Sleep just long enough that this host is not asked too often."""
    interval = HOST_INTERVAL.get(host, DEFAULT_INTERVAL)
    with _pace_lock:
        now = time.monotonic()
        earliest = _last_call.get(host, 0.0) + interval
        _last_call[host] = max(now, earliest)
        wait = earliest - now
    if wait > 0:
        time.sleep(wait)


def _get(url: str) -> tuple[int | None, str]:
    """One paced HTTP GET. Returns ``(status, body)``; ``None`` status on failure."""
    _pace(urllib.parse.urlsplit(url).hostname or "")
    request = urllib.request.Request(
        url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT) as response:
            return response.status, response.read(40000).decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return exc.code, ""
    except Exception as exc:  # DNS, TLS, timeout, offline machine
        log.debug("Lookup request failed for %s: %s", url, exc)
        return None, ""


def _tidy(name: str | None) -> str | None:
    """Collapse whitespace and cut catalogue prose down to a readable length."""
    text = " ".join((name or "").split())
    if not text:
        return None
    if len(text) > MAX_NAME:
        text = text[:MAX_NAME].rsplit(" ", 1)[0] + "..."
    return text


def _definitive(status: int | None) -> bool:
    """Whether ``status`` means "I looked, and no" rather than "I could not look".

    The distinction decides whether a miss is cached. A 404 from Open Food Facts
    genuinely means the barcode is not a food, and re-asking tomorrow wastes a
    request. A 429 or a 503 means nothing about the product at all -- caching
    that as "unknown" would hide a resolvable item for a month, which is exactly
    the bug this function exists to prevent.
    """
    return status == 404 or status == 200


def open_food_facts(upc: str) -> Found | None:
    """Brand, product and pack size from Open Food Facts. Food and drink only."""
    status, body = _get(
        f"https://world.openfoodfacts.org/api/v2/product/{urllib.parse.quote(upc)}.json"
        "?fields=product_name,brands,quantity")
    if not _definitive(status):
        raise Unavailable(f"openfoodfacts returned {status}")
    if status != 200:
        return None                      # 404 here means "not a food", not an error
    try:
        product = (json.loads(body) or {}).get("product") or {}
    except ValueError:
        return None

    # Brand first so "Great Value Toasted O's 510 g" reads the way the shelf does.
    parts = [product.get(key) for key in ("brands", "product_name", "quantity")]
    name = _tidy(" ".join(str(p) for p in parts if p))
    return Found(name, "openfoodfacts") if name else None


def upcitemdb(upc: str) -> Found | None:
    """Title from UPCitemdb's keyless trial tier. Covers non-food items."""
    status, body = _get(
        f"https://api.upcitemdb.com/prod/trial/lookup?upc={urllib.parse.quote(upc)}")
    if status == 429:
        raise RateLimited("upcitemdb quota reached")
    if not _definitive(status):
        raise Unavailable(f"upcitemdb returned {status}")
    try:
        items = (json.loads(body) or {}).get("items") or []
    except ValueError:
        return None
    if not items:
        return None
    name = _tidy(items[0].get("title"))
    return Found(name, "upcitemdb") if name else None


SOURCES = (open_food_facts, upcitemdb)


def resolve_one(upc: str, exhausted: set[str] | None = None) -> Found | None:
    """Ask each source in turn, the keyless and unmetered one first.

    Returns ``None`` only when every source answered and none recognised the
    barcode -- the one case where "unknown" is a fact worth remembering. If any
    source could not be reached, raises rather than reporting a miss, so a
    transient failure is never written into the cache as a verdict.

    ``exhausted`` names sources already known to have spent their quota; they
    are skipped, and any source that reports a quota is added to it. Passing the
    same set across a batch stops a spent source being asked twenty more times.
    """
    exhausted = set() if exhausted is None else exhausted
    every_source_answered = True
    problems: list[str] = []

    for source in SOURCES:
        if source.__name__ in exhausted:
            every_source_answered = False
            continue
        try:
            found = source(upc)
        except RateLimited as exc:
            exhausted.add(source.__name__)
            every_source_answered = False
            problems.append(str(exc))
            continue
        except Unavailable as exc:
            every_source_answered = False
            problems.append(str(exc))
            continue
        except Exception as exc:  # a source changing its JSON shape is not fatal
            every_source_answered = False
            problems.append(f"{source.__name__}: {exc}")
            log.debug("Source %s failed for %s: %s", source.__name__, upc, exc)
            continue
        if found:
            return found

    if not every_source_answered:
        raise Unavailable("; ".join(problems) or "every source was already spent")
    return None


def resolve_many(upcs: list[str]) -> dict[str, Found | None]:
    """Resolve a receipt's worth of barcodes, one at a time and time-boxed.

    Serial on purpose. Running these in parallel is the obvious optimisation and
    it backfires: the free tiers answer 429 to a burst, and a barcode refused
    that way is indistinguishable from one nobody has heard of. See
    ``HOST_INTERVAL``.

    Every barcode that got an answer appears in the result. A value of ``None``
    means the sources were reached and did not know it, which is worth caching;
    a barcode dropped because the budget ran out or the quota is spent is absent
    entirely, so a later scan asks about it again.

    One source running out of quota does not end the batch. That mistake cost a
    measurement: with UPCitemdb's daily allowance spent, a single 429 aborted the
    whole receipt and the groceries Open Food Facts would happily have named came
    back blank. A spent source is set aside; the run stops only when every source
    is spent, or the clock runs out.
    """
    deadline = time.monotonic() + BATCH_BUDGET
    out: dict[str, Found | None] = {}
    exhausted: set[str] = set()

    for upc in upcs:
        if time.monotonic() > deadline:
            log.info("Product lookup ran out of time; %d of %d barcode(s) "
                     "resolved this pass", len(out), len(upcs))
            break
        if len(exhausted) >= len(SOURCES):
            log.info("Every lookup source has spent its quota; %d of %d "
                     "barcode(s) resolved this pass", len(out), len(upcs))
            break
        try:
            out[upc] = resolve_one(upc, exhausted)
        except Unavailable as exc:
            # This barcode is unresolved for now, and deliberately not recorded,
            # so the next scan asks again instead of trusting a non-answer.
            log.debug("No answer for %s: %s", upc, exc)
    return out
