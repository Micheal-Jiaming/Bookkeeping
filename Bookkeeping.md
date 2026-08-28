# Bookkeeping

A portable Windows program that reads **photos of receipts** and keeps the
expenses in order. Add a receipt image; it reads the merchant, date, tax, total
and every line item, assigns each line an expense category, checks that the
numbers add up, and puts the result in front of you for review before it counts
as part of the books.

It is a **normal desktop application** — one window, a menu bar, no browser and
no server — and it ships as **one .exe that needs nothing installed**. Copy that
file to another computer or a USB stick and it works there, keeping its books
beside itself.

This file is the whole documentation for the project. It is written so that a
future session — human or Claude — can pick the work up cold: it carries the
requirements, the research the design is based on, the architecture and the
reasoning behind it, the exact commands, what has been verified and what has
not, and the history of fixes that must not be regressed.

- **Location:** `D:\claude\Bookkeeping`
- **Version:** 1.3.0 (see `VERSION`)
- **Ships as:** `dist\Bookkeeping.exe` — one file, 29.2 MB, Windows x64, no installer
- **Stack:** Python 3.13 · **Tkinter** · SQLite · PyInstaller
- **Recognition:** Claude vision (`claude-opus-5`) primary; Windows' built-in OCR offline, needing no key or install; Tesseract optional
- **Locale:** USD-primary, English interface (currency stored per receipt)

---

## 1. Requirements as given

From the user, 2026-08-23, in the order they arrived:

1. Study how open-source bookkeeping / receipt applications are structured
   before designing anything.
2. Build an application that does **image recognition for bookkeeping**: add
   images such as Walmart receipts, have the app recognise the content and
   categorise the expenses.
3. Keep everything under `D:\claude\Bookkeeping`.
4. Document everything in `Bookkeeping.md`, and keep it updated in step with the
   code.
5. **It must be an executable (.exe) that runs on a PC, and portable, so other
   people can use it on their own computers.** (Delivered in 1.1.0.)
6. **It must have an interface like other applications — like the Pomodoro timer
   — not a web-based interface.** (Delivered in 1.2.0: the browser interface was
   removed and rebuilt as a Tkinter window. See §10 for what "like the Pomodoro
   timer" was taken to mean.)
7. **A real Walmart receipt failed to be recognised; fix it.** (2026-08-29,
   delivered in 1.3.0. The cause was not a parsing bug: neither engine was
   installed, so recognition had never actually run on this machine. Fixed by
   adding an engine that needs no installation — see §3 and §9.)

Three design questions were answered by the user before any code was written:

| Question | Answer |
| --- | --- |
| How should images be recognised? | **Both** engines, Claude vision primary, OCR as offline fallback. Since 1.3.0 the offline half is Windows' own OCR, with Tesseract optional. |
| What form should the app take? | A local app rather than a hosted service |
| What locale? | **USD-primary, English interface** |

Requirement 6 overrides the *rendering* half of that second answer — a browser
page was the wrong reading of "local app" — but nothing else: the program is
still a single-user application that keeps all its data on the machine it runs
on.

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
  even a `prompt.go` model, i.e. the extraction prompt is *data*, not a literal.
- Scanning is **asynchronous** (an asynq/Redis job queue), because a vision call
  takes seconds and must not block the interface.

**[Budget Lens](https://github.com/1oannis/budget-lens)** — Django + PostgreSQL +
OpenAI. Confirms the minimal-schema approach: one central receipt entity, images
on the filesystem rather than in the database.

**[Firefly III](https://docs.firefly-iii.org/)** — the reference personal-finance
manager. Its **rules engine** is the model for categorisation: deterministic,
user-editable rules over transactions rather than trusting a classifier.

**LLM-extraction write-ups** (LlamaIndex's receipt OCR service, the
"open-source invoice & receipt extraction with LLMs" articles) all converge on
the same pipeline: image → structured JSON **against a fixed schema** →
validation → categorisation → human review. The failure they all warn about is a
model returning well-formed JSON with wrong numbers, which only an arithmetic
check catches.

### A second round, for the offline engine (1.3.0)

Adding the Windows OCR engine raised a different problem — turning word bounding
boxes back into receipt rows — so a second search was run specifically for that.
What it produced, and what was used:

- **[docTR](https://github.com/mindee/doctr)** (`models/builder.py`,
  `_resolve_lines`) is the reference implementation for grouping OCR words into
  lines: sort by vertical centre, and start a new row when the gap to the running
  mean exceeds **half the median word height**. A *fraction of the text size*
  rather than a pixel count is what survives photos taken at different distances.
  **This is the constant `app/extract/windows_ocr.ROW_TOLERANCE` uses.** Its
  second phase splits a row at large horizontal gaps, which would be actively
  wrong here — a right-aligned price *is* a large horizontal gap — so only phase
  one was taken.
- **[bbox-align](https://github.com/doctor-entropy/bbox-align)** (MIT) tests
  whether one box's vertical centre falls inside another's bounds, and resolves
  lines as connected components rather than greedily. Worth remembering if a
  badly crumpled receipt ever defeats the current approach.
- **[receipt-parser-legacy](https://github.com/ReceiptManager/receipt-parser-legacy)**
  (854★, Apache-2.0) contributes the separator-tolerant amount pattern
  `\d+(\.\s?|,\s?|[^a-zA-Z\d])\d{2}`, which accepts a decimal point that OCR
  rendered as a space or a speck. The same idea is why `repair_amounts` rejoins
  `"3." "04"` before parsing.
- **[nzregs/receipt-api](https://github.com/nzregs/receipt-api)** (MIT, C#) is the
  only project found using this same Microsoft OCR engine. It snaps words to a
  line within "the height of the current line less 1/3 of the average" — a looser
  tolerance than docTR's, and unnecessary here.
- **[clovaai/cord](https://github.com/clovaai/cord)** is a dataset, not code:
  11k receipts with per-word boxes *and* line-item role labels. It is the right
  evaluation set if the heuristics are ever tuned seriously, rather than against
  the one receipt in `tests/`.

Two warnings from that research were worth acting on. First, **character-confusion
repair (`O`→`0`, `S`→`5`) must be gated to the amount column**; applied across the
page it destroys item names, which is why every pattern in `repair_amounts` is
anchored to a price. Second, **every published preprocessing recommendation is
about Tesseract or EasyOCR** — none of it is about `Windows.Media.Ocr`, which does
its own normalisation. Measuring rather than trusting it was the right call: see
§10, where greyscale, upscaling, autocontrast and sharpening all failed to help
and two of them hurt.

### What this project took, and what it deliberately did not

| Taken | Left out, and why |
| --- | --- |
| Receipt → line items → category model | Multi-user groups, splitting, permissions — single-user app |
| Pluggable engine interface (`Extractor`) | Redis / external job queue — a 2-thread pool is right for one user |
| Background scan with the interface staying live | Email/IMAP receipt ingestion — out of scope |
| Rules-before-model categorisation | Full double-entry ledger — the brief is expense capture, not a general ledger |
| Schema-constrained extraction | OCR-then-LLM-over-text as the primary path — vision on the pixels reads receipts better |
| Human review before anything counts | Auto-posting confident readings — off by default (a switch exists) |
| — | A web UI. Every project studied is a *server*; this is one person's program, and §10 explains why that changes the answer. |

---

## 3. How it works

```
  Bookkeeping.exe
    ├─ pick a writable data folder (beside the .exe, else %LOCALAPPDATA%)
    ├─ take the lock for that folder (one window per set of books)
    └─ open the window (app/ui) ─────────────────────────────┐
                                                            │
   Add receipt images / Paste image / Add by hand            │
                    │                                       │
                    ▼                                       │
   app/store.create_from_image()                            │
     normalise (EXIF, ≤1568px, PNG), sha256, insert row      │
                    │                                       │
                    ▼                                       │
   app/pipeline.submit_scan()  ── 2-thread pool ──┐          │
                                                  ▼          │
                    ┌──────────────────────────────────────┐ │
                    │ scan_now()                           │ │
                    │  1. engines = build_engines(settings)│ │
                    │  2. Claude vision ─▶ ExtractedReceipt│ │
                    │     else Windows OCR ─▶ "     "      │ │
                    │     else Tesseract  ─▶ "     "       │ │
                    │  3. categorise each line             │ │
                    │  4. validate arithmetic → flags      │ │
                    │  5. write receipt + line_items       │ │
                    └──────────────────────────────────────┘ │
                                                  │          │
   the window polls every 900ms while a scan runs ─┘◀─────────┘
                    │
                    ▼
   needs_review ──review & correct──▶ confirmed ──▶ reports, CSV
   failed ────────re-scan / hand entry──▶
```

**The database is the hand-off point between threads.** Tk widgets may only be
touched from the thread that created them, so a scan worker never calls back
into the interface: it writes its result, and the window polls with `after()`
while `pipeline.busy()`. That is why there are no queues or locks in the UI code.

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
(`app/extract/claude_vision.py`) covers what matters for bookkeeping: transcribe
don't invent, keep printed abbreviations, exclude subtotal/tax/total from
`items`, negative amounts for coupons, `MM/DD/YY` → `YYYY-MM-DD`, and honest
self-reported confidence.

Deliberate omissions in that module, recorded so a later reader does not "fix"
them by accident:

- **No separate OCR step on the Claude path.** Sending pixels to the vision
  model preserves column alignment; OCR-then-LLM-over-text loses it.
- **No server-side refusal `fallbacks` parameter.** It only exists on the beta
  messages endpoint, which would mean giving up `messages.parse`, and receipt
  reading is not a refusal-prone category. A `refusal` stop reason is still
  handled explicitly rather than mistaken for a malformed reply.

### The offline engines, and why there are two

Neither needs a key or a network. They share everything downstream of getting
characters off the image: both hand plain text to
`app/extract/receipt_text.parse_receipt_text`, which decides which lines are
purchases, which are the summary block, and which are noise.

- **Windows OCR** (`windows_ocr.py`) is the one that makes the application work
  out of the box, and it is why a portable copy handed to somebody else reads
  receipts on their machine with nothing configured. `Windows.Media.Ocr` ships
  with Windows 10 and 11; the `winrt-*` packages in `requirements.txt` are
  bindings only — no model is bundled and nothing is downloaded, because the
  recogniser is already part of the operating system.
- **Tesseract** (`tesseract_ocr.py`) stays as an explicit choice for anyone who
  has installed it. It is a separate ~60 MB install this application cannot ship,
  which is exactly why it cannot be the default.

The interesting work in the Windows engine is not the OCR call, it is **putting
the words back in order**. Windows groups text into its own lines, and on a
receipt photographed at a slight angle that grouping splits the page into a
column of descriptions followed by a column of amounts — so `result.text` reads
as every item name, then every price, with nothing connecting them. Unusable.
What it also returns is a bounding box per word, and re-grouping those by
vertical position rebuilds the real rows:

```
  raw result.text            group_rows() + repair_amounts()
  ---------------            -------------------------------
  BEDINABAG                  BEDINABAG 840021403470 29.72 x
  GV TWIST MOP               GV TWIST MOP 078742352910 10.88 x
  COKE                       COKE 049000050110 F 3.04 x
  ...                        ...
  29.72 x                    SUBTOTAL 141.94
  10.88 x                    TAXI 5.5000 % 7.50
  3.  04 x                   TOTAL 149.44
```

which is the shape the shared parser already knows how to read. Both steps are
pure functions, so `tests/test_windows_ocr.py` exercises the layout logic against
stored word boxes without needing an OCR language pack installed.

Because Windows OCR reports no per-word confidence, the confidence this engine
declares is derived from the receipt's own bookkeeping instead: a reading that
found a total, produced items, and whose amounts add up to the printed subtotal
got the layout right; one that did not, did not. It is capped at 0.5 either way,
so an offline reading never auto-confirms.

### Categorisation precedence

Implemented in `app/categorize.py`, strongest first. The same list is printed on
the Categories & rules page, because a user who cannot see why a rule did not
win would reasonably think it was broken:

1. **Manual** — anything the reviewer set by hand is never overwritten, not even
   by a later rule backfill.
2. **Description rules** — keyword/regex rules on the item name. These beat the
   model because they are auditable and repeatable: if `GREAT VALUE` means
   Groceries today, it means Groceries next month.
3. **Model** — the per-line category the vision model suggested, accepted only
   if it names a category that actually exists.
4. **Merchant rules** — "everything from this shop is Groceries". Deliberately
   *below* the model: a merchant rule is a coarse safety net for lines nothing
   else recognised, and letting it outrank the model would relabel a specific,
   correct per-item judgement (`SOURDOUGH BOULE` → Dining) with a blanket store
   default. This was a real bug in 1.0.x — see §11.
5. **Default** — `Uncategorized`, so nothing silently vanishes from reports.

`category_source` on each line records which of these decided it (shown in the
review pane as `rule` / `model` / `shop` / `you`), and the rule backfill respects
it.

59 keyword rules and 15 categories are seeded on first run (`app/db.py`), tuned
for US retail receipts — `GREAT VALUE`, `MARKETSIDE`, `TIDE`, `DIAPER`,
`UNLEADED`, plus merchant defaults (`WALMART`, `COSTCO`, `CVS`, `SHELL`, …). They
are seeded **only on a fresh database**, so rules the user deletes stay deleted.

`Uncategorized` is withheld from the list offered to the model: it is this
application's marker for "nothing decided", and offering it invites the model to
use it as an easy out.

### Validation — the part that makes the numbers trustworthy

`app/validate.py`. The failure mode that matters is not a crash, it is a reading
that is well-formed and wrong: OCR turns `8.99` into `3.99` and the JSON is
perfectly valid. The only thing that catches that is checking the parts against
the printed total. Every check produces a human-readable message shown in the
review pane, because *"line items sum to 43.71 but the subtotal reads 47.09 (off
by 3.38)"* is actionable and *"confidence: low"* is not.

Checks: missing/non-positive total, missing/invalid/future/pre-2000 date, no line
items, items with no amount, **items vs. subtotal** (preferred) or **items + tax
vs. total**, subtotal + tax ≠ total, implausible tax (> 50 % of the total),
engine confidence < 0.6, and duplicate image (same SHA-256 already in the books).
Tolerance is 5 cents, because real receipts disagree with their own arithmetic by
a cent or two on weighted goods.

A flagged receipt is not blocked: the reviewer can confirm it anyway (some
receipts genuinely do not add up) and the flags stay attached as the record of
why it was questioned.

### Money

`app/money.py`. **Every amount is an integer number of cents**, everywhere,
including in the database. Floats are never used for money — `0.1 + 0.2 != 0.3`
in binary floating point, and a ledger that cannot make its own totals add up is
worthless. Decimal strings exist only at the edges (extractor output, the
entry boxes, CSV). `to_cents` copes with what receipts actually print: `$12.34`,
`1,234.56`, `-2.00`, `(2.00)`, `3.5`, and returns `None` — not `0` — for absent
values, so "no tip line" is distinguishable from "a tip of zero".

---

## 4. Data model

SQLite, `data\bookkeeping.db`, schema in `app/db.py` (`PRAGMA user_version = 1`).

| Table | Purpose | Notes |
| --- | --- | --- |
| `receipt` | one row per receipt | status, image path + sha256, merchant (+ raw as printed), date, currency, subtotal/tax/tip/total in cents, payment method, header category, engine/model/confidence, `raw_text` + `raw_response` for audit, `review_flags` JSON, timing, tokens, `cost_usd`, `error` |
| `line_item` | purchased lines | description (+ `raw_description` = the model's plain-English expansion), sku, quantity, unit price, amount, category, `category_source`, `is_discount`, `taxable` |
| `category` | expense categories | name (unique), colour chip, `is_builtin`, sort order |
| `category_rule` | keyword rules | field (`description`/`merchant`), match type (`contains`/`regex`), pattern, category, priority (lower first), enabled |
| `setting` | key/value settings | engine preference, API key, model, effort, Tesseract path, auto-confirm, **plus the interface's own state**: theme, window geometry, last page |

The interface state lives in the same database on purpose: a portable copy then
carries its appearance along with its books, and there is no second config file
to keep in step.

Statuses: `uploaded` → `scanning` → `needs_review` | `failed`, then `confirmed`
once a human signs it off. **Only `confirmed` receipts count in the reports by
default** — that is the whole point of the status.

Connections are short-lived and per-call (`with connect() as db`), WAL enabled,
because the scan worker writes from a different thread than the interface.

---

## 5. Code layout

```
bookkeeping.py          the entry point PyInstaller freezes (3 lines of logic)
app/launcher.py         data folder, logging, single-instance lock, then the window
app/ui/                 the interface
    theme.py            palette, display scaling, ttk styling, shared widgets
    window.py           the window: chrome, menus, navigation, poll loop
    receipts.py         receipt list + review pane (the main workspace)
    reports.py          stat tiles, category and month charts, tables
    rules.py            categories and keyword rules
    settings_page.py    recognition settings
app/store.py            everything done to the books, as plain function calls
app/pipeline.py         scan orchestration and engine fallback
app/extract/            recognition engines behind one interface
    base.py             the Extractor contract + the Pydantic schema/prompt
    claude_vision.py    the vision model (primary)
    windows_ocr.py      Windows' own OCR + word-box row reconstruction
    tesseract_ocr.py    Tesseract, if the user installed it
    receipt_text.py     shared: receipt text → ExtractedReceipt
app/categorize.py       the precedence chain
app/validate.py         arithmetic checks → review flags
app/db.py               schema and seed data
app/paths.py            where things live, frozen (.exe) or from source
app/money.py            integer-cent money handling
app/images.py           upload normalisation
```

**The interface never touches SQL and the store never touches a widget.** That
separation is what allowed the entire interface to be replaced in 1.2.0 without
rewriting the logic underneath — `app/store.py` was lifted out of the old HTTP
layer unchanged in behaviour, and its tests were re-pointed from HTTP calls to
function calls. Keep it that way: if a page needs a new query, add a function to
`store.py`.

Every page is a class with a `frame` attribute and a `refresh()` method; the
window packs and refreshes them and knows nothing else about them.

---

## 6. The interface

One window, four pages, a menu bar (File / View / Help) and a status bar.

- **Receipts** — the workspace. Toolbar (*Add receipt images*, *Paste image*,
  *Add by hand*, status filter, search), the receipt list on the left, and the
  **review pane** on the right: the stored image beside every extracted field,
  the arithmetic complaints in plain words, an editable line-item grid with a
  live "Lines: 60.59 (off by 4.00)" readout, and the actions — *Save & confirm*,
  *Save draft*, *Re-scan*, *Output* (exactly what the engine returned), *Delete*.
- **Reports** — four stat tiles, spend by category, spend by month, a top-merchant
  table, and a note explaining the `Tax & unitemised` bucket. Range presets plus
  explicit from/to dates, and CSV export.
- **Categories & rules** — categories with usage counts, the rule list, add and
  delete, the precedence explanation, and the backfill button.
- **Settings** — engine, API key (masked), model, effort, base URL, offline OCR
  language, Tesseract path, auto-confirm, live engine status, what a scan costs,
  and where the data folder is.

Keyboard: `Ctrl+O` add images, `Ctrl+V` paste an image from the clipboard,
`Ctrl+N` add by hand, `Ctrl+1..4` pages, `F5` refresh, `Ctrl+Q` quit.

**Clipboard paste** is worth calling out: `Win+Shift+S`, snip a receipt on
screen, `Ctrl+V` in Bookkeeping. It also accepts files copied in Explorer.

### Look and scaling

A theme is a whole palette in a dict, applied by rebuilding the widgets — the
same approach the Pomodoro timer uses, because Tk has no real theming. Dark is
the default; light is the other, chosen for its own surface rather than derived
by inverting.

The accent is the chart bar colour, and it was validated against **both**
surfaces (lightness band, chroma floor, ≥ 3:1 contrast): light `#2a78d6` on
`#fcfcfb`, dark `#3987e5` on `#1a1a19`. Substituting a prettier blue means
re-running that check.

Charts follow the same data-visualisation rules as before: each shows **one
measure, so one hue**; the row label (category) or axis (month) carries identity,
so there is no legend and no colour cycling; bars are baseline-anchored with
rounded data-ends; values are direct-labelled (every category row, and the
tallest month); and each chart has a table beside it with the same numbers.

**Every pixel measurement goes through `Theme.px()`.** Tk sizes fonts in points,
so text follows the display automatically, but Treeview column widths, canvas
heights, image thumbnails and wrap widths do not. This machine's panel is
3840×2160 at 150%, where a window sized in raw pixels comes out half the
intended size with its content clipped — which is exactly what the first build
did (§11.13).

---

## 7. Running it

### For anyone — the program

Double-click **`dist\Bookkeeping.exe`**. A window opens. To give it to someone
else, send them **that one file**: no Python, no installer, no admin rights.

- **Where the books go.** A `data` folder **beside the .exe** (database, receipt
  images, `bookkeeping.log`). Move the .exe and its `data` folder together and
  the whole installation moves — a USB stick works. If the folder holding the
  .exe is read-only (`C:\Program Files`, a locked share), it falls back to
  `%LOCALAPPDATA%\Bookkeeping\data`.
- **Closing it** is the window's X button, `Ctrl+Q`, or File → Exit. If a scan is
  still running it asks first.
- **Starting it twice** for the same books is refused with a dialog that says so;
  two portable copies with their own `data` folders both run happily.
- **First launch takes a few seconds** — a one-file build unpacks itself to a
  temp folder before starting.
- **Windows may warn** that it is from an unknown publisher (SmartScreen), and
  some antivirus products are suspicious of PyInstaller executables in general.
  The build is not code-signed; signing needs a paid certificate. Choose "More
  info → Run anyway", or build it locally with `build.bat`.

Command line, for a USB stick or debugging:

```bash
Bookkeeping.exe --help
Bookkeeping.exe --data-dir E:\receipts     # keep the books somewhere specific
Bookkeeping.exe --allow-second-window      # open a second window on the same books
Bookkeeping.exe --version
```

### First-run setup

**Nothing has to be configured.** On any Windows 10 or 11 machine with a language
pack installed the app can read a receipt the moment it opens, using the OCR built
into Windows; the header names the engines it found. For a better reading, open
**Settings**:

- **Claude vision (recommended):** paste an Anthropic API key. Model defaults to
  `claude-opus-5`, effort to `medium`. Cost depends on how many lines the receipt
  has, because the reply grows with them: measured on a real 24-line Walmart
  receipt, 2 208 input + 1 487 output tokens = **$0.048 on Opus 5**; a short
  receipt is nearer $0.012. Sonnet is about 60 % of that, Haiku about a fifth.
  The Settings page shows both ends of the range per model, and every scan
  records what it actually cost, shown in the review pane. The key is stored in
  `data\bookkeeping.db` on that machine only, and is never displayed back in
  full.
- **Windows OCR:** nothing to install. If the *Offline OCR language* dropdown is
  empty, or the engine reports no language pack, add one under **Settings → Time
  & language → Language & region** in Windows itself. Leave the dropdown on
  *Automatic* and it prefers an English recogniser, which is what US receipts
  need — the first language in a user's Windows profile is often not English,
  and reading a US receipt with the German model goes badly.
- **Tesseract (optional second offline reader):** install the binary
  (<https://github.com/UB-Mannheim/tesseract/wiki>) and, if it is not on `PATH`,
  point Settings at `tesseract.exe`. Not bundled — it is a separate ~60 MB
  program with its own installer, which is precisely why it is not the default.

`engine = auto` (the default) tries Claude, then Windows OCR, then Tesseract,
noting any fallback in the review flags. `claude`, `windows` and `tesseract` pin
one engine; `manual` turns scanning off entirely.

**What the offline engines cannot do is expand an abbreviation.** Claude turns
`CLX PLNGR` into "Clorox toilet plunger" and categorises from that; OCR only sees
`CLX PLNGR`. The keyword rules close some of the gap (§11.22) but not all of it,
so an offline reading leaves more lines uncategorised. That is the honest
trade-off for needing no key.

### From source (development)

```bash
run.bat
```

Creates `.venv` and installs dependencies on first run, then starts the same
entry point the .exe uses; any arguments are passed through. Manual equivalent:

```bash
py -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe bookkeeping.py
```

Tests (134, about 41 s — 28 of them drive the real window):

```bash
.venv\Scripts\python.exe -m pytest tests/ -q
```

A synthetic Walmart-style receipt image with known values, for testing without a
real photo:

```bash
.venv\Scripts\python.exe tools\make_sample_receipt.py sample-receipt.png
```

### Building the .exe

```bash
build.bat
```

Installs PyInstaller if missing, regenerates `assets\icon.ico` if missing,
**copies the previous `dist\Bookkeeping.exe` to `dist\Bookkeeping.previous.exe`**,
then builds from `Bookkeeping.spec`. About 90 seconds. The backup copy matters:
build output is not in Git, so if a new build is broken that file is the only way
back.

Things in the spec that must not be "tidied up":

- **`tkinter` must not be in `excludes`.** It was there while the interface was a
  web page; leaving it once the interface became a Tk window produces an .exe
  that starts and dies with no window and no message.
- `VERSION` and `icon.ico` are bundled **under `app/`**, because the code looks
  for them at `sys._MEIPASS/app/...` (see `app/paths.py:resource_dir`).
- `collect_all` is run for `anthropic`, `httpx`, `httpcore` and `certifi`: they
  carry data files (CA bundle, type metadata) an import scan misses, and without
  them the .exe starts fine but cannot make an API call.
- `console=False`, which is why `app/launcher.py` never assumes `sys.stdout`
  exists and reports fatal errors with a message box.

### The development tools

Four scripts in `tools\` exist because of specific things that went wrong. They
are part of the project, not scratch work: a future session that needs to check
the build, look at the interface, or exercise the vision path should reach for
these rather than write them again.

```bash
py tools\verify_exe.py                      # does the BUILD work?
py tools\screenshot_pages.py --out shots    # what does it LOOK like?
py tools\seed_demo.py --data-dir C:\temp\demo --with-image
py tools\mock_anthropic.py --port 8899      # a fake API, for testing without a key
py tools\make_sample_receipt.py out.png     # a synthetic receipt image
```

- **`verify_exe.py`** — the test suite proves the *code* is right; only this
  proves the *build* is. It copies the .exe to an empty folder, waits for the
  window, checks the data folder is created, screenshots it, confirms a second
  launch is refused and exits, closes the window and confirms the process ends,
  then runs `--version` to prove the lock was released. Everything it checks has
  been broken at least once. It also carries the hard-won detail that a one-file
  PyInstaller build runs the app in a **child** process, so looking for the
  window by the launched pid finds nothing (§11.19), and it fetches the process
  table in a single call rather than shelling out per node, because the slow
  version produced a false failure (§11.24).

  **After running it, read `data\bookkeeping.log` in the workspace it used**
  (pass `--keep` to stop it being deleted). The app logs which engines it found
  on startup, and that line is the only way to confirm a frozen build can really
  reach Windows OCR — an import that works from source proves nothing about what
  PyInstaller bundled.
- **`screenshot_pages.py`** — a native window cannot be inspected the way a web
  page can. `tests/test_ui.py` proves the interface *holds together*; only a
  picture shows that it *looks* right, and three real faults were visible in no
  other way (§11.12–§11.14). Note the two traps it encodes: `time.sleep` does not
  run Tk's event loop, so `after()` work (the debounced chart redraw) never
  happens unless you pump it; and `ImageGrab` captures the screen, not the
  window, so the window has to be raised first.
- **`seed_demo.py`** — demo books for screenshots and for trying the reports on
  something other than an empty database. `--data-dir` is required rather than
  defaulted, and it refuses books that already hold receipts, precisely so it can
  never be pointed at somebody's real ones by accident.
- **`mock_anthropic.py`** — serves the real 24-line Walmart reading from
  `tests/test_real_receipt.py` as a proper Messages API envelope, so
  `messages.parse` validates it against the generated schema exactly as in
  production. Point the app's Settings at `http://127.0.0.1:8899` with any
  non-empty key. This is how the vision path was exercised *inside the frozen
  .exe* without a key.

**Housekeeping that bit once:** these are all servers or long-lived processes.
A `mock_anthropic.py` instance was once left listening on port 8899 for two and
a half hours after the test that needed it had finished. Stop background helpers
when done, and check with
`Get-NetTCPConnection -State Listen | Where-Object LocalPort -ge 8760`.

---

## 8. Files

| File | Lines | What it is |
| --- | --- | --- |
| `Bookkeeping.md` | this file | the whole documentation |
| `VERSION` | 1 | `1.3.0` |
| `requirements.txt` | 31 | pinned to the versions actually installed and tested |
| `bookkeeping.py` | 24 | the entry point PyInstaller freezes |
| `run.bat` | 27 | run from source (development) |
| `build.bat` | 47 | build `dist\Bookkeeping.exe`, keeping the previous one |
| `Bookkeeping.spec` | 93 | PyInstaller build definition, with the reasoning inline |
| `make_icon.py` | 128 | draws `assets/icon.ico` (a receipt with a torn edge) |
| `assets/icon.ico` | — | 8 sizes, 16–256 px; generated but tracked, because the build needs it |
| `.gitignore` / `.gitattributes` | 31 / 1 | `data/`, `dist/`, `build/`, `.venv/`, caches and **all receipt images** ignored; `* -text` |
| `app/__init__.py` | 22 | package docstring / layout map |
| `app/store.py` | 736 | the service layer: receipts, categories, rules, reports, CSV |
| `app/ui/window.py` | 532 | the window: chrome, menus, navigation, poll loop, dialogs |
| `app/ui/receipts.py` | 645 | receipt list and the review pane |
| `app/ui/reports.py` | 355 | tiles, hand-drawn canvas charts, merchant table |
| `app/ui/theme.py` | 292 | palette, display scaling, ttk styling, shared widgets |
| `app/ui/settings_page.py` | 289 | recognition settings |
| `app/ui/rules.py` | 240 | categories and keyword rules |
| `app/ui/__init__.py` | 18 | the interface package's map |
| `app/pipeline.py` | 301 | scan orchestration, thread pool, engine fallback |
| `app/db.py` | 459 | schema, seed categories and rules, migrations, connections |
| `app/launcher.py` | 157 | data folder, logging, single-instance lock, error reporting |
| `app/categorize.py` | 131 | the precedence chain and rule matching |
| `app/validate.py` | 126 | arithmetic and sanity checks → review flags |
| `app/paths.py` | 103 | frozen vs. source paths; writable data folder with fallback |
| `app/money.py` | 66 | integer-cent money conversion |
| `app/images.py` | 66 | image normalisation (EXIF, downscale, PNG) |
| `app/settings_store.py` | 60 | settings read/write, secret masking |
| `app/extract/receipt_text.py` | 269 | shared: receipt text → `ExtractedReceipt` |
| `app/extract/windows_ocr.py` | 257 | Windows OCR engine + word-box row reconstruction |
| `app/extract/claude_vision.py` | 207 | Claude vision engine, pricing table, error mapping |
| `app/extract/base.py` | 181 | `ExtractedReceipt` schema + `Extractor` interface |
| `app/extract/tesseract_ocr.py` | 112 | Tesseract engine (parser now shared) |
| `app/extract/__init__.py` | 89 | engine registry and fallback order |
| `tools/make_sample_receipt.py` | 121 | synthetic Walmart receipt with known values |
| `tools/verify_exe.py` | 218 | drives the built .exe and checks it behaves (§7) |
| `tools/seed_demo.py` | 139 | fills a set of books with plausible demo receipts |
| `tools/mock_anthropic.py` | 135 | stand-in for the Messages API, for testing without a key |
| `tools/screenshot_pages.py` | 114 | opens the window and screenshots every page |
| `tests/test_store.py` | 646 | the service layer, end to end with a stub engine |
| `tests/test_ui.py` | 471 | builds the real window and drives it |
| `tests/test_units.py` | 295 | money, validation, precedence, OCR-text parsing |
| `tests/test_real_receipt.py` | 291 | the one real receipt this project has been tested against |
| `tests/test_claude_engine.py` | 265 | Claude engine against a local mock of the Messages API |
| `tests/test_windows_ocr.py` | 215 | row reconstruction, amount repairs, the real reading |
| `tests/test_desktop.py` | 143 | data-folder fallback, the single-instance lock, arguments |
| `tests/conftest.py` | 55 | temp-directory database fixtures |
| `tests/fixtures/walmart_ocr_words.json` | — | the 161 words Windows OCR really returned for the real receipt |

8 973 lines of Python. Not in version control: `data/` (the user's books),
`dist/` and `build/` (regenerable from the above).

---

## 9. What has and has not been verified

Verified on this machine (Windows 11, Python 3.13.11, 3840×2160 at 150 %),
2026-08-23 and again 2026-08-29 for 1.3.0:

**Automated — 161 tests pass** (`pytest tests/ -q`, ~54 s):

- The **service layer** end to end against a stub engine with a known reading:
  schema validation, rule and model categorisation, arithmetic flags, storage,
  duplicate detection, engine fallback, listing filters, reports, CSV.
- The **Claude request and reply handling** against a local HTTP stand-in for the
  Messages API: the image block is attached, the generated JSON schema and
  `effort` both arrive inside `output_config`, the allowed category list is
  passed, and 401/403/404/429/500 plus `refusal`/`max_tokens` all become
  actionable messages.
- **The real window, driven by 28 tests**: every page builds; the theme switch
  rebuilds it; editing the review pane and saving reaches the database;
  confirming without a date or total is refused; the running line total flags a
  mismatch; a line can be removed; deleting works; list filtering and search
  work; an image is loaded into the pane; flags are rendered; the charts draw
  (and are asserted to span the canvas, not the 40-pixel stub the layout bug
  produced); categories and rules can be added, backfilled and deleted; settings
  round-trip, and the API key is never echoed back.
- **Desktop behaviour**: data-folder fallback when a location is unwritable,
  bundled-resource paths under a simulated `sys._MEIPASS`, the single-instance
  lock (same folder refused, two folders both allowed, released on exit), and
  logging to the data folder.
- **Windows OCR layout handling** (`tests/test_windows_ocr.py`, 22 tests): rows
  are rebuilt top-to-bottom and left-to-right from the stored word boxes; the
  grouping is proved scale-invariant (the same words at 4× the size group
  identically) and tolerant of a row that drifts downwards across the page; each
  of the three amount repairs is checked, and checked *not* to fire inside an item
  description; and the reading of the real receipt is asserted end to end.

**By hand, against the built `Bookkeeping.exe` copied to an empty folder** (no
Python, no venv, no source) — re-run for 1.3.0 with `tools\verify_exe.py`:

- It opens a window titled `Bookkeeping 1.3.0`, class `TkTopLevel`, with the
  receipt icon in the title bar and the File/View/Help menus.
- It creates `data\bookkeeping.db` (60 KB, seeded) and `data\bookkeeping.log`
  beside itself.
- **The frozen build really reaches Windows OCR.** Its own log records
  `Engine windows ready — Windows OCR (en-GB)`, which is the only way to know
  PyInstaller bundled the `winrt` bindings correctly — an import that works from
  source proves nothing about the .exe. This is what `_log_engines()` in
  `app/ui/window.py` exists for.
- A **second launch is refused** with an "Already running" dialog and exits 0.
- **Closing the window exits cleanly** (code 0), and `--version` afterwards
  prints `1.3.0` — proving the lock was released.
- Startup measured at **3.4–3.6 s** to a visible window, over three runs.
- The four pages and both themes were screenshotted from the running program and
  inspected: list, review pane with the image and a real arithmetic flag, charts
  with correct proportions and value labels, 15 categories and 59 rules, and the
  settings controls.

**One real receipt, read and measured** (2026-08-23). The user photographed a
Walmart receipt — 24 printed lines, real abbreviations (`GV TWIST MOP`,
`HS SH CLS8.5`, `EQJELLUBE8OZ`), three 5-cent bottle deposits, two identical
`FRENCH BREAD` lines, and the top of the receipt out of frame so the store name
and date are genuinely missing. It is now a permanent fixture,
`tests/test_real_receipt.py`, and it is trustworthy because the receipt checks
itself four ways and all four hold:

    items sum                  == printed subtotal 141.94
    subtotal + tax             == printed total    149.44
    cash - total + rounding    == printed change     50.60
    printed lines - 3 deposits == "# ITEMS SOLD 21"

What that run established, pushing the real reading through the real pipeline:

- **Reading accuracy on this receipt: all 24 lines, every amount, and all four
  totals correct.** A single misread digit would have broken one of the checks.
- **Categorisation: 24 of 24 lines land where a person would put them** — after
  the fix below. 17 decided by keyword rule, 7 by the model.
- **The plain-English expansion earns its keep.** Five lines whose printed names
  are unreadable (`HS SH CLS8.5`, `AIM TP 5.5OZ`, `DWN EZS 22Z`, `GAIN`,
  `ME DEPOSIT`) are categorised by keyword rules that only match because the
  model expanded the abbreviation into `…shampoo`, `…toothpaste`, `…dish soap`,
  `…detergent`, `bottle deposit`.
- **It found a real bug** (§11.18): the seeded `GREAT VALUE` rule filed a mop, a
  bottle of ammonia and a pack of sponges as Groceries — $16.00 of a $141.94
  basket in the wrong category.
- **Validation behaved exactly as designed**: one flag, "No purchase date was
  found", and *no* arithmetic complaint, because the reading really did add up.
  The receipt could not be confirmed until a date was supplied.
- **The reports attributed every cent**: Household $76.06, Health & Pharmacy
  $28.12, Groceries $27.19, Personal Care $10.42, Tax & unitemised $7.50, Fees &
  Taxes $0.15 — summing exactly to the $149.44 spent.

**The same receipt, read offline by Windows OCR** (2026-08-29). This time the
photograph itself went through the whole application — normalised by
`app/images.py` to 1176×1568 PNG, stored, and scanned by `pipeline.scan_now`.
Measured against the fixture above:

| | Read | Truth |
| --- | --- | --- |
| Subtotal / Tax / Total | **141.94 / 7.50 / 149.44** | exact |
| Payment method | `CASH` | correct |
| Merchant, date | `None`, `None` | correct — they are not in frame |
| Line items | **20** of 24 | 4 lost |
| Item amounts | 22 of 24 recoverable, 20 kept | — |
| Categorised | 12 of 20 | — |

The four losses are genuine OCR failures, and worth naming so nobody hunts for a
parser bug that is not there: two lines (`DOVE BW 11OZ`, the second `GV 1G SP`)
had their descriptions dropped entirely; `GV AMMONIA` lost the leading `2.` of
its amount; and `DWN EZS 22Z` was misread as 3.33 instead of 3.83. Together they
leave the items summing to 131.61 against a printed subtotal of 141.94.

**That gap is reported, not hidden** — "Line items sum to 131.61 but the subtotal
reads 141.94 (off by 10.33)" — which is the designed behaviour. A missing leading
digit *could* be guessed at from the residual, and deliberately is not: a
plausible wrong number in a set of books is worse than an obvious hole, and the
review pane exists to fill holes.

**The 1568 px cap was measured, and it helps.** An earlier version of this
document listed the cap as an untested risk — "whether the item names survive it
is unmeasured". They do, and more than that: feeding Windows OCR the original
1280×1706 photograph instead of the 1176×1568 normalised copy makes the reading
*worse*. It loses the `TOTAL 149.44` row altogether and mis-parses enough numbers
that the items sum to 253.95 instead of 131.61. Re-encoding is not the cause —
the original downscaled to a 1568 long edge reads identically to the app's own
copy — so it is the resolution itself. `app/images.py` normalises for the
Anthropic API's benefit; it turns out to earn its place twice.

Categorisation is the weaker half offline, for a structural reason: the vision
model expands `CLX PLNGR` to "Clorox toilet plunger" and categorises from that,
while OCR sees only `CLX PLNGR`. Adding abbreviation and brand rules (§11.22)
took this from 3 of 20 to 12 of 20, and correctly set the receipt's own category
to Household. The 8 that remain (`BEDINABAG`, `EQJELLUBE8OZ`, `HS SH CLS8.5` …)
are not guessable from the printed text alone.

**Not verified, and honestly so:**

- **That reading was made by Claude, but not by the app.** No API key exists in
  this environment, so the transcription above is this assistant reading the
  photograph directly, encoded into the schema and replayed through the real
  engine against a local stand-in endpoint. Every part is real except the network
  call. What remains untested is the join: a live key, a real HTTP round trip,
  and what the model makes of *its own* view of the pixels rather than a reading
  handed to it.
- **Tesseract has never run here** — the binary is not installed. Its
  text-parsing half is now the shared `receipt_text.py`, which is unit-tested and
  exercised hard by the Windows engine, but Tesseract's own OCR call and its
  confidence calculation are untested in practice.
- **Windows OCR has been measured on exactly one receipt, in one language.**
  20 of 24 lines is this photograph's number, not a general accuracy figure. A
  differently-lit, more crumpled or non-English receipt is unmeasured, and the
  row-grouping tolerance has never been tuned against a corpus — `clovaai/cord`
  (§2) is the dataset for that if it is ever worth doing.
- **The .exe has only ever run on this machine**, at one DPI setting (150 %). It
  is a Windows x64 build; macOS, Linux and ARM Windows would need rebuilding
  there. Nothing about another user's machine — Defender policy, a 100 % or 200 %
  display, an old Windows build — has been observed.
- **The `%LOCALAPPDATA%` leg of the data-folder fallback** is covered by a unit
  test with a simulated permission failure, not by a genuinely read-only folder:
  `icacls` would not apply a deny rule on this machine.
- The clipboard-paste path is exercised only by hand-reasoning about
  `ImageGrab.grabclipboard()`; there is no automated test for it.
- No load or long-horizon testing; no non-USD receipt; no non-Latin-script
  receipt. The .exe is unsigned, so SmartScreen behaviour on a fresh machine is
  expected but unobserved.

### Where to pick up

The state as of 1.3.0, for whoever reads this next:

- **The application works with nothing configured.** That is new in 1.3.0 and is
  the single most important fact here: before it, a fresh copy could not read a
  receipt at all without an API key or a Tesseract install, and the first real
  receipt it was ever given failed with four red flags and no data.
- **The two open verifications**, in order of value:
  1. **A live API call.** Put a real key in Settings, add the receipt photo, and
     compare against the fixture in `tests/test_real_receipt.py` (merchant
     `null`, date `null`, subtotal 141.94, tax 7.50, total 149.44, 24 lines). If
     it matches, the last real gap closes. If it does not, the difference is the
     most interesting data this project can produce.
  2. **A second real receipt through the offline engine.** Everything known about
     Windows OCR accuracy comes from one photograph. A restaurant bill, a fuel
     receipt or a faded one would each say something the current fixture cannot.
- **The next feature worth building** is in §13: more real receipts. One found a
  mis-categorisation bug within minutes; another would likely find its own.
- **Do not** re-add a store-brand keyword rule (§11.18), reintroduce a web
  interface (§10), "simplify" the spec's excludes (§11.11), preprocess the image
  before Windows OCR (§10 — it was measured, and it makes the reading worse), or
  let the amount repairs in `windows_ocr.py` fire outside the amount column
  (§11.21).
- **Editing this file:** it contains U+202F narrow no-break spaces inside figures
  such as "150 %", which silently defeat exact-string edits. Match on lines that
  do not contain them, or patch by line number.

---

## 10. Decisions worth not re-litigating

- **A native window, not a browser.** The user asked for "an interface like other
  applications — like a Pomodoro timer". Taken to mean, and implemented as: one
  window that opens when the program starts and closes when it is closed, a menu
  bar, keyboard shortcuts, its own title-bar icon, no address bar, no localhost
  port, no second process. The Pomodoro timer in this workspace is plain
  Tkinter, so this is plain Tkinter — same toolkit, same palette-dict theming,
  same one-file windowed build, so the two projects look and build alike.
- **Tkinter rather than Qt or a web view.** Tkinter ships with Python: no extra
  dependency, no WebView2 runtime to be missing on someone else's machine, and a
  28 MB .exe instead of 150 MB. The cost is that everything is hand-built —
  scrollable frames, charts on a canvas, hover states — which is why
  `app/ui/theme.py` exists.
- **The web layer was deleted, not kept alongside.** Two interfaces for one
  application means two things to keep working, and the browser one was
  explicitly rejected. FastAPI, uvicorn and the HTML/CSS/JS are gone from the
  tree and from `requirements.txt`; they are still in Git history at tag
  `v1.1.0` if ever needed.
- **Review before the books.** A scan never lands as final. `auto_confirm_clean`
  exists but is off by default, because a reading whose arithmetic is fine can
  still have the wrong merchant or the wrong category.
- **Integer cents everywhere.** See §3. Do not introduce a float amount.
- **Description rules beat the model; merchant rules do not.** See §3 and §11.
- **The image is normalised once, at upload.** A re-scan must see exactly the same
  pixels the first scan saw, or the two readings are not comparable.
- **Items are replaced wholesale on save.** The review pane always holds the full
  list; diffing rows the user may have reordered or deleted is more code and more
  ways to lose a line.
- **One file, portable, data beside the .exe.** "Portable" was taken to mean *copy
  one file and it works, and it leaves nothing behind on a machine you
  borrowed*. Hence one-file mode, no registry, no installer, and books beside the
  binary rather than in `%APPDATA%`.
- **Windowed, not console.** A console flashing behind the window looks like a
  fault. The cost is that nothing can be printed, hence the log file and the
  message box for fatal startup errors.
- **The API key is stored in plain text** in `data\bookkeeping.db`. Acceptable for
  a local single-user app; stated here so it is not a surprise, and it matters
  more now the program is portable — *the database on a USB stick carries the key
  with it*.
- **The offline engine is the one built into Windows.** `Windows.Media.Ocr` ships
  with Windows 10 and 11, so it costs nothing to depend on and needs no setup by
  the person receiving a portable copy — which is the whole point of a portable
  copy. It is ahead of Tesseract in the `auto` order because it is the engine
  whose accuracy has actually been measured here (§9), and because an engine that
  is always present beats one that usually is not.
- **Tesseract is not bundled, and is no longer the offline default.** A separate
  ~60 MB program with its own installer; bundling it would triple the download for
  a fallback most users never enable. It stays selectable for anyone who has it.
- **The image is not preprocessed before Windows OCR.** Greyscale, 1.5×/2×/3×
  upscaling, autocontrast and sharpening were each measured against the real
  receipt: none beat the plain image, and sharpening and upscaling were *worse*
  (17 amounts recovered instead of 22). The published advice to deskew, upscale
  and binarise is all Tesseract advice — the Windows engine does its own
  normalisation and resents the help. Do not add a preprocessing step without
  re-running that comparison. The one transformation that *does* help is the
  downscale `app/images.py` already applies: the full-resolution phone photo
  reads measurably worse than the 1568 px copy (§9).
- **A missing digit is left missing.** When OCR loses the leading `2.` of `2.94`,
  the residual against the subtotal would often identify it. The reading does not
  guess: a plausible wrong number in a set of books is worse than an obvious hole,
  because the hole gets reviewed and the wrong number does not. The same reasoning
  is why `to_cents` returns `None` rather than `0` for an absent value.
- **One window per set of books, not per machine.** The lock is a file lock in the
  data folder, so two portable copies with their own books run side by side.

---

## 11. Fixes already made — do not regress these

1. **`connect()` must check `in_transaction` before COMMIT/ROLLBACK.**
   `sqlite3.executescript()` implicitly commits, so an unconditional `COMMIT`
   after the schema script raised "cannot rollback — no transaction is active"
   and *masked the real error* underneath (`app/db.py`).
2. **A receipt `UPDATE` needs its `receipt_id` binding** — the first version had
   14 placeholders and 13 values (`app/store.py`).
3. **Listing filters must be qualified with the `r.` alias.** The listing joins
   `category`, so a bare `id IN (SELECT …)` is ambiguous between `receipt.id` and
   `category.id` (`app/store.py:list_receipts`).
4. **A receipt that vanishes underneath the review pane must be handled.**
   `ReviewPane.load` catches the "no longer in the books" error, clears itself and
   reloads the list — it happens for real when a receipt is deleted elsewhere
   (`app/ui/receipts.py`).
5. **`_find_summary_amounts` checks most-specific first.** `SUBTOTAL` contains
   `TOTAL` and `TOTAL TAX` contains both, so naive substring order mislabels
   every one of them (`app/extract/tesseract_ocr.py`).
6. **httpx title-cases some header names on the wire** (`X-Api-Key`), so the mock
   API test lower-cases header keys before asserting (`tests/test_claude_engine.py`).
7. **Hand-entered receipts derive their header category from the biggest line**,
   the same way scanned ones do (`app/store.py:dominant_category`).
8. **A blanket merchant rule must not outrank the model's per-item category.**
   Found while watching the frozen build read a receipt: `SOURDOUGH BOULE` came
   back as Groceries with `category_source: rule`, because the seeded
   `WALMART → Groceries` merchant rule was evaluated in the same pass as
   description rules. `resolve_category` now runs description rules, then the
   model, then merchant rules, and the backfill follows the same order
   (`app/categorize.py`, `app/store.py:apply_rules`).
9. **Bundled resources are found via `sys._MEIPASS`, never `__file__`
   arithmetic**, and the writable data folder is chosen *before* `app.db` is
   imported, because `db.py` resolves its paths at import time (`app/paths.py`,
   `app/launcher.py`).
10. **A windowed build has no `sys.stdout`.** Nothing may assume printing works;
    the launcher installs a rotating file handler and reports fatal errors with
    `MessageBoxW` (`app/launcher.py`).
11. **`tkinter` must not be in the spec's `excludes`.** Left over from the web
    build, it produced an .exe that started, created its database, and then sat
    there with no window and nothing in the log (`Bookkeeping.spec`).
12. **A control must be created in the frame it is packed into.** The settings
    page originally created each widget with the card as parent and packed it
    with `in_=holder`. Tk allows that and then stacks the widget *behind* the
    frame, so every input on the page was invisible. `_row()` now returns the
    frame to build in (`app/ui/settings_page.py`).
13. **Every pixel measurement goes through `Theme.px()`.** Fonts scale with the
    display; Treeview column widths, canvas heights, thumbnails and wrap widths
    do not. Unscaled, on a 150 % display the window opened at half the intended
    size with every column truncated and the review pane's Delete button off the
    edge (`app/ui/theme.py` and every page).
14. **A canvas reports a width of 1 until Tk has laid it out.** Drawing then
    produced 40-pixel bars with their value labels off the left edge, and a month
    chart drawn entirely above the visible area. `_draw` now defers itself until
    the canvas has real geometry (`app/ui/reports.py`).
15. **Chart labels are truncated, not wrapped.** A wrapped category name
    overlapped the row below it, which read as a rendering fault
    (`app/ui/reports.py:_fit`).
16. **Creating and destroying Tk interpreters repeatedly breaks Tcl** ("invalid
    command name tcl_findLibrary"). The UI tests create **one** root for the
    whole session and give each test a `Toplevel`; they also make it transparent
    rather than withdrawn, because an unmapped window never gets real geometry
    and the charts would never draw (`tests/test_ui.py`).
18. **A store brand is not a category.** The seeded `GREAT VALUE → Groceries`
    rule was wrong in kind, and the first real receipt exposed it: Walmart sells
    Great Value mops, ammonia and sponges alongside Great Value milk, and all
    three were filed as Groceries. It fired more often than it would have on
    printed names alone, because rule matching also searches the model's
    plain-English expansion — `GV TWIST MOP` does not contain "GREAT VALUE" but
    `Great Value twist mop` does. The rule is gone, and a schema migration
    (`user_version` 2) removes it from databases that already have it, leaving an
    identical rule the *user* wrote in place. `MARKETSIDE` stays: that one really
    is Walmart's fresh-food line (`app/db.py`, `tests/test_real_receipt.py`).
19. **A one-file PyInstaller build runs the app in a child process.** Verifying
    "did a window appear" by filtering on the pid returned by `Popen` finds
    nothing but the bootloader's hidden window — which looks exactly like a crash
    on startup and is not. Any future verification script must walk the process
    tree.
20. **The tax flag after an amount is matched case-insensitively.** Walmart
    prints a small-capital `X`; Windows OCR reads it as a lowercase `x` on most
    lines. While the trailing-amount pattern ended in `[A-Z]?`, the amount failed
    to match at end-of-line and *the entire item was silently discarded* — 15 of
    the 20 readable lines on the real receipt vanished this way, with no error
    anywhere. A pattern that drops data on a near-miss is the worst kind: it
    looks like the OCR failed (`app/extract/receipt_text.py`,
    `tests/test_units.py`).
21. **OCR character repairs are gated to the amount column.** `O`→`0` is needed
    (Windows reads a leading zero as the letter o) but must never run over a whole
    line: `O` is a letter in half the products on a receipt, and a global
    substitution turns `GV TOASTED O` into `GV TOASTED 0` and `DOVE` into `D0VE`.
    Every pattern in `windows_ocr.repair_amounts` is anchored to a `.dd` price for
    that reason, and a test asserts a description is left untouched. The three
    repairs are also **order-dependent** — the letter-zero fix must run before the
    split-decimal rejoin, or neither matches `"o. 98"` (`app/extract/windows_ocr.py`,
    `tests/test_windows_ocr.py`).
22. **Abbreviated item names need their own rules, and they are brands, not store
    brands.** Offline OCR cannot expand `CLX PLNGR` into "Clorox toilet plunger",
    so the original plain-English keyword list matched only 3 of 20 items on the
    real receipt. Schema version 3 seeds generic product nouns (`MOP`, `AMMONIA`,
    `SPGE`) and single-category brands (`LYSOL`, `PAMPERS`), taking it to 12 of 20.
    Note the difference from 11.18: `CLOROX` sells cleaning products and nothing
    else, while `GREAT VALUE` sells everything — that is what makes one safe to
    seed and the other not. Patterns that hide inside ordinary words are excluded
    on purpose: `GAIN` is a detergent but also the end of `BARGAIN`, and `AIM` is
    a toothpaste but also the middle of `CLAIM` (`app/db.py`, `tests/test_store.py`).
23. **A cropped photo must report no merchant rather than invent one.** With the
    top of the receipt out of frame the merchant fallback took the first line
    containing letters and returned "Items Sold 21". Summary lines are now skipped
    before that fallback runs; saying nothing is the correct answer
    (`app/extract/receipt_text.py`).
24. **A verification tool that cries wolf is worse than none.** `verify_exe.py`
    shelled out to PowerShell once per process-tree node *per poll*; under the
    disk load right after a build the loop ran so rarely that it missed an
    "Already running" dialog that was on screen the whole time, and reported a
    working build as broken. It now fetches the process table once and walks the
    tree in Python — the whole check went from a spurious failure to 15 seconds
    (`tools/verify_exe.py`).

---

## 12. Version control

One repository in the project folder, named after this document, exactly like the
sibling projects under `D:\claude`:

- `core.autocrlf=false` locally, plus `.gitattributes` with `* -text`, so files
  are stored byte for byte (this machine has `core.autocrlf=true` system-wide,
  which would otherwise rewrite every text file to CRLF on the first checkout).
- `user.name = xu.jiamin`, `user.email = Xujiaming021101@163.com`, per repository.
- Remote `mirror` → `D:\claude\repos\Bookkeeping.git` (a local bare second copy;
  its `HEAD` points at `main` so a clone checks out).
- Baseline **1.0.0**. A functional change adds **0.1**, a fix or docs change adds
  **0.0.1**, updated in `VERSION` in the same commit and tagged `v<number>`.
  Tags: `v1.0.0`, `v1.0.1`, `v1.1.0`, `v1.2.0`, `v1.2.1`, `v1.2.2`, `v1.3.0`.
- Tracked: all source, `Bookkeeping.spec`, `build.bat`, `run.bat`, `make_icon.py`
  and `assets/icon.ico` (generated, but the build needs it).
- Ignored: `data/` (personal), `dist/` and `build/` (regenerable). **Because the
  .exe is not in Git, `build.bat` keeps the previous one as
  `dist\Bookkeeping.previous.exe` — that is the only way back from a bad build.**

No GitHub remote (`origin`) has been created yet — that is a publish-shaped step
and needs the user's say-so. When they want it: `gh repo create Bookkeeping
--private` under the account `Micheal-Jiaming`.

---

## 13. Ideas not built

Ranked by how much they would improve the daily experience:

1. **More real receipts.** There is now exactly one
   (`tests/test_real_receipt.py`) and it immediately found a mis-categorisation
   bug, so the next few are likely to be just as productive. Worth collecting a
   handful — a restaurant bill, a fuel receipt, something faded or folded, a
   non-USD one — with hand-checked expected values, and reporting per-field
   accuracy across them. One receipt is an anecdote.
2. **Learning from corrections.** When a reviewer re-categorises the same item
   name twice, offer to create the keyword rule. The rules table already supports
   it; only the suggestion is missing. This matters more since 1.3.0: an offline
   reading leaves ~40 % of lines uncategorised, and those corrections are exactly
   the signal that would fix it permanently.
3. **Cross-check the item count against "# ITEMS SOLD".** Walmart prints the
   number of items on the receipt, which is an independent constraint on whether
   the reading dropped a line — free, and stronger than the subtotal check alone,
   because it catches a dropped line whose amount was also missed. The offline
   engine currently loses lines silently apart from the money not adding up.
4. **Use the arithmetic residual to re-read ambiguous rows.** When the items are
   short by exactly 0.9 × a parsed amount, a leading digit was lost and which row
   it was is usually determinable. Would need care: see §10 on not guessing, so
   this should propose a correction in the review pane rather than apply one.
5. **Drag and drop onto the window.** Tk cannot do it without `tkdnd`, a
   non-stdlib dependency; the file dialog and clipboard paste cover the same need
   for now.
6. **Budgets and month-over-month deltas** on the reports page.
7. **A date picker** in the review pane instead of a typed `YYYY-MM-DD` box.
8. **PDF and emailed receipts** (the Anthropic API takes PDFs as document blocks,
   so the engine change is small).
9. **Multi-page or multi-receipt images** — currently one image is one receipt.
10. **Code signing**, to stop the SmartScreen warning. Needs a paid certificate.
11. **Batch scanning via the Message Batches API** at half price, for someone
   scanning a shoebox of receipts at once.

---

## 14. History

| Version | Date | Change |
| --- | --- | --- |
| 1.0.0 | 2026-08-23 | First version. Research of Receipt Wrangler / Budget Lens / Firefly III; Claude-vision + Tesseract engines behind one interface; rules-then-model categorisation; arithmetic validation and review workflow; FastAPI + SQLite backend; browser interface with reports and CSV export; 69 tests. |
| 1.0.1 | 2026-08-23 | Inline data-URI favicon, so a browser's automatic `/favicon.ico` request stopped logging a 404 that looked like a fault. |
| 1.1.0 | 2026-08-23 | **Portable Windows executable.** One-file PyInstaller build (`Bookkeeping.spec`, `build.bat`, generated icon); launcher with a writable-data-folder search, port selection and single-instance hand-off; Quit button and browser heartbeat so the process could not linger invisibly. Also fixed merchant rules outranking the model's per-item category (§11.8). 97 tests. |
| 1.2.0 | 2026-08-23 | **A real desktop interface.** The browser UI (FastAPI, uvicorn, HTML/CSS/JS) was removed and replaced with a Tkinter window: menu bar, four pages, review pane with the receipt image beside the extracted fields, hand-drawn canvas charts, dark/light themes, remembered window geometry, clipboard paste, keyboard shortcuts. The HTTP layer's logic was extracted intact into `app/store.py`, so the same behaviour is now reachable as function calls; the API tests became store tests and 28 new tests drive the real window. Single-instance handling changed from "hand off to the running copy" to a lock on the data folder. Fixes §11.11–§11.17. 121 tests. |
| 1.2.1 | 2026-08-23 | First real receipt read end to end (§9). Removed the seeded `GREAT VALUE` rule — a brand, not a category — with a migration for books that already exist, and kept the receipt as a permanent 12-test fixture. Settings now shows measured costs instead of estimates. 134 tests. |
| 1.2.2 | 2026-08-23 | Development tooling moved into the project and documented: `verify_exe.py`, `screenshot_pages.py`, `seed_demo.py`, `mock_anthropic.py` (previously throwaway scripts in a temp folder, which would have been lost). Added a "where to pick up" section. |
| 1.3.0 | 2026-08-29 | **The app reads receipts with nothing configured.** Diagnosis: recognition had never worked on this machine because neither engine was installed — no API key, no Tesseract — so a real Walmart receipt failed with four red flags and no data. Added a third engine using Windows' own OCR (`Windows.Media.Ocr` via the `winrt-*` bindings): no key, no install, no network, and present on every Windows 10/11 machine. Its lines arrive scrambled, so word bounding boxes are re-grouped into printed rows (docTR's half-median-height rule) and three OCR-specific price corruptions repaired. The shared receipt-text parser moved to `app/extract/receipt_text.py`. On the real receipt: subtotal, tax and total exact, 20 of 24 line items, the shortfall reported rather than guessed. Also added 55 abbreviation and brand rules (3 of 20 items categorised → 12 of 20, schema v3 with a migration), an engine-availability line in the log, and an offline OCR language setting. Fixes §11.20–§11.24. 161 tests. |
