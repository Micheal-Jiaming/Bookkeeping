"""Offline engine: the OCR built into Windows, with no install and no key.

This is the engine that makes the application work out of the box. Windows 10
and 11 ship ``Windows.Media.Ocr``, so a portable copy of Bookkeeping handed to
somebody else reads receipts on their machine with nothing configured -- no API
key to buy, no 60 MB Tesseract installer, no network. It is less accurate than
the vision model and it says so: the confidence it reports keeps every receipt
in the review queue.

The interesting part is not the OCR call, it is putting the words back in order.
Windows returns text grouped into its own lines, and on a receipt photographed
at a slight angle that grouping splits the page into a column of descriptions
followed by a column of amounts -- so ``result.text`` reads as every item name,
then every price, with nothing connecting them. Unusable. What it also returns,
and what this module actually uses, is a bounding box per word. Re-grouping
those boxes by vertical position rebuilds the real rows:

    BEDINABAG  840021403470  29.72  X

which is the shape ``receipt_text.parse_receipt_text`` already knows how to
read.

Two decisions here came from measurement rather than taste, and are recorded in
Bookkeeping.md section 11:

* **The row tolerance is half the median word height**, which is the constant
  docTR uses in ``models/builder.py`` for the same job. A fraction of the text
  size rather than a pixel count is what survives photos taken at different
  distances.
* **The image is not preprocessed.** Greyscale, upscaling, autocontrast and
  sharpening were each measured against the real Walmart receipt in
  ``tests/test_real_receipt.py``; none beat the plain image and sharpening and
  upscaling were worse. The published advice to deskew and upscale is all
  Tesseract advice -- Windows OCR does its own normalisation and resents the
  help.
"""

from __future__ import annotations

import asyncio
import re
import statistics
import time
from dataclasses import dataclass
from pathlib import Path

from .base import ExtractionError, ExtractionResult, Extractor
from .receipt_text import parse_receipt_text

# Windows OCR reports no per-word confidence, so unlike the Tesseract engine
# there is no character-level number to cap. This is the ceiling for a reading
# whose arithmetic checks out; anything less certain scores below it.
MAX_REPORTED_CONFIDENCE = 0.5

# docTR groups words into a line when the gap between vertical centres is under
# half the median word height (doctr/models/builder.py, `_resolve_lines`).
ROW_TOLERANCE = 0.5

# The three ways Windows OCR mangles a printed price, in the order they have to
# be repaired -- each step feeds the next, and applied out of order none of them
# match. Every pattern is gated on the ".dd" of a price so that character
# confusion is only ever repaired inside the amount column; running this kind of
# substitution across the whole page would wreck the item descriptions.
#
# 1. A leading zero comes back as the letter o or O: "o. 98".
_LETTER_ZERO = re.compile(r"\b[oO](?=\.[ \t]*\d{2}\b)")
# 2. The decimal point ends a glyph cluster, so the amount arrives as two
#    words: "3." and "04".
_SPLIT_DECIMAL = re.compile(r"(?<=\d)\.[ \t]+(?=\d{2}\b)")
# 3. Walmart's "O" tax flag after the price is read as a digit zero, which then
#    looks like a second number and stops the line parsing as an item at all.
_FLAG_ZERO = re.compile(r"(?<=\.\d{2})[ \t]+0[ \t]*$")


@dataclass(frozen=True)
class Word:
    """One recognised word and where it sits on the page, in pixels."""

    text: str
    x: float
    y: float
    width: float
    height: float

    @property
    def centre_y(self) -> float:
        return self.y + self.height / 2


def group_rows(words: list[Word], tolerance: float = ROW_TOLERANCE) -> list[list[Word]]:
    """Group words into printed rows by vertical position, left to right.

    Pure function so the layout logic can be tested without Windows OCR. The
    running centre is re-averaged as each word joins, which keeps a row from
    drifting upwards across a receipt that curves away from the camera.
    """
    if not words:
        return []
    limit = statistics.median(word.height for word in words) * tolerance
    rows: list[tuple[float, list[Word]]] = []
    for word in sorted(words, key=lambda w: w.centre_y):
        if rows and abs(word.centre_y - rows[-1][0]) <= limit:
            centre, members = rows[-1]
            members.append(word)
            rows[-1] = (sum(m.centre_y for m in members) / len(members), members)
        else:
            rows.append((word.centre_y, [word]))
    return [sorted(members, key=lambda w: w.x) for _, members in rows]


def rows_to_text(rows: list[list[Word]]) -> str:
    """Render grouped rows as the plain receipt text the parser expects."""
    return "\n".join(repair_amounts(" ".join(w.text for w in row)) for row in rows)


def repair_amounts(line: str) -> str:
    """Undo the ways Windows OCR reliably mangles a printed price."""
    line = _LETTER_ZERO.sub("0", line)
    line = _SPLIT_DECIMAL.sub(".", line)
    return _FLAG_ZERO.sub(" O", line)


class WindowsOcrExtractor(Extractor):
    name = "windows"

    def __init__(self, language: str = "") -> None:
        self.language = (language or "").strip()

    def _engine(self):
        """Create an OcrEngine, or raise ExtractionError explaining why not."""
        try:
            from winrt.windows.globalization import Language  # noqa: PLC0415
            from winrt.windows.media.ocr import OcrEngine  # noqa: PLC0415
        except ImportError as exc:
            raise ExtractionError(
                "The Windows OCR bindings are not installed. Run: "
                "pip install -r requirements.txt"
            ) from exc
        except OSError as exc:  # pragma: no cover - non-Windows only
            raise ExtractionError("Windows OCR is only available on Windows.") from exc

        engine = None
        if self.language:
            engine = OcrEngine.try_create_from_language(Language(self.language))
            if engine is None:
                raise ExtractionError(
                    f"Windows has no OCR language pack for '{self.language}'. "
                    "Add it under Settings > Time & language > Language & region."
                )
        if engine is None:
            # Prefer English: receipts here are English, and the recognisers are
            # per-language, so picking the user's first profile language would
            # read a US receipt with, say, the German model.
            for available in OcrEngine.available_recognizer_languages:
                if available.language_tag.lower().startswith("en"):
                    engine = OcrEngine.try_create_from_language(available)
                    break
        if engine is None:
            engine = OcrEngine.try_create_from_user_profile_languages()
        if engine is None:
            raise ExtractionError(
                "Windows has no OCR language pack installed. Add one under "
                "Settings > Time & language > Language & region."
            )
        return engine

    def available(self) -> tuple[bool, str]:
        try:
            engine = self._engine()
        except ExtractionError as exc:
            return False, str(exc)
        except Exception as exc:  # pragma: no cover - defensive
            return False, f"Windows OCR is unavailable: {exc}"
        return True, f"Windows OCR ({engine.recognizer_language.language_tag})"

    def extract(self, image_path: Path, categories: list[str]) -> ExtractionResult:
        started = time.monotonic()
        words = asyncio.run(self._read(image_path))
        elapsed_ms = int((time.monotonic() - started) * 1000)

        if not words:
            raise ExtractionError(
                "Windows OCR found no text in this image. It may be too dark, "
                "too blurred or not a receipt."
            )

        text = rows_to_text(group_rows(words))
        receipt = parse_receipt_text(text)
        receipt.confidence = _confidence(receipt)
        receipt.notes = (
            "Read by the offline Windows OCR engine, which misreads amounts and "
            "item names far more often than the vision model, and drops lines it "
            "cannot see. Check every line against the image."
        )
        return ExtractionResult(
            receipt=receipt,
            engine=self.name,
            model="windows-ocr",
            raw_text=text,
            elapsed_ms=elapsed_ms,
        )

    async def _read(self, image_path: Path) -> list[Word]:
        from winrt.windows.graphics.imaging import BitmapDecoder  # noqa: PLC0415
        from winrt.windows.storage.streams import (  # noqa: PLC0415
            DataWriter,
            InMemoryRandomAccessStream,
        )

        engine = self._engine()

        # Decode from memory rather than handing WinRT the path: StorageFile
        # applies its own file-access broker rules, which a file under a data
        # folder the user chose is not guaranteed to satisfy.
        stream = InMemoryRandomAccessStream()
        writer = DataWriter(stream)
        writer.write_bytes(image_path.read_bytes())
        await writer.store_async()
        await writer.flush_async()
        writer.detach_stream()
        stream.seek(0)

        try:
            decoder = await BitmapDecoder.create_async(stream)
            bitmap = await decoder.get_software_bitmap_async()
        except OSError as exc:
            raise ExtractionError(f"Windows could not decode this image: {exc}") from exc

        result = await engine.recognize_async(bitmap)
        return [
            Word(word.text, rect.x, rect.y, rect.width, rect.height)
            for line in result.lines
            for word, rect in ((w, w.bounding_rect) for w in line.words)
        ]


def _confidence(receipt) -> float:
    """Score the reading by whether it hangs together arithmetically.

    Windows OCR exposes no confidence of its own, so guessing one from nothing
    would be dishonest. What can be checked is the receipt's own bookkeeping: a
    reading that found a total, and whose line items add up to the printed
    subtotal, got the layout right. One that did not, did not.
    """
    from ..money import to_cents  # noqa: PLC0415 - avoids an import cycle at module load

    score = 0.2
    if receipt.total:
        score += 0.1
    if receipt.items:
        score += 0.1
    subtotal = to_cents(receipt.subtotal)
    amounts = [to_cents(item.amount) for item in receipt.items]
    if subtotal and amounts and all(a is not None for a in amounts):
        if abs(sum(amounts) - subtotal) <= 5:  # the 5-cent tolerance used in validate.py
            score += 0.1
    return round(min(score, MAX_REPORTED_CONFIDENCE), 3)
