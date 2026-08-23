"""The extraction contract shared by every recognition engine.

Everything downstream of this module -- validation, categorisation, storage, the
review UI -- only ever sees an ``ExtractedReceipt``. Adding a new engine (a
different vision model, a cloud OCR service, an offline layout parser) means
implementing ``Extractor`` and registering it in ``app/extract/__init__.py``;
nothing else has to change. This mirrors how Receipt Wrangler keeps an
``ai_client`` interface with one file per provider.
"""

from __future__ import annotations

import hashlib
from abc import ABC, abstractmethod
from pathlib import Path

from pydantic import BaseModel, Field

# Media types the Claude API accepts for image blocks, keyed by file suffix.
SUPPORTED_IMAGE_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
}


class ExtractedItem(BaseModel):
    """One printed line on the receipt.

    Field names and descriptions double as the prompt for the vision model --
    the JSON schema handed to the API is generated from this class, so the
    descriptions are load-bearing, not decoration.
    """

    description: str = Field(
        description="Item name exactly as printed on the receipt, e.g. 'GV WHL MILK'."
    )
    readable_name: str | None = Field(
        default=None,
        description=(
            "The same item expanded into plain English when the printed name is "
            "abbreviated, e.g. 'Great Value Whole Milk'. Null if already plain."
        ),
    )
    sku: str | None = Field(
        default=None, description="Item/UPC number printed beside the item, if any."
    )
    quantity: float | None = Field(
        default=None, description="Quantity or weight. Null when not printed."
    )
    unit_price: str | None = Field(
        default=None,
        description="Per-unit price as printed, decimal string without a currency symbol.",
    )
    amount: str | None = Field(
        default=None,
        description=(
            "Line total charged, decimal string without a currency symbol. "
            "Negative for discounts, coupons and voided lines."
        ),
    )
    is_discount: bool = Field(
        default=False,
        description="True for coupons, markdowns, rollbacks and other negative lines.",
    )
    taxable: bool | None = Field(
        default=None,
        description="True/false if the receipt marks the line taxable (e.g. an 'X'/'T' flag); null if unmarked.",
    )
    category: str | None = Field(
        default=None,
        description=(
            "Best-fit expense category for this item, chosen ONLY from the list of "
            "allowed categories given in the instructions. Null if genuinely unclear."
        ),
    )


class ExtractedReceipt(BaseModel):
    """A whole receipt as read off the image."""

    merchant: str | None = Field(
        default=None, description="Normalised store name, e.g. 'Walmart'."
    )
    merchant_raw: str | None = Field(
        default=None, description="Store name exactly as printed, including any store number."
    )
    purchased_at: str | None = Field(
        default=None,
        description="Purchase date as YYYY-MM-DD. Null if the receipt shows no date.",
    )
    currency: str = Field(
        default="USD", description="ISO 4217 code inferred from the receipt, e.g. 'USD'."
    )
    subtotal: str | None = Field(default=None, description="Subtotal before tax, decimal string.")
    tax: str | None = Field(default=None, description="Total tax charged, decimal string.")
    tip: str | None = Field(default=None, description="Tip or gratuity, decimal string.")
    total: str | None = Field(
        default=None, description="Grand total actually charged, decimal string."
    )
    payment_method: str | None = Field(
        default=None,
        description="How it was paid, e.g. 'VISA ****1234', 'CASH', 'DEBIT'. Null if absent.",
    )
    category: str | None = Field(
        default=None,
        description=(
            "Single best category for the receipt as a whole, chosen ONLY from the "
            "allowed category list."
        ),
    )
    items: list[ExtractedItem] = Field(
        default_factory=list,
        description=(
            "Every purchased line in printed order. Do not include subtotal, tax, "
            "total, change due, or store marketing lines here."
        ),
    )
    confidence: float = Field(
        default=0.0,
        description=(
            "Your own confidence that this reading is correct, 0.0-1.0. Be honest: "
            "use below 0.6 when the image is blurry, cropped or partly illegible."
        ),
    )
    notes: str | None = Field(
        default=None,
        description="Anything a human reviewer should know, e.g. 'bottom of receipt cut off'.",
    )


class ExtractionResult(BaseModel):
    """An ``ExtractedReceipt`` plus how it was obtained (for cost and audit)."""

    receipt: ExtractedReceipt
    engine: str
    model: str | None = None
    raw_text: str | None = None
    raw_response: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    cost_usd: float | None = None
    elapsed_ms: int | None = None


class ExtractionError(RuntimeError):
    """Raised when an engine cannot produce a reading at all."""


class Extractor(ABC):
    """A recognition engine."""

    name: str = "base"

    @abstractmethod
    def available(self) -> tuple[bool, str]:
        """Return (usable, human-readable reason). Reason explains 'why not'."""

    @abstractmethod
    def extract(self, image_path: Path, categories: list[str]) -> ExtractionResult:
        """Read the image. Raise ``ExtractionError`` if it cannot be read."""


def media_type_for(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix not in SUPPORTED_IMAGE_TYPES:
        raise ExtractionError(
            f"Unsupported image type '{suffix}'. Supported: "
            + ", ".join(sorted(SUPPORTED_IMAGE_TYPES))
        )
    return SUPPORTED_IMAGE_TYPES[suffix]


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()
