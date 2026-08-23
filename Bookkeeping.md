# Bookkeeping

A portable, single-user expense tracker whose input is a **photo of a receipt**.
Drop a Walmart receipt (or any store receipt) into the window; the app reads the
merchant, date, tax, total and every line item, assigns each line an expense
category, checks that the numbers add up, and puts the result in front of you for
review before it counts as part of the books.

It ships as **one Windows .exe that needs nothing installed** — no Python, no
runtime, no setup. Copy the file to another computer or a USB stick and it works
there, keeping its books beside itself.

This file is the whole documentation for the project. It is written so that a
future session — human or Claude — can pick the work up cold: it carries the
requirements, the research the design is based on, the architecture and the
reasoning behind it, the exact commands, what has been verified and what has
not, and the history of fixes that must not be regressed.

- **Location:** `D:\claude\Bookkeeping`
- **Version:** 1.1.0 (see `VERSION`)
- **Ships as:** `dist\Bookkeeping.exe` — one file, 26.8 MB, Windows x64, no installer
- **Stack:** Python 3.13 · FastAPI · SQLite · vanilla-JS UI in the browser · PyInstaller
- **Recognition:** Claude vision (`claude-opus-5`) primary, Tesseract OCR fallback
- **Locale:** USD-primary, English UI (currency stored per receipt)

---

## 1. Requirements as given

From the user, 2026-08-23:

1. Study how open-source bookkeeping / receipt applications are structured
   before designing anything.
2. Build an application that does **image recognition for bookkeeping**: upload
   images such as Walmart receipts, have the app recognise the content and
   categorise the expenses.
3. Keep everything under `D:\claude\Bookkeeping`.
4. Document everything in `Bookkeeping.md`.

Then, after the first version was delivered:

5. **It must be an executable (.exe) that runs on a PC, and it must be portable
   so other people can use it on their own computers.** This is what version
   1.1.0 adds; §7 and §10 explain what "portable" was taken to mean and what had
   to change to get there.
6. Keep this document updated in step with the code.

Three design questions were put to the user before any code was written, and
their answers are requirements, not defaults:

| Question | Answer |
| --- | --- |
| How should images be recognised? | **Both** engines, Claude vision primary, Tesseract as offline fallback |
| What form should the app take? | **Local web app** in the browser (Python backend, opened at localhost) |
| What locale? | **USD-primary, English UI** |

The .exe requirement does not overturn the second answer: the program is still a
local server rendering its UI in the browser. What changed is that it is now a
self-contained program that behaves like a desktop application — it starts, opens
its own window, and **closes itself when you are done** (§7, §10).

---

## 2. What the research found

Four projects were read before designing (GitHub, August 2026):

**[Receipt Wrangler](https://github.com/Receipt-Wrangler/receipt-wrangler-api)** — Go
API + desktop + mobile, the closest match to this brief. The parts worth copying,
confirmed by reading its `internal/models` and `internal/ai` package listings:

- A **receipt is its own entity, separate from the ledger figure**: `receipt.go`
  → `item.go` (line items) → `category.go`, plus `file_data.go` for the stored
  image and `comment.go` for notes. Nothing is posted to the books blindly.
- The **recognition engine is pluggable behind one interface**: `ocr_engine.go`
  and `ai_type.go` are enums, and `internal/ai/` holds `ai_client.go` with
  `gemini.go`, `open_ai.go` and `ollama.go` implementations behind it. There is
  even a `prompt.go` model, i.e. the extraction prompt is *data*, not a string
  literal.
- Scanning is **asynchronous** (an asynq/Redis job queue), because a vision call
  takes seconds and must not block the upload request.

**[Budget Lens](https://github.com/1oannis/budget-lens)** — Django + PostgreSQL +
OpenAI. Confirms the minimal-schema approach works: one central receipt entity,
images on the filesystem rather than in the database, API keys in configuration.

**[Firefly III](https://docs.firefly-iii.org/)** — the reference personal-finance
manager. Its **rules engine** is the model for categorisation: deterministic,
user-editable rules that run over transactions, rather than trusting a
classifier for everything.

**LLM-extraction write-ups** (LlamaIndex's receipt OCR service, the
"open-source invoice & receipt extraction with LLMs" articles) all converge on
the same pipeline: image → structured JSON **against a fixed schema** →
validation → categorisation → human review. The common failure they all warn
about is a model that returns well-formed JSON containing wrong numbers, which
only an arithmetic check catches.

### What this project took, and what it deliberately did not

| Taken | Left out, and why |
| --- | --- |
| Receipt → line items → category model | Multi-user groups, receipt splitting, permissions — single-user app |
| Pluggable engine interface (`Extractor`) | Redis / external job queue — a 2-thread pool is right for one user |
| Async scan + status polling | Email/IMAP receipt ingestion — out of scope |
| Rules-before-model categorisation | Full double-entry ledger with accounts — the brief is expense capture, not a general ledger |
| Schema-constrained extraction | OCR-then-LLM-over-text as the primary path — vision on the pixels reads receipts better |
| Human review before anything counts | Auto-posting confident readings — off by default (a switch exists) |
| Self-hosting simplicity | Docker/Postgres deployment — the deliverable is one .exe someone can double-click |

---

## 3. How it works

```
  Bookkeeping.exe
    ├─ pick a writable data folder (beside the .exe, else %LOCALAPPDATA%)
    ├─ pick a port (hand off to a copy already running instead of starting twice)
    ├─ start the server, open the browser
    └─ exit on Quit, or when no browser tab has said hello for 90s
                                      │
                      ┌───────────────▼──────────────────────────┐
  browser (drop) ────▶│ POST /api/receipts/upload                │
                      │  normalise image (EXIF, ≤1568px, PNG)    │
                      │  store file + sha256, insert receipt row │
                      │  return receipt id immediately           │
                      └───────────────┬──────────────────────────┘
                                      │ submit to 2-thread pool
                                      ▼
                      ┌──────────────────────────────────────────┐
                      │ app/pipeline.py  scan_now()              │
                      │  1. engines = build_engines(settings)    │
                      │  2. for each engine, in order:           │
                      │       Claude vision  ──▶ ExtractedReceipt│
                      │       Tesseract+parse ─▶ ExtractedReceipt│
                      │     first success wins; failures noted   │
                      │  3. categorise each line (rules → model) │
                      │  4. validate arithmetic → review flags   │
                      │  5. write receipt + line_items, status   │
                      └───────────────┬──────────────────────────┘
                                      │ browser polls every 1.5s while scanning
                                      ▼
                       needs_review ──review/edit──▶ confirmed ──▶ reports, CSV
                       failed ──────re-scan / hand entry──▶
```

### The recognition call

One request per receipt. The image goes in as an image content block; the reply
is constrained to the `ExtractedReceipt` JSON schema using the Anthropic SDK's
structured-output helper (`client.messages.parse(..., output_format=...)`), so
there is no prompt-and-pray JSON parsing. Categorisation for every line is asked
for in the *same* call, because the model already has the item names in front of
it and a second round trip would cost as much as the first while knowing less.

`app/extract/base.py` defines the schema as Pydantic models whose **field
descriptions are the prompt** — the JSON schema sent to the API is generated
from them, so those docstrings are load-bearing. The system prompt
(`app/extract/claude_vision.py`) covers the rules that matter for bookkeeping:
transcribe don't invent, keep printed abbreviations, exclude subtotal/tax/total
from `items`, negative amounts for coupons, `MM/DD/YY` → `YYYY-MM-DD`, and honest
self-reported confidence.

Deliberate omissions in that module, recorded so a later reader does not "fix"
them by accident:

- **No separate OCR step on the Claude path.** Sending pixels to the vision
  model preserves column alignment; OCR-then-LLM-over-text loses it.
- **No server-side refusal `fallbacks` parameter.** It only exists on the beta
  messages endpoint, which would mean giving up `messages.parse`, and receipt
  reading is not a refusal-prone category. A `refusal` stop reason is still
  handled explicitly rather than being mistaken for a malformed reply.

### Categorisation precedence

Implemented in `app/categorize.py`, strongest first:

1. **Manual** — anything the reviewer set by hand is never overwritten, not even
   by a later rule backfill.
2. **Description rules** — keyword/regex rules on the item name. These beat the
   model because they are auditable and repeatable: if `GREAT VALUE` means
   Groceries today, it means Groceries next month.
3. **Model** — the per-line category the vision model suggested, accepted only
   if it names a category that actually exists (an invented name is discarded).
4. **Merchant rules** — "everything from this shop is Groceries". Deliberately
   *below* the model: a merchant rule is a coarse safety net for lines nothing
   else recognised, and letting it outrank the model would relabel a specific,
   correct per-item judgement (`SOURDOUGH BOULE` → Dining) with a blanket store
   default. This was a real bug in 1.0.x — see §11.
5. **Default** — `Uncategorized`, so nothing silently vanishes from reports.

`category_source` on each line records which of these decided it, and the review
UI and the rule backfill both respect it.

59 keyword rules and 15 categories are seeded on first run (`app/db.py`,
`BUILTIN_RULES` / `BUILTIN_CATEGORIES`), tuned for US retail receipts —
`GREAT VALUE`, `MARKETSIDE`, `TIDE`, `DIAPER`, `UNLEADED`, plus merchant-level
defaults (`WALMART`, `COSTCO`, `CVS`, `SHELL`, …). They are seeded **only on a
fresh database**, so rules the user deletes stay deleted.

`Uncategorized` is deliberately withheld from the list offered to the model: it
is this application's marker for "nothing decided", and offering it invites the
model to use it as an easy out.

### Validation — the part that makes the numbers trustworthy

`app/validate.py`. The failure mode that matters is not a crash, it is a reading
that is well-formed and wrong: OCR turns `8.99` into `3.99` and the JSON is
perfectly valid. The only thing that catches that is checking the parts against
the printed total. Every check produces a human-readable message that goes
straight into the review pane, because *"line items sum to 43.71 but the
subtotal reads 47.09 (off by 3.38)"* is actionable and *"confidence: low"* is
not.

Checks: missing/non-positive total, missing/invalid/future/pre-2000 date, no line
items, items with no amount, **items vs. subtotal** (preferred) or **items + tax
vs. total**, subtotal + tax ≠ total, implausible tax (> 50 % of the total),
engine confidence < 0.6, and duplicate image (same SHA-256 already in the
books). Tolerance is 5 cents, because real receipts disagree with their own
arithmetic by a cent or two on weighted goods.

A flagged receipt is not blocked: the reviewer can confirm it anyway (some
receipts genuinely do not add up) and the flags stay attached as a record of why
it was questioned.

### Money

`app/money.py`. **Every amount is an integer number of cents**, everywhere,
including in the database. Floats are never used for money — `0.1 + 0.2 != 0.3`
in binary floating point, and a ledger that cannot make its own totals add up is
worthless. Decimal strings exist only at the edges (extractor output, the UI,
CSV). `to_cents` copes with what receipts actually print: `$12.34`, `1,234.56`,
`-2.00`, `(2.00)`, `3.5`, and returns `None` — not `0` — for absent values, so
"no tip line" is distinguishable from "a tip of zero".

### Process lifetime (what makes it feel like a program, not a server)

`app/runtime.py` + `app/launcher.py`. A browser-UI desktop app has a problem a
console app does not: the window the user closes is a *browser tab*, not the
program. The windowed .exe has no console to close either, so without help the
server would sit in the background forever and Task Manager would be the only
way out. Two mechanisms, both routed through `runtime.py` so the HTTP layer and
the launcher never import each other:

- **Quit** — the header's Quit button calls `POST /api/quit`; the launcher's
  watchdog thread sees the request and stops uvicorn.
- **Heartbeat** — the open page calls `POST /api/ping` every 10 s. After 90 s
  with no ping the app assumes the last tab is gone and exits. **A scan in
  flight suppresses this**, so closing the tab while a receipt is being read
  cannot throw the reading away.

Started from source (`run.bat`, which passes `--keep-alive`) the watchdog is off
and it behaves like an ordinary long-running server — but Quit still works,
because "is the desktop app" and "watchdog armed" are separate flags (§11).

---

## 4. Data model

SQLite, `data/bookkeeping.db`, schema in `app/db.py` (`PRAGMA user_version = 1`).

| Table | Purpose | Notes |
| --- | --- | --- |
| `receipt` | one row per receipt | status, image path + sha256, merchant (+ raw as printed), date, currency, subtotal/tax/tip/total in cents, payment method, header category, engine/model/confidence, `raw_text` + `raw_response` for audit, `review_flags` JSON, timing, tokens, `cost_usd`, `error` |
| `line_item` | purchased lines | description (+ `raw_description` = model's plain-English expansion), sku, quantity, unit price, amount, category, `category_source` (`rule`/`model`/`merchant`/`manual`/`default`), `is_discount`, `taxable` |
| `category` | expense categories | name (unique), colour chip, `is_builtin`, sort order |
| `category_rule` | keyword rules | field (`description`/`merchant`), match type (`contains`/`regex`), pattern, category, priority (lower runs first), enabled |
| `setting` | key/value settings | engine preference, API key, model, effort, Tesseract path, auto-confirm |

Statuses: `uploaded` → `scanning` → `needs_review` | `failed`, then
`confirmed` once a human signs it off. **Only `confirmed` receipts count in the
reports by default** — that is the whole point of the status.

Connections are short-lived and per-call (`with connect() as db`), WAL enabled,
because the scan worker writes from a different thread than the HTTP handler.

---

## 5. HTTP API

| Method & path | Purpose |
| --- | --- |
| `GET /` | the single-page UI |
| `GET /api/health` | version, receipt counts, data directory, `frozen`, `desktop`, `idle_timeout`, and `app: "bookkeeping"` (how a second .exe recognises its own kind on a port) |
| `POST /api/ping` | browser heartbeat (desktop lifetime) |
| `POST /api/quit` | stop the desktop app; reports `stopping: false` when run from source |
| `GET /api/engines` | which engines are available, and why not if not |
| `GET`/`PUT` `/api/settings` | settings; the API key is returned masked (`****1234`) |
| `POST /api/receipts/upload` | multipart, multiple files; normalises, stores, queues a scan each |
| `POST /api/receipts/manual` | blank receipt for hand entry (lost paper receipt) |
| `GET /api/receipts` | list; filters `status`, `q`, `date_from`, `date_to`, `category_id`, paging |
| `GET /api/receipts/{id}` | receipt with its line items |
| `PUT /api/receipts/{id}` | save reviewer edits; `confirm: true` also confirms |
| `POST /api/receipts/{id}/confirm` | confirm (refuses if total or date is missing) |
| `POST /api/receipts/{id}/rescan` | re-run the engines on the stored image |
| `DELETE /api/receipts/{id}` | delete receipt and image (`?keep_image=true` to keep the file) |
| `GET /api/receipts/{id}/image` | the stored PNG |
| `GET`/`POST`/`PUT`/`DELETE` `/api/categories[/{id}]` | category management; deleting moves lines to `Uncategorized` |
| `GET`/`POST`/`DELETE` `/api/rules[/{id}]` | rule management; invalid regex is refused with the regex error |
| `POST /api/rules/apply` | backfill rules over stored lines (skips `manual` lines and, unless `include_confirmed=true`, confirmed receipts) |
| `GET /api/model-categories` | the exact category list handed to the model |
| `GET /api/reports/summary` | totals, by category, by month, by merchant, pending count |
| `GET /api/export/items.csv` | one CSV row per line item |

Money crosses this boundary twice: as `*_cents` integers (the truth) and as
matching decimal strings for display. Requests may send either.

### The reports arithmetic that is easy to get wrong

Category figures come from **line items**, which do not include tax. To keep the
category breakdown summing to the money actually spent, the difference between a
receipt's total and its itemised lines is reported as its own **`Tax &
unitemised`** bucket instead of being quietly dropped. A test asserts that the
category buckets sum exactly to the receipt totals.

---

## 6. The UI

One page, four tabs, no framework and no CDN — nothing is fetched at runtime, so
the app works with no internet beyond the recognition call itself.

- **Receipts** — drag-and-drop zone, filters (status / search / date range), the
  receipt table, and a detail pane: the stored image beside editable header
  fields, the review flags, an editable line-item grid with a live "line items
  sum to …" readout, and actions (Save & confirm, Save draft, Re-scan, Show
  engine output, Delete).
- **Reports** — stat tiles, spend by category, spend by month, category and
  merchant tables, CSV export.
- **Categories & rules** — category list with usage counts, rule list, add/delete,
  and the "re-apply rules" backfill button.
- **Settings** — engine preference, API key, model, effort, Tesseract path,
  base URL, auto-confirm switch, and live engine availability.
- **Header** — engine-status pill, light/dark toggle, and (desktop build only) the
  Quit button, which shows a "Bookkeeping is closed" panel once the app stops.

Charts are hand-rolled HTML/CSS following the project's data-visualisation
rules: spend-by-category and spend-by-month are each **one measure, so one
hue** — the row label or the axis carries identity, and there is no legend and
no per-category colour cycling. Bars are baseline-anchored with 4px rounded
data-ends and a 2px surface gap; the monthly columns have hover tooltips and are
keyboard-focusable; both charts are accompanied by a table view. The bar colour
was validated with the palette validator against **both** surfaces (light
`#2a78d6` on `#fcfcfb`, dark `#3987e5` on `#1a1a19`): lightness band, chroma
floor and ≥ 3:1 contrast all pass. Dark mode is a selected set of steps, not an
inversion, and is remembered in `localStorage`.

---

## 7. Running it

### For anyone — the portable program

Double-click **`dist\Bookkeeping.exe`**. It opens in your default browser. To
give it to someone else, send them **that one file**; nothing else is needed —
no Python, no installer, no admin rights.

- **Where the books go.** A `data` folder **beside the .exe** (database, receipt
  images, `bookkeeping.log`). Move the .exe and its `data` folder together and
  the whole installation moves — a USB stick works. If the folder holding the
  .exe is read-only (`C:\Program Files`, a locked share, a CD), it falls back to
  `%LOCALAPPDATA%\Bookkeeping\data` instead of failing.
- **Closing it.** Click **Quit** in the header. If you just close the browser
  tab it notices within about 90 seconds and closes itself. It never leaves a
  server running that you cannot see.
- **Double-clicking it twice** opens a second browser tab pointing at the copy
  already running; it does not start a second server. If the port is taken by
  something else entirely, it moves to the next free one.
- **First launch takes a few seconds** — a one-file build unpacks itself to a
  temp folder before starting.
- **Windows may warn** that it is from an unknown publisher (SmartScreen), and
  some antivirus products are suspicious of PyInstaller executables in general.
  The build is not code-signed; signing needs a paid certificate. Choose "More
  info → Run anyway", or build it locally with `build.bat` if you would rather
  not trust a binary.

Command-line options (for a USB stick, an odd port, or debugging):

```bash
Bookkeeping.exe --help
Bookkeeping.exe --data-dir E:\receipts   # keep the books somewhere specific
Bookkeeping.exe --port 9000              # preferred port
Bookkeeping.exe --no-browser             # do not open a browser
Bookkeeping.exe --keep-alive             # never close on its own
Bookkeeping.exe --idle-timeout 300       # seconds of silence before closing
```

### First-run setup

The app starts with **no engine available** and says so in the header pill. To
turn on recognition, open **Settings**:

- **Claude vision (recommended):** paste an Anthropic API key. Model defaults to
  `claude-opus-5`, effort to `medium`. A receipt is roughly 1.5–2.5 k input
  tokens plus a few hundred output, i.e. **about $0.02–0.03 per receipt on Opus 5**
  (`claude-sonnet-5` is cheaper; `claude-haiku-4-5` cheaper still and noticeably
  less careful with faint print). The per-scan cost is stored and shown in the
  detail pane. The key is saved in `data\bookkeeping.db` on that machine only.
- **Offline OCR:** install the Tesseract binary
  (<https://github.com/UB-Mannheim/tesseract/wiki>) and, if it is not on `PATH`,
  put the full path to `tesseract.exe` in Settings. Not bundled — it is a
  separate ~50 MB program with its own installer.

`engine = auto` (the default) tries Claude first and falls back to Tesseract,
noting the fallback in the review flags. `claude` and `tesseract` pin one engine;
`manual` turns scanning off entirely.

### From source (development)

```bash
run.bat
```

Creates `.venv` and installs dependencies on first run, then starts the same
entry point the .exe uses, with `--keep-alive` so it does not close itself while
you are working on it. Manual equivalents from `D:\claude\Bookkeeping`:

```bash
py -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe bookkeeping.py --keep-alive
.venv\Scripts\python.exe -m uvicorn app.main:app --port 8765   # server only, no launcher
```

Tests (97, about 27 s):

```bash
.venv\Scripts\python.exe -m pytest tests/ -q
```

A synthetic Walmart-style receipt image with known values, for testing without a
real photo:

```bash
.venv\Scripts\python.exe tools\make_sample_receipt.py data\sample-receipt.png
```

### Building the .exe

```bash
build.bat
```

Installs PyInstaller if missing, regenerates `assets\icon.ico` if missing,
**copies the previous `dist\Bookkeeping.exe` to `dist\Bookkeeping.previous.exe`**,
then builds from `Bookkeeping.spec`. Takes about 40 seconds. The backup copy
matters: build output is deliberately not in Git, so if a new build turns out to
be broken, that file is the only way back to a working binary.

Things in the spec that must not be "tidied up":

- `app/static` and `VERSION` are bundled **under `app/`**, because the code looks
  for them at `sys._MEIPASS/app/...` (see `app/paths.py:resource_dir`).
- uvicorn loads its protocol and loop implementations *by string name* at
  runtime, so PyInstaller cannot see them — they are listed as hidden imports.
  Without them the server dies on the first request.
- `collect_all` is run for `anthropic`, `httpx`, `httpcore` and `certifi`: they
  carry data files (CA bundle, type metadata) that an import scan misses.
- `console=False`, which is why `app/launcher.py` never assumes `sys.stdout`
  exists and passes `log_config=None` to uvicorn (its default logging config
  attaches a stream handler to a `None` stdout and raises on the first record).

---

## 8. Files

| File | Lines | What it is |
| --- | --- | --- |
| `Bookkeeping.md` | this file | the whole documentation |
| `VERSION` | 1 | `1.1.0` |
| `requirements.txt` | 21 | pinned to the versions actually installed and tested |
| `bookkeeping.py` | 24 | the entry point PyInstaller freezes; three lines of logic on purpose |
| `run.bat` | 27 | run from source (development) |
| `build.bat` | 47 | build `dist\Bookkeeping.exe`, keeping the previous one |
| `Bookkeeping.spec` | 94 | PyInstaller build definition, with the reasoning inline |
| `make_icon.py` | 128 | draws `assets/icon.ico` (a receipt with a torn edge) |
| `assets/icon.ico` | — | 8 sizes, 16–256 px; generated but tracked, because the build needs it |
| `.gitignore` / `.gitattributes` | 16 / 1 | `data/`, `dist/`, `build/`, `.venv/`, caches ignored; `* -text` |
| `app/__init__.py` | 15 | package docstring / layout map |
| `app/main.py` | 910 | FastAPI app: every route, request models, serialisation, reports, CSV |
| `app/launcher.py` | 266 | desktop entry: data dir, port/hand-off, browser, logging, shutdown |
| `app/runtime.py` | 140 | process lifetime — heartbeat, quit, "a scan is running" |
| `app/paths.py` | 103 | frozen vs. source paths; writable data directory with fallback |
| `app/pipeline.py` | 300 | scan orchestration, thread pool, engine fallback, result storage |
| `app/db.py` | 307 | schema, seed categories and rules, connection handling |
| `app/categorize.py` | 131 | the precedence chain and rule matching |
| `app/validate.py` | 126 | arithmetic and sanity checks → review flags |
| `app/money.py` | 66 | integer-cent money conversion |
| `app/images.py` | 66 | upload normalisation (EXIF, downscale, PNG) |
| `app/settings_store.py` | 60 | settings read/write, secret masking |
| `app/extract/base.py` | 181 | `ExtractedReceipt` schema + `Extractor` interface |
| `app/extract/claude_vision.py` | 207 | Claude vision engine, pricing table, error mapping |
| `app/extract/tesseract_ocr.py` | 351 | Tesseract engine + heuristic receipt-text parser |
| `app/extract/__init__.py` | 80 | engine registry and fallback order |
| `app/static/index.html` | 231 | the single page |
| `app/static/app.js` | 753 | all front-end behaviour, no dependencies |
| `app/static/styles.css` | 310 | light/dark colour roles, layout, chart CSS |
| `tools/make_sample_receipt.py` | 121 | synthetic Walmart receipt generator with known values |
| `tests/conftest.py` | 61 | temp-directory database fixtures |
| `tests/test_units.py` | 271 | money, validation, precedence, OCR-text parsing |
| `tests/test_api.py` | 539 | end-to-end API tests with a stub engine |
| `tests/test_claude_engine.py` | 265 | Claude engine against a local mock of the Messages API |
| `tests/test_desktop.py` | 278 | data-dir fallback, ports and hand-off, lifetime, quit |

4 996 lines of Python plus 1 294 lines of front end. Not in version control:
`data/` (the user's books), `dist/` and `build/` (regenerable from the above).

---

## 9. What has and has not been verified

Verified on this machine (Windows 11, Python 3.13.11), 2026-08-23:

**Automated — 97 tests pass** (`pytest tests/ -q`, ~27 s):

- The **whole pipeline downstream of the model** — schema validation, rule and
  model categorisation, arithmetic flags, storage, duplicate detection, engine
  fallback, reports, CSV — end to end against a stub engine with a known reading.
- The **Claude request and reply handling** against a local HTTP stand-in for the
  Messages API: the image block is attached, the generated JSON schema and
  `effort` both arrive inside `output_config`, the allowed category list is
  passed, and 401/403/404/429/500 plus `refusal`/`max_tokens` all become
  actionable messages.
- **Desktop behaviour**: data-directory fallback when a location is unwritable,
  bundled-resource paths under a simulated `sys._MEIPASS`, port selection, the
  three hand-off cases (our app / a stranger / nothing listening), the idle
  watchdog including "do not exit mid-scan", and Quit with the watchdog off.

**By hand, against the built `Bookkeeping.exe` copied to an empty folder** (no
Python, no venv, no source):

- Starts, serves the UI and static files, creates `data\bookkeeping.db`,
  `data\images\` and `data\bookkeeping.log` beside itself; `/api/health` reports
  `frozen: true`.
- **Uploading a receipt works inside the bundle** — Pillow normalisation, the
  SHA-256, the background scan thread, the failure path with no engine
  configured, and serving the stored image back.
- **The Anthropic SDK path works inside the bundle**: pointed at a local mock of
  the API, the .exe produced a complete receipt — merchant, date, `68.46` total,
  5 categorised line items, 1 841/512 tokens, `$0.022005`, no review flags. This
  is the check that would have caught a missing `certifi` or pydantic metadata.
- **Idle exit**: with `--idle-timeout 15` it closed 15 s after start with no
  browser; with the default it closed after 90 s. Clean shutdown, exit code 0.
- **Hand-off**: a second launch logged *"already running … opening a tab
  instead"* and exited 0 in ~3 s without starting a second server.
- **Quit** stops the process even with `--keep-alive`; the browser shows the
  "Bookkeeping is closed" panel.
- **Heartbeat**: the open page posted `/api/ping` on schedule (17 requests over
  the session) and the Quit button ended it.
- An impossible `--data-dir Z:\nope\books` fell back to the folder beside the
  .exe and started normally.
- The UI itself: receipt list, detail pane (image loading, editable fields and
  items, live sum), reports (tiles, bars, monthly columns with correct
  proportions), categories/rules (15 categories, 59 rules), settings, both colour
  modes, clean console. `run.bat` also verified from source.

**Not verified, and honestly so:**

- **A real Claude vision call has never been made** — no API key was available in
  this environment. The request shape is verified against a mock; the *accuracy*
  of the reading on a real crumpled Walmart receipt is unmeasured. This is the
  first thing to check once a key is configured.
- **Tesseract has never run here** — the binary is not installed on this machine.
  Its text-parsing half (`parse_receipt_text`) is unit-tested against realistic
  OCR text, but the OCR half and the confidence calculation are untested in
  practice. Expect the offline path to need tuning against real output.
- **The .exe has only ever run on this machine.** It is a Windows x64 build; it
  will not run on macOS, Linux, or ARM Windows without rebuilding there. Nothing
  about another user's machine — Defender policy, missing VC++ runtime on an old
  Windows build, a locked-down profile — has been observed.
- **The `%LOCALAPPDATA%` leg of the data-directory fallback** is covered by a
  unit test with a simulated permission failure, not by a genuinely read-only
  folder: `icacls` would not apply a deny rule on this machine, so the real
  read-only case is untested.
- No load, concurrency or long-horizon testing. No non-USD receipt. No
  non-Latin-script receipt. The .exe is unsigned, so SmartScreen behaviour on a
  fresh machine is expected but unobserved.

---

## 10. Decisions worth not re-litigating

- **Review before the books.** A scan never lands as final. `auto_confirm_clean`
  exists but is off by default, because a reading whose arithmetic is fine can
  still have the wrong merchant or the wrong category.
- **Integer cents everywhere.** See §3. Do not introduce a float amount.
- **Description rules beat the model; merchant rules do not.** See §3 and §11.
- **The image is normalised once, at upload.** A re-scan must see exactly the
  same pixels the first scan saw, or the two readings are not comparable.
- **Items are replaced wholesale on save.** The review UI always sends the full
  list; diffing rows the user may have reordered or deleted is more code and
  more ways to lose a line.
- **A browser UI, not a native window.** Keeping the browser as the renderer is
  why the whole UI is 1 294 dependency-free lines and why the .exe is 27 MB
  rather than 150 MB. The cost is the tab-versus-program confusion, which the
  Quit button and heartbeat address directly.
- **One file, portable, data beside the .exe.** "Portable" was taken to mean
  *copy one file and it works, and it leaves nothing behind on a machine you
  borrowed*. Hence one-file mode, no registry, no installer, and books beside
  the binary rather than in `%APPDATA%`.
- **Windowed, not console.** A console window flashing behind a browser looks
  like a bug. The cost is that nothing can be printed, which is why there is a
  log file and a `MessageBoxW` for fatal startup errors.
- **No authentication, bound to 127.0.0.1.** A login on a single-user localhost
  app buys nothing. **Do not expose this process to a network without adding
  one** — anyone who can reach the port can read the books and the API key.
- **The API key is stored in plain text** in `data\bookkeeping.db`. Acceptable
  for a local single-user app; stated here so it is not a surprise, and it
  matters more now that the program is portable — *the database on a USB stick
  carries the key with it*. It is never sent to the browser (reads are masked)
  and `__clear__` removes it.
- **One process, one thread pool, no Redis.** Two scan workers is the right size
  for one person photographing receipts.
- **Tesseract is not bundled.** It is a separate ~50 MB program with its own
  installer and language data; bundling it would triple the download for a
  fallback most users will not turn on.

---

## 11. Fixes already made — do not regress these

1. **`connect()` must check `in_transaction` before COMMIT/ROLLBACK.**
   `sqlite3.executescript()` implicitly commits the pending transaction, so an
   unconditional `COMMIT` after the schema script raised "cannot rollback — no
   transaction is active" and *masked the real error* underneath. Every API test
   failed with a misleading message until this was fixed (`app/db.py`).
2. **The receipt `UPDATE` in `update_receipt` was missing its `receipt_id`
   binding** — 14 placeholders, 13 values (`app/main.py`).
3. **Listing filters must be qualified with the `r.` alias.** The listing joins
   `category`, so a bare `id IN (SELECT …)` in the search clause is ambiguous
   between `receipt.id` and `category.id` (`app/main.py`, `list_receipts`).
4. **A vanished receipt must not produce an unhandled promise rejection.**
   `selectReceipt` catches the 404, closes the pane, reloads the list and toasts
   — this happens for real when a receipt is deleted in another window
   (`app/static/app.js`).
5. **`_find_summary_amounts` checks most-specific first.** `SUBTOTAL` contains
   `TOTAL` and `TOTAL TAX` contains both, so naive substring order mislabels
   every one of them (`app/extract/tesseract_ocr.py`).
6. **httpx title-cases some header names on the wire** (`X-Api-Key`), so the mock
   API test lower-cases header keys before asserting (`tests/test_claude_engine.py`).
7. **Hand-entered receipts derive their header category from the biggest line**,
   the same way scanned ones do (`app/main.py`, `pipeline.dominant_category`).
8. **A blanket merchant rule must not outrank the model's per-item category.**
   Found while watching the frozen build read a receipt: `SOURDOUGH BOULE` came
   back as Groceries with `category_source: rule`, because the seeded
   `WALMART → Groceries` merchant rule was evaluated in the same pass as
   description rules — contradicting the "fallback for items nothing else
   matched" intent those rules were seeded with. `resolve_category` now runs
   description rules, then the model, then merchant rules, and the backfill in
   `/api/rules/apply` follows the same order (`app/categorize.py`,
   `app/main.py`).
9. **`--keep-alive` must not disable the Quit button.** It originally turned off
   one flag that meant both "watchdog armed" and "is the desktop app", so a
   windowed .exe started with `--keep-alive` could only be killed from Task
   Manager. `runtime.desktop` and `runtime.armed` are now separate
   (`app/runtime.py`, `app/launcher.py`, `app/static/app.js`).
10. **A windowed build has no `sys.stdout`.** uvicorn's default logging config
    attaches a stream handler to it and raises on the first log record; the
    launcher passes `log_config=None` and installs its own rotating file handler.
    For the same reason a fatal startup error is shown with `MessageBoxW`, not
    `print` (`app/launcher.py`).
11. **Bundled resources are found via `sys._MEIPASS`, never `__file__`
    arithmetic**, and the writable data directory is chosen *before* `app.db` is
    imported, because `db.py` resolves its paths at import time
    (`app/paths.py`, `app/launcher.py`).
12. **Inline data-URI favicon**, so a browser's automatic `/favicon.ico` request
    stops logging a 404 that looks like a fault (`app/static/index.html`).

---

## 12. Version control

One repository in the project folder, named after this document, exactly like the
sibling projects under `D:\claude`:

- `core.autocrlf=false` locally, plus `.gitattributes` with `* -text`, so files
  are stored byte for byte (this machine has `core.autocrlf=true` system-wide,
  which would otherwise rewrite every file to CRLF on the first checkout).
- `user.name = xu.jiamin`, `user.email = Xujiaming021101@163.com`, set per
  repository.
- Remote `mirror` → `D:\claude\repos\Bookkeeping.git` (a local bare second copy
  on disk; its `HEAD` was pointed at `main` so a clone checks out).
- Baseline **1.0.0**, tagged. Thereafter: a functional change adds **0.1**, a fix
  or docs change adds **0.0.1**, updated in `VERSION` in the same commit as the
  change and tagged `v<number>`. Tags so far: `v1.0.0`, `v1.0.1`, `v1.1.0`.
- Tracked: all source, `Bookkeeping.spec`, `build.bat`, `run.bat`, and
  `assets/icon.ico` (generated, but the build needs it).
- Ignored: `data/` (personal), `dist/` and `build/` (regenerable). **Because the
  .exe is not in Git, `build.bat` keeps the previous one as
  `dist\Bookkeeping.previous.exe` — that is the only way back from a bad build.**

No GitHub remote (`origin`) has been created yet — that is a publish-shaped step
and needs the user's say-so. When they want it: `gh repo create Bookkeeping
--private`, under the account `Micheal-Jiaming`, hyphenated name `Bookkeeping`.

---

## 13. Ideas not built

Ranked by how much they would improve the daily experience:

1. **Accuracy measurement.** A small fixture set of real receipt photos with
   hand-checked expected values, and a script that reports per-field accuracy.
   Without this, "the recognition is good" is an opinion.
2. **Learning from corrections.** When a reviewer re-categorises the same item
   name twice, offer to create the keyword rule. The rules table already
   supports it; only the suggestion is missing.
3. **Budgets and month-over-month deltas** on the reports page.
4. **A tray icon** instead of the heartbeat, so the running app is visible while
   no tab is open. Needs a GUI dependency (`pystray`), which is why it was not
   done for 1.1.0.
5. **PDF and emailed receipts** (the Anthropic API takes PDFs as document
   blocks, so the engine change is small).
6. **Multi-page or multi-receipt images** — currently one image is one receipt.
7. **Code signing**, to stop the SmartScreen warning. Needs a paid certificate.
8. **Batch scanning via the Message Batches API** at half price, for someone
   uploading a shoebox of receipts at once.

---

## 14. History

| Version | Date | Change |
| --- | --- | --- |
| 1.0.0 | 2026-08-23 | First version. Research of Receipt Wrangler / Budget Lens / Firefly III; Claude-vision + Tesseract engines behind one interface; rules-then-model categorisation; arithmetic validation and review workflow; FastAPI + SQLite backend; single-page UI with reports and CSV export; 69 tests. |
| 1.0.1 | 2026-08-23 | Inline data-URI favicon, so a browser's automatic `/favicon.ico` request stops logging a 404 that looks like a fault. |
| 1.1.0 | 2026-08-23 | **Portable Windows executable.** One-file PyInstaller build (`Bookkeeping.spec`, `build.bat`, generated icon); desktop launcher with a writable-data-directory search, port selection and single-instance hand-off, browser opening, file logging and a `MessageBoxW` for fatal startup errors; process lifetime via a Quit button and a browser heartbeat that will not exit mid-scan; `--data-dir/--port/--no-browser/--keep-alive/--idle-timeout` options. Also fixes merchant rules outranking the model's per-item category (§11.8). 97 tests. |
