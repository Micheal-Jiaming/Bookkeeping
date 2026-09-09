"""The RapidOCR engine, and the things about it that are easy to get wrong.

Most of this needs neither RapidOCR nor a photograph: the conversion from its
polygons to the row grouper's boxes, the confidence cap, and the engine order
are all arithmetic. The one test that does read an image skips itself when the
library is absent, because the slim build legitimately has no RapidOCR at all
and a red test would be reporting the wrong thing.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.extract import build_engines, engine_status  # noqa: E402
from app.money import to_cents  # noqa: E402
from app.extract.rapid_ocr import (  # noqa: E402
    MAX_CONFIDENCE, ROW_TOLERANCE, RapidOcrExtractor, _as_words, _confidence,
    _skew,
)

PHOTO = Path(__file__).resolve().parent.parent / "pictures" / "COSTCO1.jpg"

rapid_installed = pytest.mark.skipif(
    __import__("importlib").util.find_spec("rapidocr") is None,
    reason="RapidOCR is not installed; that is the slim build's normal state")


class _Output:
    """The shape RapidOCR returns: parallel polygons, texts and scores."""

    def __init__(self, boxes, txts, scores=None):
        self.boxes, self.txts, self.scores = boxes, txts, scores


def _box(x, y, w, h):
    return [(x, y), (x + w, y), (x + w, y + h), (x, y + h)]


# --------------------------------------------------- polygons to boxes

def test_a_polygon_becomes_its_bounding_box():
    """The detector returns four corners, not a rectangle: text on a curled
    receipt comes back as a rotated quadrilateral."""
    words = _as_words(_Output([[(10, 20), (110, 24), (110, 54), (10, 50)]], ["MILK"]))
    assert len(words) == 1
    word = words[0]
    assert (word.text, word.x, word.y) == ("MILK", 10, 20)
    assert word.width == 100 and word.height == 34


def test_an_empty_box_is_dropped():
    words = _as_words(_Output([_box(0, 0, 5, 5), _box(0, 10, 50, 20)], ["  ", "MILK"]))
    assert [w.text for w in words] == ["MILK"]


def test_an_output_of_the_wrong_shape_is_not_a_crash():
    """A future version returning something else must degrade, not explode."""
    assert _as_words(_Output(None, None)) == []


# ------------------------------------------------------------ confidence

def test_confidence_is_the_mean_of_the_line_scores():
    """Scores chosen to average below the cap, so this tests the mean and the
    next test tests the cap. They averaged 0.6 until the cap came down to 0.55,
    at which point this was silently measuring the cap instead."""
    assert _confidence(_Output([], [], [0.4, 0.6])) == pytest.approx(0.5)


def test_confidence_never_reaches_auto_confirm():
    """However sure the model sounds, a reading nobody has checked is not a
    confirmed receipt -- the same discipline the Windows engine works under,
    though not the same number: it caps at 0.5 and this at 0.55.

    **Compared against the real threshold, not against the constant.** The
    earlier version asserted the cap equalled ``MAX_CONFIDENCE``, which is true
    of any value whatsoever: it passed while the cap sat at 0.75, above the 0.6
    that raises the low-confidence flag, so a reading with clean arithmetic
    auto-confirmed itself with nobody having looked at it.
    """
    from app.validate import LOW_CONFIDENCE

    assert _confidence(_Output([], [], [1.0, 1.0])) == MAX_CONFIDENCE
    assert MAX_CONFIDENCE < LOW_CONFIDENCE, (
        f"a reading capped at {MAX_CONFIDENCE} clears the {LOW_CONFIDENCE} flag "
        "and can auto-confirm without review")


def test_no_scores_is_not_a_division_by_zero():
    assert 0.0 < _confidence(_Output([], [], None)) <= MAX_CONFIDENCE


# ----------------------------------------------------- how it is wired in

def test_the_row_tolerance_is_the_one_that_was_measured():
    """RapidOCR returns line boxes where Windows OCR returns words, so the
    grouping tolerance differs. 0.34-0.45 all score 72-73 lines matched against
    the confirmed receipts, 0.50 drops to 69: a plateau, not a fitted spike."""
    assert 0.34 <= ROW_TOLERANCE <= 0.45


def test_rapidocr_is_tried_before_the_windows_engine():
    """It is better on every photograph here. It stays below Claude, which
    reads a receipt rather than characters."""
    order = [engine.name for engine in build_engines({"engine": "auto"})]
    assert order.index("rapid") < order.index("windows")
    assert order.index("claude") < order.index("rapid")


def test_it_can_be_chosen_on_its_own():
    assert [e.name for e in build_engines({"engine": "rapid"})] == ["rapid"]


def test_it_appears_in_the_settings_page_status():
    assert "rapid" in {row["name"] for row in engine_status({})}


def test_asking_whether_it_is_available_does_not_load_the_models(monkeypatch):
    """Called on every Settings draw and at every start-up. Building the engine
    to answer took three and a half seconds off each one."""
    import app.extract.rapid_ocr as module
    monkeypatch.setattr(module, "_shared", None)
    monkeypatch.setattr(module.RapidOcrExtractor, "_build",
                        lambda self: pytest.fail("available() must not build"))
    RapidOcrExtractor().available()


def test_a_build_without_rapidocr_says_so_rather_than_failing(monkeypatch):
    """The slim build has no RapidOCR, and that is not an error condition."""
    import builtins

    import app.extract.rapid_ocr as module
    monkeypatch.setattr(module, "_shared", None)
    real_import = builtins.__import__

    def no_rapidocr(name, *args, **kwargs):
        if name == "rapidocr":
            raise ImportError("no rapidocr here")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", no_rapidocr)
    ok, why = RapidOcrExtractor().available()
    assert ok is False
    assert "full build" in why or "pip install" in why


# ------------------------------------------- reading the page's tilt (1.17.0)


def _poly(x, y, width, height, slope=0.0):
    """A detector quadrilateral for one line of text, tilted by ``slope``."""
    return [(x, y), (x + width, y + width * slope),
            (x + width, y + width * slope + height), (x, y + height)]


def test_an_upright_page_measures_as_no_tilt_at_all():
    """The answer that changes nothing, and the common case. Four of the seven
    photographs here measure exactly 0.0."""
    boxes = [_poly(0, 100 * i, 400, 40) for i in range(6)]
    assert _skew(_Output(boxes, ["x"] * 6)) == 0.0


def test_the_tilt_is_the_median_of_the_lines_own_angles():
    boxes = [_poly(0, 100 * i, 400, 40, slope=0.03) for i in range(6)]
    assert _skew(_Output(boxes, ["x"] * 6)) == pytest.approx(0.03, abs=1e-6)


def test_one_wildly_rotated_detection_cannot_move_it():
    """Taken as a median precisely so that a single bad polygon -- a stray mark
    read as text, a logo at an angle -- does not tip the whole page."""
    boxes = [_poly(0, 100 * i, 400, 40, slope=0.03) for i in range(6)]
    boxes.append(_poly(0, 700, 400, 40, slope=0.9))
    assert _skew(_Output(boxes, ["x"] * 7)) == pytest.approx(0.03, abs=1e-6)


def test_a_tilt_too_large_to_believe_is_refused():
    """Past about eight degrees the estimate is likelier to be broken than the
    photograph is to be sideways, and a wrong slope scrambles every row."""
    boxes = [_poly(0, 100 * i, 400, 40, slope=0.4) for i in range(6)]
    assert _skew(_Output(boxes, ["x"] * 6)) == 0.0


def test_a_box_too_short_to_have_an_angle_is_not_asked():
    """A two-character box like "FA" spans a few pixels, so its corner-to-corner
    slope is mostly detector noise. Only boxes wider than twice their height
    are measured -- here the short ones would drag the median to zero."""
    boxes = [_poly(0, 100 * i, 400, 40, slope=0.03) for i in range(4)]
    boxes += [_poly(600, 100 * i, 30, 40, slope=0.0) for i in range(5)]
    assert _skew(_Output(boxes, ["x"] * 9)) == pytest.approx(0.03, abs=1e-6)


def test_an_output_with_no_polygons_is_not_a_crash():
    assert _skew(_Output(None, None)) == 0.0
    assert _skew(_Output([[(0, 0), (1, 1)]], ["x"])) == 0.0


# ------------------------------------------------------- the real thing

@rapid_installed
@pytest.mark.skipif(not PHOTO.exists(), reason="the photographs are gitignored")
def test_it_reads_the_receipt_the_windows_engine_could_not():
    """The headline claim, checked rather than asserted in a comment.

    Windows OCR cannot read the Costco logo at any scale -- the three-pass
    merchant match exists because of it -- and returns ORG SPINfiCH for ORG
    SPINACH. Both are fixed here, and the totals still have to come out right.
    """
    result = RapidOcrExtractor().extract(PHOTO, [])
    receipt = result.receipt
    assert result.engine == "rapid"
    assert receipt.merchant == "Costco"
    assert receipt.subtotal == "188.37"
    assert receipt.tax == "5.15"
    assert receipt.total == "193.52"
    assert receipt.items_sold == 16
    names = [item.description for item in receipt.items]
    assert "DRUMSTICKS" in names
    assert "BEEF STEW" in names, "a line the Windows engine never saw at all"
    # Every printed line, since the baseline tilt was corrected in 1.17.0.
    # ORG SPINACH was the last one missing and was not a recognition failure at
    # all -- it was read, and then grouped into the row above it.
    assert "ORG SPINACH" in names
    assert len(receipt.items) == 16, f"only read {len(receipt.items)}"
    # And they add up, which is the check a reviewer actually cares about.
    total = sum(to_cents(item.amount) or 0 for item in receipt.items)
    assert total == to_cents(receipt.subtotal), f"{total} against {receipt.subtotal}"


def test_onnxruntime_is_loaded_before_anything_touches_winrt():
    """The load-order trap, guarded so it cannot be tidied away.

    Initialising the WinRT bindings first makes onnxruntime's extension module
    fail to load for the rest of the process -- and `engine_status` asks Windows
    OCR whether it is available at every start-up, which is enough to do it. The
    import at the top of `rapid_ocr` is what gets in first, so this checks it
    really is at module scope and really did run.
    """
    import app.extract.rapid_ocr as module
    source = Path(module.__file__).read_text(encoding="utf-8")
    body = source.split("class RapidOcrExtractor")[0]
    assert 'importlib.import_module("onnxruntime")' in body, (
        "the pre-import has been moved, removed, or turned back into a plain "
        "import statement. Moving it breaks RapidOCR whenever the Windows "
        "engine is asked about first; making it a plain import puts onnxruntime "
        "into the slim build and triples its size.")
    assert "onnxruntime" in sys.modules, "the pre-import did not run"


@rapid_installed
@pytest.mark.skipif(not PHOTO.exists(), reason="the photographs are gitignored")
def test_it_still_works_after_the_windows_engine_has_been_built():
    """The exact sequence the application performs at start-up."""
    from app.extract.windows_ocr import WindowsOcrExtractor
    WindowsOcrExtractor().available()
    result = RapidOcrExtractor().extract(PHOTO, [])
    assert result.receipt.merchant == "Costco"
