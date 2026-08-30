"""Tests for the barcode arithmetic and the online product-name lookup.

No test here touches the network. ``_get`` -- the single function that performs
an HTTP request -- is replaced with a scripted answer, which is enough to
exercise every parser, the fallback between sources, the rate-limit path and the
cache. The one thing that cannot be covered this way is whether Open Food Facts
and UPCitemdb still return the shapes assumed here; that was measured by hand
against the real services and is recorded in Bookkeeping.md section 9.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.lookup import names_for_skus  # noqa: E402
from app.lookup import product_names as pn  # noqa: E402
from app.lookup.upc import barcode_for, check_digit, is_valid  # noqa: E402

# Printed on the receipt, and the barcode it actually stands for. Walmart prints
# the first eleven digits and pads column twelve with a zero, so all but the
# Coca-Cola line are invalid UPC-A codes as printed.
WALMART = [
    ("840021403470", "840021403479"),   # BEDINABAG
    ("078742356220", "078742356228"),   # GV 1G SP
    ("078742352910", "078742352916"),   # GV TWIST MOP
    ("194346525620", "194346525621"),   # GV TOASTED O
    ("070982051940", "070982051949"),   # CLX PLNGR
    ("078742276610", "078742276618"),   # GV AMMONIA
    ("078742364220", "078742364223"),   # GV HD SPGE 4
    ("049000050110", "049000050110"),   # COKE -- already valid, must not change
]


def test_check_digit_matches_known_barcodes():
    assert check_digit("04900005011") == "0"
    assert check_digit("07874235622") == "8"
    assert is_valid("049000050110")
    assert not is_valid("049000050111")


@pytest.mark.parametrize("printed, expected", WALMART,
                         ids=[p for p, _ in WALMART])
def test_the_printed_number_is_repaired_into_a_real_barcode(printed, expected):
    assert barcode_for(printed) == expected


def test_a_valid_code_is_never_rewritten():
    """A chain that prints the true twelve digits must survive untouched."""
    assert is_valid("049000050110")
    assert barcode_for("049000050110") == "049000050110"


@pytest.mark.parametrize("code", ["200989000000", "412345678901",
                                  "512345678901", "912345678901"])
def test_locally_assigned_codes_are_not_worth_asking_about(code):
    """Number systems 2, 4, 5 and 9 are assigned by the shop, not by GS1."""
    assert barcode_for(code) is None


@pytest.mark.parametrize("code", [None, "", "12345", "abcdefghijkl",
                                  "0787423562201"])
def test_unusable_numbers_are_rejected(code):
    assert barcode_for(code) is None


def test_a_receipt_line_with_no_sku_is_skipped():
    assert names_for_skus([None, ""]) == {}


# --- codes from the two receipts of 2026-08-29 -----------------------------

@pytest.mark.parametrize("printed, expected", [
    ("036000554800", "036000554809"),   # COTT CLN 12M
    ("756809105660", "756809105667"),   # an item with no printed name
    ("850043215670", "850043215677"),   # HARDWHOOKS
    ("631656716810", "631656716818"),   # SS CREA FP
    ("194346193890", "194346193899"),   # BG ALM UNVAN
    ("078742089640", "078742089645"),   # GV CC IC
    ("078742002830", "078742002835"),   # GVCORNSTARCH
])
def test_the_second_receipts_codes_are_repaired_too(printed, expected):
    assert barcode_for(printed) == expected


def test_the_true_upc_is_recovered_from_an_unnamed_item():
    """A receipt printed both forms of one barcode, which confirms the rule.

    The line read "756809105667 756809105660": the true UPC, whose check digit
    really is 7, beside the same code with that digit replaced by a zero. The
    repair turns the second into the first.
    """
    assert is_valid("756809105667")
    assert not is_valid("756809105660")
    assert barcode_for("756809105660") == "756809105667"


@pytest.mark.parametrize("code, why", [
    ("000787423909", "a Maine bottle deposit, not a product"),
    ("000000004612", "a produce PLU padded out to twelve columns"),
])
def test_a_code_that_is_not_zero_padded_is_left_alone(code, why):
    """Rebuilding these would be a guess, and a lucky guess is the bad case.

    The trailing zero is the evidence that a code was truncated. Without it
    there is nothing to say the number is a barcode at all, and a rebuilt code
    that happens to exist would put somebody else's product on the line.
    """
    assert not is_valid(code)
    assert barcode_for(code) is None, why


def test_a_plu_code_is_never_sent_to_a_barcode_database():
    """No GS1 company prefix starts with six zeros, so this cannot resolve."""
    assert barcode_for("000000004011") is None      # bananas
    assert barcode_for("000000004612") is None      # ginger root


# --- the two sources -------------------------------------------------------

def _scripted(monkeypatch, answers: dict[str, tuple[int | None, str]]):
    """Replace the HTTP layer with a table of url-substring -> (status, body)."""
    calls: list[str] = []

    def fake_get(url: str):
        calls.append(url)
        for needle, answer in answers.items():
            if needle in url:
                return answer
        return 404, ""

    monkeypatch.setattr(pn, "_get", fake_get)
    return calls


def test_open_food_facts_reads_brand_name_and_size(monkeypatch):
    body = json.dumps({"product": {"brands": "Great Value",
                                   "product_name": "Natural spring water",
                                   "quantity": "1 GAL (3.78 L)"}})
    _scripted(monkeypatch, {"openfoodfacts": (200, body)})
    found = pn.open_food_facts("078742356228")
    assert found.name == "Great Value Natural spring water 1 GAL (3.78 L)"
    assert found.source == "openfoodfacts"


def test_open_food_facts_treats_a_404_as_not_a_food(monkeypatch):
    _scripted(monkeypatch, {"openfoodfacts": (404, "")})
    assert pn.open_food_facts("070982051949") is None


def test_open_food_facts_survives_a_broken_body(monkeypatch):
    _scripted(monkeypatch, {"openfoodfacts": (200, "not json at all")})
    assert pn.open_food_facts("078742356228") is None


def test_upcitemdb_reads_the_title(monkeypatch):
    body = json.dumps({"items": [{"title": "Clorox Plunger & Toilet Brush"}]})
    _scripted(monkeypatch, {"upcitemdb": (200, body)})
    found = pn.upcitemdb("070982051949")
    assert found.name == "Clorox Plunger & Toilet Brush"
    assert found.source == "upcitemdb"


def test_upcitemdb_reports_its_daily_quota_running_out(monkeypatch):
    _scripted(monkeypatch, {"upcitemdb": (429, "")})
    with pytest.raises(pn.RateLimited):
        pn.upcitemdb("070982051949")


def test_a_non_food_falls_through_to_the_second_source(monkeypatch):
    body = json.dumps({"items": [{"title": "Clorox Plunger & Toilet Brush"}]})
    calls = _scripted(monkeypatch, {"openfoodfacts": (404, ""),
                                    "upcitemdb": (200, body)})
    found = pn.resolve_one("070982051949")
    assert found.name == "Clorox Plunger & Toilet Brush"
    assert len(calls) == 2, "the food database should be asked first"


def test_a_long_catalogue_entry_is_cut_to_a_readable_length(monkeypatch):
    body = json.dumps({"items": [{"title": "Diet Coke Bottle 2 Liters " + "x" * 200}]})
    _scripted(monkeypatch, {"upcitemdb": (200, body)})
    name = pn.upcitemdb("049000050110").name
    assert len(name) <= pn.MAX_NAME + 3
    assert name.endswith("...")
    assert name.startswith("Diet Coke Bottle 2 Liters")


def test_an_unreachable_service_is_reported_not_treated_as_a_miss(monkeypatch):
    """"I could not ask" and "the answer is no" must not look the same."""
    _scripted(monkeypatch, {"openfoodfacts": (None, ""), "upcitemdb": (None, "")})
    with pytest.raises(pn.Unavailable):
        pn.resolve_one("078742356228")


def test_an_offline_machine_degrades_to_the_printed_name(monkeypatch):
    """A whole batch with no network yields nothing, and raises nothing."""
    _scripted(monkeypatch, {"openfoodfacts": (None, ""), "upcitemdb": (None, "")})
    assert pn.resolve_many(["078742356228", "070982051949"]) == {}


def test_one_source_running_out_does_not_silence_the_other(monkeypatch):
    """The bug this guards cost a whole receipt's groceries their names.

    With UPCitemdb's daily allowance spent, every barcode draws a 429 from it.
    That must set UPCitemdb aside for the rest of the batch, not end the batch
    -- Open Food Facts is still answering, and it is the one that knows food.
    """
    food = json.dumps({"product": {"product_name": "Toasted O's"}})
    seen: list[str] = []

    def fake_get(url):
        seen.append(url)
        if "upcitemdb" in url:
            return 429, ""
        return (200, food) if "194346525621" in url else (404, "")

    monkeypatch.setattr(pn, "_get", fake_get)
    out = pn.resolve_many(["070982051949", "194346525621", "078742352916"])

    assert out["194346525621"].name == "Toasted O's", "the food source still works"
    assert len([u for u in seen if "upcitemdb" in u]) == 1, \
        "a spent source should be asked once, not once per barcode"


def test_one_barcode_failing_does_not_abandon_the_rest(monkeypatch):
    """A 503 on one line must not cost the other nineteen their names."""
    body = json.dumps({"product": {"product_name": "Toasted O's"}})

    def fake_get(url):
        if "194346525621" in url:
            return 200, body
        return 503, ""

    monkeypatch.setattr(pn, "_get", fake_get)
    out = pn.resolve_many(["070982051949", "194346525621"])
    assert "070982051949" not in out, "an unanswered barcode must not be recorded"
    assert out["194346525621"].name == "Toasted O's"


# --- the cache -------------------------------------------------------------

def test_a_resolved_name_is_cached_and_not_asked_for_twice(books, monkeypatch):
    body = json.dumps({"product": {"brands": "Great Value",
                                   "product_name": "Toasted O's"}})
    calls = _scripted(monkeypatch, {"openfoodfacts": (200, body)})

    first = names_for_skus(["194346525620"])
    assert first == {"194346525620": "Great Value Toasted O's"}
    asked_once = len(calls)

    second = names_for_skus(["194346525620"])
    assert second == first
    assert len(calls) == asked_once, "the second scan should come from the cache"


def test_a_miss_is_cached_too(books, monkeypatch):
    """Otherwise every unresolvable line re-queries on every scan, for ever."""
    calls = _scripted(monkeypatch, {"openfoodfacts": (404, ""),
                                    "upcitemdb": (200, json.dumps({"items": []}))})

    assert names_for_skus(["078742364220"]) == {}
    asked_once = len(calls)
    assert asked_once == 2

    assert names_for_skus(["078742364220"]) == {}
    assert len(calls) == asked_once, "a known miss should not be asked again"


def test_being_rate_limited_is_never_cached_as_a_miss(books, monkeypatch):
    """The bug this guards: a 429 recorded as "unknown" hides a real product.

    Open Food Facts refusing a burst says nothing whatsoever about the barcode.
    Writing that down as a verdict would suppress the name for a month, and the
    cache would look exactly like one holding a genuine miss.
    """
    _scripted(monkeypatch, {"openfoodfacts": (429, ""), "upcitemdb": (429, "")})
    assert names_for_skus(["070982051940"]) == {}

    with books["db"].connect() as db:
        cached = db.execute("SELECT COUNT(*) AS n FROM product_name").fetchone()
    assert cached["n"] == 0, "a refused request must leave no verdict behind"

    body = json.dumps({"items": [{"title": "Clorox Plunger & Toilet Brush"}]})
    _scripted(monkeypatch, {"openfoodfacts": (404, ""), "upcitemdb": (200, body)})
    assert names_for_skus(["070982051940"]) == {
        "070982051940": "Clorox Plunger & Toilet Brush"}


def test_a_stale_miss_is_retried(books, monkeypatch):
    """Catalogues grow, so a miss is worth re-asking after a month."""
    _scripted(monkeypatch, {"openfoodfacts": (404, ""),
                            "upcitemdb": (200, json.dumps({"items": []}))})
    assert names_for_skus(["078742364220"]) == {}

    with books["db"].connect() as db:
        db.execute("UPDATE product_name SET fetched_at = '2020-01-01T00:00:00+00:00'")

    body = json.dumps({"items": [{"title": "Great Value Heavy Duty Sponge, 4-Pack"}]})
    _scripted(monkeypatch, {"openfoodfacts": (404, ""), "upcitemdb": (200, body)})
    assert names_for_skus(["078742364220"]) == {
        "078742364220": "Great Value Heavy Duty Sponge, 4-Pack"}


def test_turning_the_setting_off_still_uses_names_already_known(books, monkeypatch):
    body = json.dumps({"product": {"product_name": "Toasted O's"}})
    calls = _scripted(monkeypatch, {"openfoodfacts": (200, body)})
    assert names_for_skus(["194346525620"]) == {"194346525620": "Toasted O's"}
    before = len(calls)

    # Cached, so it still resolves; uncached, so it stays unknown and silent.
    offline = names_for_skus(["194346525620", "070982051940"], enabled=False)
    assert offline == {"194346525620": "Toasted O's"}
    assert len(calls) == before, "nothing should reach the network"


# --- the pipeline hand-off -------------------------------------------------

def test_expansion_fills_blanks_without_overwriting_the_model(books, monkeypatch):
    from app.extract import ExtractedItem, ExtractedReceipt, ExtractionResult

    body = json.dumps({"items": [{"title": "Clorox Plunger & Toilet Brush"}]})
    _scripted(monkeypatch, {"openfoodfacts": (404, ""), "upcitemdb": (200, body)})

    result = ExtractionResult(
        receipt=ExtractedReceipt(
            currency="USD", total="20.80", confidence=0.9,
            items=[
                ExtractedItem(description="CLX PLNGR", sku="070982051940",
                              amount="17.76"),
                ExtractedItem(description="GV 1G SP", sku="078742356220",
                              amount="1.37",
                              readable_name="Great Value 1 gallon spring water"),
            ]),
        engine="stub", model="stub-1", raw_response="{}",
        input_tokens=0, output_tokens=0, cost_usd=0.0, elapsed_ms=1)

    filled = books["pipeline"]._expand_item_names(result, {"online_lookup": "1"})
    assert filled == 1
    assert result.receipt.items[0].readable_name == "Clorox Plunger & Toilet Brush"
    assert (result.receipt.items[1].readable_name
            == "Great Value 1 gallon spring water"), "the model's own name wins"


def test_a_lookup_failure_never_breaks_a_scan(books, monkeypatch):
    from app.extract import ExtractedItem, ExtractedReceipt, ExtractionResult

    def explode(*_args, **_kwargs):
        raise RuntimeError("the service changed shape")

    monkeypatch.setattr("app.lookup.names_for_skus", explode)
    result = ExtractionResult(
        receipt=ExtractedReceipt(
            currency="USD", total="17.76", confidence=0.9,
            items=[ExtractedItem(description="CLX PLNGR", sku="070982051940",
                                 amount="17.76")]),
        engine="stub", model="stub-1", raw_response="{}",
        input_tokens=0, output_tokens=0, cost_usd=0.0, elapsed_ms=1)

    assert books["pipeline"]._expand_item_names(result, {"online_lookup": "1"}) == 0
    assert result.receipt.items[0].readable_name is None
