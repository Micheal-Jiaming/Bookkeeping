"""The palette rules every theme has to satisfy, enforced rather than remembered.

`app/ui/theme.py` explains why the accent is not a free choice: it is the bar
colour in the report charts, so it has to stay legible on that palette's own
chart surface and stay distinguishable from the reserved status colours. Those
checks were run by hand when each theme was added, which is exactly the kind of
thing that quietly stops happening. Here they run on every commit, against every
theme, including ones nobody has written yet.

The two measures are deliberately different, because they answer different
questions -- see the module docstring in `app/ui/theme.py`.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.ui.theme import (  # noqa: E402
    DEFAULT_THEME,
    THEME_LABELS,
    THEME_ORDER,
    THEMES,
)


def _linear(channel: float) -> float:
    channel /= 255.0
    return channel / 12.92 if channel <= 0.04045 else ((channel + 0.055) / 1.055) ** 2.4


def _rgb(colour: str) -> tuple[float, float, float]:
    text = colour.lstrip("#")
    return tuple(_linear(int(text[i:i + 2], 16)) for i in (0, 2, 4))


def contrast(one: str, two: str) -> float:
    """WCAG contrast ratio, 1.0 to 21.0. Answers 'can this be read?'."""
    def luminance(colour: str) -> float:
        r, g, b = _rgb(colour)
        return 0.2126 * r + 0.7152 * g + 0.0722 * b

    a, b = luminance(one), luminance(two)
    return (max(a, b) + 0.05) / (min(a, b) + 0.05)


def delta_e(one: str, two: str) -> float:
    """Perceptual distance in OKLab, x100. Answers 'could these be confused?'."""
    def oklab(colour: str) -> tuple[float, float, float]:
        r, g, b = _rgb(colour)
        l = (0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b) ** (1 / 3)
        m = (0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b) ** (1 / 3)
        s = (0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b) ** (1 / 3)
        return (0.2104542553 * l + 0.7936177850 * m - 0.0040720468 * s,
                1.9779984951 * l - 2.4285922050 * m + 0.4505937099 * s,
                0.0259040371 * l + 0.7827717662 * m - 0.8086757660 * s)

    return 100 * sum((x - y) ** 2 for x, y in zip(oklab(one), oklab(two))) ** 0.5


NAMES = sorted(THEMES)

# (label, foreground key, background key, minimum ratio).
#
# 4.5 is WCAG AA for body text, and 3.0 is WCAG AA for large or bold text and
# for non-text UI -- which is what the accent is, filling a chart bar, and what
# a bold button label is.
#
# The DIM, GOOD and BAD rows take 3.0 as a deliberate local relaxation rather
# than anything WCAG says: all three are small text, which AA would hold to 4.5.
# They sit at 3.0 because each is a short, repeated, secondary label that always
# appears beside the same information in full-contrast text.
#
# Of the four palettes shipping today only DIM (3.49 solarized, 3.50 light) and
# BAD (3.62 dark) actually fall below 4.5; GOOD clears it everywhere, from 4.75
# to 10.38. So the floor is headroom for a palette that wants a genuinely grey
# grey, not an excuse for the present ones. Raising it is a design decision, not
# a bug fix.
READABILITY = [
    ("accent on the chart surface", "ACCENT", "CARD", 3.0),
    ("body text on a card", "FG", "CARD", 4.5),
    ("body text on the window", "FG", "BG", 4.5),
    ("secondary text on a card", "MUTED", "CARD", 4.5),
    ("hint text on a card", "DIM", "CARD", 3.0),
    ("label on an accent button", "ON_ACCENT", "ACCENT", 3.0),
    ("text typed into a field", "FG", "ENTRY", 4.5),
    ("the good status on a card", "GOOD", "CARD", 3.0),
    ("the bad status on a card", "BAD", "CARD", 3.0),
]
CONFUSION_FLOOR = 15.0


@pytest.mark.parametrize("name", NAMES)
def test_every_theme_defines_the_whole_palette(name):
    """A missing key is a KeyError deep in a widget, long after startup."""
    assert set(THEMES[name]) == set(THEMES[DEFAULT_THEME]), (
        f"{name} does not define the same colours as {DEFAULT_THEME}")


@pytest.mark.parametrize("name", NAMES)
def test_every_colour_is_a_hex_triplet(name):
    for key, value in THEMES[name].items():
        assert len(value) == 7 and value.startswith("#"), f"{name}.{key} = {value!r}"
        int(value[1:], 16)


@pytest.mark.parametrize("name", NAMES)
@pytest.mark.parametrize("label, fg, bg, floor", READABILITY,
                         ids=[c[0].replace(" ", "-") for c in READABILITY])
def test_every_theme_is_readable(name, label, fg, bg, floor):
    ratio = contrast(THEMES[name][fg], THEMES[name][bg])
    assert ratio >= floor, (
        f"{name}: {label} is {ratio:.2f}:1, needs {floor}:1 "
        f"({THEMES[name][fg]} on {THEMES[name][bg]})")


@pytest.mark.parametrize("name", NAMES)
@pytest.mark.parametrize("status", ["GOOD", "WARN", "BAD"])
def test_the_chart_colour_is_never_mistakable_for_a_status(name, status):
    """The accent fills the report bars; the status colours mean something else.

    Measured in OKLab, not by contrast ratio: two colours of the same lightness
    and different hue -- a blue and a dark red, say -- score about 1:1 on
    contrast while being impossible to confuse, so contrast cannot answer this.
    """
    gap = delta_e(THEMES[name]["ACCENT"], THEMES[name][status])
    assert gap >= CONFUSION_FLOOR, (
        f"{name}: the accent {THEMES[name]['ACCENT']} is only dE {gap:.1f} from "
        f"{status} {THEMES[name][status]}; needs {CONFUSION_FLOOR}")


def test_the_cycle_order_covers_every_theme_exactly_once():
    assert sorted(THEME_ORDER) == NAMES
    assert len(set(THEME_ORDER)) == len(THEME_ORDER)


def test_the_cycle_groups_dark_themes_before_light_ones():
    """One press of the Theme button should not flip the room brightness.

    Grouping, not sorting: the light themes may sit in any order among
    themselves -- Solarized's cream is fractionally darker than the plain light
    theme -- as long as the cycle crosses from dark to light exactly once.
    """
    is_light = [contrast(THEMES[name]["BG"], "#000000") > 5.0
                for name in THEME_ORDER]
    assert is_light == sorted(is_light), (
        f"cycle order {THEME_ORDER} crosses between dark and light more than once")
    assert set(is_light) == {False, True}, "expected both dark and light themes"


def test_every_theme_has_a_menu_label():
    assert set(THEME_LABELS) == set(NAMES)


def test_the_default_theme_exists():
    assert DEFAULT_THEME in THEMES
