"""Primary recognition engine: Claude vision, one call per receipt.

The whole reading happens in a single request. The receipt image goes in as an
image content block; the reply is constrained to the ``ExtractedReceipt`` JSON
schema through the SDK's structured-output helper (``messages.parse`` with
``output_format=``), so there is no prompt-and-pray JSON parsing and no regex
scraping of prose. Categorisation for each line is asked for in the same call
because the model already has the item names in front of it -- a second round
trip would cost as much as the first and know less.

Deliberate omissions, so a future reader does not think they were oversights:

* No separate OCR step. Passing the pixels straight to the vision model reads
  crumpled thermal paper better than OCR-then-LLM-over-text, because layout and
  column alignment survive.
* No server-side refusal ``fallbacks`` parameter. It lives on the beta endpoint
  and would mean giving up ``messages.parse``; receipt reading is not a
  refusal-prone category. A ``refusal`` stop reason is still handled explicitly
  below rather than being mistaken for a malformed reply.
"""

from __future__ import annotations

import base64
import json
import time
from pathlib import Path

import anthropic

from .base import (
    ExtractedReceipt,
    ExtractionError,
    ExtractionResult,
    Extractor,
    media_type_for,
)

# USD per million tokens (input, output), from the Anthropic pricing table.
# Used only to show the user what a scan cost; unknown models fall back to None.
PRICING: dict[str, tuple[float, float]] = {
    "claude-opus-5": (5.00, 25.00),
    "claude-opus-4-8": (5.00, 25.00),
    "claude-opus-4-7": (5.00, 25.00),
    "claude-opus-4-6": (5.00, 25.00),
    "claude-sonnet-5": (3.00, 15.00),
    "claude-sonnet-4-6": (3.00, 15.00),
    "claude-haiku-4-5": (1.00, 5.00),
    "claude-fable-5": (10.00, 50.00),
}

SYSTEM_PROMPT = """\
You are the receipt-reading component of a bookkeeping application. You read a \
photo or scan of a purchase receipt and report exactly what it says.

Rules:
- Transcribe, do not invent. If a field is not printed on the receipt, return \
null for it. Never guess a date, a total or a store name that is not visible.
- Copy item names character for character into `description`, keeping the \
abbreviations the store printed. Put the expanded plain-English name in \
`readable_name` when the printed name is cryptic.
- `items` contains only purchased lines. Subtotal, tax, total, change due, \
loyalty points, survey invitations and store slogans are not items. Coupons, \
markdowns and voided lines ARE items, with a negative `amount` and \
`is_discount` set to true.
- Amounts are decimal strings with no currency symbol and no thousands \
separators: "12.34", "-2.00".
- Dates are YYYY-MM-DD. US receipts print MM/DD/YY; convert them. A two-digit \
year 00-79 means 2000-2079.
- Assign a category to every item and to the receipt as a whole, choosing only \
from the allowed list. Use null rather than forcing a bad fit.
- Report your honest `confidence`. If the image is blurry, glare-washed, folded \
or cut off, say so in `notes` and lower the confidence -- a flagged receipt \
gets a human review, a falsely confident one silently corrupts the books.\
"""


class ClaudeVisionExtractor(Extractor):
    name = "claude"

    def __init__(
        self,
        api_key: str,
        model: str = "claude-opus-5",
        effort: str = "medium",
        base_url: str = "",
        timeout: float = 180.0,
    ) -> None:
        self.api_key = (api_key or "").strip()
        self.model = model or "claude-opus-5"
        self.effort = effort or "medium"
        self.base_url = (base_url or "").strip()
        self.timeout = timeout

    def available(self) -> tuple[bool, str]:
        if not self.api_key:
            return False, "No Anthropic API key configured (Settings -> API key)."
        return True, f"Claude vision via {self.model}"

    def _client(self) -> anthropic.Anthropic:
        kwargs: dict[str, object] = {"api_key": self.api_key, "timeout": self.timeout}
        if self.base_url:
            kwargs["base_url"] = self.base_url
        return anthropic.Anthropic(**kwargs)

    def extract(self, image_path: Path, categories: list[str]) -> ExtractionResult:
        ok, reason = self.available()
        if not ok:
            raise ExtractionError(reason)

        media_type = media_type_for(image_path)
        image_b64 = base64.standard_b64encode(image_path.read_bytes()).decode("ascii")
        allowed = ", ".join(categories) if categories else "Other"

        started = time.monotonic()
        try:
            response = self._client().messages.parse(
                model=self.model,
                max_tokens=16000,
                system=SYSTEM_PROMPT,
                output_config={"effort": self.effort},
                output_format=ExtractedReceipt,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "image",
                                "source": {
                                    "type": "base64",
                                    "media_type": media_type,
                                    "data": image_b64,
                                },
                            },
                            {
                                "type": "text",
                                "text": (
                                    "Read this receipt and return it as structured data.\n\n"
                                    f"Allowed categories (use these names exactly): {allowed}"
                                ),
                            },
                        ],
                    }
                ],
            )
        except anthropic.AuthenticationError as exc:
            raise ExtractionError(
                "Anthropic rejected the API key. Check it in Settings."
            ) from exc
        except anthropic.PermissionDeniedError as exc:
            raise ExtractionError(
                "The API key lacks permission for this model. Try a different model."
            ) from exc
        except anthropic.NotFoundError as exc:
            raise ExtractionError(
                f"Model '{self.model}' was not found. Check the model id in Settings."
            ) from exc
        except anthropic.RateLimitError as exc:
            raise ExtractionError(
                "Rate limited by the Anthropic API. Wait a moment and re-scan."
            ) from exc
        except anthropic.APIConnectionError as exc:
            raise ExtractionError(
                "Could not reach the Anthropic API. Check the network connection."
            ) from exc
        except anthropic.APIStatusError as exc:
            raise ExtractionError(f"Anthropic API error {exc.status_code}: {exc.message}") from exc

        elapsed_ms = int((time.monotonic() - started) * 1000)

        if response.stop_reason == "refusal":
            detail = getattr(response.stop_details, "explanation", None) or ""
            raise ExtractionError(
                "The model declined to read this image. " + detail
            )
        if response.stop_reason == "max_tokens":
            raise ExtractionError(
                "The reading was cut off before it finished (max_tokens). "
                "This usually means an unusually long receipt; try cropping it."
            )

        receipt = response.parsed_output
        if receipt is None:
            raise ExtractionError("The model returned no structured output.")

        usage = response.usage
        input_tokens = getattr(usage, "input_tokens", None)
        output_tokens = getattr(usage, "output_tokens", None)

        return ExtractionResult(
            receipt=receipt,
            engine=self.name,
            model=self.model,
            raw_response=json.dumps(receipt.model_dump(), indent=2),
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=estimate_cost(self.model, input_tokens, output_tokens),
            elapsed_ms=elapsed_ms,
        )


def estimate_cost(model: str, input_tokens: int | None, output_tokens: int | None) -> float | None:
    price = PRICING.get(model)
    if price is None or input_tokens is None or output_tokens is None:
        return None
    in_rate, out_rate = price
    return round(input_tokens / 1e6 * in_rate + output_tokens / 1e6 * out_rate, 6)
