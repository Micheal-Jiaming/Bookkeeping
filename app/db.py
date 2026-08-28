"""SQLite storage: connection handling, schema, and first-run seed data.

Design notes
------------
* One file database under ``data/bookkeeping.db``. WAL journalling is enabled so
  the background scan worker can write while the HTTP thread reads.
* Connections are short lived and per call (``with connect() as db``). SQLite
  objects are not shareable across threads, and the scan pipeline runs in a
  thread pool, so a module-level shared connection would be a latent crash.
* Schema is created idempotently and versioned via ``PRAGMA user_version`` so
  later releases can migrate without dropping the user's books.
"""

from __future__ import annotations

import logging
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from .paths import default_data_dir

log = logging.getLogger("bookkeeping.db")

SCHEMA_VERSION = 3

APP_DIR = Path(__file__).resolve().parent
PROJECT_DIR = APP_DIR.parent
# Resolved at import from BOOKKEEPING_DATA if the desktop launcher set it,
# otherwise "data" beside the program. See app/paths.py for the reasoning.
DATA_DIR = default_data_dir()
IMAGE_DIR = DATA_DIR / "images"
DB_PATH = DATA_DIR / "bookkeeping.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS setting (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS category (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT NOT NULL UNIQUE,
    kind       TEXT NOT NULL DEFAULT 'expense',   -- expense | income
    color      TEXT NOT NULL DEFAULT '#7a8290',
    is_builtin INTEGER NOT NULL DEFAULT 0,
    sort_order INTEGER NOT NULL DEFAULT 100
);

CREATE TABLE IF NOT EXISTS category_rule (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    field       TEXT NOT NULL DEFAULT 'description',  -- description | merchant
    match_type  TEXT NOT NULL DEFAULT 'contains',     -- contains | regex
    pattern     TEXT NOT NULL,
    category_id INTEGER NOT NULL REFERENCES category(id) ON DELETE CASCADE,
    priority    INTEGER NOT NULL DEFAULT 100,         -- lower runs first
    enabled     INTEGER NOT NULL DEFAULT 1,
    is_builtin  INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS receipt (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    status         TEXT NOT NULL DEFAULT 'uploaded',
        -- uploaded | scanning | needs_review | confirmed | failed
    image_path     TEXT,             -- file name inside data/images
    image_sha256   TEXT,             -- duplicate-upload detection
    original_name  TEXT,
    merchant       TEXT,
    merchant_raw   TEXT,             -- exactly what was printed, before cleanup
    purchased_at   TEXT,             -- ISO-8601 date, YYYY-MM-DD
    currency       TEXT NOT NULL DEFAULT 'USD',
    subtotal_cents INTEGER,
    tax_cents      INTEGER,
    tip_cents      INTEGER,
    total_cents    INTEGER,
    payment_method TEXT,
    category_id    INTEGER REFERENCES category(id) ON DELETE SET NULL,
    notes          TEXT,
    engine         TEXT,             -- claude | windows | tesseract | manual
    model          TEXT,
    confidence     REAL,
    raw_text       TEXT,             -- OCR text (either offline engine)
    raw_response   TEXT,             -- extractor JSON, kept for auditing
    review_flags   TEXT,             -- JSON array of validation messages
    extract_ms     INTEGER,
    input_tokens   INTEGER,
    output_tokens  INTEGER,
    cost_usd       REAL,
    error          TEXT,
    created_at     TEXT NOT NULL,
    updated_at     TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_receipt_status ON receipt(status);
CREATE INDEX IF NOT EXISTS idx_receipt_date   ON receipt(purchased_at);
CREATE INDEX IF NOT EXISTS idx_receipt_hash   ON receipt(image_sha256);

CREATE TABLE IF NOT EXISTS line_item (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    receipt_id       INTEGER NOT NULL REFERENCES receipt(id) ON DELETE CASCADE,
    line_no          INTEGER NOT NULL DEFAULT 0,
    description      TEXT NOT NULL DEFAULT '',
    raw_description  TEXT,
    sku              TEXT,
    quantity         REAL,
    unit_price_cents INTEGER,
    amount_cents     INTEGER,
    category_id      INTEGER REFERENCES category(id) ON DELETE SET NULL,
    category_source  TEXT,            -- rule | model | manual | default
    is_discount      INTEGER NOT NULL DEFAULT 0,
    taxable          INTEGER
);

CREATE INDEX IF NOT EXISTS idx_item_receipt ON line_item(receipt_id);
"""

# (name, color, sort_order). Deliberately a short, general list: a long taxonomy
# makes the model's category choice worse and the review UI slower to use.
BUILTIN_CATEGORIES: list[tuple[str, str, int]] = [
    ("Groceries", "#4f8a5b", 10),
    ("Dining", "#c2703d", 20),
    ("Household", "#5b7ba8", 30),
    ("Personal Care", "#9a6ba8", 40),
    ("Health & Pharmacy", "#3f8f95", 50),
    ("Clothing", "#a85b7b", 60),
    ("Electronics", "#5f6b9a", 70),
    ("Baby & Kids", "#c58fa8", 80),
    ("Pets", "#8a7a4f", 90),
    ("Transport & Fuel", "#7a6ba8", 100),
    ("Entertainment", "#a85b5b", 110),
    ("Office & Supplies", "#6b8a9a", 120),
    ("Fees & Taxes", "#8a8a8a", 130),
    ("Other", "#7a8290", 900),
    ("Uncategorized", "#9aa0aa", 999),
]

# Keyword rules, checked before the model's own suggestion. These encode the
# things a keyword can decide with certainty; anything ambiguous is left to the
# model. Walmart receipts print abbreviated, upper-case item names, so patterns
# are matched case-insensitively as substrings.
# Added in schema version 3, when the offline OCR engine started reading real
# receipts and it turned out the original keyword list only understood plain
# English. Claude vision expands "CLX PLNGR" to "Clorox toilet plunger" and
# categorises from that; OCR cannot, so on the first real Walmart receipt only
# 3 of 20 items matched any rule at all.
#
# Two kinds of pattern are safe to seed here and a third is not:
#   * generic product nouns ("MOP", "AMMONIA"), which name a category directly;
#   * product brands that sell one kind of thing ("LYSOL", "PAMPERS");
#   * NOT store brands -- see the note on BUILTIN_RULES below about "GREAT VALUE".
#
# Patterns are matched as substrings, so anything that hides inside a longer
# ordinary word is left out however useful it looks: "GAIN" is a laundry brand
# but also the end of "BARGAIN", and "AIM" is a toothpaste but also the middle
# of "CLAIM".
RULES_ADDED_IN_V3: list[tuple[str, str, str, int]] = [
    ("MOP", "Household", "description", 60),
    ("BROOM", "Household", "description", 60),
    ("SPONGE", "Household", "description", 60),
    ("SPGE", "Household", "description", 60),
    ("BUCKET", "Household", "description", 60),
    ("HANGER", "Household", "description", 60),
    ("AMMONIA", "Household", "description", 60),
    ("BLEACH", "Household", "description", 60),
    ("PLUNGER", "Household", "description", 60),
    ("PLNGR", "Household", "description", 60),
    ("LAUNDRY", "Household", "description", 60),
    ("FABRIC SOFTENER", "Household", "description", 60),
    ("LIGHT BULB", "Household", "description", 60),
    ("CLX", "Household", "description", 60),
    ("LYSOL", "Household", "description", 60),
    ("WINDEX", "Household", "description", 60),
    ("SWIFFER", "Household", "description", 60),
    ("FEBREZE", "Household", "description", 60),
    ("CHARMIN", "Household", "description", 60),
    ("BOUNTY", "Household", "description", 60),
    ("KLEENEX", "Household", "description", 60),
    ("BODY WASH", "Personal Care", "description", 60),
    ("TOOTHBRUSH", "Personal Care", "description", 60),
    ("CONDITIONER", "Personal Care", "description", 60),
    ("LOTION", "Personal Care", "description", 60),
    ("DOVE", "Personal Care", "description", 60),
    ("COLGATE", "Personal Care", "description", 60),
    ("CREST", "Personal Care", "description", 60),
    ("GILLETTE", "Personal Care", "description", 60),
    ("PANTENE", "Personal Care", "description", 60),
    ("OLD SPICE", "Personal Care", "description", 60),
    ("COKE", "Groceries", "description", 60),
    ("COCA-COLA", "Groceries", "description", 60),
    ("PEPSI", "Groceries", "description", 60),
    ("SPRITE", "Groceries", "description", 60),
    ("JUICE", "Groceries", "description", 60),
    ("CHEESE", "Groceries", "description", 60),
    ("BUTTER", "Groceries", "description", 60),
    ("PASTA", "Groceries", "description", 60),
    ("SUGAR", "Groceries", "description", 60),
    ("FLOUR", "Groceries", "description", 60),
    ("COOKIE", "Groceries", "description", 60),
    ("TUNA", "Groceries", "description", 60),
    ("POTATO", "Groceries", "description", 60),
    ("TOMATO", "Groceries", "description", 60),
    ("LETTUCE", "Groceries", "description", 60),
    ("PROTEIN", "Health & Pharmacy", "description", 60),
    ("ADVIL", "Health & Pharmacy", "description", 60),
    ("ALLERGY", "Health & Pharmacy", "description", 60),
    ("BANDAGE", "Health & Pharmacy", "description", 60),
    ("PAMPERS", "Baby & Kids", "description", 60),
    ("HUGGIES", "Baby & Kids", "description", 60),
    ("PURINA", "Pets", "description", 60),
    ("PEDIGREE", "Pets", "description", 60),
    # Bottle deposits are printed as "ME DEPOSIT", "CRV" and a dozen other
    # state-specific spellings, so match the word itself rather than each one.
    ("DEPOSIT", "Fees & Taxes", "description", 40),
]

BUILTIN_RULES: list[tuple[str, str, str, int]] = [
    # (pattern, category, field, priority)
    #
    # No store-brand patterns here. "GREAT VALUE" was seeded originally and had
    # to be removed (see the migration below): it is a *brand*, not a category --
    # Walmart sells Great Value mops, ammonia and sponges next to Great Value
    # milk, and on a real receipt it filed all three of those as Groceries.
    # "MARKETSIDE" stays because it is specifically Walmart's fresh-food line.
    ("MARKETSIDE", "Groceries", "description", 50),
    ("BANANA", "Groceries", "description", 60),
    ("MILK", "Groceries", "description", 60),
    ("BREAD", "Groceries", "description", 60),
    ("EGGS", "Groceries", "description", 60),
    ("CHICKEN", "Groceries", "description", 60),
    ("COFFEE", "Groceries", "description", 60),
    ("YOGURT", "Groceries", "description", 60),
    ("CEREAL", "Groceries", "description", 60),
    ("SODA", "Groceries", "description", 60),
    ("WATER 24", "Groceries", "description", 60),
    ("TIDE", "Household", "description", 60),
    ("CLOROX", "Household", "description", 60),
    ("PAPER TOWEL", "Household", "description", 60),
    ("BATH TISSUE", "Household", "description", 60),
    ("TRASH BAG", "Household", "description", 60),
    ("DISH SOAP", "Household", "description", 60),
    ("DETERGENT", "Household", "description", 60),
    ("SHAMPOO", "Personal Care", "description", 60),
    ("TOOTHPASTE", "Personal Care", "description", 60),
    ("DEODORANT", "Personal Care", "description", 60),
    ("RAZOR", "Personal Care", "description", 60),
    ("IBUPROFEN", "Health & Pharmacy", "description", 60),
    ("TYLENOL", "Health & Pharmacy", "description", 60),
    ("VITAMIN", "Health & Pharmacy", "description", 60),
    ("PHARMACY", "Health & Pharmacy", "description", 60),
    ("DIAPER", "Baby & Kids", "description", 60),
    ("BABY WIPE", "Baby & Kids", "description", 60),
    ("FORMULA", "Baby & Kids", "description", 60),
    ("DOG FOOD", "Pets", "description", 60),
    ("CAT FOOD", "Pets", "description", 60),
    ("CAT LITTER", "Pets", "description", 60),
    ("UNLEADED", "Transport & Fuel", "description", 60),
    ("GASOLINE", "Transport & Fuel", "description", 60),
    ("HDMI", "Electronics", "description", 60),
    ("BATTERIES", "Electronics", "description", 60),
    ("EARBUD", "Electronics", "description", 60),
    ("BAG FEE", "Fees & Taxes", "description", 40),
    ("BOTTLE DEPOSIT", "Fees & Taxes", "description", 40),
    *RULES_ADDED_IN_V3,
    # Merchant-level defaults, used for the receipt header and as the fallback
    # for items nothing else matched.
    ("WALMART", "Groceries", "merchant", 200),
    ("SAM'S CLUB", "Groceries", "merchant", 200),
    ("TARGET", "Household", "merchant", 200),
    ("COSTCO", "Groceries", "merchant", 200),
    ("KROGER", "Groceries", "merchant", 200),
    ("SAFEWAY", "Groceries", "merchant", 200),
    ("TRADER JOE", "Groceries", "merchant", 200),
    ("WHOLE FOODS", "Groceries", "merchant", 200),
    ("CVS", "Health & Pharmacy", "merchant", 200),
    ("WALGREENS", "Health & Pharmacy", "merchant", 200),
    ("SHELL", "Transport & Fuel", "merchant", 200),
    ("CHEVRON", "Transport & Fuel", "merchant", 200),
    ("STARBUCKS", "Dining", "merchant", 200),
    ("MCDONALD", "Dining", "merchant", 200),
    ("CHIPOTLE", "Dining", "merchant", 200),
    ("BEST BUY", "Electronics", "merchant", 200),
    ("HOME DEPOT", "Household", "merchant", 200),
    ("PETCO", "Pets", "merchant", 200),
    ("PETSMART", "Pets", "merchant", 200),
]

DEFAULT_SETTINGS = {
    "engine": "auto",                 # auto | claude | windows | tesseract | manual
    "anthropic_api_key": "",
    "anthropic_base_url": "",
    "model": "claude-opus-5",
    "effort": "medium",               # low | medium | high | xhigh | max
    "currency": "USD",
    "ocr_language": "",               # Windows OCR language tag; "" picks English
    "tesseract_cmd": "",              # explicit path to tesseract.exe if not on PATH
    "auto_confirm_clean": "0",        # 1 = skip review when validation is clean
    # Interface state. Kept here rather than in a separate config file so a
    # portable copy carries its own appearance with the books.
    "theme": "dark",                  # dark | light
    "window_geometry": "",            # last size and position, e.g. 1180x760+120+80
    "last_page": "receipts",
    "schema_version": str(SCHEMA_VERSION),
}


def _dict_factory(cursor: sqlite3.Cursor, row: tuple) -> dict:
    return {col[0]: row[idx] for idx, col in enumerate(cursor.description)}


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    """Open a connection, commit on success, roll back on error, always close."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=15.0, isolation_level=None)
    conn.row_factory = _dict_factory
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=15000")
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        conn.execute("BEGIN")
        yield conn
        # `in_transaction` is checked because sqlite3.executescript() commits any
        # pending transaction before running -- so after init_db's schema script
        # there is nothing left to commit, and an unconditional COMMIT would
        # raise "no transaction is active" and mask the real error.
        if conn.in_transaction:
            conn.execute("COMMIT")
    except Exception:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise
    finally:
        conn.close()


def init_db() -> None:
    """Create the schema, seed built-in data, migrate. Safe to call every start."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    IMAGE_DIR.mkdir(parents=True, exist_ok=True)
    with connect() as db:
        # Read the version *before* the schema script, which is what tells a
        # migration whether it has already run.
        previous = db.execute("PRAGMA user_version").fetchone()["user_version"]
        db.executescript(SCHEMA)
        _seed_categories(db)
        _seed_rules(db)
        _seed_settings(db)
        _migrate(db, previous)
        db.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")


def _migrate(db: sqlite3.Connection, previous: int) -> None:
    """Bring an existing database up to ``SCHEMA_VERSION``.

    Migrations must be safe to run on a brand-new database too (a fresh file
    reports version 0), so each one is written to be a no-op when there is
    nothing to change.
    """
    if previous < 2:
        # Version 2 drops the seeded "GREAT VALUE" rule. It encoded a store
        # brand rather than a category, and because rule matching also searches
        # the model's plain-English expansion of an item name, it captured
        # every Great Value product -- filing a mop, ammonia and sponges as
        # Groceries on the first real receipt this app ever read. Only the
        # built-in copy is removed: a rule the user created themselves, even
        # with the same pattern, is theirs to keep.
        removed = db.execute(
            "DELETE FROM category_rule WHERE is_builtin = 1 AND field = 'description' "
            "AND pattern = 'GREAT VALUE'"
        ).rowcount
        if removed:
            log.info("Removed %d built-in 'GREAT VALUE' rule(s): a brand, not a "
                     "category", removed)

    if previous < 3:
        # Version 3 adds the abbreviation and brand rules the offline OCR engine
        # needs. _seed_rules only ever fires on an empty database -- deliberately,
        # so that rules the user deleted stay deleted -- which means existing
        # books would otherwise never see these. A pattern the user already has,
        # built-in or their own, is left exactly as it is.
        ids = {
            row["name"]: row["id"]
            for row in db.execute("SELECT id, name FROM category").fetchall()
        }
        added = 0
        for pattern, category, field, priority in RULES_ADDED_IN_V3:
            category_id = ids.get(category)
            if category_id is None:
                continue
            clash = db.execute(
                "SELECT 1 FROM category_rule WHERE field = ? AND pattern = ?",
                (field, pattern),
            ).fetchone()
            if clash:
                continue
            db.execute(
                "INSERT INTO category_rule (field, match_type, pattern, category_id, "
                "priority, enabled, is_builtin) VALUES (?, 'contains', ?, ?, ?, 1, 1)",
                (field, pattern, category_id, priority),
            )
            added += 1
        if added:
            log.info("Added %d built-in categorisation rules for abbreviated "
                     "item names", added)


def _seed_categories(db: sqlite3.Connection) -> None:
    for name, color, order in BUILTIN_CATEGORIES:
        db.execute(
            "INSERT INTO category (name, kind, color, is_builtin, sort_order) "
            "VALUES (?, 'expense', ?, 1, ?) ON CONFLICT(name) DO NOTHING",
            (name, color, order),
        )


def _seed_rules(db: sqlite3.Connection) -> None:
    # Seed only when no built-in rule survives. Re-inserting them on every start
    # would resurrect rules the user deliberately deleted -- which is also why
    # the test is "are there any built-ins left?" rather than "is this database
    # new?": a user who deleted every one of them does get them back, but a user
    # who deleted some keeps their choices.
    existing = db.execute(
        "SELECT COUNT(*) AS n FROM category_rule WHERE is_builtin = 1"
    ).fetchone()["n"]
    if existing:
        return
    ids = {
        row["name"]: row["id"]
        for row in db.execute("SELECT id, name FROM category").fetchall()
    }
    for pattern, category, field, priority in BUILTIN_RULES:
        category_id = ids.get(category)
        if category_id is None:
            continue
        db.execute(
            "INSERT INTO category_rule (field, match_type, pattern, category_id, "
            "priority, enabled, is_builtin) VALUES (?, 'contains', ?, ?, ?, 1, 1)",
            (field, pattern, category_id, priority),
        )


def _seed_settings(db: sqlite3.Connection) -> None:
    for key, value in DEFAULT_SETTINGS.items():
        db.execute(
            "INSERT INTO setting (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO NOTHING",
            (key, value),
        )


def category_id_for(db: sqlite3.Connection, name: str | None) -> int | None:
    """Look up a category id by name, case-insensitively."""
    if not name:
        return None
    row = db.execute(
        "SELECT id FROM category WHERE name = ? COLLATE NOCASE", (name.strip(),)
    ).fetchone()
    return row["id"] if row else None
