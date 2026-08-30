"""Plain-English product names for receipt lines, with a local cache.

The cache is the reason this is usable rather than merely possible. A product
name never changes, the free sources are rate-limited, and a household buys the
same milk every week -- so the second receipt from a shop should ask the network
almost nothing. Misses are cached too, and deliberately: without that, the eight
lines on the reference receipt that no database knows would be re-queried on
every single scan and would exhaust the daily quota on questions already
answered.

A miss is retried after ``MISS_RETRY_DAYS`` because these catalogues grow. A hit
is kept forever.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Iterable

from ..db import connect
from .product_names import Found, resolve_many
from .upc import barcode_for, check_digit, is_valid

__all__ = [
    "Found",
    "barcode_for",
    "check_digit",
    "is_valid",
    "names_for_skus",
]

log = logging.getLogger("bookkeeping.lookup")

MISS_RETRY_DAYS = 30


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _stale(fetched_at: str | None) -> bool:
    """True when a cached miss is old enough to be worth asking about again."""
    if not fetched_at:
        return True
    try:
        when = datetime.fromisoformat(fetched_at)
    except ValueError:
        return True
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) - when > timedelta(days=MISS_RETRY_DAYS)


def _read_cache(db, barcodes: set[str]) -> dict[str, tuple[str | None, str | None]]:
    """Cached ``barcode -> (name, fetched_at)``, name ``None`` for a known miss."""
    if not barcodes:
        return {}
    holes = ",".join("?" * len(barcodes))
    rows = db.execute(
        f"SELECT upc, name, fetched_at FROM product_name WHERE upc IN ({holes})",
        tuple(barcodes)).fetchall()
    return {row["upc"]: (row["name"], row["fetched_at"]) for row in rows}


def names_for_skus(
    skus: Iterable[str | None], *, enabled: bool = True
) -> dict[str, str]:
    """Map the numbers printed on a receipt to readable product names.

    Keyed by the sku exactly as printed, so a caller can look up what it already
    holds without repeating the barcode arithmetic. Only resolved lines appear.

    ``enabled=False`` restricts this to the cache and never touches the network,
    which is what the "look products up online" setting turns off. Barcodes seen
    on an earlier, online scan keep working offline, which is the behaviour a
    user disabling the setting would expect.
    """
    printed_to_barcode = {}
    for sku in skus:
        barcode = barcode_for(sku)
        if barcode and sku:
            printed_to_barcode[sku] = barcode
    if not printed_to_barcode:
        return {}

    wanted = set(printed_to_barcode.values())
    with connect() as db:
        cached = _read_cache(db, wanted)

    resolved: dict[str, str] = {
        barcode: name for barcode, (name, _) in cached.items() if name
    }
    if enabled:
        ask = sorted(
            barcode for barcode in wanted
            if barcode not in cached or (
                cached[barcode][0] is None and _stale(cached[barcode][1]))
        )
        if ask:
            fresh = resolve_many(ask)
            _write_cache(fresh)
            resolved.update(
                {barcode: found.name for barcode, found in fresh.items() if found})

    return {
        printed: resolved[barcode]
        for printed, barcode in printed_to_barcode.items()
        if barcode in resolved
    }


def _write_cache(results: dict[str, Found | None]) -> None:
    """Record hits and misses alike. Never lets a cache write break a scan."""
    if not results:
        return
    stamp = _now()
    rows = [(barcode, found.name if found else None,
             found.source if found else None, stamp)
            for barcode, found in results.items()]
    try:
        with connect() as db:
            db.executemany(
                "INSERT INTO product_name (upc, name, source, fetched_at) "
                "VALUES (?, ?, ?, ?) "
                "ON CONFLICT(upc) DO UPDATE SET name = excluded.name, "
                "source = excluded.source, fetched_at = excluded.fetched_at",
                rows)
    except Exception:
        log.exception("Could not cache %d product name(s)", len(rows))
