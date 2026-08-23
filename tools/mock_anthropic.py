"""A stand-in for the Anthropic Messages API, for testing without a key.

Why this exists: the app's whole point is the vision call, and there is no way to
exercise it end to end -- least of all from inside the built .exe -- without
either a paid key or something that answers like the API. This serves a canned
``ExtractedReceipt`` reply to whatever asks, and prints what it was sent, so you
can confirm the request really carried the image, the schema and the effort.

    py tools\\mock_anthropic.py                       # canned Walmart reading
    py tools\\mock_anthropic.py --port 8899
    py tools\\mock_anthropic.py --reading my.json     # your own reading

Then in the app's Settings: any non-empty API key, and Base URL
``http://127.0.0.1:8899``. Or from the tests, point ``ClaudeVisionExtractor``
at it directly.

The reply is a real Messages API envelope, so the SDK's ``messages.parse``
validates the body against the generated JSON schema exactly as it would in
production. What this cannot tell you is whether the *model* reads a receipt
correctly -- only whether this application handles a reply correctly.
"""

from __future__ import annotations

import argparse
import json
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

# The real 24-line Walmart receipt from tests/test_real_receipt.py, which is the
# most demanding reading available: opaque abbreviations, repeated lines,
# bottle deposits, and a header that is genuinely missing from the photo.
DEFAULT_READING = {
    "merchant": None, "merchant_raw": None, "purchased_at": None,
    "currency": "USD", "subtotal": "141.94", "tax": "7.50", "tip": None,
    "total": "149.44", "payment_method": "CASH", "category": None,
    "confidence": 0.88,
    "notes": "The top of the receipt is not in frame: the store name, address, "
             "date and time are missing. The items and totals are legible.",
    "items": [
        {"description": name, "readable_name": readable, "sku": sku,
         "quantity": None, "unit_price": None, "amount": amount,
         "is_discount": False, "taxable": taxable, "category": category}
        for name, sku, amount, taxable, readable, category in [
            ("BEDINABAG", "840021403470", "29.72", True, "Bed-in-a-Bag bedding set", "Household"),
            ("GV 1G SP", "078742356220", "1.37", True, "Great Value 1 gallon spring water", "Groceries"),
            ("ME DEPOSIT", "000787423909", "0.05", False, "Maine bottle deposit", "Fees & Taxes"),
            ("GV 1G SP", "078742356220", "1.37", True, "Great Value 1 gallon spring water", "Groceries"),
            ("ME DEPOSIT", "000787423909", "0.05", False, "Maine bottle deposit", "Fees & Taxes"),
            ("GV TWIST MOP", "078742352910", "10.88", True, "Great Value twist mop", "Household"),
            ("COKE", "049000050110", "3.04", True, "Coca-Cola", "Groceries"),
            ("ME DEPOSIT", "000787423909", "0.05", False, "Maine bottle deposit", "Fees & Taxes"),
            ("HANGERS", "802404007800", "2.98", True, "Clothes hangers", "Household"),
            ("HS SH CLS8.5", "037000949590", "3.97", True, "Head & Shoulders Classic Clean shampoo 8.5oz", "Personal Care"),
            ("DOVE BW 11OZ", "011111064940", "5.47", True, "Dove body wash 11oz", "Personal Care"),
            ("AIM TP 5.5OZ", "033200000930", "0.98", True, "Aim toothpaste 5.5oz", "Personal Care"),
            ("GV TOASTED O", "194346525620", "2.47", False, "Great Value Toasted Oats cereal", "Groceries"),
            ("DWN EZS 22Z", "030772224910", "3.83", True, "Dawn EZ-Squeeze dish soap 22oz", "Household"),
            ("CLX PLNGR", "070982051940", "17.76", True, "Clorox toilet plunger", "Household"),
            ("GV AMMONIA", "078742276610", "2.94", True, "Great Value ammonia cleaner", "Household"),
            ("FRENCH BREAD", "200989000000", "1.47", False, "French bread loaf", "Groceries"),
            ("FRENCH BREAD", "200989000000", "1.47", False, "French bread loaf", "Groceries"),
            ("PROTEINSUPPL", "660726503370", "23.18", True, "Protein supplement", "Health & Pharmacy"),
            ("GV HD SPGE 4", "078742364220", "2.18", True, "Great Value heavy duty sponges, 4 pack", "Household"),
            ("TIDE PODS 57", "030772259580", "17.94", True, "Tide Pods laundry detergent, 57 count", "Household"),
            ("ST BUCKET", "073149120830", "2.86", True, "Storage bucket", "Household"),
            ("GAIN", "037000976140", "0.97", True, "Gain laundry detergent", "Household"),
            ("EQJELLUBE8OZ", "194346600820", "4.94", True, "Equate lubricating jelly 8oz", "Health & Pharmacy"),
        ]
    ],
}


def make_handler(reading: dict, quiet: bool):
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            length = int(self.headers.get("Content-Length", "0"))
            request = json.loads(self.rfile.read(length) or b"{}")
            if not quiet:
                blocks = request.get("messages", [{}])[0].get("content", [])
                image = next((b for b in blocks if isinstance(b, dict)
                              and b.get("type") == "image"), None)
                config = request.get("output_config", {})
                print(f"{self.path}  model={request.get('model')} "
                      f"effort={config.get('effort')} "
                      f"schema={'yes' if 'format' in config else 'NO'} "
                      f"image={len(image['source']['data']) if image else 0} b64 bytes",
                      flush=True)
            body = json.dumps({
                "id": "msg_mock", "type": "message", "role": "assistant",
                "model": request.get("model", "claude-opus-5"),
                "content": [{"type": "text", "text": json.dumps(reading)}],
                "stop_reason": "end_turn", "stop_sequence": None,
                "usage": {"input_tokens": 2208, "output_tokens": 1487},
            }).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            return

    return Handler


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--port", type=int, default=8899)
    parser.add_argument("--reading", type=Path,
                        help="JSON file holding an ExtractedReceipt to serve.")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()

    reading = DEFAULT_READING
    if args.reading:
        reading = json.loads(args.reading.read_text(encoding="utf-8"))

    server = HTTPServer(("127.0.0.1", args.port), make_handler(reading, args.quiet))
    print(f"Stand-in Anthropic API on http://127.0.0.1:{args.port} "
          f"({len(reading['items'])} items, total {reading['total']}). Ctrl+C to stop.",
          flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("stopped")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
