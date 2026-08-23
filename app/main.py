"""HTTP layer: JSON API plus the static single-page UI.

Everything is local. The server binds to 127.0.0.1 by default (see run.bat) and
has no authentication, because adding a login to a single-user application on
localhost buys nothing. That decision is recorded in Bookkeeping.md; do not
expose this process to a network without adding one.

Money crosses this boundary in two forms: ``*_cents`` integers, which are the
truth, and matching decimal strings for display. Requests may send either.
"""

from __future__ import annotations

import csv
import io
import json
import logging
import re
from collections import defaultdict
from contextlib import asynccontextmanager
from datetime import date, datetime, timezone
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import paths, pipeline, settings_store
from .categorize import category_index, category_names, load_rules, resolve_category
from .db import IMAGE_DIR, connect, init_db
from .extract import engine_status, sha256_file
from .images import ImageError, normalise
from .money import from_cents, to_cents
from .runtime import PING_INTERVAL_SECONDS, runtime
from .validate import check

log = logging.getLogger("bookkeeping")

# Resolved through app/paths.py so the frozen build finds the bundled copy under
# sys._MEIPASS rather than next to a source file that no longer exists.
STATIC_DIR = paths.static_dir()
MAX_UPLOAD_BYTES = 20 * 1024 * 1024
VALID_STATUSES = {"uploaded", "scanning", "needs_review", "confirmed", "failed"}


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    log.info("Bookkeeping ready. Database: %s", (IMAGE_DIR.parent / "bookkeeping.db"))
    yield
    pipeline.shutdown()


def _version() -> str:
    """The single source of truth for the version is the VERSION file.

    In the frozen build the file is bundled beside the package, so both
    locations are tried.
    """
    for version_file in (
        paths.resource_dir() / "VERSION",
        Path(__file__).resolve().parent.parent / "VERSION",
    ):
        try:
            text = version_file.read_text(encoding="utf-8").strip()
        except OSError:
            continue
        if text:
            return text
    return "0.0.0"


app = FastAPI(title="Bookkeeping", version=_version(), lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


# --------------------------------------------------------------------------- #
# Request models
# --------------------------------------------------------------------------- #


class ItemIn(BaseModel):
    id: int | None = None
    description: str = ""
    raw_description: str | None = None
    sku: str | None = None
    quantity: float | None = None
    unit_price: str | None = None
    amount: str | None = None
    unit_price_cents: int | None = None
    amount_cents: int | None = None
    category_id: int | None = None
    category_source: str | None = None
    is_discount: bool = False
    taxable: bool | None = None


class ReceiptIn(BaseModel):
    merchant: str | None = None
    purchased_at: str | None = None
    currency: str = "USD"
    subtotal: str | None = None
    tax: str | None = None
    tip: str | None = None
    total: str | None = None
    payment_method: str | None = None
    category_id: int | None = None
    notes: str | None = None
    items: list[ItemIn] = Field(default_factory=list)
    confirm: bool = False


class CategoryIn(BaseModel):
    name: str
    kind: str = "expense"
    color: str = "#7a8290"
    sort_order: int = 100


class RuleIn(BaseModel):
    field: str = "description"
    match_type: str = "contains"
    pattern: str
    category_id: int
    priority: int = 100
    enabled: bool = True


class SettingsIn(BaseModel):
    engine: str | None = None
    anthropic_api_key: str | None = None
    anthropic_base_url: str | None = None
    model: str | None = None
    effort: str | None = None
    currency: str | None = None
    tesseract_cmd: str | None = None
    auto_confirm_clean: str | None = None


# --------------------------------------------------------------------------- #
# Serialisation
# --------------------------------------------------------------------------- #

_MONEY_FIELDS = ("subtotal", "tax", "tip", "total")


def _receipt_json(row: dict, items: list[dict] | None = None) -> dict:
    out = dict(row)
    for field in _MONEY_FIELDS:
        out[field] = from_cents(row.get(f"{field}_cents"))
    out["review_flags"] = json.loads(row.get("review_flags") or "[]")
    out["items_total"] = from_cents(
        sum((item.get("amount_cents") or 0) for item in (items or []))
    )
    if items is not None:
        out["items"] = [_item_json(item) for item in items]
    return out


def _item_json(row: dict) -> dict:
    out = dict(row)
    out["unit_price"] = from_cents(row.get("unit_price_cents"))
    out["amount"] = from_cents(row.get("amount_cents"))
    out["is_discount"] = bool(row.get("is_discount"))
    if row.get("taxable") is not None:
        out["taxable"] = bool(row["taxable"])
    return out


def _cents(explicit: int | None, text: str | None) -> int | None:
    """Prefer an explicit cents value, else parse the decimal string."""
    if explicit is not None:
        return int(explicit)
    return to_cents(text)


# --------------------------------------------------------------------------- #
# Pages and status
# --------------------------------------------------------------------------- #


@app.get("/", response_class=HTMLResponse)
def index() -> HTMLResponse:
    return HTMLResponse((STATIC_DIR / "index.html").read_text(encoding="utf-8"))


@app.get("/api/health")
def health() -> dict:
    with connect() as db:
        counts = {
            row["status"]: row["n"]
            for row in db.execute(
                "SELECT status, COUNT(*) AS n FROM receipt GROUP BY status"
            ).fetchall()
        }
    return {
        # "bookkeeping" identifies this server to a second copy of the .exe,
        # which uses it to tell "my app is already running on this port" from
        # "something else has the port".
        "app": "bookkeeping",
        "ok": True,
        "version": app.version,
        "receipts": counts,
        "data_dir": str(IMAGE_DIR.parent),
        "frozen": paths.is_frozen(),
        "desktop": runtime.desktop,
        "ping_interval": PING_INTERVAL_SECONDS,
        # Null means "the idle watchdog is off", which is the front end's signal
        # that it need not send heartbeats at all.
        "idle_timeout": runtime.idle_timeout if runtime.armed else None,
    }


@app.post("/api/ping")
def ping() -> dict:
    """Heartbeat from the open page; see app/runtime.py."""
    runtime.ping()
    return {"ok": True, "ping_interval": PING_INTERVAL_SECONDS}


@app.post("/api/quit")
def quit_app() -> dict:
    """Ask the desktop build to stop. A no-op worth reporting when not frozen."""
    if not runtime.desktop:
        return {
            "stopping": False,
            "detail": "Not running as the desktop app; stop the server in its own window.",
        }
    runtime.request_shutdown()
    return {"stopping": True, "detail": "Bookkeeping is closing. You can close this tab."}


@app.get("/api/engines")
def engines() -> dict:
    settings = settings_store.get_all()
    return {"preference": settings.get("engine"), "engines": engine_status(settings)}


@app.get("/api/settings")
def read_settings() -> dict:
    return settings_store.public_view()


@app.put("/api/settings")
def write_settings(payload: SettingsIn) -> dict:
    updates = {k: v for k, v in payload.model_dump().items() if v is not None}
    if "effort" in updates and updates["effort"] not in {
        "low", "medium", "high", "xhigh", "max"
    }:
        raise HTTPException(400, "effort must be low, medium, high, xhigh or max")
    if "engine" in updates and updates["engine"] not in {
        "auto", "claude", "tesseract", "manual"
    }:
        raise HTTPException(400, "engine must be auto, claude, tesseract or manual")
    return settings_store.save(updates)


# --------------------------------------------------------------------------- #
# Receipts
# --------------------------------------------------------------------------- #


@app.post("/api/receipts/upload")
async def upload(files: list[UploadFile]) -> dict:
    """Store one or more images and queue each for scanning."""
    if not files:
        raise HTTPException(400, "No files were uploaded.")

    created: list[dict] = []
    errors: list[dict] = []
    for upload_file in files:
        data = await upload_file.read()
        if not data:
            errors.append({"file": upload_file.filename, "error": "The file was empty."})
            continue
        if len(data) > MAX_UPLOAD_BYTES:
            errors.append(
                {
                    "file": upload_file.filename,
                    "error": f"Larger than the {MAX_UPLOAD_BYTES // (1024 * 1024)} MB limit.",
                }
            )
            continue
        try:
            png = normalise(data)
        except ImageError as exc:
            errors.append({"file": upload_file.filename, "error": str(exc)})
            continue

        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")
        safe = re.sub(r"[^A-Za-z0-9_.-]", "_", Path(upload_file.filename or "receipt").stem)[:40]
        name = f"{stamp}_{safe}.png"
        path = pipeline.image_dir() / name
        path.write_bytes(png)

        with connect() as db:
            cursor = db.execute(
                "INSERT INTO receipt (status, image_path, image_sha256, original_name, "
                "currency, created_at, updated_at) VALUES ('uploaded', ?, ?, ?, ?, ?, ?)",
                (
                    name,
                    sha256_file(path),
                    upload_file.filename,
                    settings_store.get("currency", "USD"),
                    pipeline.now_iso(),
                    pipeline.now_iso(),
                ),
            )
            receipt_id = cursor.lastrowid
        pipeline.submit_scan(receipt_id)
        created.append({"id": receipt_id, "file": upload_file.filename})

    if not created and errors:
        raise HTTPException(400, json.dumps(errors))
    return {"created": created, "errors": errors}


@app.post("/api/receipts/manual")
def create_manual() -> dict:
    """A blank receipt for hand entry (no image, e.g. a lost paper receipt)."""
    with connect() as db:
        cursor = db.execute(
            "INSERT INTO receipt (status, currency, purchased_at, engine, created_at, "
            "updated_at) VALUES ('needs_review', ?, ?, 'manual', ?, ?)",
            (
                settings_store.get("currency", "USD"),
                date.today().isoformat(),
                pipeline.now_iso(),
                pipeline.now_iso(),
            ),
        )
        receipt_id = cursor.lastrowid
    return get_receipt(receipt_id)


@app.get("/api/receipts")
def list_receipts(
    status: str | None = None,
    q: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    category_id: int | None = None,
    limit: int = Query(200, ge=1, le=1000),
    offset: int = Query(0, ge=0),
) -> dict:
    where: list[str] = []
    params: list[object] = []
    # Every condition is qualified with the `r.` alias: the listing joins
    # category, and an unqualified `id` would be ambiguous across the two tables.
    if status:
        wanted = [s for s in status.split(",") if s in VALID_STATUSES]
        if wanted:
            where.append(f"r.status IN ({','.join('?' * len(wanted))})")
            params.extend(wanted)
    if q:
        where.append("(r.merchant LIKE ? OR r.merchant_raw LIKE ? OR r.notes LIKE ? "
                     "OR r.id IN (SELECT receipt_id FROM line_item WHERE description LIKE ?))")
        params.extend([f"%{q}%"] * 4)
    if date_from:
        where.append("r.purchased_at >= ?")
        params.append(date_from)
    if date_to:
        where.append("r.purchased_at <= ?")
        params.append(date_to)
    if category_id:
        where.append("(r.category_id = ? OR r.id IN (SELECT receipt_id FROM line_item "
                     "WHERE category_id = ?))")
        params.extend([category_id, category_id])

    clause = f"WHERE {' AND '.join(where)}" if where else ""
    with connect() as db:
        total = db.execute(
            f"SELECT COUNT(*) AS n FROM receipt r {clause}", params
        ).fetchone()["n"]
        rows = db.execute(
            f"SELECT r.*, c.name AS category_name, c.color AS category_color, "
            f"(SELECT COUNT(*) FROM line_item li WHERE li.receipt_id = r.id) AS item_count "
            f"FROM receipt r LEFT JOIN category c ON c.id = r.category_id "
            f"{clause} "
            f"ORDER BY COALESCE(r.purchased_at, r.created_at) DESC, r.id DESC "
            f"LIMIT ? OFFSET ?",
            [*params, limit, offset],
        ).fetchall()
    return {
        "total": total,
        "receipts": [_receipt_json(row) for row in rows],
    }


@app.get("/api/receipts/{receipt_id}")
def get_receipt(receipt_id: int) -> dict:
    with connect() as db:
        row = db.execute(
            "SELECT r.*, c.name AS category_name, c.color AS category_color "
            "FROM receipt r LEFT JOIN category c ON c.id = r.category_id WHERE r.id = ?",
            (receipt_id,),
        ).fetchone()
        if row is None:
            raise HTTPException(404, f"No receipt {receipt_id}")
        items = db.execute(
            "SELECT li.*, c.name AS category_name, c.color AS category_color "
            "FROM line_item li LEFT JOIN category c ON c.id = li.category_id "
            "WHERE li.receipt_id = ? ORDER BY li.line_no ASC, li.id ASC",
            (receipt_id,),
        ).fetchall()
    return _receipt_json(row, items)


@app.put("/api/receipts/{receipt_id}")
def update_receipt(receipt_id: int, payload: ReceiptIn) -> dict:
    """Save reviewer edits.

    Items are replaced wholesale: the review UI always sends the full list, and
    diffing rows the user may have reordered or deleted would be more code and
    more ways to lose a line. Any category the reviewer touched is marked
    ``manual`` so a later rule re-run leaves it alone.
    """
    subtotal_cents = to_cents(payload.subtotal)
    tax_cents = to_cents(payload.tax)
    tip_cents = to_cents(payload.tip)
    total_cents = to_cents(payload.total)

    items = [
        {
            "line_no": index,
            "description": (item.description or "").strip(),
            "raw_description": item.raw_description,
            "sku": item.sku,
            "quantity": item.quantity,
            "unit_price_cents": _cents(item.unit_price_cents, item.unit_price),
            "amount_cents": _cents(item.amount_cents, item.amount),
            "category_id": item.category_id,
            "category_source": item.category_source or "manual",
            "is_discount": 1 if item.is_discount else 0,
            "taxable": None if item.taxable is None else int(item.taxable),
        }
        for index, item in enumerate(payload.items)
    ]

    flags = check(
        purchased_at=payload.purchased_at,
        total_cents=total_cents,
        subtotal_cents=subtotal_cents,
        tax_cents=tax_cents,
        tip_cents=tip_cents,
        items=items,
        confidence=None,
    )
    # A hand-entered receipt usually has no header category. Derive it from the
    # lines, the same way a scanned one does, so the listing and the merchant
    # report are not full of blanks.
    category_id = payload.category_id or pipeline.dominant_category(items)
    # Confirming is the reviewer's call: they can confirm a receipt whose
    # arithmetic still disagrees (some receipts really do not add up), and the
    # flags stay attached as a record of why it was questioned.
    status = "confirmed" if payload.confirm else "needs_review"

    with connect() as db:
        if db.execute("SELECT 1 FROM receipt WHERE id = ?", (receipt_id,)).fetchone() is None:
            raise HTTPException(404, f"No receipt {receipt_id}")
        db.execute(
            """
            UPDATE receipt SET merchant = ?, purchased_at = ?, currency = ?,
                subtotal_cents = ?, tax_cents = ?, tip_cents = ?, total_cents = ?,
                payment_method = ?, category_id = ?, notes = ?, status = ?,
                review_flags = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                (payload.merchant or "").strip() or None,
                (payload.purchased_at or "").strip() or None,
                payload.currency or "USD",
                subtotal_cents,
                tax_cents,
                tip_cents,
                total_cents,
                payload.payment_method,
                category_id,
                payload.notes,
                status,
                json.dumps(flags),
                pipeline.now_iso(),
                receipt_id,
            ),
        )
        db.execute("DELETE FROM line_item WHERE receipt_id = ?", (receipt_id,))
        for item in items:
            db.execute(
                "INSERT INTO line_item (receipt_id, line_no, description, raw_description, "
                "sku, quantity, unit_price_cents, amount_cents, category_id, "
                "category_source, is_discount, taxable) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
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
    return get_receipt(receipt_id)


@app.post("/api/receipts/{receipt_id}/confirm")
def confirm_receipt(receipt_id: int) -> dict:
    with connect() as db:
        row = db.execute("SELECT * FROM receipt WHERE id = ?", (receipt_id,)).fetchone()
        if row is None:
            raise HTTPException(404, f"No receipt {receipt_id}")
        if row["total_cents"] is None:
            raise HTTPException(400, "Cannot confirm a receipt with no total.")
        if not row["purchased_at"]:
            raise HTTPException(400, "Cannot confirm a receipt with no date.")
        db.execute(
            "UPDATE receipt SET status = 'confirmed', updated_at = ? WHERE id = ?",
            (pipeline.now_iso(), receipt_id),
        )
    return get_receipt(receipt_id)


@app.post("/api/receipts/{receipt_id}/rescan")
def rescan_receipt(receipt_id: int) -> dict:
    with connect() as db:
        row = db.execute("SELECT image_path FROM receipt WHERE id = ?", (receipt_id,)).fetchone()
    if row is None:
        raise HTTPException(404, f"No receipt {receipt_id}")
    if not row["image_path"]:
        raise HTTPException(400, "This receipt has no image to scan.")
    if not pipeline.submit_scan(receipt_id):
        raise HTTPException(409, "A scan of this receipt is already running.")
    return get_receipt(receipt_id)


@app.delete("/api/receipts/{receipt_id}")
def delete_receipt(receipt_id: int, keep_image: bool = False) -> dict:
    with connect() as db:
        row = db.execute("SELECT image_path FROM receipt WHERE id = ?", (receipt_id,)).fetchone()
        if row is None:
            raise HTTPException(404, f"No receipt {receipt_id}")
        db.execute("DELETE FROM receipt WHERE id = ?", (receipt_id,))
    if row["image_path"] and not keep_image:
        # The row is gone either way; a failure to unlink the file must not turn
        # into a 500 that suggests the delete did not happen.
        try:
            (IMAGE_DIR / row["image_path"]).unlink(missing_ok=True)
        except OSError as exc:
            log.warning("Could not delete image for receipt %s: %s", receipt_id, exc)
    return {"deleted": receipt_id}


@app.get("/api/receipts/{receipt_id}/image")
def receipt_image(receipt_id: int) -> FileResponse:
    with connect() as db:
        row = db.execute("SELECT image_path FROM receipt WHERE id = ?", (receipt_id,)).fetchone()
    if row is None or not row["image_path"]:
        raise HTTPException(404, "No image for this receipt.")
    path = IMAGE_DIR / row["image_path"]
    if not path.exists():
        raise HTTPException(404, "The image file is missing from data/images.")
    return FileResponse(path, media_type="image/png")


# --------------------------------------------------------------------------- #
# Categories and rules
# --------------------------------------------------------------------------- #


@app.get("/api/categories")
def list_categories() -> dict:
    with connect() as db:
        rows = db.execute(
            "SELECT c.*, (SELECT COUNT(*) FROM line_item li WHERE li.category_id = c.id) "
            "AS item_count FROM category c ORDER BY c.sort_order ASC, c.name ASC"
        ).fetchall()
    return {"categories": rows}


@app.post("/api/categories")
def create_category(payload: CategoryIn) -> dict:
    name = payload.name.strip()
    if not name:
        raise HTTPException(400, "A category needs a name.")
    with connect() as db:
        existing = db.execute(
            "SELECT id FROM category WHERE name = ? COLLATE NOCASE", (name,)
        ).fetchone()
        if existing:
            raise HTTPException(409, f"A category named '{name}' already exists.")
        cursor = db.execute(
            "INSERT INTO category (name, kind, color, is_builtin, sort_order) "
            "VALUES (?, ?, ?, 0, ?)",
            (name, payload.kind, payload.color, payload.sort_order),
        )
        return {"id": cursor.lastrowid, "name": name}


@app.put("/api/categories/{category_id}")
def update_category(category_id: int, payload: CategoryIn) -> dict:
    with connect() as db:
        if db.execute("SELECT 1 FROM category WHERE id = ?", (category_id,)).fetchone() is None:
            raise HTTPException(404, f"No category {category_id}")
        db.execute(
            "UPDATE category SET name = ?, kind = ?, color = ?, sort_order = ? WHERE id = ?",
            (payload.name.strip(), payload.kind, payload.color, payload.sort_order, category_id),
        )
    return {"id": category_id, "name": payload.name.strip()}


@app.delete("/api/categories/{category_id}")
def delete_category(category_id: int) -> dict:
    """Delete a category. Items pointing at it fall back to Uncategorized."""
    with connect() as db:
        row = db.execute("SELECT * FROM category WHERE id = ?", (category_id,)).fetchone()
        if row is None:
            raise HTTPException(404, f"No category {category_id}")
        if row["name"] == "Uncategorized":
            raise HTTPException(400, "Uncategorized is the fallback category and cannot be deleted.")
        fallback = db.execute(
            "SELECT id FROM category WHERE name = 'Uncategorized'"
        ).fetchone()
        fallback_id = fallback["id"] if fallback else None
        db.execute(
            "UPDATE line_item SET category_id = ?, category_source = 'default' "
            "WHERE category_id = ?",
            (fallback_id, category_id),
        )
        db.execute("UPDATE receipt SET category_id = ? WHERE category_id = ?",
                   (fallback_id, category_id))
        db.execute("DELETE FROM category WHERE id = ?", (category_id,))
    return {"deleted": category_id}


@app.get("/api/rules")
def list_rules() -> dict:
    with connect() as db:
        rows = db.execute(
            "SELECT r.*, c.name AS category_name FROM category_rule r "
            "JOIN category c ON c.id = r.category_id "
            "ORDER BY r.priority ASC, r.id ASC"
        ).fetchall()
    return {"rules": rows}


@app.post("/api/rules")
def create_rule(payload: RuleIn) -> dict:
    if not payload.pattern.strip():
        raise HTTPException(400, "A rule needs a pattern.")
    if payload.field not in {"description", "merchant"}:
        raise HTTPException(400, "field must be 'description' or 'merchant'")
    if payload.match_type not in {"contains", "regex"}:
        raise HTTPException(400, "match_type must be 'contains' or 'regex'")
    if payload.match_type == "regex":
        try:
            re.compile(payload.pattern)
        except re.error as exc:
            raise HTTPException(400, f"That is not a valid regular expression: {exc}") from exc
    with connect() as db:
        cursor = db.execute(
            "INSERT INTO category_rule (field, match_type, pattern, category_id, priority, "
            "enabled, is_builtin) VALUES (?, ?, ?, ?, ?, ?, 0)",
            (
                payload.field,
                payload.match_type,
                payload.pattern.strip(),
                payload.category_id,
                payload.priority,
                1 if payload.enabled else 0,
            ),
        )
        return {"id": cursor.lastrowid}


@app.delete("/api/rules/{rule_id}")
def delete_rule(rule_id: int) -> dict:
    with connect() as db:
        if db.execute("SELECT 1 FROM category_rule WHERE id = ?", (rule_id,)).fetchone() is None:
            raise HTTPException(404, f"No rule {rule_id}")
        db.execute("DELETE FROM category_rule WHERE id = ?", (rule_id,))
    return {"deleted": rule_id}


@app.post("/api/rules/apply")
def apply_rules(include_confirmed: bool = False) -> dict:
    """Re-run the rule set over stored line items.

    Manual categorisations are never touched -- the user's own decision outranks
    any rule. By default confirmed receipts are left alone too, so re-running
    rules cannot silently rewrite books that were already signed off; pass
    ``include_confirmed=true`` to reclassify everything.

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
    return {"examined": len(rows), "changed": changed}


@app.get("/api/model-categories")
def model_categories() -> dict:
    """The category list handed to the vision model, for transparency."""
    with connect() as db:
        return {"categories": category_names(db)}


# --------------------------------------------------------------------------- #
# Reports
# --------------------------------------------------------------------------- #


@app.get("/api/reports/summary")
def report_summary(
    date_from: str | None = None,
    date_to: str | None = None,
    status: str = "confirmed",
) -> dict:
    """Spending rollups.

    Category figures come from line items, which do not include tax. To keep the
    category breakdown summing to the money actually spent, the difference
    between a receipt's total and its itemised lines is reported as its own
    'Tax & unitemised' bucket rather than being quietly dropped.
    """
    statuses = [s for s in status.split(",") if s in VALID_STATUSES] or ["confirmed"]
    where = [f"r.status IN ({','.join('?' * len(statuses))})"]
    params: list[object] = list(statuses)
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
            row["category_name"], {"category": row["category_name"], "color": row["color"],
                                   "amount_cents": 0, "items": 0}
        )
        bucket["amount_cents"] += row["amount_cents"] or 0
        bucket["items"] += 1

    itemised_by_receipt: dict[int, int] = defaultdict(int)
    for row in item_rows:
        itemised_by_receipt[row["receipt_id"]] += row["amount_cents"] or 0
    residual = sum(
        max(0, (row["total_cents"] or 0) - itemised_by_receipt.get(row["id"], 0))
        for row in receipts
    )
    if residual > 0:
        by_category["Tax & unitemised"] = {
            "category": "Tax & unitemised",
            "color": "#8a8a8a",
            "amount_cents": residual,
            "items": 0,
        }

    categories = sorted(by_category.values(), key=lambda b: -b["amount_cents"])
    denominator = sum(max(0, bucket["amount_cents"]) for bucket in categories) or 1
    for bucket in categories:
        bucket["share"] = round(max(0, bucket["amount_cents"]) / denominator, 4)
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
        bucket = by_merchant.setdefault(name, {"merchant": name, "amount_cents": 0, "receipts": 0})
        bucket["amount_cents"] += row["total_cents"] or 0
        bucket["receipts"] += 1
    merchants = sorted(by_merchant.values(), key=lambda b: -b["amount_cents"])[:12]
    for bucket in merchants:
        bucket["amount"] = from_cents(bucket["amount_cents"])

    return {
        "date_from": date_from,
        "date_to": date_to,
        "statuses": statuses,
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


@app.get("/api/export/items.csv")
def export_items(date_from: str | None = None, date_to: str | None = None,
                 status: str = "confirmed") -> StreamingResponse:
    """One row per line item -- the form most useful for a spreadsheet pivot."""
    statuses = [s for s in status.split(",") if s in VALID_STATUSES] or ["confirmed"]
    where = [f"r.status IN ({','.join('?' * len(statuses))})"]
    params: list[object] = list(statuses)
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

    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
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
            row["description"] or "", row["quantity"] if row["quantity"] is not None else "",
            from_cents(row["unit_price_cents"]), from_cents(row["amount_cents"]),
            "yes" if row["is_discount"] else "no", row["category"], row["category_source"] or "",
        ])
    buffer.seek(0)
    stamp = date.today().isoformat()
    return StreamingResponse(
        iter([buffer.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="bookkeeping-items-{stamp}.csv"'},
    )
