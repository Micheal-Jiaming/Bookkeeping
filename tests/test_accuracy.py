"""Tests for the accuracy harness itself.

A measurement tool that is wrong and a measurement tool that is right look
exactly the same from outside -- both print a number. This project has already
paid for that lesson twice, so the harness gets tested like anything else, and
the tests below are mostly about the ways a scorer can flatter the code it is
scoring.

Everything here is pure arithmetic over a constructed reading: no OCR, no
photographs, no Windows. The one test that needs a real image is marked and
skips when `pictures/` is empty, which is the normal state of a fresh clone.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.extract.base import ExtractedItem, ExtractedReceipt  # noqa: E402
from tools.accuracy import (  # noqa: E402
    Truth, load_truth, match_lines, normalise_name, score_reading,
)

import test_real_receipt as walmart1  # noqa: E402


def reading(items, **header) -> ExtractedReceipt:
    """A receipt carrying only what a test cares about."""
    return ExtractedReceipt(
        items=[ExtractedItem(description=name, amount=amount)
               for name, amount in items],
        **header,
    )


# ------------------------------------------------------------ line matching


def test_a_line_the_reader_missed_counts_as_missed():
    matched, missed, invented, _names = match_lines(
        [("BREAD", 147)], [("BREAD", 147), ("MILK", 399)])
    assert (matched, missed, invented) == (1, 1, 0)


def test_a_line_the_reader_invented_counts_as_invented():
    """The metric this harness exists for.

    Scored on the money gap alone an invented line looks like an improvement,
    because it closes the difference between the item sum and the subtotal.
    Scored here it has to show up as a fabrication.
    """
    matched, missed, invented, _names = match_lines(
        [("BREAD", 147), ("GHOST", 500)], [("BREAD", 147)])
    assert (matched, missed, invented) == (1, 0, 1)


def test_repeated_amounts_are_counted_with_multiplicity():
    """Two 1.47 bread lines need two reads, not one.

    Set-wise comparison would score a reader that found one of them as having
    found both, which is exactly the receipt this project already has.
    """
    matched, missed, _invented, _names = match_lines(
        [("FRENCH BREAD", 147)], [("FRENCH BREAD", 147), ("FRENCH BREAD", 147)])
    assert (matched, missed) == (1, 1)


def test_a_right_amount_under_a_wrong_name_still_matches_the_line():
    """Amount pairs the lines; the name is scored separately, not used to pair.

    OCR mangles descriptions far more often than amounts, so pairing on names
    would report lines that were read correctly as lines that were missed.
    """
    matched, _missed, _invented, names_exact = match_lines(
        [("FRENCM 8READ", 147)], [("FRENCH BREAD", 147)])
    assert matched == 1
    assert names_exact == 0


def test_names_are_compared_case_and_space_insensitively():
    assert normalise_name("  gv   twist  mop ") == normalise_name("GV TWIST MOP")


# ------------------------------------------------------------- self-checks


def test_unaccounted_money_is_the_subtotal_less_what_was_read():
    score = score_reading(
        reading([("BREAD", "1.47")], subtotal="10.00"), truth=None)
    assert score.unaccounted_cents == 853


def test_unaccounted_is_none_when_the_subtotal_was_not_read():
    """Absent, not zero. A missing subtotal means the check could not run, and
    reporting 0.00 would read as a perfect reconciliation."""
    score = score_reading(reading([("BREAD", "1.47")]), truth=None)
    assert score.unaccounted_cents is None


def test_arithmetic_check_catches_a_total_that_does_not_add_up():
    ok = score_reading(reading([], subtotal="10.00", tax="0.50", total="10.50"), None)
    bad = score_reading(reading([], subtotal="10.00", tax="0.50", total="99.99"), None)
    assert ok.arithmetic_ok is True
    assert bad.arithmetic_ok is False


def test_items_without_a_readable_amount_are_not_counted_as_items():
    score = score_reading(reading([("BREAD", "1.47"), ("MYSTERY", None)]), None)
    assert score.items_read == 1


# ----------------------------------------------------------- header truth


def test_a_field_verified_absent_is_scored_and_a_missing_field_is_not():
    """The distinction the truth file is shaped around.

    Walmart1 has its top out of frame, so `merchant: null` means the reader is
    *wrong* to produce one. A field nobody has checked is simply not scored --
    if both cases were spelled the same way, an unchecked field would silently
    demand that the reader return nothing.
    """
    verified_absent = Truth(photo="p.jpg", header={"merchant": None})
    unchecked = Truth(photo="p.jpg", header={})

    invented = reading([], merchant="Walmart")
    assert score_reading(invented, verified_absent).header_correct == 0
    assert score_reading(invented, verified_absent).header_checked == 1
    assert score_reading(invented, unchecked).header_checked == 0


def test_money_fields_are_compared_as_money_not_as_text():
    truth = Truth(photo="p.jpg", header={"tax": "7.50"})
    assert score_reading(reading([], tax="7.5"), truth).header_correct == 1


# ------------------------------------------------- the committed truth file


def test_the_truth_file_loads_and_walmart1_is_human_verified():
    truths = load_truth()
    assert "Walmart1.jpg" in truths
    assert truths["Walmart1.jpg"].verified_by == "human"
    assert truths["Walmart1.jpg"].has_lines


def test_the_truth_file_agrees_with_the_transcription_it_came_from():
    """Guards against the two copies of the Walmart1 transcription drifting.

    The lines were seeded from tests/test_real_receipt.py, and nothing stops
    somebody editing one and not the other. If this fails, decide which is
    right by re-reading the photograph -- do not simply copy one over the other.
    """
    truth = load_truth()["Walmart1.jpg"]
    from app.money import to_cents

    expected = [(name, to_cents(amount))
                for name, _sku, amount, _flag, _readable, _cat in walmart1.LINES]
    assert list(truth.lines) == expected
    assert truth.header["subtotal"] == walmart1.SUBTOTAL
    assert truth.header["total"] == walmart1.TOTAL


def test_every_truth_record_names_the_photograph_it_describes():
    """A truth record without a hash could silently be scored against a
    different image after a re-photograph -- which has already happened once in
    this project, when ALDI1 was retaken as ALDI1_new."""
    for name, truth in load_truth().items():
        assert truth.sha256, f"{name} has no photograph hash"
        assert len(truth.sha256) == 64


# --------------------------------------------------- regression comparison


def test_a_fabricated_improvement_is_reported_as_a_regression(tmp_path):
    """The end-to-end version of this harness's reason for existing.

    The change being simulated is the one that was actually proposed and
    rejected twice: invent lines until the money reconciles. Unaccounted money
    falls from 5.00 to 0.00, which on that metric alone is a total success. The
    comparison has to reject it anyway.
    """
    from tools.accuracy import Score
    from tools.measure_accuracy import compare

    baseline = tmp_path / "baseline.json"
    baseline.write_text(json.dumps({"receipts": {"r.jpg": {
        "lines_matched": 10, "lines_invented": 0, "names_exact": 8,
        "header_correct": 5, "header_present": 5, "items_read": 10,
        "unaccounted_cents": 500,
    }}}), encoding="utf-8")

    fabricated = Score(photo="r.jpg", items_read=12, unaccounted_cents=0,
                       lines_matched=10, lines_invented=2, names_exact=8,
                       header_correct=5, header_present=5)
    problems = compare([fabricated], baseline)

    assert any("invented" in line for line in problems), problems


def test_a_genuine_improvement_is_not_reported_as_a_regression(tmp_path):
    from tools.accuracy import Score
    from tools.measure_accuracy import compare

    baseline = tmp_path / "baseline.json"
    baseline.write_text(json.dumps({"receipts": {"r.jpg": {
        "lines_matched": 10, "lines_invented": 0, "names_exact": 8,
        "header_correct": 5, "header_present": 5, "items_read": 10,
        "unaccounted_cents": 500,
    }}}), encoding="utf-8")

    better = Score(photo="r.jpg", items_read=11, unaccounted_cents=0,
                   lines_matched=11, lines_invented=0, names_exact=9,
                   header_correct=5, header_present=5)
    assert compare([better], baseline) == []


def test_a_new_receipt_is_reported_but_is_not_a_failure_of_the_reader(tmp_path):
    from tools.accuracy import Score
    from tools.measure_accuracy import compare

    baseline = tmp_path / "baseline.json"
    baseline.write_text(json.dumps({"receipts": {}}), encoding="utf-8")
    problems = compare([Score(photo="new.jpg")], baseline)
    assert problems and "not in the baseline" in problems[0]


# ------------------------------------------- which engine produced the numbers


def test_a_baseline_from_another_engine_is_refused_rather_than_compared(tmp_path):
    """The reason this harness grew an engine flag at all.

    RapidOCR matches ten more lines per corpus than Windows OCR does. Scored
    against a Windows baseline every one of those looks like an improvement the
    code earned, and in the other direction a slim build looks catastrophically
    broken. Both readings are arithmetic about nothing, so the comparison has to
    decline to make it.
    """
    from tools.accuracy import Score
    from tools.measure_accuracy import compare

    baseline = tmp_path / "baseline.json"
    baseline.write_text(json.dumps({"engine": "windows", "receipts": {
        "r.jpg": {"lines_matched": 63, "lines_invented": 3},
    }}), encoding="utf-8")

    problems = compare([Score(photo="r.jpg", lines_matched=73)], baseline, "rapid")
    assert len(problems) == 1, problems
    assert "not comparable" in problems[0]
    # And specifically not a per-metric report, which would read as a verdict.
    assert "lines matched" not in problems[0]


def test_a_baseline_with_no_engine_recorded_is_taken_as_the_windows_one():
    """Every baseline written before 1.17.0 predates the flag, and Windows OCR
    is the only engine that could have produced one."""
    from tools.accuracy import Score
    from tools.measure_accuracy import compare
    import json as _json
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        baseline = Path(tmp) / "baseline.json"
        baseline.write_text(_json.dumps({"receipts": {"r.jpg": {"lines_matched": 5}}}),
                            encoding="utf-8")
        assert compare([Score(photo="r.jpg", lines_matched=5)], baseline, "windows") == []
        assert compare([Score(photo="r.jpg", lines_matched=5)], baseline, "rapid")


def test_each_engine_gets_its_own_baseline_file():
    from tools.accuracy import baseline_path_for

    assert baseline_path_for("rapid").name == "accuracy_baseline.rapid.json"
    assert baseline_path_for("windows") != baseline_path_for("rapid")


def test_auto_never_resolves_to_claude():
    """It costs money, needs a key, and -- the reason that actually decides it --
    its reading is not deterministic, so it cannot back a regression gate."""
    from tools.measure_accuracy import MEASURABLE, resolve_engine

    assert "claude" not in MEASURABLE
    assert resolve_engine("auto").name in MEASURABLE


def test_a_misspelled_engine_is_refused_rather_than_falling_back():
    """`build_engines` answers an unknown name with the whole fallback list,
    whose first entry is Claude. Taking it would turn a typo into a paid,
    non-deterministic run of the very engine `auto` excludes."""
    from tools.measure_accuracy import resolve_engine

    with pytest.raises(SystemExit) as raised:
        resolve_engine("rapidocr")
    assert "no engine called" in str(raised.value)


def test_the_harness_measures_the_engine_the_application_would_use():
    """The defect this replaced: the harness read Windows OCR unconditionally,
    so `--check` guarded an engine the full build had stopped using."""
    from app.extract import build_engines
    from tools.measure_accuracy import resolve_engine

    would_use = [e.name for e in build_engines({"engine": "auto"})
                 if e.name != "claude" and e.available()[0]]
    assert resolve_engine("auto").name == would_use[0]


# ------------------------------------------------------------- integration


@pytest.mark.skipif(
    not list((Path(__file__).resolve().parent.parent / "pictures").glob("*.jpg")),
    reason="no photographs on this machine; pictures/ is gitignored")
def test_the_harness_runs_against_the_real_photographs():
    """The whole path, on real images. Skipped wherever the photographs are not.

    Deliberately asserts almost nothing about quality -- that is what the
    baseline is for. It asserts only that the harness runs, scores every
    photograph it finds, and never reports a photograph as changed, which would
    mean the committed hashes have gone stale.
    """
    from tools.measure_accuracy import measure

    scores = measure()
    assert scores, "photographs are present but nothing was scored"
    for score in scores:
        assert not any("PHOTO CHANGED" in note for note in score.notes), score.photo


# --------- exporting confirmed receipts as truth (1.15.0)

def _books_with(tmp_path, rows):
    """A throwaway database holding the receipts described by ``rows``."""
    import sqlite3
    from app import db as appdb
    data = tmp_path / "data"
    (data / "images").mkdir(parents=True)
    appdb.DATA_DIR, appdb.IMAGE_DIR = data, data / "images"
    appdb.DB_PATH = data / "bookkeeping.db"
    appdb.init_db()
    with appdb.connect() as conn:
        for name, status, header, items in rows:
            cur = conn.execute(
                "INSERT INTO receipt (status, original_name, merchant, purchased_at,"
                " subtotal_cents, tax_cents, total_cents, currency, created_at,"
                " updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, 'USD', '', '')",
                (status, name, *header))
            for index, (description, cents) in enumerate(items):
                conn.execute(
                    "INSERT INTO line_item (receipt_id, line_no, description,"
                    " amount_cents) VALUES (?, ?, ?, ?)",
                    (cur.lastrowid, index, description, cents))
    return appdb.DB_PATH


def _photo(tmp_path, name: str) -> Path:
    photos = tmp_path / "pictures"
    photos.mkdir(exist_ok=True)
    (photos / name).write_bytes(b"not really a jpeg, but it hashes")
    return photos


def test_a_confirmed_receipt_becomes_a_truth_record(tmp_path):
    """The point of the whole thing: hand-verification stops being disposable."""
    from tools.export_truth import collect
    db_path = _books_with(tmp_path, [
        ("SHOP1.jpg", "confirmed", ("Shop", "2026-09-06", 1000, 50, 1050),
         [("MILK", 400), ("BREAD", 600)]),
    ])
    entries, _notes = collect(db_path, _photo(tmp_path, "SHOP1.jpg"))
    assert list(entries) == ["SHOP1.jpg"]
    entry = entries["SHOP1.jpg"]
    assert entry["header"] == {"merchant": "Shop", "purchased_at": "2026-09-06",
                               "subtotal": "10.00", "tax": "0.50", "total": "10.50"}
    assert entry["lines"] == [["MILK", "4.00"], ["BREAD", "6.00"]]
    assert entry["verified_by"] == "confirmed-in-app"
    assert entry["sha256"], "the record has to be bound to the image it describes"


def test_a_receipt_still_under_review_is_not_truth(tmp_path):
    """Only the reviewer's sign-off makes a reading evidence."""
    from tools.export_truth import collect
    db_path = _books_with(tmp_path, [
        ("SHOP1.jpg", "needs_review", ("Shop", "2026-09-06", 1000, 50, 1050),
         [("MILK", 1000)]),
    ])
    entries, _notes = collect(db_path, _photo(tmp_path, "SHOP1.jpg"))
    assert entries == {}


def test_truth_about_a_photograph_nobody_has_is_reported_not_written(tmp_path):
    from tools.export_truth import collect
    db_path = _books_with(tmp_path, [
        ("GONE.jpg", "confirmed", ("Shop", "2026-09-06", 1000, 50, 1050),
         [("MILK", 1000)]),
    ])
    entries, notes = collect(db_path, _photo(tmp_path, "OTHER.jpg"))
    assert entries == {}
    assert any("GONE.jpg" in note for note in notes)


def test_a_transcription_from_the_paper_outranks_what_the_books_hold(tmp_path):
    """Walmart1's header is out of frame, so the paper has no merchant and no
    date -- but the books hold both, because the reviewer typed them. The
    tracked record says *verified absent* and is right; the export must not
    quietly overwrite it, and the disagreement is worth printing."""
    from tools.export_truth import collect
    db_path = _books_with(tmp_path, [
        ("Walmart1.jpg", "confirmed", ("Walmart", "2026-08-18", 14194, 750, 14944),
         [("BEDINABAG", 2972)]),
    ])
    entries, notes = collect(db_path, _photo(tmp_path, "Walmart1.jpg"))
    assert "Walmart1.jpg" not in entries
    assert any("merchant" in note for note in notes)


def test_a_placeholder_is_filled_in_rather_than_deferred_to(tmp_path, monkeypatch):
    """Most tracked entries name a photograph and assert nothing about it. Those
    are not evidence, and treating them as coverage silently exported nothing
    for five of the six receipts on the first attempt at this."""
    from tools import export_truth
    from tools.accuracy import Truth
    monkeypatch.setattr(export_truth, "load_truth",
                        lambda *a, **k: {"SHOP1.jpg": Truth(photo="SHOP1.jpg")})
    db_path = _books_with(tmp_path, [
        ("SHOP1.jpg", "confirmed", ("Shop", "2026-09-06", 1000, 50, 1050),
         [("MILK", 1000)]),
    ])
    entries, _notes = export_truth.collect(db_path, _photo(tmp_path, "SHOP1.jpg"))
    assert "SHOP1.jpg" in entries


def test_the_local_file_never_overrides_a_real_tracked_record(tmp_path):
    from tools.accuracy import load_truth
    tracked = tmp_path / "tracked.json"
    local = tmp_path / "local.json"
    tracked.write_text(json.dumps({"receipts": {
        "A.jpg": {"header": {"merchant": None}, "lines": [["X", "1.00"]]},
        "B.jpg": {},
    }}), encoding="utf-8")
    local.write_text(json.dumps({"receipts": {
        "A.jpg": {"header": {"merchant": "Walmart"}, "lines": [["Y", "2.00"]]},
        "B.jpg": {"header": {"merchant": "Aldi"}, "lines": [["Z", "3.00"]]},
        "C.jpg": {"header": {"merchant": "Costco"}, "lines": None},
    }}), encoding="utf-8")

    truths = load_truth(tracked, local)
    assert truths["A.jpg"].header == {"merchant": None}, "a real record wins"
    assert truths["B.jpg"].header == {"merchant": "Aldi"}, "a placeholder is filled"
    assert truths["C.jpg"].header == {"merchant": "Costco"}, "a new photo is added"


def test_the_merchant_is_compared_without_its_capitals():
    """A reviewer types ALDI as the sign prints it; the parser returns Aldi."""
    from tools.accuracy import Truth, score_reading
    receipt = ExtractedReceipt(currency="USD", merchant="Aldi")
    score = score_reading(receipt, Truth(photo="x.jpg", header={"merchant": "ALDI"}))
    assert score.header_correct == 1
