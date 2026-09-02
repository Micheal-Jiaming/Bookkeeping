"""Run the real OCR over the real photographs and report how well it did.

Split from ``tools/accuracy.py`` on purpose. The scoring in that module is pure
arithmetic over a reading and needs neither Windows nor a photograph, so its
tests run anywhere; everything that needs `winrt` and somebody's receipt on the
disk is here, in the part that is allowed to be skipped.

    py tools\\measure_accuracy.py                  read the photographs, print a report
    py tools\\measure_accuracy.py --json out.json  also write the numbers out
    py tools\\measure_accuracy.py --check          fail if anything regressed
    py tools\\measure_accuracy.py --update-baseline   record today as the baseline

`--check` is the one meant for a commit hook or CI. It compares against
``tests/fixtures/accuracy_baseline.json`` and exits non-zero when the reader got
worse, where *worse* explicitly includes inventing more lines -- not only the
money gap, which is the number a fabricating change would improve.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.extract.base import sha256_file                       # noqa: E402
from app.extract.windows_ocr import WindowsOcrExtractor        # noqa: E402
from app.db import BUILTIN_CATEGORIES                          # noqa: E402
from tools.accuracy import (                                   # noqa: E402
    BASELINE_PATH, PHOTO_DIR, Score, load_truth, score_reading,
)

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


def read_photographs(photo_dir: Path = PHOTO_DIR) -> list[tuple[str, object, str]]:
    """OCR every photograph present, newest engine settings, one pass each.

    Returns ``(filename, receipt, sha256)`` per image. Missing directory or no
    images is not an error -- the photographs are gitignored, so a clone on
    another machine legitimately has none.
    """
    if not photo_dir.is_dir():
        return []

    categories = [name for name, _colour, _order in BUILTIN_CATEGORIES]
    extractor = WindowsOcrExtractor()
    available, why = extractor.available()
    if not available:
        raise SystemExit(f"Windows OCR is not available: {why}")

    readings = []
    for photo in sorted(photo_dir.glob("*.jpg")) + sorted(photo_dir.glob("*.png")):
        result = extractor.extract(photo, categories)
        readings.append((photo.name, result.receipt, sha256_file(photo)))
    return readings


def measure(photo_dir: Path = PHOTO_DIR) -> list[Score]:
    """Read every photograph and score it against whatever truth exists."""
    truths = load_truth()
    scores = []
    for name, receipt, digest in read_photographs(photo_dir):
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


def compare(scores: list[Score], baseline_path: Path = BASELINE_PATH) -> list[str]:
    """Return one line per metric that got worse. Empty means nothing regressed.

    A photograph absent from the baseline is not a regression -- it is a new
    receipt, and refusing it would mean the harness fights every addition to the
    corpus. It is reported so the baseline gets updated deliberately.
    """
    if not baseline_path.exists():
        return ["no baseline recorded yet; run with --update-baseline"]

    baseline = json.loads(baseline_path.read_text(encoding="utf-8")).get("receipts", {})
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
    parser.add_argument("--json", type=Path, help="write the scores to this file")
    parser.add_argument("--check", action="store_true",
                        help="exit non-zero if anything regressed against the baseline")
    parser.add_argument("--update-baseline", action="store_true",
                        help="record the current scores as the baseline")
    parser.add_argument("--photos", type=Path, default=PHOTO_DIR)
    args = parser.parse_args(argv)

    scores = measure(args.photos)
    print(report(scores))

    if args.json:
        args.json.write_text(
            json.dumps({"receipts": {s.photo: asdict(s) for s in scores}}, indent=2),
            encoding="utf-8")

    if args.update_baseline:
        if not scores:
            print("\nRefusing to write an empty baseline: no photographs were read.")
            return 1
        BASELINE_PATH.write_text(
            json.dumps({"receipts": {s.photo: asdict(s) for s in scores}}, indent=2),
            encoding="utf-8")
        print(f"\nBaseline updated: {BASELINE_PATH}")

    if args.check:
        if not scores:
            print("\nNothing to check: no photographs present.")
            return 0
        problems = compare(scores)
        if problems:
            print("\nREGRESSED:")
            for line in problems:
                print(f"  - {line}")
            return 1
        print("\nNo regression against the baseline.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
