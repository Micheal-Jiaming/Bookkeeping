"""Everything the application does to the books, as plain function calls.

This module is the whole service layer. It knows about SQLite, receipts, line
items, categories, rules and reports, and it knows nothing about the user
interface -- no Tk widgets, no HTTP. That separation is what let the interface be
replaced (a browser page in 1.0/1.1, a desktop window in 1.2) without touching
the logic underneath, and it is why the logic can be tested without opening a
window.

Two conventions hold throughout:

* Money is passed and returned as **integer cents**. Callers that need a display
  string use ``app.money.from_cents``; callers holding user-typed text pass it
  through ``to_cents``.
* Returned rows are plain dicts, already joined to the names the UI wants
  (``category_name``, ``item_count``), with ``review_flags`` decoded from JSON.
"""

from __future__ import annotations

import csv
import json
import logging
import re
import sqlite3
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path

from . import settings_store
from .categorize import category_index, category_names, load_rules, resolve_category
from .db import IMAGE_DIR, connect
from .extract import sha256_file
from .images import normalise
from .money import from_cents, to_cents
from .validate import check

log = logging.getLogger("bookkeeping.store")

MAX_IMAGE_BYTES = 20 * 1024 * 1024
STATUSES = ("uploaded", "scanning", "needs_review", "confirmed", "failed")
DEFAULT_REPORT_STATUSES = ("confirmed",)


class StoreError(Exception):
    """A request that cannot be carried out, with a message fit to show a user."""


@dataclass
class ItemEdit:
    """One line as the reviewer left it. Amounts may be text or cents."""

    description: str = ""
    quantity: float | None = None
    unit_price_cents: int | None = None
    amount_cents: int | None = None
    category_id: int | None = None
    category_source: str = "manual"
    raw_description: str | None = None
    sku: str | None = None
    is_discount: bool = False
    taxable: bool | None = None


@dataclass
class ReceiptEdit:
    """A receipt's header as the reviewer left it."""

    merchant: str | None = None
    purchased_at: str | None = None
    currency: str = "USD"
    subtotal_cents: int | None = None
    tax_cents: int | None = None
    tip_cents: int | None = None
    total_cents: int | None = None
    payment_method: str | None = None
    category_id: int | None = None
    notes: str | None = None
    items: list[ItemEdit] = field(default_factory=list)


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


# --------------------------------------------------------------------------- #
# Reading receipts
# --------------------------------------------------------------------------- #


def status_counts() -> dict[str, int]:
    with connect() as db:
        return {
            row["status"]: row["n"]
            for row in db.execute(
                "SELECT status, COUNT(*) AS n FROM receipt GROUP BY status"
            ).fetchall()
        }


def list_receipts(
    *,
    statuses: list[str] | tuple[str, ...] | None = None,
    query: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    category_id: int | None = None,
    limit: int = 500,
    offset: int = 0,
) -> tuple[int, list[dict]]:
    """Filtered receipt list, newest first. Returns (total matching, rows)."""
    # Every condition is qualified with the `r.` alias: the listing joins
    # category, and an unqualified `id` would be ambiguous across the two tables.
    where: list[str] = []
    params: list[object] = []
    wanted = [s for s in (statuses or ()) if s in STATUSES]
    if wanted:
        where.append(f"r.status IN ({','.join('?' * len(wanted))})")
        params.extend(wanted)
    if query:
        where.append(
            "(r.merchant LIKE ? OR r.merchant_raw LIKE ? OR r.notes LIKE ? "
            "OR r.id IN (SELECT receipt_id FROM line_item WHERE description LIKE ?))"
        )
        params.extend([f"%{query}%"] * 4)
    if date_from:
        where.append("r.purchased_at >= ?")
        params.append(date_from)
    if date_to:
        where.append("r.purchased_at <= ?")
        params.append(date_to)
    if category_id:
        where.append(
            "(r.category_id = ? OR r.id IN "
            "(SELECT receipt_id FROM line_item WHERE category_id = ?))"
        )
        params.extend([category_id, category_id])

    clause = f"WHERE {' AND '.join(where)}" if where else ""
    with connect() as db:
        total = db.execute(
            f"SELECT COUNT(*) AS n FROM receipt r {clause}", params
        ).fetchone()["n"]
        rows = db.execute(
            f"SELECT r.*, c.name AS category_name, c.color AS category_color, "
            f"(SELECT COUNT(*) FROM line_item li WHERE li.receipt_id = r.id) AS item_count "
            f"FROM receipt r LEFT JOIN category c ON c.id = r.category_id {clause} "
            f"ORDER BY COALESCE(r.purchased_at, r.created_at) DESC, r.id DESC "
            f"LIMIT ? OFFSET ?",
            [*params, limit, offset],
        ).fetchall()
    return total, [_decode(row) for row in rows]


def get_receipt(receipt_id: int) -> dict:
    """One receipt with its line items. Raises StoreError if it is gone."""
    with connect() as db:
        row = db.execute(
            "SELECT r.*, c.name AS category_name, c.color AS category_color "
            "FROM receipt r LEFT JOIN category c ON c.id = r.category_id WHERE r.id = ?",
            (receipt_id,),
        ).fetchone()
        if row is None:
            raise StoreError(f"Receipt #{receipt_id} is no longer in the books.")
        items = db.execute(
            "SELECT li.*, c.name AS category_name, c.color AS category_color "
            "FROM line_item li LEFT JOIN category c ON c.id = li.category_id "
            "WHERE li.receipt_id = ? ORDER BY li.line_no ASC, li.id ASC",
            (receipt_id,),
        ).fetchall()
    receipt = _decode(row)
    receipt["items"] = [dict(item) for item in items]
    receipt["items_total_cents"] = sum((i["amount_cents"] or 0) for i in items)
    return receipt


def image_path(receipt_id: int) -> Path | None:
    """Where the stored image is, or None if this receipt has no readable image."""
    with connect() as db:
        row = db.execute(
            "SELECT image_path FROM receipt WHERE id = ?", (receipt_id,)
        ).fetchone()
    if row is None or not row["image_path"]:
        return None
    path = IMAGE_DIR / row["image_path"]
    return path if path.exists() else None


def _decode(row: dict) -> dict:
    out = dict(row)
    out["review_flags"] = json.loads(row.get("review_flags") or "[]")
    return out


# --------------------------------------------------------------------------- #
# Creating receipts
# --------------------------------------------------------------------------- #


def create_from_image(data: bytes, original_name: str) -> int:
    """Normalise and store an image, returning the new receipt id.

    Raises StoreError with a message fit for a dialog when the bytes are not a
    usable image.
    """
    if not data:
        raise StoreError(f"{original_name} is empty.")
    if len(data) > MAX_IMAGE_BYTES:
        raise StoreError(
            f"{original_name} is larger than the "
            f"{MAX_IMAGE_BYTES // (1024 * 1024)} MB limit."
        )
    try:
        png = normalise(data)
    except ValueError as exc:  # ImageError subclasses ValueError
        raise StoreError(f"{original_name}: {exc}") from exc

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", Path(original_name or "receipt").stem)[:40]
    name = f"{stamp}_{safe}.png"
    IMAGE_DIR.mkdir(parents=True, exist_ok=True)
    path = IMAGE_DIR / name
    path.write_bytes(png)

    with connect() as db:
        cursor = db.execute(
            "INSERT INTO receipt (status, image_path, image_sha256, original_name, "
            "currency, created_at, updated_at) VALUES ('uploaded', ?, ?, ?, ?, ?, ?)",
            (
                name,
                sha256_file(path),
                original_name,
                settings_store.get("currency", "USD"),
                now_iso(),
                now_iso(),
            ),
        )
        return int(cursor.lastrowid)


def create_manual() -> int:
    """A blank receipt for hand entry (a lost paper receipt)."""
    with connect() as db:
        cursor = db.execute(
            "INSERT INTO receipt (status, currency, purchased_at, engine, created_at, "
            "updated_at) VALUES ('needs_review', ?, ?, 'manual', ?, ?)",
            (
                settings_store.get("currency", "USD"),
                date.today().isoformat(),
                now_iso(),
                now_iso(),
            ),
        )
        return int(cursor.lastrowid)


# --------------------------------------------------------------------------- #
# Editing receipts
# --------------------------------------------------------------------------- #


def save_receipt(receipt_id: int, edit: ReceiptEdit, *, confirm: bool = False) -> dict:
    """Save reviewer edits and return the stored receipt.

    Items are replaced wholesale: the review pane always holds the full list, and
    diffing rows the user may have reordered or deleted would be more code and
    more ways to lose a line. Any category the reviewer touched keeps its
    ``manual`` source so a later rule backfill leaves it alone.
    """
    items = [
        {
            "line_no": index,
            "description": (item.description or "").strip(),
            "raw_description": item.raw_description,
            "sku": item.sku,
            "quantity": item.quantity,
            "unit_price_cents": item.unit_price_cents,
            "amount_cents": item.amount_cents,
            "category_id": item.category_id,
            "category_source": item.category_source or "manual",
            "is_discount": 1 if item.is_discount else 0,
            "taxable": None if item.taxable is None else int(item.taxable),
        }
        for index, item in enumerate(edit.items)
    ]

    flags = check(
        purchased_at=edit.purchased_at,
        total_cents=edit.total_cents,
        subtotal_cents=edit.subtotal_cents,
        tax_cents=edit.tax_cents,
        tip_cents=edit.tip_cents,
        items=items,
        confidence=None,
    )
    # Confirming is the reviewer's call: they may confirm a receipt whose
    # arithmetic still disagrees (some receipts really do not add up), and the
    # flags stay attached as a record of why it was questioned.
    status = "confirmed" if confirm else "needs_review"
    # A hand-entered receipt usually has no header category. Derive it from the
    # lines, the same way a scanned one does, so the listing and the merchant
    # report are not full of blanks.
    category_id = edit.category_id or dominant_category(items)

    with connect() as db:
        if db.execute("SELECT 1 FROM receipt WHERE id = ?", (receipt_id,)).fetchone() is None:
            raise StoreError(f"Receipt #{receipt_id} is no longer in the books.")
        db.execute(
            """
            UPDATE receipt SET merchant = ?, purchased_at = ?, currency = ?,
                subtotal_cents = ?, tax_cents = ?, tip_cents = ?, total_cents = ?,
                payment_method = ?, category_id = ?, notes = ?, status = ?,
                review_flags = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                (edit.merchant or "").strip() or None,
                (edit.purchased_at or "").strip() or None,
                edit.currency or "USD",
                edit.subtotal_cents,
                edit.tax_cents,
                edit.tip_cents,
                edit.total_cents,
                edit.payment_method,
                category_id,
                edit.notes,
                status,
                json.dumps(flags),
                now_iso(),
                receipt_id,
            ),
        )
        _replace_items(db, receipt_id, items)
    return get_receipt(receipt_id)


def confirm_receipt(receipt_id: int) -> dict:
    with connect() as db:
        row = db.execute("SELECT * FROM receipt WHERE id = ?", (receipt_id,)).fetchone()
        if row is None:
            raise StoreError(f"Receipt #{receipt_id} is no longer in the books.")
        if row["total_cents"] is None:
            raise StoreError("This receipt has no total yet, so it cannot be confirmed.")
        if not row["purchased_at"]:
            raise StoreError("This receipt has no date yet, so it cannot be confirmed.")
        db.execute(
            "UPDATE receipt SET status = 'confirmed', updated_at = ? WHERE id = ?",
            (now_iso(), receipt_id),
        )
    return get_receipt(receipt_id)


def delete_receipt(receipt_id: int, *, keep_image: bool = False) -> None:
    with connect() as db:
        row = db.execute(
            "SELECT image_path FROM receipt WHERE id = ?", (receipt_id,)
        ).fetchone()
        if row is None:
            raise StoreError(f"Receipt #{receipt_id} is no longer in the books.")
        db.execute("DELETE FROM receipt WHERE id = ?", (receipt_id,))
    if row["image_path"] and not keep_image:
        # The row is gone either way; a failure to unlink the file must not look
        # to the user like the delete did not happen.
        try:
            (IMAGE_DIR / row["image_path"]).unlink(missing_ok=True)
        except OSError as exc:
            log.warning("Could not delete image for receipt %s: %s", receipt_id, exc)


def _replace_items(db: sqlite3.Connection, receipt_id: int, items: list[dict]) -> None:
    db.execute("DELETE FROM line_item WHERE receipt_id = ?", (receipt_id,))
    for item in items:
        db.execute(
            "INSERT INTO line_item (receipt_id, line_no, description, raw_description, "
            "sku, quantity, unit_price_cents, amount_cents, category_id, "
            "category_source, is_discount, taxable) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                receipt_id, item["line_no"], item["description"], item["raw_description"],
                item["sku"], item["quantity"], item["unit_price_cents"],
                item["amount_cents"], item["category_id"], item["category_source"],
                item["is_discount"], item["taxable"],
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


# --------------------------------------------------------------------------- #
# Categories and rules
# --------------------------------------------------------------------------- #


def list_categories() -> list[dict]:
    with connect() as db:
        return [
            dict(row)
            for row in db.execute(
                "SELECT c.*, (SELECT COUNT(*) FROM line_item li WHERE li.category_id = c.id) "
                "AS item_count FROM category c ORDER BY c.sort_order ASC, c.name ASC"
            ).fetchall()
        ]


def model_category_names() -> list[str]:
    with connect() as db:
        return category_names(db)


def create_category(name: str, color: str = "#7a8290", sort_order: int = 100) -> int:
    name = (name or "").strip()
    if not name:
        raise StoreError("A category needs a name.")
    with connect() as db:
        if db.execute(
            "SELECT 1 FROM category WHERE name = ? COLLATE NOCASE", (name,)
        ).fetchone():
            raise StoreError(f"A category named '{name}' already exists.")
        cursor = db.execute(
            "INSERT INTO category (name, kind, color, is_builtin, sort_order) "
            "VALUES (?, 'expense', ?, 0, ?)",
            (name, color, sort_order),
        )
        return int(cursor.lastrowid)


def delete_category(category_id: int) -> None:
    """Delete a category; its lines fall back to Uncategorized."""
    with connect() as db:
        row = db.execute("SELECT * FROM category WHERE id = ?", (category_id,)).fetchone()
        if row is None:
            raise StoreError("That category no longer exists.")
        if row["name"] == "Uncategorized":
            raise StoreError("Uncategorized is the fallback category and cannot be deleted.")
        fallback = db.execute(
            "SELECT id FROM category WHERE name = 'Uncategorized'"
        ).fetchone()
        fallback_id = fallback["id"] if fallback else None
        db.execute(
            "UPDATE line_item SET category_id = ?, category_source = 'default' "
            "WHERE category_id = ?",
            (fallback_id, category_id),
        )
        db.execute(
            "UPDATE receipt SET category_id = ? WHERE category_id = ?",
            (fallback_id, category_id),
        )
        db.execute("DELETE FROM category WHERE id = ?", (category_id,))


def list_rules() -> list[dict]:
    with connect() as db:
        return [
            dict(row)
            for row in db.execute(
                "SELECT r.*, c.name AS category_name FROM category_rule r "
                "JOIN category c ON c.id = r.category_id "
                "ORDER BY r.priority ASC, r.id ASC"
            ).fetchall()
        ]


def create_rule(
    pattern: str, category_id: int, *, field_name: str = "description",
    match_type: str = "contains", priority: int = 100,
) -> int:
    pattern = (pattern or "").strip()
    if not pattern:
        raise StoreError("A rule needs a pattern.")
    if field_name not in {"description", "merchant"}:
        raise StoreError("A rule matches either an item name or a merchant.")
    if match_type not in {"contains", "regex"}:
        raise StoreError("A rule is either a 'contains' or a 'regex' match.")
    if match_type == "regex":
        try:
            re.compile(pattern)
        except re.error as exc:
            raise StoreError(f"That is not a valid regular expression: {exc}") from exc
    with connect() as db:
        cursor = db.execute(
            "INSERT INTO category_rule (field, match_type, pattern, category_id, "
            "priority, enabled, is_builtin) VALUES (?, ?, ?, ?, ?, 1, 0)",
            (field_name, match_type, pattern, category_id, priority),
        )
        return int(cursor.lastrowid)


def delete_rule(rule_id: int) -> None:
    with connect() as db:
        if db.execute(
            "SELECT 1 FROM category_rule WHERE id = ?", (rule_id,)
        ).fetchone() is None:
            raise StoreError("That rule no longer exists.")
        db.execute("DELETE FROM category_rule WHERE id = ?", (rule_id,))


def apply_rules(*, include_confirmed: bool = False) -> tuple[int, int]:
    """Re-run the rule set over stored line items. Returns (examined, changed).

    Manual categorisations are never touched -- the user's own decision outranks
    any rule. By default confirmed receipts are left alone too, so re-running
    rules cannot silently rewrite books that were already signed off.

    A line already categorised by the model is only overwritten by a
    *description* rule, never by a broad merchant rule -- the same precedence
    ``resolve_category`` applies during a scan. A merchant rule can still fill in
    a line that nothing had categorised at all.
    """
    changed = 0
    with connect() as db:
        rules = load_rules(db)
        by_name = category_index(db)
        clause = "" if include_confirmed else "AND r.status <> 'confirmed'"
        rows = db.execute(
            f"SELECT li.id, li.description, li.raw_description, li.category_id, "
            f"li.category_source, r.merchant, r.merchant_raw "
            f"FROM line_item li JOIN receipt r ON r.id = li.receipt_id "
            f"WHERE COALESCE(li.category_source, '') <> 'manual' {clause}"
        ).fetchall()
        for row in rows:
            category_id, source = resolve_category(
                rules,
                by_name,
                description=f"{row['description']} {row['raw_description'] or ''}",
                merchant=row["merchant"] or row["merchant_raw"] or "",
                model_suggestion=None,
            )
            if category_id == row["category_id"]:
                continue
            fills_a_gap = source == "merchant" and (row["category_source"] or "") in (
                "", "default",
            )
            if source == "rule" or fills_a_gap:
                db.execute(
                    "UPDATE line_item SET category_id = ?, category_source = ? WHERE id = ?",
                    (category_id, source, row["id"]),
                )
                changed += 1
    return len(rows), changed


# --------------------------------------------------------------------------- #
# Reports
# --------------------------------------------------------------------------- #


def report_summary(
    *,
    date_from: str | None = None,
    date_to: str | None = None,
    statuses: tuple[str, ...] | list[str] = DEFAULT_REPORT_STATUSES,
) -> dict:
    """Spending rollups.

    Category figures come from line items, which do not include tax. To keep the
    category breakdown summing to the money actually spent, the difference
    between a receipt's total and its itemised lines is reported as its own
    'Tax & unitemised' bucket rather than being quietly dropped.
    """
    wanted = [s for s in statuses if s in STATUSES] or list(DEFAULT_REPORT_STATUSES)
    where = [f"r.status IN ({','.join('?' * len(wanted))})"]
    params: list[object] = list(wanted)
    if date_from:
        where.append("r.purchased_at >= ?")
        params.append(date_from)
    if date_to:
        where.append("r.purchased_at <= ?")
        params.append(date_to)
    clause = " AND ".join(where)

    with connect() as db:
        receipts = db.execute(
            f"SELECT r.id, r.merchant, r.purchased_at, r.total_cents, r.tax_cents, "
            f"r.currency FROM receipt r WHERE {clause}", params
        ).fetchall()
        item_rows = db.execute(
            f"SELECT li.receipt_id, li.amount_cents, li.category_id, "
            f"COALESCE(c.name, 'Uncategorized') AS category_name, "
            f"COALESCE(c.color, '#9aa0aa') AS color "
            f"FROM line_item li JOIN receipt r ON r.id = li.receipt_id "
            f"LEFT JOIN category c ON c.id = li.category_id WHERE {clause}", params
        ).fetchall()
        pending = db.execute(
            "SELECT COUNT(*) AS n FROM receipt WHERE status IN ('needs_review', 'failed', "
            "'scanning', 'uploaded')"
        ).fetchone()["n"]

    spend_cents = sum(row["total_cents"] or 0 for row in receipts)
    tax_cents = sum(row["tax_cents"] or 0 for row in receipts)

    by_category: dict[str, dict] = {}
    for row in item_rows:
        bucket = by_category.setdefault(
            row["category_name"],
            {"category": row["category_name"], "color": row["color"],
             "amount_cents": 0, "items": 0},
        )
        bucket["amount_cents"] += row["amount_cents"] or 0
        bucket["items"] += 1

    itemised: dict[int, int] = defaultdict(int)
    for row in item_rows:
        itemised[row["receipt_id"]] += row["amount_cents"] or 0
    residual = sum(
        max(0, (row["total_cents"] or 0) - itemised.get(row["id"], 0)) for row in receipts
    )
    if residual > 0:
        by_category["Tax & unitemised"] = {
            "category": "Tax & unitemised", "color": "#8a8a8a",
            "amount_cents": residual, "items": 0,
        }

    categories = sorted(by_category.values(), key=lambda b: -b["amount_cents"])
    denominator = sum(max(0, bucket["amount_cents"]) for bucket in categories) or 1
    for bucket in categories:
        bucket["share"] = max(0, bucket["amount_cents"]) / denominator
        bucket["amount"] = from_cents(bucket["amount_cents"])

    by_month: dict[str, int] = defaultdict(int)
    for row in receipts:
        if row["purchased_at"]:
            by_month[row["purchased_at"][:7]] += row["total_cents"] or 0
    months = [
        {"month": month, "amount_cents": cents, "amount": from_cents(cents)}
        for month, cents in sorted(by_month.items())
    ]

    by_merchant: dict[str, dict] = {}
    for row in receipts:
        name = row["merchant"] or "(unknown)"
        bucket = by_merchant.setdefault(
            name, {"merchant": name, "amount_cents": 0, "receipts": 0}
        )
        bucket["amount_cents"] += row["total_cents"] or 0
        bucket["receipts"] += 1
    merchants = sorted(by_merchant.values(), key=lambda b: -b["amount_cents"])[:12]
    for bucket in merchants:
        bucket["amount"] = from_cents(bucket["amount_cents"])

    return {
        "date_from": date_from,
        "date_to": date_to,
        "statuses": wanted,
        "totals": {
            "receipts": len(receipts),
            "spend_cents": spend_cents,
            "spend": from_cents(spend_cents),
            "tax_cents": tax_cents,
            "tax": from_cents(tax_cents),
            "items": len(item_rows),
            "average_cents": round(spend_cents / len(receipts)) if receipts else 0,
        },
        "pending_review": pending,
        "by_category": categories,
        "by_month": months,
        "by_merchant": merchants,
    }


def export_items_csv(
    destination: Path,
    *,
    date_from: str | None = None,
    date_to: str | None = None,
    statuses: tuple[str, ...] | list[str] = DEFAULT_REPORT_STATUSES,
) -> int:
    """Write one CSV row per line item. Returns the number of data rows."""
    wanted = [s for s in statuses if s in STATUSES] or list(DEFAULT_REPORT_STATUSES)
    where = [f"r.status IN ({','.join('?' * len(wanted))})"]
    params: list[object] = list(wanted)
    if date_from:
        where.append("r.purchased_at >= ?")
        params.append(date_from)
    if date_to:
        where.append("r.purchased_at <= ?")
        params.append(date_to)

    with connect() as db:
        rows = db.execute(
            f"SELECT r.id AS receipt_id, r.purchased_at, r.merchant, r.currency, "
            f"r.total_cents, li.line_no, li.description, li.quantity, "
            f"li.unit_price_cents, li.amount_cents, li.is_discount, "
            f"COALESCE(c.name, '') AS category, li.category_source "
            f"FROM receipt r LEFT JOIN line_item li ON li.receipt_id = r.id "
            f"LEFT JOIN category c ON c.id = li.category_id "
            f"WHERE {' AND '.join(where)} "
            f"ORDER BY r.purchased_at ASC, r.id ASC, li.line_no ASC", params
        ).fetchall()

    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    # newline="" plus utf-8-sig: Excel opens the file with the right encoding and
    # without a blank line between every row.
    with destination.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow([
            "receipt_id", "date", "merchant", "currency", "receipt_total", "line_no",
            "description", "quantity", "unit_price", "amount", "is_discount",
            "category", "category_source",
        ])
        for row in rows:
            writer.writerow([
                row["receipt_id"], row["purchased_at"] or "", row["merchant"] or "",
                row["currency"], from_cents(row["total_cents"]),
                row["line_no"] if row["line_no"] is not None else "",
                row["description"] or "",
                row["quantity"] if row["quantity"] is not None else "",
                from_cents(row["unit_price_cents"]), from_cents(row["amount_cents"]),
                "yes" if row["is_discount"] else "no", row["category"],
                row["category_source"] or "",
            ])
    return len(rows)


# Re-exported so the UI has one import for money handling.
__all__ = [
    "ItemEdit", "ReceiptEdit", "StoreError", "apply_rules", "confirm_receipt",
    "create_category", "create_from_image", "create_manual", "create_rule",
    "delete_category", "delete_receipt", "delete_rule", "dominant_category",
    "export_items_csv", "from_cents", "get_receipt", "image_path", "list_categories",
    "list_receipts", "list_rules", "model_category_names", "report_summary",
    "save_receipt", "status_counts", "to_cents",
]
