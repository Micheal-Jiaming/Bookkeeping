"""Offline fallback engine: Tesseract OCR plus a heuristic receipt parser.

This engine exists so the application still works with no API key, no network
and no per-scan cost. It is genuinely worse than the vision model and the code
does not pretend otherwise: it reports a capped confidence, so every receipt it
reads lands in the review queue instead of being auto-confirmed.

What it does:
  1. Runs Tesseract over the normalised image to get plain text.
  2. Walks the text line by line with a small set of layout rules that hold for
     most retail receipts (Walmart, Target, grocery chains): a purchased line is
     a description followed by a trailing amount; summary lines announce
     themselves with the words SUBTOTAL / TAX / TOTAL; the date is the first
     date-shaped token.

What it does not do: multi-column layouts, handwritten receipts, restaurant
tickets with the amount printed above the item, or any non-Latin script. When it
gets those wrong the reviewer fixes them by hand -- which is exactly why the
review step is not optional.
"""

from __future__ import annotations

import re
import time
from datetime import date
from pathlib import Path

from ..money import to_cents
from .base import (
    ExtractedItem,
    ExtractedReceipt,
    ExtractionError,
    ExtractionResult,
    Extractor,
)

# Tesseract's own confidence is about character recognition, not about whether
# this parser understood the layout. Cap what we report so a clean OCR pass on a
# misparsed receipt never looks trustworthy.
MAX_REPORTED_CONFIDENCE = 0.5

# A trailing amount, optionally with the trailing-minus that thermal printers
# use for credits, and optionally followed by a tax flag letter (Walmart prints
# "X", "O", "T", "N", "F").
_TRAILING_AMOUNT = re.compile(
    r"(?P<amount>-?\$?\d[\d,]*\.\d{2})\s*(?P<minus>-)?\s*(?P<flag>[A-Z])?\s*$"
)
_QTY_AT_PRICE = re.compile(r"(?P<qty>\d+(?:\.\d+)?)\s*(?:@|X)\s*\$?(?P<unit>\d[\d,]*\.\d{2})")
_LEADING_QTY = re.compile(r"^(?P<qty>\d{1,3})\s+(?=\D)")
_SKU = re.compile(r"\b(\d{9,14})\b")

_DATE_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\b(20\d{2})[-/.](\d{1,2})[-/.](\d{1,2})\b"), "ymd"),
    (re.compile(r"\b(\d{1,2})[-/.](\d{1,2})[-/.](\d{4})\b"), "mdy"),
    (re.compile(r"\b(\d{1,2})[-/.](\d{1,2})[-/.](\d{2})\b"), "mdy2"),
]

# Lines that are never purchased items.
_SUMMARY_WORDS = (
    "SUBTOTAL", "SUB TOTAL", "TOTAL", "TAX", "TIP", "GRATUITY", "BALANCE",
    "CHANGE", "CASH", "DEBIT", "CREDIT", "VISA", "MASTERCARD", "AMEX",
    "DISCOVER", "TEND", "PAYMENT", "AMOUNT DUE", "SAVINGS", "ITEMS SOLD",
    "TC#", "REF #", "APPROVAL", "AUTH", "ACCOUNT", "NETWORK ID", "TERMINAL",
    "THANK YOU", "SURVEY", "www.", "POINTS", "REWARD", "MEMBER", "STORE #",
    "OP#", "TE#", "TR#",
)
_DISCOUNT_WORDS = (
    "COUPON", "DISCOUNT", "ROLLBACK", "MARKDOWN", "PROMO", "VOID", "REFUND",
    "PRICE CUT", "SAVED",
)

# Store names worth recognising, so the merchant field is not simply the first
# OCR line (often a phone number or a slogan).
_KNOWN_MERCHANTS = {
    "WALMART": "Walmart",
    "WAL-MART": "Walmart",
    "SAM'S CLUB": "Sam's Club",
    "TARGET": "Target",
    "COSTCO": "Costco",
    "KROGER": "Kroger",
    "SAFEWAY": "Safeway",
    "TRADER JOE": "Trader Joe's",
    "WHOLE FOODS": "Whole Foods Market",
    "ALDI": "Aldi",
    "PUBLIX": "Publix",
    "CVS": "CVS Pharmacy",
    "WALGREENS": "Walgreens",
    "HOME DEPOT": "The Home Depot",
    "LOWE'S": "Lowe's",
    "BEST BUY": "Best Buy",
    "STARBUCKS": "Starbucks",
    "MCDONALD": "McDonald's",
    "CHIPOTLE": "Chipotle",
    "SHELL": "Shell",
    "CHEVRON": "Chevron",
    "PETCO": "Petco",
    "PETSMART": "PetSmart",
}


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
                "The Tesseract binary was not found. Install it from "
                "https://github.com/UB-Mannheim/tesseract/wiki, then set its path "
                "in Settings if it is not on PATH."
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


def parse_receipt_text(text: str) -> ExtractedReceipt:
    """Turn OCR text into an ``ExtractedReceipt``. Pure function, unit tested."""
    lines = [line.strip() for line in text.splitlines()]
    lines = [line for line in lines if line]

    receipt = ExtractedReceipt(currency="USD")
    receipt.merchant_raw, receipt.merchant = _find_merchant(lines)
    receipt.purchased_at = _find_date(lines)
    receipt.payment_method = _find_payment(lines)

    summary = _find_summary_amounts(lines)
    receipt.subtotal = summary.get("subtotal")
    receipt.tax = summary.get("tax")
    receipt.tip = summary.get("tip")
    receipt.total = summary.get("total")

    receipt.items = _find_items(lines)
    return receipt


def _find_merchant(lines: list[str]) -> tuple[str | None, str | None]:
    upper_head = [line.upper() for line in lines[:12]]
    for index, line in enumerate(upper_head):
        for needle, pretty in _KNOWN_MERCHANTS.items():
            if needle in line:
                return lines[index], pretty
    # Nothing recognised: use the first line that looks like a name rather than
    # an address, phone number or receipt barcode.
    for index, line in enumerate(lines[:6]):
        letters = sum(ch.isalpha() for ch in line)
        digits = sum(ch.isdigit() for ch in line)
        if letters >= 3 and digits <= letters:
            cleaned = re.sub(r"\s{2,}", " ", line).strip(" *-#")
            return line, cleaned.title() if cleaned.isupper() else cleaned
    return None, None


def _find_date(lines: list[str]) -> str | None:
    for line in lines:
        for pattern, order in _DATE_PATTERNS:
            match = pattern.search(line)
            if not match:
                continue
            try:
                if order == "ymd":
                    year, month, day = (int(g) for g in match.groups())
                elif order == "mdy":
                    month, day, year = (int(g) for g in match.groups())
                else:
                    month, day, short_year = (int(g) for g in match.groups())
                    year = 2000 + short_year if short_year <= 79 else 1900 + short_year
                if month > 12 and day <= 12:
                    # A DD/MM receipt slipped through; swap rather than fail.
                    month, day = day, month
                return date(year, month, day).isoformat()
            except ValueError:
                continue
    return None


def _find_payment(lines: list[str]) -> str | None:
    for line in lines:
        upper = line.upper()
        for word in ("VISA", "MASTERCARD", "AMEX", "DISCOVER", "DEBIT", "CREDIT", "CASH"):
            if word in upper:
                digits = re.search(r"(\d{4})\s*$", upper)
                return f"{word} ****{digits.group(1)}" if digits else word
    return None


def _find_summary_amounts(lines: list[str]) -> dict[str, str]:
    """Pull subtotal/tax/tip/total out of the summary block.

    Checked most-specific first: 'SUBTOTAL' contains 'TOTAL', and 'TOTAL TAX'
    contains both, so naive substring order would mislabel every one of them.
    The last amount on the line is the value; the last matching line wins,
    because receipts print the true total below any per-department subtotals.
    """
    found: dict[str, str] = {}
    for line in lines:
        upper = line.upper()
        amount = _trailing_amount_text(line)
        if amount is None:
            continue
        if "SUBTOTAL" in upper or "SUB TOTAL" in upper:
            found["subtotal"] = amount
        elif "TAX" in upper:
            found["tax"] = amount
        elif "TIP" in upper or "GRATUITY" in upper:
            found["tip"] = amount
        elif "TOTAL" in upper or "AMOUNT DUE" in upper or "BALANCE DUE" in upper:
            found["total"] = amount
    return found


def _trailing_amount_text(line: str) -> str | None:
    match = _TRAILING_AMOUNT.search(line.replace("$", " $"))
    if not match:
        return None
    raw = match.group("amount").replace("$", "").replace(",", "")
    if match.group("minus"):
        raw = f"-{raw}"
    return raw


def _is_summary_line(upper: str) -> bool:
    return any(word in upper for word in _SUMMARY_WORDS)


def _find_items(lines: list[str]) -> list[ExtractedItem]:
    items: list[ExtractedItem] = []
    for line in lines:
        upper = line.upper()
        if _is_summary_line(upper):
            continue
        match = _TRAILING_AMOUNT.search(line.replace("$", " $"))
        if not match:
            continue
        amount_text = _trailing_amount_text(line)
        if amount_text is None:
            continue

        head = line[: match.start()].strip(" .-*")
        is_discount = any(word in upper for word in _DISCOUNT_WORDS) or amount_text.startswith("-")
        if is_discount and not amount_text.startswith("-"):
            amount_text = f"-{amount_text}"

        sku_match = _SKU.search(head)
        sku = sku_match.group(1) if sku_match else None
        if sku:
            head = head.replace(sku, " ")

        quantity: float | None = None
        unit_price: str | None = None
        qty_match = _QTY_AT_PRICE.search(head)
        if qty_match:
            quantity = float(qty_match.group("qty"))
            unit_price = qty_match.group("unit").replace(",", "")
            head = head[: qty_match.start()].strip(" .-*x")
        else:
            lead = _LEADING_QTY.match(head)
            if lead:
                quantity = float(lead.group("qty"))
                head = head[lead.end():].strip()

        description = re.sub(r"\s{2,}", " ", head).strip(" .-*")
        # A "description" of one character or pure punctuation means the regex
        # latched onto a barcode or a phone number, not a purchase.
        if len(re.sub(r"[^A-Za-z]", "", description)) < 2:
            continue
        if to_cents(amount_text) in (None, 0):
            continue

        items.append(
            ExtractedItem(
                description=description,
                sku=sku,
                quantity=quantity,
                unit_price=unit_price,
                amount=amount_text,
                is_discount=is_discount,
                taxable={"X": True, "T": True, "N": False, "O": False}.get(
                    match.group("flag") or ""
                ),
            )
        )
    return items
