"""Offline engine: Tesseract OCR, feeding the shared receipt-text parser.

Tesseract is the most accurate of the two offline engines when it is installed
and the image is clean, but it is a separate ~60 MB install that this
application cannot ship. When it is absent the Windows OCR engine takes over --
see ``windows_ocr.py`` -- so the offline path still works out of the box.

Either way the reading is genuinely worse than the vision model and the code
does not pretend otherwise: it reports a capped confidence, so every receipt it
reads lands in the review queue instead of being auto-confirmed.
"""

from __future__ import annotations

import time
from pathlib import Path

from .base import ExtractionError, ExtractionResult, Extractor
from .receipt_text import parse_receipt_text

# Tesseract's own confidence is about character recognition, not about whether
# the parser understood the layout. Cap what we report so a clean OCR pass on a
# misparsed receipt never looks trustworthy.
MAX_REPORTED_CONFIDENCE = 0.5


class TesseractExtractor(Extractor):
    name = "tesseract"

    def __init__(self, tesseract_cmd: str = "") -> None:
        self.tesseract_cmd = (tesseract_cmd or "").strip()

    def _module(self):
        try:
            import pytesseract  # noqa: PLC0415 - optional dependency, imported on use
        except ImportError as exc:  # pragma: no cover - depends on environment
            raise ExtractionError(
                "pytesseract is not installed. Run: pip install pytesseract"
            ) from exc
        if self.tesseract_cmd:
            pytesseract.pytesseract.tesseract_cmd = self.tesseract_cmd
        return pytesseract

    def available(self) -> tuple[bool, str]:
        try:
            pytesseract = self._module()
            version = pytesseract.get_tesseract_version()
        except ExtractionError as exc:
            return False, str(exc)
        except Exception:  # pytesseract raises TesseractNotFoundError and friends
            return False, (
                "Not installed (optional). Windows OCR covers the offline case; "
                "install Tesseract from https://github.com/UB-Mannheim/tesseract/wiki "
                "only if you want a second offline reader."
            )
        return True, f"Tesseract {version}"

    def extract(self, image_path: Path, categories: list[str]) -> ExtractionResult:
        ok, reason = self.available()
        if not ok:
            raise ExtractionError(reason)
        pytesseract = self._module()
        from PIL import Image  # noqa: PLC0415 - keep Pillow out of import time

        started = time.monotonic()
        with Image.open(image_path) as image:
            grey = image.convert("L")
            # --psm 6: "assume a single uniform block of text", which is what a
            # receipt is. The default page-segmentation mode hunts for columns
            # and shuffles the line order.
            text = pytesseract.image_to_string(grey, config="--psm 6")
            ocr_confidence = _mean_confidence(pytesseract, grey)
        elapsed_ms = int((time.monotonic() - started) * 1000)

        if not text.strip():
            raise ExtractionError(
                "Tesseract found no text in this image. It may be too dark, too "
                "small or not a receipt."
            )

        receipt = parse_receipt_text(text)
        receipt.confidence = round(min(ocr_confidence, MAX_REPORTED_CONFIDENCE), 3)
        receipt.notes = (
            "Read by offline OCR, which misreads amounts and item names far more "
            "often than the vision model. Check every line against the image."
        )
        return ExtractionResult(
            receipt=receipt,
            engine=self.name,
            model="tesseract",
            raw_text=text,
            elapsed_ms=elapsed_ms,
        )


def _mean_confidence(pytesseract, image) -> float:
    """Mean per-word confidence, 0.0-1.0. Falls back to 0.3 if unavailable."""
    try:
        data = pytesseract.image_to_data(
            image, config="--psm 6", output_type=pytesseract.Output.DICT
        )
    except Exception:  # pragma: no cover - depends on the local binary
        return 0.3
    values = [float(c) for c in data.get("conf", []) if str(c) not in ("-1", "")]
    if not values:
        return 0.3
    return max(0.0, min(1.0, sum(values) / len(values) / 100.0))
