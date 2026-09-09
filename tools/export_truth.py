"""Turn the receipts you have confirmed in the app into ground truth.

    py tools\\export_truth.py            write tests/fixtures/receipts_truth.local.json
    py tools\\export_truth.py --show     print what it would write, change nothing
    py tools\\export_truth.py --data-dir C:\\somewhere\\data

**Why this exists.** Verifying a receipt by hand is the most expensive thing
anybody does with this application and, until now, the least durable: the
corrections went into the books and nowhere else, so the next change to the
parser could quietly undo them and no test would notice. `tools/accuracy.py`
already knows how to score a reading against a transcription and
`measure_accuracy.py --check` already fails on a regression -- what was missing
was a way to get the transcription out of the books, where the reviewer has
already produced it. Every receipt you confirm now becomes a permanent
regression test.

**Why the output is not committed.** The truth for a photograph is only usable
beside the photograph, and the photographs are gitignored on purpose: a receipt
is somebody's shopping and their payment method. Truth data is the same
material -- an itemised list of what one person bought, for how much, on what
day -- so it gets the same treatment, and `receipts_truth.local.json` is
gitignored too. Nothing is lost by that. A clone on another machine has no
photographs to score, so it could not use the file if it had it.

`tests/fixtures/receipts_truth.json` stays tracked and keeps the one receipt
whose figures are already public.

**Confirmed in the app is not quite the same as transcribed from the paper**,
and the difference is recorded rather than smoothed over. A reviewer fills in
what a receipt *should* say, including fields the photograph does not show:
Walmart1 was photographed with its header out of frame, so the paper has no
merchant and no date, and a reader that supplies one is wrong -- but the books
hold "Walmart" and a date, because the user typed them. The tracked file says
those two are *verified absent*, and it is right.

So an entry that already *says* something in the tracked file is left alone, and
this fills in the rest. Most of the tracked entries are placeholders -- they name
a photograph and assert nothing -- and those are not evidence to defer to. Where
the two genuinely disagree the difference is printed, because that is worth a
human's attention rather than a silent overwrite.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.extract.base import sha256_file                      # noqa: E402
from app.money import from_cents                              # noqa: E402
from tools.accuracy import (                                  # noqa: E402
    LOCAL_TRUTH_PATH, PHOTO_DIR, TRUTH_PATH, load_truth,
)

HEADER_FIELDS = ("merchant", "purchased_at", "subtotal", "tax", "total")

COMMENT = [
    "Ground truth exported from confirmed receipts in the books.",
    "Written by tools/export_truth.py; edit the books and re-export rather than",
    "editing this by hand. Gitignored, for the same reason the photographs are:",
    "an itemised list of one person's shopping does not belong in a public",
    "repository, and it is useless without the photograph anyway.",
    "",
    "verified_by is 'confirmed-in-app' rather than 'human': the reviewer signed",
    "the row off, which is strong evidence, but they may also have filled in a",
    "field the photograph does not show.",
]


def _books(data_dir: Path | None) -> Path:
    if data_dir:
        return data_dir / "bookkeeping.db"
    from app.db import DB_PATH
    return DB_PATH


def collect(db_path: Path, photo_dir: Path) -> tuple[dict, list[str]]:
    """Confirmed receipts as truth entries, plus anything worth reporting."""
    notes: list[str] = []
    if not db_path.exists():
        raise SystemExit(f"No books at {db_path}")
    db = sqlite3.connect(db_path)
    db.row_factory = sqlite3.Row

    tracked = load_truth(TRUTH_PATH, local=None)
    entries: dict[str, dict] = {}
    rows = db.execute(
        "SELECT * FROM receipt WHERE status = 'confirmed' "
        "AND COALESCE(original_name, '') <> '' ORDER BY original_name"
    ).fetchall()

    for row in rows:
        name = row["original_name"]
        photo = photo_dir / name
        if not photo.exists():
            # Truth about an image nobody has cannot be scored against anything.
            notes.append(f"{name}: confirmed, but no photograph in {photo_dir.name}/")
            continue

        header = {
            "merchant": row["merchant"] or None,
            "purchased_at": row["purchased_at"] or None,
            "subtotal": from_cents(row["subtotal_cents"]),
            "tax": from_cents(row["tax_cents"]),
            "total": from_cents(row["total_cents"]),
        }
        lines = [
            [item["description"], from_cents(item["amount_cents"])]
            for item in db.execute(
                "SELECT description, amount_cents FROM line_item "
                "WHERE receipt_id = ? ORDER BY line_no, id", (row["id"],)).fetchall()
            if item["amount_cents"] is not None
        ]

        known = tracked.get(name)
        if known is not None and known.says_anything:
            for field in HEADER_FIELDS:
                if field in known.header and known.header[field] != header[field]:
                    notes.append(
                        f"{name}: {field} is {header[field]!r} in the books but "
                        f"{known.header[field]!r} in the tracked truth — keeping "
                        "the tracked one")
            continue

        entries[name] = {
            "verified_by": "confirmed-in-app",
            "sha256": sha256_file(photo),
            "header": header,
            "lines": lines,
        }
    return entries, notes


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--data-dir", type=Path,
                        help="Books to read (default: the app's usual data folder).")
    parser.add_argument("--photos", type=Path, default=PHOTO_DIR)
    parser.add_argument("--out", type=Path, default=LOCAL_TRUTH_PATH)
    parser.add_argument("--show", action="store_true",
                        help="Print a summary and write nothing.")
    args = parser.parse_args(argv)

    entries, notes = collect(_books(args.data_dir), args.photos)
    for note in notes:
        print(f"  ! {note}")
    for name, entry in entries.items():
        header = sum(1 for value in entry["header"].values() if value is not None)
        print(f"  {name:<18} {len(entry['lines']):>3} lines, {header}/5 header fields")
    if not entries:
        print("Nothing to export: no confirmed receipt has a photograph "
              "that is not already in the tracked truth.")
        return 0

    if args.show:
        print(f"\n--show: {args.out} not written.")
        return 0

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps({"_comment": COMMENT, "receipts": entries},
                   ensure_ascii=False, indent=1) + "\n",
        encoding="utf-8")
    print(f"\nWrote {len(entries)} receipt(s) to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
