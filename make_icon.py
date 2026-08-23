"""Generates the app icon (assets/icon.ico) for Bookkeeping.

The mark says what the app does: a receipt -- a slip of paper with a torn
(zig-zag) bottom edge and printed lines -- with the last line rendered as a
solid total bar. On the blue app tile it reads as "paper receipt" at a glance.

Small sizes get a simplified master: at 16-24 px the printed lines and the tear
turn to mud, so those sizes keep only the slip, two lines and the total bar.

Usage:
    py make_icon.py             # writes assets/icon.ico
    py make_icon.py --preview   # also writes assets/icon-preview.png
"""

from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image, ImageDraw

HERE = Path(__file__).resolve().parent
ASSETS = HERE / "assets"

ICO_SIZES = [256, 128, 64, 48, 32, 24, 20, 16]
SMALL_AT_OR_BELOW = 24

# The same blue the UI uses for its bars, and the same near-white surface.
BLUE = (42, 120, 214, 255)
PAPER = (252, 252, 251, 255)
INK = (82, 81, 78, 255)
TOTAL = (42, 120, 214, 255)


def draw_master(size: int, simplified: bool) -> Image.Image:
    """Draw the icon at `size` px. Everything is proportional to size."""
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    unit = size / 32.0

    # Rounded blue tile.
    radius = max(2, round(7 * unit))
    draw.rounded_rectangle([0, 0, size - 1, size - 1], radius=radius, fill=BLUE)

    # The receipt slip: a white rectangle whose bottom edge is torn.
    left = round(8 * unit)
    right = size - 1 - round(8 * unit)
    top = round(5 * unit)
    tear_top = size - 1 - round(8 * unit)
    draw.rectangle([left, top, right, tear_top], fill=PAPER)

    teeth = 4 if simplified else 6
    tooth_width = (right - left) / teeth
    tooth_height = max(1.0, 1.8 * unit)
    points = [(left, tear_top)]
    for index in range(teeth):
        x_mid = left + tooth_width * (index + 0.5)
        x_end = left + tooth_width * (index + 1)
        points.append((x_mid, tear_top + tooth_height))
        points.append((x_end, tear_top))
    points.append((right, top))
    points.append((left, top))
    draw.polygon(points, fill=PAPER)

    # Printed lines, then the total bar.
    line_left = left + round(2.5 * unit)
    line_right = right - round(2.5 * unit)
    thickness = max(1, round(1.1 * unit))
    rows = [10.5, 14.0] if simplified else [9.0, 12.0, 15.0]
    for row in rows:
        y = top + round(row * unit) - top // 2
        y = round(top + (row - 5) * unit)
        short = 0 if row == rows[-1] else round(3 * unit)
        draw.rounded_rectangle(
            [line_left, y, line_right - short, y + thickness],
            radius=thickness / 2, fill=INK,
        )

    total_y = tear_top - round(4.2 * unit)
    total_height = max(2, round(2.4 * unit))
    draw.rounded_rectangle(
        [line_left, total_y, line_right, total_y + total_height],
        radius=total_height / 2, fill=TOTAL,
    )
    return image


def build_ico(path: Path) -> None:
    # Each size is drawn at 4x and downsampled, which keeps the diagonal tear
    # edge smooth instead of stair-stepped.
    frames = []
    for size in ICO_SIZES:
        simplified = size <= SMALL_AT_OR_BELOW
        master = draw_master(size * 4, simplified)
        frames.append(master.resize((size, size), Image.LANCZOS))
    path.parent.mkdir(parents=True, exist_ok=True)
    frames[0].save(path, format="ICO", sizes=[(f.width, f.height) for f in frames],
                   append_images=frames[1:])


def build_preview(path: Path) -> None:
    """A strip of every size on a neutral background, for eyeballing."""
    pad = 12
    width = sum(size + pad for size in ICO_SIZES) + pad
    height = max(ICO_SIZES) + pad * 2
    sheet = Image.new("RGBA", (width, height), (240, 240, 238, 255))
    x = pad
    for size in ICO_SIZES:
        icon = draw_master(size * 4, size <= SMALL_AT_OR_BELOW).resize(
            (size, size), Image.LANCZOS
        )
        sheet.alpha_composite(icon, (x, (height - size) // 2))
        x += size + pad
    sheet.save(path)


def main() -> None:
    ico = ASSETS / "icon.ico"
    build_ico(ico)
    print(f"wrote {ico} ({', '.join(f'{s}px' for s in ICO_SIZES)})")
    if "--preview" in sys.argv:
        preview = ASSETS / "icon-preview.png"
        build_preview(preview)
        print(f"wrote {preview}")


if __name__ == "__main__":
    main()
