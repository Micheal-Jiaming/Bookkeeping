"""RapidOCR: PaddleOCR's models, on ONNX Runtime, offline and on the CPU.

Windows OCR is what makes this application work with nothing installed, and it
is the weakest part of it. It cannot read the Costco logo at any scale -- the
three-pass merchant match in ``receipt_text`` exists entirely to work around
that -- and it returns ``ORG SPINfiCH`` for ``ORG SPINACH``, which RapidOCR
reads correctly.

RapidOCR is the same PP-OCR models Baidu trains for PaddleOCR, converted to ONNX
and run through ONNX Runtime. Both engines through the same parser, over the
**eight photographs the user has confirmed line by line** (2026-09-09):

    Windows OCR   66 matched   19 missed   3 invented   41 names exact
    RapidOCR      81 matched    4 missed   0 invented   76 names exact

Recomputed with ``py tools\\measure_accuracy.py --engine <name>``, and the same
figures are in ``tests/fixtures/accuracy_baseline.{rapid,windows}.json``. **Say
which corpus a number covers or do not quote it**: this docstring carried two
different scores for Windows OCR at once for a while, one of them measured on
six photographs and one on seven, and neither said so.

**Why it is not simply the default.** It is not free: `onnxruntime`, `numpy`,
`opencv` and 32 MB of model weights take the frozen executable from 30 MB to
125 MB and its start-up from 3.5 to 5.2 seconds. This project's defining
constraint is one file somebody can copy onto a USB stick, so both builds exist
and `build.bat --full` is the one that includes this. See section 7 of
Bookkeeping_record.md.

**The import is deliberately inside the method that needs it**, like the other
two offline engines: the slim build does not bundle `rapidocr`, and importing at
module scope would break the whole application rather than one engine.
"""

from __future__ import annotations

import importlib
import logging
import statistics
import threading
import time
from pathlib import Path

from .base import ExtractedReceipt, ExtractionError, ExtractionResult, Extractor
from .receipt_text import parse_receipt_text
from .windows_ocr import Word, group_rows, rows_to_text

log = logging.getLogger("bookkeeping.extract.rapid")

# **Load order matters, and getting it wrong breaks RapidOCR completely.**
#
# If the WinRT bindings the Windows OCR engine uses are initialised first, then
# importing onnxruntime afterwards fails outright:
#
#     DLL load failed while importing onnxruntime_pybind11_state:
#     the dynamic link library initialisation routine failed
#
# Reproduced directly -- build a WindowsOcrExtractor, then a RapidOcrExtractor,
# and the second raises every time; reverse the two and both work. WinRT
# initialises the thread's COM apartment when it starts, and onnxruntime's
# extension module will not initialise underneath that.
#
# This is not theoretical for the application: ``engine_status`` asks every
# engine whether it is available at start-up, and Windows OCR answering that
# question is enough to poison RapidOCR for the rest of the session.
#
# So onnxruntime is loaded *here*, at module import, and this module is imported
# by ``app.extract`` before anything touches WinRT -- which is lazy, inside
# ``WindowsOcrExtractor._engine``. Do not make this lazy "for consistency" with
# the other engines; the whole point is that it happens first.
#
# **Written as importlib rather than `import onnxruntime`, but do not rely on
# that to keep it out of the slim build -- it does not.** PyInstaller resolves a
# literal module name handed to `importlib.import_module` exactly as it resolves
# an import statement, and its graph still reported `onnxruntime imported by
# app.extract.rapid_ocr`. An earlier version of this comment claimed the string
# form was invisible to the analysis; it is not, and the slim build came out at
# 97 MB proving it. **The `excludes` list in Bookkeeping.spec is what actually
# keeps the slim build slim**, and deleting it on the strength of this paragraph
# would triple the file the whole build exists to keep small.
#
# The importlib form stays for the other half of the reason: it keeps the module
# loadable when the package is genuinely absent, which is the slim build's
# normal state, without a bare import failing at module scope.
#
# Failing is normal, not a fault: the slim build has no onnxruntime to load.
try:
    importlib.import_module("onnxruntime")
except Exception:             # not in this build, or not loadable here
    pass

# RapidOCR returns one box per *text line*; Windows OCR returns one per *word*.
# The row grouper takes its tolerance as a fraction of the median box height, so
# the same number means different things to the two, and the value tuned for
# words over-merges lines: on a slightly skewed Walmart photograph a COKE line
# and the bottle deposit beneath it arrived as one row.
#
# Swept against the confirmed receipts rather than guessed -- on the six-receipt
# corpus of 1.17.0, before the skew correction and the two new chains, so the
# absolute scores below are lower than anything measured today. 0.34 through
# 0.45 all scored 72-73 lines matched, 0.50 dropped to 69, 0.15 collapsed to 43 --
# so this sits on a plateau rather than a spike, which is the only reason a
# fitted constant is worth trusting.
ROW_TOLERANCE = 0.40

# Confidence is reported per line by the recogniser. Averaged into a single
# number for the review pane, and capped **below** ``validate.LOW_CONFIDENCE``
# so that a machine reading can never satisfy `auto_confirm_clean` on its own:
# a reading nobody has checked is not a confirmed receipt, however sure the
# model sounds.
#
# This said exactly that while the value was 0.75 and the threshold 0.6, which
# made it false -- a RapidOCR reading whose arithmetic happened to balance
# raised no flag at all and confirmed itself. Windows OCR was doing the right
# thing at 0.5 next door, which is what made the wrong claim so easy to believe.
# Sits above that 0.5 because this engine really is the more accurate of the
# two, and below 0.6 because the promise above has to be true.
# ``test_confidence_never_reaches_auto_confirm`` now compares against the real
# threshold rather than against this constant, so the two cannot drift apart.
MAX_CONFIDENCE = 0.55

# Building the engine loads three ONNX models and takes about four tenths of a
# second, so it is built once and shared. The first version rebuilt it on every
# call -- including every ``available()`` from the Settings page -- and took the
# test suite from 90 seconds to over 400.
#
# Only the *construction* is serialised. ONNX Runtime is safe to call from
# several threads, and holding the lock across a read would have blocked the
# interface for the second and a half a scan takes, every time the Settings page
# asked whether the engine was there.
_build_lock = threading.Lock()
_shared = None


class RapidOcrExtractor(Extractor):
    name = "rapid"

    def _engine(self):
        """The shared RapidOCR engine, built on first use.

        Raises ``ExtractionError`` with something a user can act on when the
        library is absent -- which is the normal state of the slim build, not a
        fault.
        """
        global _shared
        with _build_lock:
            if _shared is not None:
                return _shared
            _shared = self._build()
            return _shared

    def _build(self):
        try:
            # Loaded by name for the same reason onnxruntime is above: to stay
            # loadable when the package is absent. It is **not** what keeps
            # rapidocr out of the slim build -- PyInstaller resolves the literal
            # string too, and the exclusion in Bookkeeping.spec is what does the
            # work. Measured when that was got wrong: the slim build reached
            # 97 MB, cv2.pyd alone 29 MB of it.
            RapidOCR = importlib.import_module("rapidocr").RapidOCR
        except ImportError as exc:
            raise ExtractionError(
                "RapidOCR is not installed in this build. Use the full build, "
                "or run: pip install rapidocr onnxruntime"
            ) from exc
        try:
            engine = RapidOCR()
        except Exception as exc:  # a broken model file, a missing DLL
            raise ExtractionError(f"RapidOCR could not start: {exc}") from exc
        _quieten()
        log.info("RapidOCR ready")
        return engine

    def available(self) -> tuple[bool, str]:
        """Whether this build has RapidOCR, without loading it to find out.

        **Deliberately only an import check.** The other engines build
        themselves here because doing so is instant; RapidOCR loads three ONNX
        models and takes the better part of a second, and this is called every
        time the Settings page is drawn and once at every start-up. Building it
        to answer a question about the Settings page cost the test suite three
        and a half seconds per window.

        The cost of the weaker check is that a corrupt model file reads as
        "available" until the first scan, which then fails with a message
        naming the real problem. That is the right way round: the status line
        is a hint, and the scan is where an answer has to be true.
        """
        if _shared is not None:
            return True, "RapidOCR (PP-OCR on ONNX Runtime, offline)"
        try:
            import rapidocr  # noqa: F401, PLC0415
        except ImportError:
            return False, ("Not in this build (optional). Use the full build, "
                           "or run: pip install rapidocr onnxruntime")
        return True, "RapidOCR (PP-OCR on ONNX Runtime, offline)"

    def extract(self, image_path: Path, categories: list[str]) -> ExtractionResult:
        started = time.monotonic()
        engine = self._engine()
        try:
            output = engine(str(image_path))
        except Exception as exc:
            raise ExtractionError(f"RapidOCR failed to read the image: {exc}") from exc

        words = _as_words(output)
        if not words:
            raise ExtractionError(
                "RapidOCR found no text in this image. It may be too dark, too "
                "blurred or not a receipt."
            )
        text = rows_to_text(
            group_rows(words, tolerance=ROW_TOLERANCE, skew=_skew(output)))
        receipt = parse_receipt_text(text)
        receipt.confidence = _confidence(output)
        receipt.notes = (
            "Read offline by RapidOCR. More accurate than the built-in Windows "
            "reader, but still a machine reading: check the line items and the "
            "totals against the image."
        )
        return ExtractionResult(
            receipt=receipt,
            engine=self.name,
            model="PP-OCR / onnxruntime",
            raw_text=text,
            elapsed_ms=int((time.monotonic() - started) * 1000),
        )


def _quieten() -> None:
    """Stop RapidOCR narrating every model it loads.

    It announces four lines at INFO on each construction. Its logger sets its
    own level during start-up, so this has to run *after* the engine is built --
    silencing it beforehand does nothing, which the first attempt at this proved.

    Note what is *not* wrong with the noise: the logger is configured with
    ``propagate=False`` and its own stderr handler, so none of it ever reaches
    ``bookkeeping.log``. This is tidiness, not a fix for polluted logs. The
    handler is lowered as well as the logger because the handler carries its own
    level.
    """
    noisy = logging.getLogger("RapidOCR")
    noisy.setLevel(logging.WARNING)
    for handler in noisy.handlers:
        handler.setLevel(logging.WARNING)


def _as_words(output) -> list[Word]:
    """RapidOCR's polygons as the ``Word`` boxes the row grouper expects.

    Each polygon is four corners rather than a rectangle, because the detector
    can return a rotated quadrilateral for text on a curled receipt. Reduced to
    its bounding box, which is what ``group_rows`` works in and is close enough
    at the angles a photographed till roll actually produces.
    """
    boxes = getattr(output, "boxes", None)
    texts = getattr(output, "txts", None)
    if boxes is None or texts is None:
        return []
    words: list[Word] = []
    for box, text in zip(boxes, texts):
        if not str(text).strip():
            continue
        xs = [float(point[0]) for point in box]
        ys = [float(point[1]) for point in box]
        words.append(Word(str(text), min(xs), min(ys),
                          max(xs) - min(xs), max(ys) - min(ys)))
    return words


# A photograph rotated further than about eight degrees is not a tilt any more,
# and a slope that large is more likely to mean the estimate itself has gone
# wrong than that somebody photographed a receipt sideways. A guard, not a tuned
# value: the worst of the receipts here measures 0.039.
MAX_SKEW = 0.15


def _skew(output) -> float:
    """The page's text slope, read off the detector's own quadrilaterals.

    **The angle is already in the data and ``_as_words`` throws it away.** The
    detector returns a rotated four-corner polygon per line, so the tilt of its
    top and bottom edges is the tilt of the printing -- there is nothing to
    search for and nothing to infer.

    That matters, because inferring it is genuinely hard. Two estimators were
    tried against these photographs first and both failed on the same thing: a
    receipt is a table, its columns are evenly spaced, and a shear that slides
    one column onto the row below produces a projection every bit as sharp as
    the true angle. Fitting slopes inside already-grouped rows failed too, since
    the rows it had to learn from were the mis-grouped ones.

    Taken as a median over every polygon long enough to have a measurable angle
    -- wider than twice its own height -- so a single wildly rotated detection
    cannot move it. On a receipt photographed square the detector returns
    upright boxes and this is exactly 0.0, which is the answer that changes
    nothing.
    """
    boxes = getattr(output, "boxes", None)
    if boxes is None:
        return 0.0
    slopes = []
    for box in boxes:
        try:
            (x0, y0), (x1, y1), (x2, y2), (x3, y3) = (
                (float(p[0]), float(p[1])) for p in box)
        except (TypeError, ValueError):   # a shape this code does not know
            continue
        top, bottom = x1 - x0, x2 - x3
        height = ((y3 - y0) + (y2 - y1)) / 2
        if top <= 0 or bottom <= 0 or (top + bottom) / 2 < 2 * abs(height):
            continue
        slopes.append(((y1 - y0) / top + (y2 - y3) / bottom) / 2)
    if not slopes:
        return 0.0
    slope = statistics.median(slopes)
    return slope if abs(slope) <= MAX_SKEW else 0.0


def _confidence(output) -> float:
    """The recogniser's own mean score, capped so nothing auto-confirms."""
    scores = [float(s) for s in (getattr(output, "scores", None) or [])]
    if not scores:
        return 0.5
    return min(MAX_CONFIDENCE, sum(scores) / len(scores))
