"""Tests for the Claude vision engine against a local stand-in for the API.

There is no API key in the test environment, and a real call would cost money
and need a network, so these tests point the SDK's ``base_url`` at a throwaway
HTTP server on localhost. That still exercises the parts that can actually be
wrong in this project: the shape of the request (image block, system prompt,
structured-output schema, effort), the parsing of a well-formed reply, and the
translation of API failures into messages a user can act on.

What these tests do NOT prove: that the model reads real receipts accurately.
Nothing offline can prove that -- see the verification notes in Bookkeeping.md.
"""

from __future__ import annotations

import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.extract import ExtractionError  # noqa: E402
from app.extract.claude_vision import ClaudeVisionExtractor, estimate_cost  # noqa: E402

READING = {
    "merchant": "Walmart",
    "merchant_raw": "Walmart Store #100",
    "purchased_at": "2026-07-14",
    "currency": "USD",
    "subtotal": "64.59",
    "tax": "3.87",
    "tip": None,
    "total": "68.46",
    "payment_method": "VISA ****4471",
    "category": "Groceries",
    "confidence": 0.94,
    "notes": None,
    "items": [
        {"description": "GV WHL MILK", "readable_name": "Great Value Whole Milk",
         "sku": "007874203912", "quantity": None, "unit_price": None, "amount": "3.24",
         "is_discount": False, "taxable": True, "category": "Groceries"},
        {"description": "MANAGER COUPON", "readable_name": None, "sku": None,
         "quantity": None, "unit_price": None, "amount": "-2.00",
         "is_discount": True, "taxable": None, "category": "Groceries"},
    ],
}


class _Handler(BaseHTTPRequestHandler):
    status = 200
    body: dict = {}
    captured: list[dict] = []

    def do_POST(self):  # noqa: N802 - name fixed by BaseHTTPRequestHandler
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length)
        type(self).captured.append({
            "path": self.path,
            # Lower-cased: httpx title-cases some header names on the wire
            # ("X-Api-Key"), so a case-sensitive lookup would be flaky.
            "headers": {key.lower(): value for key, value in self.headers.items()},
            "body": json.loads(raw or b"{}"),
        })
        payload = json.dumps(type(self).body).encode()
        self.send_response(type(self).status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *args):  # keep the test output clean
        return


@pytest.fixture()
def fake_api():
    """A one-request Anthropic look-alike. Yields a configurator."""

    class Handler(_Handler):
        captured: list[dict] = []

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    class Fake:
        base_url = f"http://127.0.0.1:{server.server_address[1]}"
        captured = Handler.captured

        @staticmethod
        def respond(body: dict, status: int = 200):
            Handler.body = body
            Handler.status = status

    Fake.respond(message_response(READING))
    try:
        yield Fake
    finally:
        server.shutdown()
        server.server_close()


def message_response(reading: dict, stop_reason: str = "end_turn") -> dict:
    return {
        "id": "msg_test",
        "type": "message",
        "role": "assistant",
        "model": "claude-opus-5",
        "content": [{"type": "text", "text": json.dumps(reading)}],
        "stop_reason": stop_reason,
        "stop_sequence": None,
        "usage": {"input_tokens": 1500, "output_tokens": 600},
    }


def engine(fake_api, **kwargs) -> ClaudeVisionExtractor:
    return ClaudeVisionExtractor(
        api_key="sk-ant-test", base_url=fake_api.base_url, timeout=10.0, **kwargs
    )


# --------------------------------------------------------------------- basics


def test_no_key_means_unavailable_with_an_actionable_reason():
    ok, reason = ClaudeVisionExtractor(api_key="").available()
    assert ok is False
    assert "Settings" in reason


def test_a_configured_key_reports_the_model(fake_api):
    ok, reason = engine(fake_api, model="claude-sonnet-5").available()
    assert ok is True
    assert "claude-sonnet-5" in reason


# ------------------------------------------------------------- request shape


def test_the_request_carries_the_image_the_schema_and_the_effort(
    fake_api, sample_receipt_png
):
    path, _ = sample_receipt_png
    engine(fake_api, effort="medium").extract(path, ["Groceries", "Household"])

    assert len(fake_api.captured) == 1
    request = fake_api.captured[0]
    assert request["path"].endswith("/v1/messages")
    assert request["headers"].get("x-api-key") == "sk-ant-test"

    body = request["body"]
    assert body["model"] == "claude-opus-5"
    assert body["output_config"]["effort"] == "medium"
    # Structured output: the schema the reply is validated against must be sent.
    schema = body["output_config"]["format"]
    assert schema["type"] == "json_schema"
    assert "items" in json.dumps(schema)

    blocks = body["messages"][0]["content"]
    image_block = next(b for b in blocks if b["type"] == "image")
    assert image_block["source"]["media_type"] == "image/png"
    assert len(image_block["source"]["data"]) > 1000, "the image itself must be attached"

    text_block = next(b for b in blocks if b["type"] == "text")
    assert "Groceries, Household" in text_block["text"], "allowed categories are passed"
    assert "Transcribe, do not invent" in body["system"]


def test_the_model_and_effort_settings_are_honoured(fake_api, sample_receipt_png):
    path, _ = sample_receipt_png
    engine(fake_api, model="claude-haiku-4-5", effort="low").extract(path, [])
    body = fake_api.captured[0]["body"]
    assert body["model"] == "claude-haiku-4-5"
    assert body["output_config"]["effort"] == "low"


# ------------------------------------------------------------ reply handling


def test_a_well_formed_reply_becomes_an_extraction_result(fake_api, sample_receipt_png):
    path, _ = sample_receipt_png
    result = engine(fake_api).extract(path, ["Groceries"])

    assert result.engine == "claude"
    assert result.model == "claude-opus-5"
    assert result.receipt.merchant == "Walmart"
    assert result.receipt.purchased_at == "2026-07-14"
    assert result.receipt.total == "68.46"
    assert result.receipt.confidence == 0.94
    assert [item.description for item in result.receipt.items] == [
        "GV WHL MILK", "MANAGER COUPON",
    ]
    assert result.receipt.items[1].is_discount is True
    assert result.input_tokens == 1500
    assert result.output_tokens == 600
    # 1500 in + 600 out on Opus 5 at $5/$25 per MTok.
    assert result.cost_usd == pytest.approx(0.0225)
    assert result.elapsed_ms is not None
    assert json.loads(result.raw_response)["merchant"] == "Walmart"


def test_a_refusal_is_reported_as_such(fake_api, sample_receipt_png):
    body = message_response(READING, stop_reason="refusal")
    body["stop_details"] = {"type": "refusal", "category": "other",
                            "explanation": "declined for safety"}
    fake_api.respond(body)
    path, _ = sample_receipt_png
    with pytest.raises(ExtractionError, match="declined to read"):
        engine(fake_api).extract(path, [])


def test_a_truncated_reply_is_not_silently_accepted(fake_api, sample_receipt_png):
    fake_api.respond(message_response(READING, stop_reason="max_tokens"))
    path, _ = sample_receipt_png
    with pytest.raises(ExtractionError, match="cut off"):
        engine(fake_api).extract(path, [])


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (401, "rejected the API key"),
        (403, "lacks permission"),
        (404, "was not found"),
        (429, "Rate limited"),
        (500, "Anthropic API error 500"),
    ],
)
def test_api_errors_become_messages_a_user_can_act_on(
    fake_api, sample_receipt_png, status, expected
):
    fake_api.respond({"type": "error", "error": {"type": "x", "message": "nope"}}, status=status)
    path, _ = sample_receipt_png
    with pytest.raises(ExtractionError, match=expected):
        # Retries would make a 429/500 test slow; the SDK's own retry policy is
        # not what is under test here.
        eng = engine(fake_api)
        eng.timeout = 5.0
        eng.extract(path, [])


def test_an_unsupported_file_type_is_refused_before_any_api_call(fake_api, tmp_path):
    odd = tmp_path / "receipt.tiff"
    odd.write_bytes(b"not really a tiff")
    with pytest.raises(ExtractionError, match="Unsupported image type"):
        engine(fake_api).extract(odd, [])
    assert fake_api.captured == [], "no request should have been made"


# ---------------------------------------------------------------- cost model


def test_cost_estimates_use_the_published_rates():
    assert estimate_cost("claude-opus-5", 1_000_000, 1_000_000) == pytest.approx(30.0)
    assert estimate_cost("claude-sonnet-5", 1_000_000, 1_000_000) == pytest.approx(18.0)
    assert estimate_cost("claude-haiku-4-5", 1_000_000, 1_000_000) == pytest.approx(6.0)


def test_an_unknown_model_reports_no_cost_rather_than_a_wrong_one():
    assert estimate_cost("some-future-model", 1000, 1000) is None
    assert estimate_cost("claude-opus-5", None, None) is None
