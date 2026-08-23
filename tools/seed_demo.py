"""Fill a set of books with plausible demo receipts.

For screenshots, for trying the reports on something other than an empty
database, and for showing the app to someone without handing over real receipts.

    py tools\\seed_demo.py --data-dir C:\\temp\\demo-books
    py tools\\seed_demo.py --data-dir C:\\temp\\demo-books --force

It refuses to touch a set of books that already has receipts unless ``--force``
is given, and ``--data-dir`` is required rather than defaulting: the whole point
is that this must never be pointed at somebody's real books by accident.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# (merchant, date, printed total, tax, [(item, amount, category)], confirmed)
RECEIPTS = [
    ("Walmart", "2026-06-08", "82.14", "4.65", [
        ("GV WHL MILK", "3.24", "Groceries"), ("BANANAS", "1.98", "Groceries"),
        ("TIDE PODS 42CT", "12.97", "Household"), ("DOG FOOD 16LB", "18.62", "Pets"),
        ("MARKETSIDE SALAD", "4.98", "Groceries"),
        ("PAPER TOWELS 6PK", "9.44", "Household"),
        ("CHICKEN BREAST 3LB", "11.86", "Groceries"),
        ("HDMI CABLE 6FT", "9.88", "Electronics"),
        ("IBUPROFEN 200CT", "4.52", "Health & Pharmacy"),
    ], True),
    ("Costco", "2026-07-22", "214.77", "11.62", [
        ("ROTISSERIE CHICKEN", "4.99", "Groceries"),
        ("OLIVE OIL 2L", "21.99", "Groceries"),
        ("PAPER TOWELS 12PK", "24.49", "Household"),
        ("CAT LITTER 40LB", "16.99", "Pets"),
        ("SALMON FILLET", "32.14", "Groceries"),
        ("VITAMIN D 600CT", "13.79", "Health & Pharmacy"),
        ("KIDS SNEAKERS", "24.99", "Clothing"),
        ("LAUNDRY DETERGENT", "18.99", "Household"),
        ("COFFEE BEANS 3LB", "19.49", "Groceries"),
        ("BATTERIES 48PK", "25.29", "Electronics"),
    ], True),
    ("Starbucks", "2026-08-03", "12.85", "0.73", [
        ("GRANDE LATTE", "5.45", "Dining"),
        ("BUTTER CROISSANT", "3.95", "Dining"),
        ("BOTTLED WATER", "2.72", "Dining"),
    ], True),
    ("Shell", "2026-08-11", "48.20", "0.00", [
        ("UNLEADED 11.4 GAL", "48.20", "Transport & Fuel"),
    ], True),
    ("CVS Pharmacy", "2026-08-18", "37.64", "1.32", [
        ("SHAMPOO 24OZ", "8.99", "Personal Care"),
        ("TYLENOL 100CT", "11.49", "Health & Pharmacy"),
        ("BABY WIPES 6PK", "12.99", "Baby & Kids"),
        ("RAZOR BLADES", "2.85", "Personal Care"),
    ], True),
]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--data-dir", required=True, type=Path,
                        help="Where to create the demo books. Required on purpose.")
    parser.add_argument("--force", action="store_true",
                        help="Add to books that already contain receipts.")
    parser.add_argument("--with-image", action="store_true",
                        help="Also add an unreviewed receipt with an image and a "
                             "deliberate 4.00 mismatch, to exercise the flags.")
    args = parser.parse_args()

    args.data_dir.mkdir(parents=True, exist_ok=True)
    os.environ["BOOKKEEPING_DATA"] = str(args.data_dir.resolve())

    from app import store
    from app.db import init_db
    from app.money import to_cents

    init_db()
    if store.status_counts() and not args.force:
        print(f"{args.data_dir} already has receipts: {store.status_counts()}\n"
              "Refusing to add more. Use --force if that is really what you want.")
        return 1

    ids = {category["name"]: category["id"] for category in store.list_categories()}
    for merchant, day, total, tax, items, confirm in RECEIPTS:
        receipt_id = store.create_manual()
        subtotal = f"{sum(float(amount) for _, amount, _ in items):.2f}"
        store.save_receipt(receipt_id, store.ReceiptEdit(
            merchant=merchant, purchased_at=day, currency="USD",
            subtotal_cents=to_cents(subtotal), tax_cents=to_cents(tax),
            total_cents=to_cents(total), payment_method="VISA ****4471",
            items=[store.ItemEdit(description=name, amount_cents=to_cents(amount),
                                  category_id=ids[category])
                   for name, amount, category in items],
        ), confirm=confirm)
        print(f"{merchant:<14} {day}  {total:>8}  {len(items)} lines")

    if args.with_image:
        from tools.make_sample_receipt import build

        image = args.data_dir / "demo-receipt.png"
        build().save(image)
        receipt_id = store.create_from_image(image.read_bytes(), "walmart-demo.png")
        store.save_receipt(receipt_id, store.ReceiptEdit(
            merchant="Walmart", purchased_at="2026-07-14", currency="USD",
            subtotal_cents=to_cents("64.59"), tax_cents=to_cents("3.87"),
            total_cents=to_cents("68.46"), payment_method="VISA ****4471",
            # 9.88 printed as 5.88: leaves the review pane with a real flag.
            items=[store.ItemEdit(description=name, amount_cents=to_cents(amount),
                                  category_id=ids[category], category_source=source)
                   for name, amount, category, source in [
                       ("GV WHL MILK", "3.24", "Groceries", "rule"),
                       ("BANANAS", "1.48", "Groceries", "rule"),
                       ("MARKETSIDE SALAD", "4.98", "Groceries", "rule"),
                       ("GREAT VALUE EGGS", "2.86", "Groceries", "rule"),
                       ("TIDE PODS 42CT", "12.97", "Household", "rule"),
                       ("PAPER TOWELS 6PK", "9.44", "Household", "rule"),
                       ("COLGATE TOOTHPASTE", "3.12", "Personal Care", "rule"),
                       ("DOG FOOD 16LB", "18.62", "Pets", "rule"),
                       ("HDMI CABLE 6FT", "5.88", "Electronics", "model"),
                       ("MANAGER COUPON", "-2.00", "Groceries", "rule"),
                   ]],
        ))
        print("Walmart        2026-07-14    68.46  10 lines, with image, needs review")

    report = store.report_summary(date_from="2026-01-01")
    print(f"\n{report['totals']['receipts']} confirmed receipts, "
          f"${report['totals']['spend']} spent, "
          f"{report['pending_review']} awaiting review")
    print(f"Run the app against these books with:\n"
          f"  run.bat --data-dir {args.data_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
