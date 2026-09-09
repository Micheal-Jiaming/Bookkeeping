"""Scan orchestration: image on disk -> reviewed, categorised receipt row.

A scan is slow (seconds of model latency), so it must not happen inside the HTTP
request that uploaded the file. Uploads return immediately with a receipt id in
status ``scanning``; a small thread pool does the work and writes the result
back; the browser polls. This is the same shape as Receipt Wrangler's async
queue, minus the Redis dependency -- for a single-user local app a two-worker
pool is the right size, and it keeps the whole application to one process.

Failure policy: a scan that fails does not lose the receipt. The row stays, with
status ``failed`` and the error message, and the user can re-scan after fixing
the cause or fill the fields in by hand.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

from . import lookup, settings_store
from .categorize import category_index, category_names, load_rules, resolve_category
from .db import IMAGE_DIR, connect
from .extract import (ExtractedItem, ExtractedReceipt, ExtractionError,
                      ExtractionResult, build_engines)
from .lookup import shorthand
from .money import from_cents, to_cents
from .validate import check

log = logging.getLogger("bookkeeping.pipeline")

# ``line_item.name_source`` for a line whose barcode was looked up and came
# back with nothing. Distinct from NULL, which means nothing was ever asked.
NOT_FOUND = "notfound"

MAX_WORKERS = 2
_executor = ThreadPoolExecutor(max_workers=MAX_WORKERS, thread_name_prefix="scan")
# Receipt ids currently being scanned, so a double-click on "Re-scan" cannot
# start two scans that race to write the same row.
_in_flight: set[int] = set()
_in_flight_lock = threading.Lock()


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def submit_scan(receipt_id: int) -> bool:
    """Queue a scan. Returns False if one is already running for this receipt."""
    with _in_flight_lock:
        if receipt_id in _in_flight:
            return False
        _in_flight.add(receipt_id)
    _set_status(receipt_id, "scanning", error=None)
    _executor.submit(_run_scan, receipt_id)
    return True


def shutdown() -> None:
    _executor.shutdown(wait=False, cancel_futures=True)


def _run_scan(receipt_id: int) -> None:
    try:
        scan_now(receipt_id)
    except Exception:  # a worker thread that dies silently is a debugging hole
        log.exception("Scan of receipt %s crashed", receipt_id)
        _set_status(receipt_id, "failed", error="Internal error during scan; see the log.")
    finally:
        with _in_flight_lock:
            _in_flight.discard(receipt_id)


def busy() -> bool:
    """Whether any scan is running. The window uses this to keep polling."""
    with _in_flight_lock:
        return bool(_in_flight)


def scan_now(receipt_id: int) -> dict:
    """Run the engines against a receipt's image and store the reading.

    Synchronous and safe to call directly (the tests do). Returns the updated
    receipt row.
    """
    with connect() as db:
        row = db.execute("SELECT * FROM receipt WHERE id = ?", (receipt_id,)).fetchone()
        if row is None:
            raise KeyError(f"No receipt {receipt_id}")
        categories = category_names(db)

    image_path = IMAGE_DIR / (row["image_path"] or "")
    if not row["image_path"] or not image_path.exists():
        _set_status(receipt_id, "failed", error="The stored image file is missing.")
        return _fetch(receipt_id)

    settings = settings_store.get_all()
    engines = build_engines(settings)
    if not engines:
        _set_status(
            receipt_id,
            "needs_review",
            error="Recognition is set to 'manual'; enter the receipt by hand.",
        )
        return _fetch(receipt_id)

    attempts: list[str] = []
    result: ExtractionResult | None = None
    for engine in engines:
        try:
            result = engine.extract(image_path, categories)
            break
        except ExtractionError as exc:
            attempts.append(f"{engine.name}: {exc}")
            log.warning("Engine %s failed on receipt %s: %s", engine.name, receipt_id, exc)
        except Exception as exc:  # unexpected engine bug: try the next engine
            attempts.append(f"{engine.name}: unexpected error: {exc}")
            log.exception("Engine %s crashed on receipt %s", engine.name, receipt_id)

    if result is None:
        _set_status(receipt_id, "failed", error=" | ".join(attempts))
        return _fetch(receipt_id)

    name_sources = _expand_item_names(result, settings)
    _translate_item_names(result, settings)
    _store_result(receipt_id, result, fallback_notes=attempts,
                  name_sources=name_sources)
    return _fetch(receipt_id)


def submit_enrich(receipt_id: int) -> bool:
    """Queue the lookup-and-translate pass for a receipt just edited by hand.

    Returns False if that receipt is already busy, so a second save while the
    first pass is still running does not start two writers on the same rows.
    """
    with _in_flight_lock:
        if receipt_id in _in_flight:
            return False
        _in_flight.add(receipt_id)
    _executor.submit(_run_enrich, receipt_id)
    return True


def _run_enrich(receipt_id: int) -> None:
    try:
        enrich_now(receipt_id)
    except Exception:  # a worker thread that dies silently is a debugging hole
        log.exception("Enriching receipt %s crashed", receipt_id)
    finally:
        with _in_flight_lock:
            _in_flight.discard(receipt_id)


def enrich_now(receipt_id: int) -> dict[str, int]:
    """Look up and translate the lines of a receipt already in the books.

    **Why this is not part of the scan.** A line the reviewer typed or corrected
    never went through a scan, so nothing had ever offered it a product name or
    a translation -- a hand-added "DOVE BW 11OZ" sat blank for ever, and the
    user reported it as the application ignoring what they had entered. It was
    not ignoring it; it had never been asked.

    Runs the same two passes a scan runs, over the stored rows instead of a
    fresh reading, and writes back only the expansion and where it came from.
    Amounts, categories, the reviewer's own edits and the receipt's status are
    left exactly as they are: this fills in blanks, it does not re-read
    anything. In particular a confirmed receipt stays confirmed.

    Synchronous and safe to call directly (the tests do). Returns a small tally
    for the log and for callers that want to say what happened.
    """
    settings = settings_store.get_all()
    with connect() as db:
        row = db.execute("SELECT * FROM receipt WHERE id = ?", (receipt_id,)).fetchone()
        if row is None:
            raise KeyError(f"No receipt {receipt_id}")
        stored = db.execute(
            "SELECT id, description, raw_description, name_source, sku, amount_cents "
            "FROM line_item WHERE receipt_id = ? ORDER BY line_no ASC, id ASC",
            (receipt_id,)).fetchall()

    receipt = ExtractedReceipt(
        currency=row["currency"] or "USD",
        merchant=row["merchant"] or row["merchant_raw"],
        items=[ExtractedItem(description=item["description"] or "",
                             readable_name=item["raw_description"],
                             sku=item["sku"],
                             amount=from_cents(item["amount_cents"]))
               for item in stored],
    )
    result = ExtractionResult(receipt=receipt, engine=row["engine"] or "manual")

    sources = _expand_item_names(result, settings)
    translated = _translate_item_names(result, settings)

    written = 0
    with connect() as db:
        for index, item in enumerate(receipt.items):
            source = sources.get(index)
            if source is None:
                continue
            name = item.readable_name if source != NOT_FOUND else None
            if (name or None) == (stored[index]["raw_description"] or None)                     and source == stored[index]["name_source"]:
                continue
            db.execute(
                "UPDATE line_item SET raw_description = ?, name_source = ? WHERE id = ?",
                (name, source, stored[index]["id"]))
            written += 1

    tally = {"lines": len(stored), "named": written, "translated": translated}
    if written or translated:
        log.info("Enriched receipt %s: %d name(s) written, %d translation(s)",
                 receipt_id, written, translated)
    return tally


def _expand_item_names(
    result: ExtractionResult, settings: dict[str, str]
) -> dict[int, str]:
    """Fill in plain-English names for lines whose printed name is shorthand.

    Runs between reading and storing so the expansion is available to category
    matching as well as to the reviewer -- ``resolve_category`` searches the
    readable name too, which is how ``CLX PLNGR`` reaches Household at all.

    Two sources, in this order. A barcode catalogue names the actual product and
    is tried first. Where the receipt prints no barcode to try -- every line of a
    Costco receipt, because the number beside it is Costco's own item number --
    ``shorthand.expand`` unpicks the abbreviations from the printed text alone.

    Only ever fills a blank. A name the vision model supplied stays: it was
    produced from the receipt in front of it, including context neither of these
    has, so it is the better of the three.

    Returns which lines were filled here and by what, keyed by position in
    ``result.receipt.items``, so the review pane can tell the reader where a name
    came from instead of claiming all of them came from a barcode.
    """
    merchant = result.receipt.merchant or ""
    missing = [(index, item) for index, item in enumerate(result.receipt.items)
               if not (item.readable_name or "").strip()]
    if not missing:
        return {}

    asked = True
    try:
        names = lookup.names_for_skus(
            [item.sku for _, item in missing],
            enabled=settings.get("online_lookup", "1") == "1",
        )
    except Exception:  # offline, DNS down, a service changing shape
        # Not a return: the shorthand pass below reads the printed text and
        # never touches the network, so it still has something to offer on a
        # machine where the lookup could not run at all.
        #
        # ``asked`` stays False so nothing here is recorded as NOT_FOUND. A
        # service that could not be reached has told us nothing about whether
        # the product exists, and writing "no product name found" against the
        # line would state as fact something we do not know -- and would stop
        # the next save from trying again.
        log.exception("Product name lookup failed; keeping the printed names")
        names = {}
        asked = False

    sources: dict[int, str] = {}
    for index, item in missing:
        name = names.get(item.sku or "")
        if name:
            item.readable_name = name
            sources[index] = "barcode"
            continue
        local = shorthand.expand(item.description or "", merchant)
        if local:
            item.readable_name = local
            sources[index] = "shorthand"
        elif asked and lookup.barcode_for(item.sku):
            # A question was asked and nobody had an answer, which is not the
            # same as never having asked. Recorded so the review pane can say
            # so: a line that simply sits blank looks like the application not
            # bothering, and the user reported it as exactly that.
            sources[index] = NOT_FOUND

    filled = {index: source for index, source in sources.items()
              if source != NOT_FOUND}
    if filled:
        by_barcode = sum(1 for source in filled.values() if source == "barcode")
        log.info("Expanded %d of %d abbreviated item name(s): %d from a barcode, "
                 "%d from receipt shorthand",
                 len(filled), len(missing), by_barcode, len(filled) - by_barcode)
    return sources


def _translate_item_names(result: ExtractionResult, settings: dict[str, str]) -> int:
    """Fill the Chinese translation cache for this receipt's item names.

    Done here rather than while drawing the review pane, because translating is
    a network round trip per name and the interface must never wait on one. By
    the time a receipt is reviewed the answers are already in the database, and
    the pane reads them without touching the network.

    Only runs when the interface is in Chinese. An English reader would never
    see the result, so translating for them would be latency spent on nothing.
    """
    if settings.get("language", "en") != "zh":
        return 0
    if settings.get("translate_items", "1") != "1":
        return 0

    names = [(item.readable_name or item.description or "").strip()
             for item in result.receipt.items]
    names = [name for name in names if name]
    if not names:
        return 0
    try:
        translated = lookup.chinese_for(names)
    except Exception:  # offline, a service changing shape, anything
        log.exception("Item-name translation failed; the names stay in English")
        return 0
    if translated:
        log.info("Translated %d of %d item name(s) into Chinese",
                 len(translated), len(set(names)))
    return len(translated)


def _store_result(
    receipt_id: int,
    result: ExtractionResult,
    fallback_notes: list[str],
    name_sources: dict[int, str] | None = None,
) -> None:
    receipt = result.receipt
    # A line carrying an expansion that this scan did not produce got it from the
    # vision model, which is the only other thing that fills the field.
    filled_by = name_sources or {}
    with connect() as db:
        rules = load_rules(db)
        by_name = category_index(db)
        merchant = receipt.merchant or receipt.merchant_raw or ""

        items: list[dict] = []
        for index, item in enumerate(receipt.items):
            description = (item.description or "").strip()
            category_id, source = resolve_category(
                rules,
                by_name,
                description=f"{description} {item.readable_name or ''}",
                merchant=merchant,
                model_suggestion=item.category,
            )
            items.append(
                {
                    "line_no": index,
                    "description": description,
                    "raw_description": item.readable_name,
                    "name_source": (
                        filled_by.get(index)
                        or ("model" if item.readable_name else None)
                    ),
                    "sku": item.sku,
                    "quantity": item.quantity,
                    "unit_price_cents": to_cents(item.unit_price),
                    "amount_cents": to_cents(item.amount),
                    "category_id": category_id,
                    "category_source": source,
                    "is_discount": 1 if item.is_discount else 0,
                    "taxable": None if item.taxable is None else int(item.taxable),
                }
            )

        header_category_id, header_source = resolve_category(
            rules,
            by_name,
            description="",
            merchant=merchant,
            model_suggestion=receipt.category,
        )
        if header_source == "default":
            # No rule or suggestion for the header: use whatever the items
            # mostly say, which is more informative than "Uncategorized".
            header_category_id = dominant_category(items) or header_category_id

        total_cents = to_cents(receipt.total)
        duplicate_of = _find_duplicate(db, receipt_id)
        flags = check(
            purchased_at=receipt.purchased_at,
            total_cents=total_cents,
            subtotal_cents=to_cents(receipt.subtotal),
            tax_cents=to_cents(receipt.tax),
            tip_cents=to_cents(receipt.tip),
            items=items,
            confidence=receipt.confidence,
            duplicate_of=duplicate_of,
            items_sold=receipt.items_sold,
        )
        if fallback_notes:
            flags.append(
                "Fell back to another engine after: " + " | ".join(fallback_notes)
            )

        auto_confirm = settings_store.get("auto_confirm_clean", "0") == "1"
        status = "confirmed" if (not flags and auto_confirm) else "needs_review"

        db.execute(
            """
            UPDATE receipt SET
                status = ?, merchant = ?, merchant_raw = ?, purchased_at = ?,
                currency = ?, subtotal_cents = ?, tax_cents = ?, tip_cents = ?,
                total_cents = ?, payment_method = ?, items_sold = ?,
                category_id = ?, notes = ?,
                engine = ?, model = ?, confidence = ?, raw_text = ?,
                raw_response = ?, review_flags = ?, extract_ms = ?,
                input_tokens = ?, output_tokens = ?, cost_usd = ?, error = NULL,
                updated_at = ?
            WHERE id = ?
            """,
            (
                status,
                receipt.merchant,
                receipt.merchant_raw,
                receipt.purchased_at,
                receipt.currency or "USD",
                to_cents(receipt.subtotal),
                to_cents(receipt.tax),
                to_cents(receipt.tip),
                total_cents,
                receipt.payment_method,
                receipt.items_sold,
                header_category_id,
                receipt.notes,
                result.engine,
                result.model,
                receipt.confidence,
                result.raw_text,
                result.raw_response,
                json.dumps(flags),
                result.elapsed_ms,
                result.input_tokens,
                result.output_tokens,
                result.cost_usd,
                now_iso(),
                receipt_id,
            ),
        )
        db.execute("DELETE FROM line_item WHERE receipt_id = ?", (receipt_id,))
        for item in items:
            db.execute(
                """
                INSERT INTO line_item (
                    receipt_id, line_no, description, raw_description,
                    name_source, sku,
                    quantity, unit_price_cents, amount_cents, category_id,
                    category_source, is_discount, taxable
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    receipt_id,
                    item["line_no"],
                    item["description"],
                    item["raw_description"],
                    item["name_source"],
                    item["sku"],
                    item["quantity"],
                    item["unit_price_cents"],
                    item["amount_cents"],
                    item["category_id"],
                    item["category_source"],
                    item["is_discount"],
                    item["taxable"],
                ),
            )


def dominant_category(items: list[dict]) -> int | None:
    """The category accounting for the most money across the line items."""
    totals: dict[int, int] = {}
    for item in items:
        category_id = item.get("category_id")
        amount = item.get("amount_cents") or 0
        if category_id is None or amount <= 0:
            continue
        totals[category_id] = totals.get(category_id, 0) + amount
    if not totals:
        return None
    return max(totals.items(), key=lambda pair: pair[1])[0]


def _find_duplicate(db: sqlite3.Connection, receipt_id: int) -> int | None:
    row = db.execute(
        "SELECT image_sha256 FROM receipt WHERE id = ?", (receipt_id,)
    ).fetchone()
    if not row or not row["image_sha256"]:
        return None
    other = db.execute(
        "SELECT id FROM receipt WHERE image_sha256 = ? AND id <> ? ORDER BY id ASC LIMIT 1",
        (row["image_sha256"], receipt_id),
    ).fetchone()
    return other["id"] if other else None


def _set_status(receipt_id: int, status: str, error: str | None) -> None:
    with connect() as db:
        db.execute(
            "UPDATE receipt SET status = ?, error = ?, updated_at = ? WHERE id = ?",
            (status, error, now_iso(), receipt_id),
        )


def _fetch(receipt_id: int) -> dict:
    with connect() as db:
        return db.execute("SELECT * FROM receipt WHERE id = ?", (receipt_id,)).fetchone()


def image_dir() -> Path:
    IMAGE_DIR.mkdir(parents=True, exist_ok=True)
    return IMAGE_DIR
