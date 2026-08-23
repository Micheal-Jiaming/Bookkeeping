"""Upload-time image normalisation.

Phone photos of receipts arrive rotated (EXIF orientation), enormous (12 MP), and
occasionally in formats the Claude API will not accept. Normalising once at
upload is much better than at every scan: the stored file is what the reviewer
sees next to the extracted fields, and a re-scan must see exactly the same
pixels the first scan saw or the two readings are not comparable.

* EXIF orientation is applied and stripped, so the receipt is upright.
* The long edge is capped at ``MAX_EDGE``. Anthropic's guidance is that images
  above roughly 1568px on the long edge are downscaled server side anyway, so
  sending more just costs tokens without improving the reading.
* Output is always PNG (lossless, universally accepted). Receipts are mostly
  flat text on white, so PNG stays small.
"""

from __future__ import annotations

import io
from pathlib import Path

from PIL import Image, ImageOps, UnidentifiedImageError

MAX_EDGE = 1568
# Multi-page/animated inputs are read as their first frame only.
NORMALISED_SUFFIX = ".png"


class ImageError(ValueError):
    """The uploaded bytes are not an image this application can use."""


def normalise(data: bytes) -> bytes:
    """Return upright, size-capped PNG bytes for an uploaded image."""
    try:
        image = Image.open(io.BytesIO(data))
        image.load()
    except (UnidentifiedImageError, OSError) as exc:
        raise ImageError(
            "That file is not a readable image. Upload a PNG, JPEG, WebP, GIF or BMP."
        ) from exc

    image = ImageOps.exif_transpose(image)
    if image.mode not in ("RGB", "L"):
        # Flatten transparency onto white; a receipt photo has no meaningful alpha
        # and RGBA PNGs render unpredictably in the review pane.
        if image.mode in ("RGBA", "LA", "P"):
            image = image.convert("RGBA")
            background = Image.new("RGBA", image.size, (255, 255, 255, 255))
            image = Image.alpha_composite(background, image)
        image = image.convert("RGB")

    long_edge = max(image.size)
    if long_edge > MAX_EDGE:
        scale = MAX_EDGE / long_edge
        new_size = (max(1, round(image.width * scale)), max(1, round(image.height * scale)))
        image = image.resize(new_size, Image.LANCZOS)

    out = io.BytesIO()
    image.save(out, format="PNG", optimize=True)
    return out.getvalue()


def dimensions(path: Path) -> tuple[int, int]:
    with Image.open(path) as image:
        return image.size
