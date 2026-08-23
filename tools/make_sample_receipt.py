"""Generate a synthetic Walmart-style receipt image with known values.

Used for testing the whole pipeline without spending money on API calls and
without needing a real receipt photo. Because the expected values are known
exactly, a test can assert that the reading is correct rather than merely
plausible.

    py tools/make_sample_receipt.py [out.png]

Prints the expected values as JSON on stdout.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

WIDTH = 620
LINE_HEIGHT = 26
MARGIN = 28

ITEMS: list[tuple[str, str, str]] = [
    # (printed name, item number, amount)
    ("GV WHL MILK", "007874203912", "3.24"),
    ("BANANAS", "000000004011", "1.48"),
    ("MARKETSIDE SALAD", "068113106422", "4.98"),
    ("GREAT VALUE EGGS", "007874200043", "2.86"),
    ("TIDE PODS 42CT", "003700091783", "12.97"),
    ("PAPER TOWELS 6PK", "003600041560", "9.44"),
    ("COLGATE TOOTHPASTE", "003500097281", "3.12"),
    ("DOG FOOD 16LB", "001780012384", "18.62"),
    ("HDMI CABLE 6FT", "006842712001", "9.88"),
    ("MANAGER COUPON", "", "-2.00"),
]
SUBTOTAL = "64.59"
TAX = "3.87"
TOTAL = "68.46"
MERCHANT = "Walmart"
PURCHASED_AT = "2026-07-14"


def _font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    """A monospace face, so the columns line up the way a receipt printer does."""
    candidates = [
        "C:/Windows/Fonts/consolab.ttf" if bold else "C:/Windows/Fonts/consola.ttf",
        "C:/Windows/Fonts/cour.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
    ]
    for path in candidates:
        if Path(path).exists():
            return ImageFont.truetype(path, size)
    return ImageFont.load_default()


def build() -> Image.Image:
    lines: list[tuple[str, str, bool]] = []  # (left, right, bold)
    lines.append(("Walmart", "", True))
    lines.append(("Save money. Live better.", "", False))
    lines.append(("(479) 273-4000", "", False))
    lines.append(("MANAGER DIANE HAWKINS", "", False))
    lines.append(("702 SW 8TH ST", "", False))
    lines.append(("BENTONVILLE AR 72716", "", False))
    lines.append(("ST# 00100 OP# 000912 TE# 44 TR# 07321", "", False))
    lines.append(("", "", False))
    for name, sku, amount in ITEMS:
        left = f"{name} {sku}".strip()
        lines.append((left, f"{amount} X", False))
    lines.append(("", "", False))
    lines.append(("SUBTOTAL", SUBTOTAL, False))
    lines.append(("TAX 1  6.000 %", TAX, False))
    lines.append(("TOTAL", TOTAL, True))
    lines.append(("VISA TEND", TOTAL, False))
    lines.append(("", "", False))
    lines.append(("ITEMS SOLD 9", "", False))
    lines.append(("TC# 7789 4412 9083 1122 4471", "", False))
    lines.append(("07/14/26                    19:42:08", "", False))
    lines.append(("", "", False))
    lines.append(("Thank you for shopping with us", "", False))

    height = MARGIN * 2 + LINE_HEIGHT * len(lines)
    image = Image.new("RGB", (WIDTH, height), "white")
    draw = ImageDraw.Draw(image)
    regular, bold = _font(18), _font(20, bold=True)

    y = MARGIN
    for left, right, is_bold in lines:
        font = bold if is_bold else regular
        draw.text((MARGIN, y), left, font=font, fill="black")
        if right:
            text_width = draw.textlength(right, font=font)
            draw.text((WIDTH - MARGIN - text_width, y), right, font=font, fill="black")
        y += LINE_HEIGHT
    return image


def expected() -> dict:
    return {
        "merchant": MERCHANT,
        "purchased_at": PURCHASED_AT,
        "subtotal": SUBTOTAL,
        "tax": TAX,
        "total": TOTAL,
        "item_count": len(ITEMS),
        "items": [
            {"description": name, "sku": sku, "amount": amount} for name, sku, amount in ITEMS
        ],
    }


def main() -> None:
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("sample-receipt.png")
    out.parent.mkdir(parents=True, exist_ok=True)
    build().save(out)
    print(json.dumps({"path": str(out), "expected": expected()}, indent=2))


if __name__ == "__main__":
    main()
