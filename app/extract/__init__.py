"""Engine registry.

``build_engines`` turns the stored settings into the ordered list of engines the
pipeline should try. The order encodes the fallback policy chosen for this
project: Claude vision first because it is far more accurate, then **RapidOCR
where it is present**, then Windows OCR so the application still reads receipts
with no key, no network and nothing installed, then Tesseract for anyone who has
gone to the trouble of installing it.

RapidOCR sits above Windows OCR on the totals over the eight photographs the
user confirmed line by line -- 81 printed lines matched against 66, 4 missed
against 19, none invented against 3, and 74 item names exact against 41
(Bookkeeping_record.md section 9). Per photograph it is **better on six,
identical on `Walmart2.jpg`, and worse on `Walmart4.jpg`**, where it loses the
space in `GV RY RD IC` and scores 2 names exact to the Windows engine's 3.
Stated that way because an earlier version of this comment claimed "better on
every photograph", which was never true and survived two rewrites. It is *below*
Claude for the same reason Windows OCR is: it reads characters, where the vision
model reads a receipt.

It is only present in the full build, because it costs 95 MB. The slim build
reports it unavailable and falls through, which is why the order matters rather
than the choice: one list of engines serves both builds.

Windows OCR sits ahead of Tesseract because it is the engine whose accuracy has
actually been measured here and because it needs no setup at all, which is what
a portable .exe handed to someone else depends on. Tesseract remains selectable
outright for anyone who prefers it.
"""

from __future__ import annotations

from .base import (
    ExtractedItem,
    ExtractedReceipt,
    ExtractionError,
    ExtractionResult,
    Extractor,
    media_type_for,
    sha256_file,
)
from .claude_vision import ClaudeVisionExtractor, estimate_cost
from .rapid_ocr import RapidOcrExtractor
from .receipt_text import parse_receipt_text
from .tesseract_ocr import TesseractExtractor
from .windows_ocr import WindowsOcrExtractor

__all__ = [
    "ClaudeVisionExtractor",
    "ExtractedItem",
    "ExtractedReceipt",
    "ExtractionError",
    "ExtractionResult",
    "Extractor",
    "RapidOcrExtractor",
    "TesseractExtractor",
    "WindowsOcrExtractor",
    "build_engines",
    "engine_status",
    "estimate_cost",
    "media_type_for",
    "parse_receipt_text",
    "sha256_file",
]


def _engines(settings: dict[str, str]) -> dict[str, Extractor]:
    """Every engine, configured from settings, keyed by its preference name."""
    return {
        "claude": ClaudeVisionExtractor(
            api_key=settings.get("anthropic_api_key", ""),
            model=settings.get("model", "claude-opus-5"),
            effort=settings.get("effort", "medium"),
            base_url=settings.get("anthropic_base_url", ""),
        ),
        "rapid": RapidOcrExtractor(),
        "windows": WindowsOcrExtractor(language=settings.get("ocr_language", "")),
        "tesseract": TesseractExtractor(tesseract_cmd=settings.get("tesseract_cmd", "")),
    }


def build_engines(settings: dict[str, str]) -> list[Extractor]:
    """Engines to try, in order, for the configured engine preference.

    ``engine`` setting:
      auto      -- Claude, then RapidOCR, then Windows OCR, then Tesseract
      claude    -- Claude only; fail loudly rather than silently degrading
      rapid     -- RapidOCR only (full build, or pip install rapidocr)
      windows   -- the built-in Windows OCR only
      tesseract -- Tesseract only
      manual    -- no automatic reading at all
    """
    available = _engines(settings)
    preference = (settings.get("engine") or "auto").lower()
    if preference == "manual":
        return []
    if preference in available:
        return [available[preference]]
    return [available["claude"], available["rapid"],
            available["windows"], available["tesseract"]]


def engine_status(settings: dict[str, str]) -> list[dict[str, object]]:
    """Availability of every engine, for the Settings page."""
    out = []
    for engine in _engines(settings).values():
        ok, reason = engine.available()
        out.append({"name": engine.name, "available": ok, "detail": reason})
    return out
