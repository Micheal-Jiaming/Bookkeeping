"""Turning an item name into Chinese, for when the interface is in Chinese.

The receipts are printed in English, so reading them in Chinese means machine
translation. Two keyless services are used, in order:

* **Google Translate**, through the endpoint its own Chrome extension uses
  (``clients5.google.com/translate_a/t?client=dict-chrome-ex``). This is the
  service the user asked for and its output is the better of the two.
* **MyMemory**, as a fallback. It leaves brand names alone more often --
  "Clorox活塞和马桶刷" against Google's "Clorox 柱塞和马桶刷，带携带盒" -- which is
  sometimes an improvement and sometimes not, but it answers when Google does
  not.

**The obvious endpoint does not work here and is not worth retrying.**
``translate.googleapis.com/translate_a/single``, the one every snippet on the
internet uses, answers `429 Too Many Requests` to the *first* request from this
address -- not after a burst, immediately. That is a block rather than a rate
limit, so pacing cannot get around it. The ``clients5`` endpoint answered twelve
consecutive requests at half a second apart without complaint.

Everything is cached in the database, because a household buys the same things
week after week and a translation never changes. Nothing here may raise: a name
that cannot be translated is shown in English, which is exactly what it was
before this file existed.
"""

from __future__ import annotations

import json
import logging
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

from ..db import connect

log = logging.getLogger("bookkeeping.translate")

USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")
REQUEST_TIMEOUT = 8.0
BATCH_BUDGET = 40.0

# Half a second between requests to the same host: measured, not guessed, as the
# cadence that translated twelve names in a row without a refusal.
HOST_INTERVAL = 0.5
MAX_SOURCE = 120        # a catalogue title longer than this is not a name

_pace_lock = threading.Lock()
_last_call: dict[str, float] = {}


class Unavailable(Exception):
    """A service could not answer; nothing may be cached about this text."""


def _pace(host: str) -> None:
    with _pace_lock:
        now = time.monotonic()
        earliest = _last_call.get(host, 0.0) + HOST_INTERVAL
        _last_call[host] = max(now, earliest)
        wait = earliest - now
    if wait > 0:
        time.sleep(wait)


def _get(url: str) -> tuple[int | None, str]:
    _pace(urllib.parse.urlsplit(url).hostname or "")
    request = urllib.request.Request(
        url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT) as response:
            return response.status, response.read(20000).decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return exc.code, ""
    except Exception as exc:
        log.debug("Translation request failed for %s: %s", url, exc)
        return None, ""


def google(text: str) -> str | None:
    """Google Translate through its Chrome-extension endpoint."""
    status, body = _get(
        "https://clients5.google.com/translate_a/t?client=dict-chrome-ex"
        "&sl=en&tl=zh-CN&q=" + urllib.parse.quote(text))
    if status != 200:
        raise Unavailable(f"google returned {status}")
    try:
        data = json.loads(body)
    except ValueError:
        return None
    # The reply is ["translated"], or [["translated", "source"]] for longer text.
    if isinstance(data, list) and data:
        first = data[0]
        if isinstance(first, str):
            return first.strip() or None
        if isinstance(first, list) and first and isinstance(first[0], str):
            return first[0].strip() or None
    return None


def mymemory(text: str) -> str | None:
    """MyMemory's free anonymous tier."""
    status, body = _get(
        "https://api.mymemory.translated.net/get?langpair=en%7Czh-CN&q="
        + urllib.parse.quote(text))
    if status != 200:
        raise Unavailable(f"mymemory returned {status}")
    try:
        data = json.loads(body)
    except ValueError:
        return None
    name = ((data.get("responseData") or {}).get("translatedText") or "").strip()
    # It reports failures as prose in the very field that should hold the answer.
    if not name or name.upper().startswith(("NO QUERY", "QUERY LENGTH", "INVALID")):
        return None
    return name


SOURCES = (google, mymemory)


def translate_one(text: str) -> str | None:
    """Chinese for ``text``, or ``None`` if every service answered and had none.

    Raises ``Unavailable`` when no service could be reached, so that a network
    problem is never written into the cache as "this has no translation".
    """
    problems: list[str] = []
    answered = False
    for source in SOURCES:
        try:
            result = source(text)
        except Unavailable as exc:
            problems.append(str(exc))
            continue
        except Exception as exc:
            problems.append(f"{source.__name__}: {exc}")
            continue
        answered = True
        if result and result != text:
            return result
    if not answered:
        raise Unavailable("; ".join(problems) or "no service answered")
    return None


def chinese_for(texts: list[str], *, enabled: bool = True) -> dict[str, str]:
    """Chinese for each of ``texts``, from the cache and then from the services.

    Only translated entries appear in the result. ``enabled=False`` restricts
    this to the cache and never touches the network, so turning the setting off
    keeps whatever has already been learned.
    """
    wanted = {t.strip() for t in texts if t and t.strip()}
    wanted = {t for t in wanted if len(t) <= MAX_SOURCE}
    if not wanted:
        return {}

    with connect() as db:
        holes = ",".join("?" * len(wanted))
        rows = db.execute(
            f"SELECT source, zh FROM translation WHERE source IN ({holes})",
            tuple(wanted)).fetchall()
    known = {row["source"]: row["zh"] for row in rows}
    out = {source: zh for source, zh in known.items() if zh}

    if enabled:
        deadline = time.monotonic() + BATCH_BUDGET
        fresh: list[tuple[str, str | None]] = []
        for text in sorted(wanted - set(known)):
            if time.monotonic() > deadline:
                log.info("Translation ran out of time; %d of %d done",
                         len(fresh), len(wanted - set(known)))
                break
            try:
                zh = translate_one(text)
            except Unavailable as exc:
                log.debug("No translation for %r: %s", text, exc)
                continue
            fresh.append((text, zh))
            if zh:
                out[text] = zh
        _remember(fresh)
    return out


def _remember(pairs: list[tuple[str, str | None]]) -> None:
    """Record translations, and the definite absence of one. Never raises."""
    if not pairs:
        return
    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    try:
        with connect() as db:
            db.executemany(
                "INSERT INTO translation (source, zh, fetched_at) VALUES (?, ?, ?) "
                "ON CONFLICT(source) DO UPDATE SET zh = excluded.zh, "
                "fetched_at = excluded.fetched_at",
                [(source, zh, stamp) for source, zh in pairs])
    except Exception:
        log.exception("Could not cache %d translation(s)", len(pairs))
