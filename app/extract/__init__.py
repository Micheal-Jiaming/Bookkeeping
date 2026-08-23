"""Engine registry.

``build_engines`` turns the stored settings into the ordered list of engines the
pipeline should try. The order encodes the fallback policy chosen for this
project: Claude vision first because it is far more accurate, Tesseract second
so the application still works offline or without a key.
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
from .tesseract_ocr import TesseractExtractor, parse_receipt_text

__all__ = [
    "ClaudeVisionExtractor",
    "ExtractedItem",
    "ExtractedReceipt",
    "ExtractionError",
    "ExtractionResult",
    "Extractor",
    "TesseractExtractor",
    "build_engines",
    "engine_status",
    "estimate_cost",
    "media_type_for",
    "parse_receipt_text",
    "sha256_file",
]


def build_engines(settings: dict[str, str]) -> list[Extractor]:
    """Engines to try, in order, for the configured engine preference.

    ``engine`` setting:
      auto      -- Claude, then Tesseract (the default)
      claude    -- Claude only; fail loudly rather than silently degrading
      tesseract -- Tesseract only
      manual    -- no automatic reading at all
    """
    preference = (settings.get("engine") or "auto").lower()
    claude = ClaudeVisionExtractor(
        api_key=settings.get("anthropic_api_key", ""),
        model=settings.get("model", "claude-opus-5"),
        effort=settings.get("effort", "medium"),
        base_url=settings.get("anthropic_base_url", ""),
    )
    tesseract = TesseractExtractor(tesseract_cmd=settings.get("tesseract_cmd", ""))

    if preference == "claude":
        return [claude]
    if preference == "tesseract":
        return [tesseract]
    if preference == "manual":
        return []
    return [claude, tesseract]


def engine_status(settings: dict[str, str]) -> list[dict[str, object]]:
    """Availability of every engine, for the Settings page."""
    claude = ClaudeVisionExtractor(
        api_key=settings.get("anthropic_api_key", ""),
        model=settings.get("model", "claude-opus-5"),
        effort=settings.get("effort", "medium"),
        base_url=settings.get("anthropic_base_url", ""),
    )
    tesseract = TesseractExtractor(tesseract_cmd=settings.get("tesseract_cmd", ""))
    out = []
    for engine in (claude, tesseract):
        ok, reason = engine.available()
        out.append({"name": engine.name, "available": ok, "detail": reason})
    return out
