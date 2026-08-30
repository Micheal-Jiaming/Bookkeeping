"""The interface language, and the machine translation of item names.

No test here touches the network. The translation services are reached through
one function, ``translate._get``, which is replaced with scripted answers.
"""

from __future__ import annotations

import ast
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import i18n  # noqa: E402
from app.i18n import CHINESE, LANGUAGES, t  # noqa: E402
from app.lookup import chinese_for  # noqa: E402
from app.lookup import translate  # noqa: E402


@pytest.fixture(autouse=True)
def english_again():
    """Language is process-global, so put it back however a test ends."""
    yield
    i18n.set_language("en")


# --- the language itself ---------------------------------------------------

def test_only_chinese_is_offered():
    """The user asked for exactly one extra language and said so explicitly.

    A speculative third language is 200-odd strings nobody maintains, so this
    test exists to make adding one a deliberate act rather than a drive-by.
    """
    assert set(LANGUAGES) == {"en", "zh"}


def test_english_is_returned_unchanged():
    i18n.set_language("en")
    assert t("Save draft") == "Save draft"


def test_chinese_is_returned_when_chosen():
    i18n.set_language("zh")
    assert t("Save draft") == "保存草稿"
    assert t("Settings") == "设置"


def test_an_untranslated_string_falls_back_to_english():
    """Better a missing translation than a visible key."""
    i18n.set_language("zh")
    assert t("A string nobody has translated") == "A string nobody has translated"


def test_an_unknown_language_falls_back_rather_than_raising():
    i18n.set_language("kl")
    assert i18n.current() == "en"
    assert t("Settings") == "Settings"


def test_the_font_follows_the_language():
    """Segoe UI has no Chinese glyphs, so the family has to change with it."""
    i18n.set_language("en")
    assert i18n.font_family() == "Segoe UI"
    i18n.set_language("zh")
    assert i18n.font_family() == "Microsoft YaHei UI"


def test_every_translation_still_matches_a_string_in_the_code():
    """Guards the one real weakness of using English text as the key.

    Editing an English string leaves its translation behind, silently: the
    interface falls back to English for that line and nothing complains. This
    compares against the string constants the source actually contains, so
    implicit concatenation across lines is handled properly.
    """
    literals: set[str] = set()
    for path in sorted((ROOT / "app").rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                literals.add(node.value)

    orphans = sorted(key for key in CHINESE if key not in literals)
    assert not orphans, (
        "these translations no longer match any string in app/, so they are "
        f"dead and the interface will show English instead: {orphans}")


def test_the_status_filter_survives_being_translated(books):
    """A combobox hands back the text on screen, not the English behind it."""
    from app.ui.receipts import _statuses_for

    i18n.set_language("zh")
    assert _statuses_for("已确认") == ("confirmed",)
    assert _statuses_for("全部") is None


def test_the_engine_setting_round_trips_in_chinese(books):
    """Whatever the interface shows, the stored value stays English."""
    from app.ui.settings_page import ENGINES, _label_for, _value_for

    i18n.set_language("zh")
    shown = _label_for(ENGINES, "windows")
    assert shown == "仅离线 OCR（Windows 内置）"
    assert _value_for(ENGINES, shown) == "windows"


# --- translating item names ------------------------------------------------

def _scripted(monkeypatch, answers: dict[str, tuple[int | None, str]]):
    calls: list[str] = []

    def fake_get(url: str):
        calls.append(url)
        for needle, answer in answers.items():
            if needle in url:
                return answer
        return 404, ""

    monkeypatch.setattr(translate, "_get", fake_get)
    return calls


def test_google_reads_the_short_reply_shape(monkeypatch):
    _scripted(monkeypatch, {"clients5": (200, json.dumps(["西兰花冠"]))})
    assert translate.google("Broccoli Crowns") == "西兰花冠"


def test_google_reads_the_nested_reply_shape(monkeypatch):
    """Longer text comes back as [["translated", "source"]] instead."""
    body = json.dumps([["高乐氏柱塞和马桶刷", "Clorox Plunger & Toilet Brush"]])
    _scripted(monkeypatch, {"clients5": (200, body)})
    assert translate.google("Clorox Plunger & Toilet Brush") == "高乐氏柱塞和马桶刷"


def test_mymemory_rejects_its_own_error_prose(monkeypatch):
    """It reports failure in the field that should hold the answer."""
    body = json.dumps({"responseData": {"translatedText": "QUERY LENGTH LIMIT EXCEEDED"}})
    _scripted(monkeypatch, {"mymemory": (200, body)})
    assert translate.mymemory("something") is None


def test_a_refused_service_is_not_a_missing_translation(monkeypatch):
    """The bug this guards: caching a 429 as "this has no Chinese".

    Google answers 429 to the very first request from some addresses. Recording
    that as a verdict would leave the name in English for ever.
    """
    _scripted(monkeypatch, {"clients5": (429, ""), "mymemory": (429, "")})
    with pytest.raises(translate.Unavailable):
        translate.translate_one("Broccoli Crowns")


def test_google_is_asked_before_mymemory(monkeypatch):
    body = json.dumps(["大鸡蛋"])
    calls = _scripted(monkeypatch, {"clients5": (200, body)})
    assert translate.translate_one("Large Eggs") == "大鸡蛋"
    assert len(calls) == 1 and "clients5" in calls[0]


def test_a_translation_is_cached_and_not_asked_for_twice(books, monkeypatch):
    calls = _scripted(monkeypatch, {"clients5": (200, json.dumps(["酵母面包"]))})
    assert chinese_for(["Sourdough Loaf"]) == {"Sourdough Loaf": "酵母面包"}
    once = len(calls)
    assert chinese_for(["Sourdough Loaf"]) == {"Sourdough Loaf": "酵母面包"}
    assert len(calls) == once, "the second scan should come from the cache"


def test_nothing_reaches_the_network_when_reading_the_cache(books, monkeypatch):
    """The review pane calls this on the interface thread; it must not block."""
    calls = _scripted(monkeypatch, {"clients5": (200, json.dumps(["大鸡蛋"]))})
    chinese_for(["Large Eggs"])
    before = len(calls)
    assert chinese_for(["Large Eggs", "Never Seen Before"], enabled=False) == {
        "Large Eggs": "大鸡蛋"}
    assert len(calls) == before


def test_an_offline_machine_leaves_the_names_in_english(books, monkeypatch):
    _scripted(monkeypatch, {"clients5": (None, ""), "mymemory": (None, "")})
    assert chinese_for(["Large Eggs"]) == {}


def test_the_pipeline_only_translates_for_a_chinese_interface(books, monkeypatch):
    """An English reader would never see the result, so it is latency for nothing."""
    from app.extract import ExtractedItem, ExtractedReceipt, ExtractionResult

    calls = _scripted(monkeypatch, {"clients5": (200, json.dumps(["大鸡蛋"]))})
    result = ExtractionResult(
        receipt=ExtractedReceipt(currency="USD", total="1.66", confidence=0.9,
                                 items=[ExtractedItem(description="Large Eggs",
                                                      amount="1.66")]),
        engine="stub", model="stub-1", raw_response="{}",
        input_tokens=0, output_tokens=0, cost_usd=0.0, elapsed_ms=1)

    assert books["pipeline"]._translate_item_names(result, {"language": "en"}) == 0
    assert calls == []
    assert books["pipeline"]._translate_item_names(result, {"language": "zh"}) == 1
    assert calls, "a Chinese interface should translate"
