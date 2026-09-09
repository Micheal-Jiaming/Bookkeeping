"""Run the real OCR over the real photographs and report how well it did.

Split from ``tools/accuracy.py`` on purpose. The scoring in that module is pure
arithmetic over a reading and needs neither an OCR engine nor a photograph, so
its tests run anywhere; everything that needs a real reader and somebody's
receipt on the disk is here, in the part that is allowed to be skipped.

    py tools\\measure_accuracy.py                  read the photographs, print a report
    py tools\\measure_accuracy.py --json out.json  also write the numbers out
    py tools\\measure_accuracy.py --engine windows  measure a named engine
    py tools\\measure_accuracy.py --check          fail if anything regressed
    py tools\\measure_accuracy.py --update-baseline   record today as the baseline

`--check` is the one meant for a commit hook or CI. It compares against
``tests/fixtures/accuracy_baseline.<engine>.json`` and exits non-zero when the
reader got worse, where *worse* explicitly includes inventing more lines -- not
only the money gap, which is the number a fabricating change would improve.

## Which engine gets measured

Whichever one the application would actually use: RapidOCR wherever it is
installed, Windows OCR otherwise. This harness read Windows OCR unconditionally
until 1.17.0, long after RapidOCR became the default in the full build -- so
`--check` was guarding an engine most users had stopped running, and doing it
silently, which is the worst way for a gate to be wrong.

Claude is excluded from `auto` on purpose, and not because of the money. Its
reading is not deterministic, so it cannot back a regression baseline: the same
photograph scored twice can differ, and a gate that fires at random gets turned
off. Name it explicitly if you want to see what it does.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.extract import build_engines                          # noqa: E402
from app.extract.base import Extractor, sha256_file            # noqa: E402
from app.db import BUILTIN_CATEGORIES                          # noqa: E402
from tools.accuracy import (                                   # noqa: E402
    PHOTO_DIR, Score, baseline_path_for, load_truth, score_reading,
)

# What `--engine auto` will settle for, best first. This is the pipeline's own
# order from `app.extract` with Claude removed -- see the module docstring for
# why a non-deterministic engine cannot back a regression baseline.
MEASURABLE = ("rapid", "windows", "tesseract")

# A regression in any of these blocks `--check`. `lines_invented` is on the list
# for the reason the module docstring gives: a change that fabricates lines
# improves `unaccounted_cents` while making the reading worse, so the gap alone
# must never be the gate.
GUARDED = (
    ("lines_matched", "fewer lines matched", -1),
    ("lines_invented", "more lines invented", +1),
    ("names_exact", "fewer names exact", -1),
    ("header_correct", "fewer header fields correct", -1),
    ("header_present", "fewer header fields read", -1),
    ("items_read", "fewer items read", -1),
)


def money(cents: int | None) -> str:
    return "     -" if cents is None else f"{cents / 100:>6.2f}"


def photographs(photo_dir: Path = PHOTO_DIR) -> list[Path]:
    """Every receipt image on the disk, in a stable order."""
    if not photo_dir.is_dir():
        return []
    return sorted(photo_dir.glob("*.jpg")) + sorted(photo_dir.glob("*.png"))


def resolve_engine(preference: str = "auto") -> Extractor:
    """The engine to measure with, or exit saying why there is not one.

    ``auto`` walks ``MEASURABLE`` and takes the first that reports itself
    available, which on a full build is RapidOCR and on a slim one is Windows
    OCR. Any other name is taken literally and is *not* allowed to fall back:
    asking for one engine and silently being given another produces a number
    filed under the wrong heading, which is worse than no number.
    """
    if preference == "auto":
        for name in MEASURABLE:
            engine = build_engines({"engine": name})[0]
            if engine.available()[0]:
                return engine
        raise SystemExit(
            "No OCR engine is available. Install RapidOCR (pip install rapidocr "
            "onnxruntime), or run on a Windows machine with its built-in reader.")

    # Checked here rather than trusted to `build_engines`, which answers an
    # unrecognised name with the whole fallback list -- so taking its first
    # entry would quietly hand back Claude, the one engine deliberately kept
    # out of an automatic measurement, in response to a typo.
    known = (*MEASURABLE, "claude")
    if preference not in known:
        raise SystemExit(f"There is no engine called {preference!r}. "
                         f"Choose from: auto, {', '.join(known)}.")
    engine = build_engines({"engine": preference})[0]
    available, why = engine.available()
    if not available:
        raise SystemExit(f"{preference} is not available: {why}")
    return engine


def read_photographs(photo_dir: Path = PHOTO_DIR,
                     extractor: Extractor | None = None
                     ) -> list[tuple[str, object, str]]:
    """OCR every photograph present, one pass each.

    Returns ``(filename, receipt, sha256)`` per image. Missing directory or no
    images is not an error -- the photographs are gitignored, so a clone on
    another machine legitimately has none.
    """
    photos = photographs(photo_dir)
    if not photos:
        return []

    # Resolved only once there is something to read, so that a clone with no
    # photographs still reports "none found" rather than "no engine available".
    # On a machine with neither, the missing photographs are the honest answer.
    categories = [name for name, _colour, _order in BUILTIN_CATEGORIES]
    extractor = extractor or resolve_engine()

    readings = []
    for photo in photos:
        result = extractor.extract(photo, categories)
        readings.append((photo.name, result.receipt, sha256_file(photo)))
    return readings


def measure(photo_dir: Path = PHOTO_DIR,
            extractor: Extractor | None = None) -> list[Score]:
    """Read every photograph and score it against whatever truth exists."""
    truths = load_truth()
    scores = []
    for name, receipt, digest in read_photographs(photo_dir, extractor):
        truth = truths.get(name)
        score = score_reading(receipt, truth)
        score.photo = name
        if truth is None:
            score.notes.append("no truth record: self-checks only")
        elif truth.sha256 and truth.sha256 != digest:
            # The truth file describes a different image than the one on disk.
            # The score is still computed and still compared against the
            # baseline -- it is flagged, not quarantined -- so a re-photographed
            # receipt can show up as a regression when nothing regressed. The
            # note is what tells the reader to re-transcribe rather than to go
            # looking for a code fault.
            score.notes.append("PHOTO CHANGED since the truth was written")
        elif not truth.header and not truth.has_lines:
            score.notes.append("nothing verified yet: self-checks only")
        elif not truth.has_lines:
            score.notes.append(
                f"header verified ({truth.verified_by}), lines not transcribed")
        scores.append(score)
    return scores


def report(scores: list[Score]) -> str:
    """The human-readable table. Self-checks left of the divider, truth right."""
    if not scores:
        return ("No photographs found in pictures\\.\n"
                "They are gitignored deliberately, so this is expected on a "
                "fresh clone. Put the receipt images there to measure.")

    lines = [
        "photo            items     sum   unacc arith | hdr    lines m/x/i   names",
        "-" * 76,
    ]
    for s in scores:
        arith = {True: "ok", False: "BAD", None: "-"}[s.arithmetic_ok]
        header = (f"{s.header_correct}/{s.header_checked}"
                  if s.header_checked else f"({s.header_present}/5)")
        if s.truth_lines:
            triple = f"{s.lines_matched}/{s.lines_missed}/{s.lines_invented}"
            names = f"{s.names_exact}/{s.lines_matched}"
        else:
            triple, names = "-", "-"
        lines.append(
            f"{s.photo:<16}{s.items_read:>5} {money(s.items_sum_cents)} "
            f"{money(s.unaccounted_cents)} {arith:>5} | {header:>5} "
            f"{triple:>12} {names:>7}")

    unaccounted = [s.unaccounted_cents for s in scores if s.unaccounted_cents is not None]
    lines += [
        "-" * 76,
        f"{len(scores)} photographs, {sum(s.items_read for s in scores)} items read, "
        f"{sum(unaccounted) / 100:.2f} unaccounted in total",
        "",
        "hdr  = header fields correct/checked; (n/5) where nothing is verified yet",
        "m/x/i = lines matched / missed / invented, against a human transcription",
    ]
    for s in scores:
        for note in s.notes:
            lines.append(f"  {s.photo}: {note}")
    return "\n".join(lines)


def compare(scores: list[Score], baseline_path: Path,
            engine: str | None = None) -> list[str]:
    """Return one line per metric that got worse. Empty means nothing regressed.

    A photograph absent from the baseline is not a regression -- it is a new
    receipt, and refusing it would mean the harness fights every addition to the
    corpus. It is reported so the baseline gets updated deliberately.

    Pass ``engine`` to have the comparison refuse a baseline some other engine
    wrote. It is optional only so that the tests of the metric arithmetic, which
    are about the numbers and not about where they came from, do not have to
    invent an engine name; every real caller supplies it.
    """
    if not baseline_path.exists():
        return [f"no baseline recorded yet at {baseline_path.name}; "
                "run with --update-baseline"]

    document = json.loads(baseline_path.read_text(encoding="utf-8"))
    # Baselines written before 1.17.0 carry no engine, and Windows OCR is the
    # only engine that could have written one -- it was the only engine this
    # harness ever ran.
    wrote_it = document.get("engine", "windows")
    if engine is not None and wrote_it != engine:
        # Refusing outright rather than reporting per-metric differences. Across
        # engines the deltas are real numbers about nothing: every one of them
        # measures the change of engine, not a change in the code.
        return [f"baseline was written by {wrote_it}, but {engine} was measured; "
                "these are not comparable. Use --engine to match it, or "
                "--update-baseline to replace it."]

    baseline = document.get("receipts", {})
    problems = []
    for s in scores:
        was = baseline.get(s.photo)
        if was is None:
            problems.append(f"{s.photo}: not in the baseline (new receipt?)")
            continue
        for field_name, description, worse_direction in GUARDED:
            now, before = getattr(s, field_name), was.get(field_name)
            if before is None:
                continue
            if (now - before) * worse_direction > 0:
                problems.append(
                    f"{s.photo}: {description} ({before} -> {now})")
        # Money is guarded separately, and deliberately only in one direction:
        # a gap that GROWS is reported, a gap that shrinks is not. That looks
        # like a hole -- a gap can shrink because lines were invented to close
        # it -- but closing it that way is caught by `lines_invented` in
        # GUARDED above, which is the right place for it. Guarding the shrink
        # here as well would report every genuine improvement as a regression;
        # `test_a_genuine_improvement_is_not_reported_as_a_regression` pins
        # that. Any increase at all is reported: these are integer cents and
        # there is no tolerance band.
        now, before = s.unaccounted_cents, was.get("unaccounted_cents")
        if None not in (now, before) and abs(now) > abs(before):
            problems.append(
                f"{s.photo}: more money unaccounted "
                f"({before / 100:.2f} -> {now / 100:.2f})")
    return problems


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--engine", default="auto",
                        choices=("auto", *MEASURABLE, "claude"),
                        help="which reader to measure (default: the best installed)")
    parser.add_argument("--json", type=Path, help="write the scores to this file")
    parser.add_argument("--check", action="store_true",
                        help="exit non-zero if anything regressed against the baseline")
    parser.add_argument("--update-baseline", action="store_true",
                        help="record the current scores as the baseline")
    parser.add_argument("--baseline", type=Path,
                        help="use this baseline file instead of the engine's own")
    parser.add_argument("--photos", type=Path, default=PHOTO_DIR)
    args = parser.parse_args(argv)

    if not photographs(args.photos):
        print(report([]))
        # Nothing to check and nothing to record, so neither is an error -- the
        # photographs are gitignored and a fresh clone legitimately has none.
        # `--update-baseline` is the exception: it was asked to write something,
        # it wrote nothing, and reporting success would let a scripted caller
        # believe a baseline exists. It never overwrites a good one with an
        # empty one, which is the part that matters.
        return 1 if args.update_baseline else 0

    extractor = resolve_engine(args.engine)
    scores = measure(args.photos, extractor)
    baseline_path = args.baseline or baseline_path_for(extractor.name)

    print(f"engine: {extractor.name}\n")
    print(report(scores))

    document = {"engine": extractor.name,
                "receipts": {s.photo: asdict(s) for s in scores}}

    if args.json:
        args.json.write_text(json.dumps(document, indent=2), encoding="utf-8")

    if args.update_baseline:
        baseline_path.write_text(json.dumps(document, indent=2), encoding="utf-8")
        print(f"\nBaseline updated: {baseline_path}")

    if args.check:
        problems = compare(scores, baseline_path, extractor.name)
        if problems:
            print("\nREGRESSED:")
            for line in problems:
                print(f"  - {line}")
            return 1
        print(f"\nNo regression against the {extractor.name} baseline.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
