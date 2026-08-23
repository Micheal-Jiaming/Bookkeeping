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

from . import settings_store
from .categorize import category_index, category_names, load_rules, resolve_category
from .db import IMAGE_DIR, connect
from .extract import ExtractionError, ExtractionResult, build_engines
from .money import to_cents
from .runtime import runtime
from .validate import check

log = logging.getLogger("bookkeeping.pipeline")

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
    # Marked as work in progress so the desktop build's idle watchdog cannot
    # exit mid-scan and throw the reading away.
    runtime.begin_work()
    try:
        scan_now(receipt_id)
    except Exception:  # a worker thread that dies silently is a debugging hole
        log.exception("Scan of receipt %s crashed", receipt_id)
        _set_status(receipt_id, "failed", error="Internal error during scan; see the log.")
    finally:
        runtime.end_work()
        with _in_flight_lock:
            _in_flight.discard(receipt_id)


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

    _store_result(receipt_id, result, fallback_notes=attempts)
    return _fetch(receipt_id)


def _store_result(
    receipt_id: int, result: ExtractionResult, fallback_notes: list[str]
) -> None:
    receipt = result.receipt
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
                total_cents = ?, payment_method = ?, category_id = ?, notes = ?,
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
                    receipt_id, line_no, description, raw_description, sku,
                    quantity, unit_price_cents, amount_cents, category_id,
                    category_source, is_discount, taxable
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    receipt_id,
                    item["line_no"],
                    item["description"],
                    item["raw_description"],
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
