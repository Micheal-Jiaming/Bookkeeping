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
- **Version:** 1.4.1 (see `VERSION`)
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
8. **Show product names an ordinary person understands, not the store's internal
   shorthand.** (2026-08-31, delivered in 1.5.0 — see §3. The user asked for the
   names to be looked up on Walmart's website; that turned out to be impossible
   from a program, and the barcode route was used instead. Both are explained in
   §3, because the substitution was a judgement call worth recording.)
9. **A Chinese interface, and Chinese item names.** (2026-08-31, delivered in
   1.9.0 — see §3. Chinese only: the user asked for one extra language and said
   so explicitly.)
10. **The application should ultimately operate online**, because some information
   can only be obtained by querying it and accuracy depends on that. (Stated
   2026-08-31. Partly delivered in 1.5.0. This reverses the emphasis of the
   1.3.0 work without discarding it: offline operation remains the fallback that
   makes a portable .exe usable on any machine, but it is no longer the target.)

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


### Reading the image twice (1.8.0)

Windows OCR is run twice over every receipt -- once at the stored size, once
shrunk to a 1176-pixel long edge -- and the two readings are combined. Neither
size is better than the other, which is the entire reason both are used:

* **The full-size pass is better at the summary block.** It found the totals on
  five of the six real photographs; the reduced pass lost the TOTAL on all three
  Walmart receipts.
* **The reduced pass is better at line items.** It reads all 24 lines of the
  first Walmart receipt where full size reads 20, and 18 of 18 on the crumpled
  Aldi one where full size reads 11.

Combining them needs one judgement -- which set of line items to keep -- and the
receipt makes it rather than a preference written into the code: **whichever
list lands closer to the printed subtotal wins.** That is the same evidence
`_confidence` already uses, and it means a pass that invents lines is rejected
by its own arithmetic. Header fields need no judgement: a field was either read
or it was not, so the primary reading is used and any gap filled from the other.

Measured across all six photographs, the same code with the second pass off and
on:

| | one pass | two passes |
| --- | --- | --- |
| Summary figures found (subtotal, tax, total) | 14 of 18 | **16 of 18** |
| Money the line items could not account for | $44.02 | **$28.47** |
| Line items found | 66 | **69** |

No receipt was worse on any measure. The second pass costs about two tenths of a
second against the seconds a scan already takes.

### Turning `CLX PLNGR` into something a person can read (1.5.0)

A till prints its own shorthand, and no amount of local cleverness recovers the
words: "Clorox Plunger" simply is not in the string `CLX PLNGR`. The expansion
has to come from somewhere that knows the product, and the receipt already
carries the key — the barcode number printed beside each line.

**The detail that decides whether this works at all**, and the one to preserve if
this code is ever rewritten: *the number Walmart prints is not a valid barcode.*
It prints the first eleven digits of the UPC and pads the twelfth column with a
zero, dropping the check digit that every product database validates before it
answers. Seven of the eight codes on the reference receipt fail UPC-A validation
as printed; the eighth passes only because its true check digit happens to be
zero. Querying the printed number returns "bad request" or "not found" — which is
indistinguishable from "this product does not exist", so the failure looks like
the lookup service being useless rather than like a bug. `app/lookup/upc.py`
recomputes the check digit, and that alone is what makes the feature work.


**Two later receipts confirmed the zero-padding rule from the receipt itself.**
One line read `756809105667 756809105660  5.88 X` -- an item with no printed
name, showing the true UPC (whose check digit really is 7) beside the same code
with that digit replaced by a zero. That is the truncation written out in full,
on a single line, by the till.

They also showed where the repair must **stop**. Two codes did not end in a
zero: a Maine bottle deposit (`000787423909`) and a produce PLU
(`000000004612`). Neither is a barcode. Rebuilding them would be a guess, and
the dangerous outcome is not a guess that fails but one that succeeds -- landing
on a real product and labelling the line with somebody else's goods. The trailing
zero is the evidence of truncation, so without it `barcode_for` declines. Codes
beginning with six zeros are refused outright, since no GS1 company prefix looks
like that; they are PLUs padded out to twelve columns.

Measured across those two receipts: **7 of 9 usable barcodes resolved**, and on
the larger of the two, 6 of 6.

Two free, keyless sources answer, and they are complementary rather than
redundant:

- **Open Food Facts** — open data, no key, no quota, food and drink only.
- **UPCitemdb** — a commercial catalogue whose trial tier needs no key and allows
  roughly a hundred lookups a day per address. It covers the household and
  personal-care items Open Food Facts has never heard of.

Measured on the reference receipt (20 distinct lines): **12 resolve** with both
services answering, **8** with UPCitemdb's daily allowance spent and Open Food
Facts alone. Two lines can never resolve — a bottle deposit and bakery bread
carry codes the shop assigned itself, in UPC number systems 2/4/5/9, which are
unique only inside that chain — so the real ceiling is 18, not 20.

The expansion pays for itself twice, because `resolve_category` searches the
readable name as well as the printed one: correct categories on that receipt go
from **14/20 to 17/20**. `HS SH CLS8.5` and `AIM TP 5.5OZ` only reach Personal
Care once something in the text says "shampoo" and "toothpaste". No line was
categorised worse.

**Walmart's own website is not a source and cannot be.** Requests to walmart.com
from a program are answered with a bot-check page titled "Robot or human?"
rather than the product, whatever headers are sent — verified directly, on both
the search and product-page URLs. Their catalogue is reachable only through the
affiliate/marketplace API, which needs an approved developer account and a
signed key. The barcode route gets the same names without pretending to be a
browser, and works for any chain rather than just this one.

Everything is cached in the `product_name` table, hits and misses alike, so a
second receipt from the same shop asks the network almost nothing. Caching the
misses is deliberate: without it the eight unresolvable lines would be re-queried
on every scan and would exhaust the free quota on questions already answered. A
miss is retried after 30 days, because these catalogues grow.


### Reading the interface, and the receipt, in Chinese (1.9.0)

The interface switches between English and Chinese from **View → Language**, and
the item names read off a receipt are machine-translated to match.

**The English text is the translation key.** ``t("Save draft")`` looks the
English up in ``app/i18n.py`` and returns the Chinese; there is no catalogue of
symbolic names, no `.po` files and no gettext dependency. The source therefore
still reads as prose rather than as ``t("btn.save_draft")``, and a string with no
entry falls back to showing the English instead of a bare key. The price is that
editing an English string silently orphans its translation, so
``tests/test_i18n.py`` compares every key against the string constants the
source actually contains — implicit concatenation included — and fails on any
that no longer match.

**Chinese only, on purpose.** The user asked for exactly one additional language
and said so. The machinery would take more; that is not an invitation, because
every language added is 200-odd strings to maintain for ever. A test asserts the
set is exactly `{en, zh}`, to make adding a third a deliberate act.

Three details that are easy to get wrong:

* **A combobox hands back the text on screen.** The engine picker and the status
  filter used their English label as the lookup key, so in Chinese the lookup
  found nothing. ``_value_for`` and ``_statuses_for`` match on the *translated*
  label and return the English code, so what is stored stays English whatever
  the interface shows.
* **Segoe UI has no Chinese glyphs.** ``Theme.font`` asks ``i18n.font_family()``,
  which returns Microsoft YaHei UI for Chinese. Without it Windows substitutes
  per-glyph and a line ends up in two typefaces.
* **Category names live in the database, not the code**, and get the same
  treatment: shown translated, matched back to the English when one is
  chosen, so the books stay in English whatever the interface shows. A
  category the user created themselves has no translation and appears as
  they typed it.
* **Switching language rebuilds every widget**, exactly as switching theme does,
  because a Tk widget holds its text as an instance option. ``set_language``
  simply delegates to ``set_theme``.

#### Translating the item names

Receipts are printed in English, so this is machine translation, cached in the
``translation`` table for the same reason product names are: it never changes,
and the same groceries come back every week.

**The endpoint everybody uses does not work.**
``translate.googleapis.com/translate_a/single`` — the one in every snippet on the
internet — answers `429 Too Many Requests` to the *first* request from this
address, not after a burst. That is a block, and no amount of pacing gets around
it. The endpoint Google's own Chrome extension uses,
``clients5.google.com/translate_a/t?client=dict-chrome-ex``, answered twelve
consecutive names at half a second apart without complaint, and is what the code
calls. MyMemory is the fallback; it leaves brand names alone more often, which
is sometimes better and sometimes not.

Translation happens **during the scan**, not while drawing the review pane: a
request per name on the interface thread would freeze the window. By review time
the answers are in the database and the pane reads them without touching the
network. A receipt scanned before the language was switched therefore keeps its
English names until it is scanned again — a real limitation, and the honest
trade for never blocking the interface.

Sample of the output, from the real receipts: `Broccoli Crowns` → 西兰花冠,
`Large Eggs` → 大鸡蛋, `Sourdough Loaf` → 酵母面包, `Clorox Plunger & Toilet
Brush with Carry Caddy` → Clorox 柱塞和马桶刷，带携带盒. It is machine
translation and reads like it: `24ct Paper Bowl` comes back as 24克拉纸碗,
having taken "ct" for carats.

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

176 keyword rules and 15 categories are seeded on first run (`app/db.py`),
tuned for US retail receipts: product nouns (`MOP`, `AMMONIA`, `DIAPER`,
`UNLEADED`), single-category brands (`TIDE`, `LYSOL`, `PAMPERS`, `CLX`), and
merchant defaults (`WALMART`, `COSTCO`, `CVS`, `SHELL`, …). **No store-brand
pattern is among them** — `GREAT VALUE` was seeded originally and had to be
removed, for the reason in §11.18.

Seeding fires only when **no built-in rule survives**, so rules the user deletes
stay deleted. That is not the same as "only on a new database", and the
difference is deliberate: a user who deleted every built-in gets them back, one
who deleted some keeps their choices. New built-ins added in a later version
therefore need a migration to reach existing books — schema v3 does exactly that
for the 55 abbreviation rules, skipping any pattern the user already has.

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

SQLite, `data\bookkeeping.db`, schema in `app/db.py`. **`PRAGMA user_version` is
at 6**; migrations live in `_migrate` and each is written to be a no-op on a
database that already has the change, so they are safe to re-run. v2 removed the
`GREAT VALUE` rule (§11.18), v3 added the abbreviation rules the offline engine
needs, v4 added the `product_name` cache, and v5 added the grocery vocabulary
(§11.35). v4 has no migration body because both its pieces arrive through paths
that run on every open (`CREATE TABLE IF NOT EXISTS`, and `_seed_settings`
inserting any missing key). v3 and v5 share `_add_missing_rules`, which skips
any pattern the user already has so a rule they deleted stays deleted.

| Table | Purpose | Notes |
| --- | --- | --- |
| `receipt` | one row per receipt | status, image path + sha256, merchant (+ raw as printed), date, currency, subtotal/tax/tip/total in cents, payment method, header category, engine/model/confidence, `raw_text` + `raw_response` for audit, `review_flags` JSON, timing, tokens, `cost_usd`, `error` |
| `line_item` | purchased lines | description (+ `raw_description` = the model's plain-English expansion), sku, quantity, unit price, amount, category, `category_source`, `is_discount`, `taxable` |
| `category` | expense categories | name (unique), colour chip, `is_builtin`, sort order |
| `category_rule` | keyword rules | field (`description`/`merchant`), match type (`contains`/`regex`), pattern, category, priority (lower first), enabled |
| `translation` | English name → Chinese | the machine translation cache; a `NULL` means the services were asked and had none |
| `product_name` | barcode → readable name | the online lookup's cache: repaired UPC, name, which source knew it, when. A `NULL` name is a real answer ("asked, nobody knew"), not a gap — see §3 |
| `setting` | key/value settings | engine preference, API key, model, effort, Tesseract path, auto-confirm, online lookup, item translation, **plus the interface's own state**: language, theme, window geometry, last page |

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
app/lookup/             plain-English product names from a barcode (online)
    upc.py              the check-digit repair a Walmart receipt needs
    product_names.py    Open Food Facts + UPCitemdb, paced and time-boxed
    translate.py        item names into Chinese, cached (Google, then MyMemory)
    __init__.py         the SQLite cache, and the entry point the pipeline calls
app/i18n.py             interface language: English or Chinese
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

One window, four pages, a menu bar (File / View / Help) and a status bar. The
interface is in English or Chinese, chosen from **View → Language** (§3).
Themes are chosen from **View → Theme**, which marks the active one; the
*Theme* button in the header cycles through them.

- **Receipts** — the workspace. Toolbar (*Add receipt images*, *Paste image*,
  *Add by hand*, status filter, search), the receipt list on the left, and the
  **review pane** on the right: the stored image beside every extracted field,
  the arithmetic complaints in plain words, an editable line-item grid with a
  live "Lines: 60.59 (off by 4.00)" readout, and the actions — *Save & confirm*,
  *Save draft*, *Re-scan*, *Output* (exactly what the engine returned), *Delete*.
  Where a plain-English name is known, it sits in small muted text **under** the
  item, not in place of it: the editable field keeps what the receipt actually
  says, and the expansion answers "what *is* `EQJELLUBE8OZ`?" without
  overwriting the evidence.
- **Reports** — four stat tiles, spend by category, spend by month, a top-merchant
  table, and a note explaining the `Tax & unitemised` bucket. Range presets plus
  explicit from/to dates, and CSV export.
- **Categories & rules** — categories with usage counts, the rule list, add and
  delete, the precedence explanation, and the backfill button.
- **Settings** — engine, API key (masked), model, effort, base URL, offline OCR
  language, Tesseract path, auto-confirm, **look product names up online**, live
  engine status, what a scan costs, and where the data folder is. The lookup
  checkbox says what leaves the machine: only the barcode printed beside an item,
  never the shop, the date or the price.

Keyboard: `Ctrl+O` add images, `Ctrl+V` paste an image from the clipboard,
`Ctrl+N` add by hand, `Ctrl+1..4` pages, `F5` refresh, `Ctrl+Q` quit.

**Clipboard paste** is worth calling out: `Win+Shift+S`, snip a receipt on
screen, `Ctrl+V` in Bookkeeping. It also accepts files copied in Explorer.

### Look and scaling

A theme is a whole palette in a dict, applied by rebuilding the widgets — the
same approach the Pomodoro timer uses, because Tk has no real theming. Each is
designed for its own surface rather than derived by inverting another. There are
four, picked from a set of five candidates rendered in the real window:

| Theme | | Accent | Accent on chart surface |
| --- | --- | --- | --- |
| **Dark** | the default | `#3987e5` | 4.79:1 |
| **Dracula** | dark violet | `#bd93f9` | 5.90:1 |
| **Light** | | `#2a78d6` | 4.30:1 |
| **Solarized** | warm cream | `#1f6f9c` | 5.11:1 |

Pick one from **View → Theme**, which marks the active one; the header's *Theme*
button cycles. The cycle order runs dark themes first and then light ones, so a
single press never flips the screen brightness — `tests/test_theme.py` asserts
that, and asserts the crossing happens exactly once.

**The accent is the chart bar colour, so it is not a free choice.** Every palette
is checked two ways, and both checks must be re-run when one is added or edited:

1. **Legibility, by WCAG contrast ratio** — accent on that palette's own chart
   surface ≥ 3:1, body text ≥ 4.5:1, hint text and bold button labels ≥ 3:1.
2. **Confusability with the reserved status colours, by OKLab ΔE ≥ 15.**

Using contrast for the second question is a trap worth naming: `#0a4fa8` and
`#a8001b` score 1.01:1 because they are equally *dark*, while being obviously
different colours. Distinctness is a hue question, and needs a perceptual metric.
Both checks now run in `tests/test_theme.py` against every theme, including any
added later — they were done by hand originally, which is exactly the sort of
step that quietly stops happening.

Two candidate palettes were changed by those measurements rather than by taste:
a *Forest* theme's teal accent sat only ΔE 8.2 from its own green "good" status,
so chart bars would have read as a status colour, and Solarized's own `#93a1a1`
manages just 2.48:1 as hint text on cream. Forest was not among the two chosen,
but the finding is why the check exists.

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
- **Windows may warn** that it is from an unknown publisher (SmartScreen) — see
  the section below for exactly when and why. Choose "More info → Run anyway",
  or build it locally with `build.bat`, which avoids the warning entirely.

### The SmartScreen warning: what it is and what it is not

This confuses people, so it is written out properly. Verified on this machine:
`Get-AuthenticodeSignature dist\Bookkeeping.exe` reports **NotSigned**, and the
file carries **no `Zone.Identifier` stream**.

**The warning is not about the code.** SmartScreen has not examined the program,
found nothing wrong with it, and is not reporting a defect. It weighs exactly two
things: whether the file is signed by a publisher it recognises, and whether that
exact file has been downloaded enough times by enough people without incident.
A brand-new executable scores zero on both, and a brand-new executable is what
every honest first release is.

**It only fires on a file that carries the Mark of the Web.** When a browser, an
email client or a chat app saves a file, it tags it with an NTFS alternate data
stream recording that it came from the internet. SmartScreen checks that tag.
This is why the .exe runs silently here but would warn on the machine of anyone
you send it to: the local build has no such tag. The practical consequences:

- Building it yourself with `build.bat` — never warns.
- Copying it over a USB stick or a LAN share — normally no tag, so no warning.
- Downloading it from GitHub, or receiving it through email, WeChat or Teams —
  tagged, so it warns.
- The tag can be removed by the recipient: file → Properties → **Unblock**, or
  `Unblock-File .\Bookkeeping.exe` in PowerShell.

Being unsigned has a second cost that matters more over time: **an unsigned file
builds reputation per file hash, and every rebuild starts from zero.** A signed
one accumulates reputation against the certificate, so later versions inherit it.
Unsigned means version 1.6.0 is as unrecognised as 1.0.0 was.

**Correcting a widely repeated myth:** an EV certificate no longer buys instant
SmartScreen trust. It used to, and most advice online still says so. Microsoft's
current guidance is explicit that this behaviour no longer exists and that paying
the EV premium *for that reason alone* is not justified. A signed app still shows
a warning on first download — with the publisher's verified name in it, which is
the real difference.

**The options, honestly costed.** This is the user's decision, not a technical
one, and doing nothing is defensible for a portfolio project:

| Option | Cost | What it gets |
| --- | --- | --- |
| Do nothing | free | The warning stays. Tell recipients to expect it; "More info → Run anyway" works. Fine while the audience is people you can talk to. |
| Tell people to Unblock | free | Removes the warning per file, per recipient. Needs a sentence of instruction. |
| Azure Artifact Signing (was Trusted Signing) | ~$9.99/month | Microsoft's own service, no hardware token, integrates with CI. Individual sign-up is open in **the USA and Canada** — which covers this user. Still warns until reputation builds, but with a verified publisher name, and reputation carries across versions. Cheapest real answer. |
| A traditional OV certificate | ~$200–400/year | Same practical result. Since June 2023 the private key must live on a hardware token or HSM, so it is more fuss than the above. Certificate lifetimes are capped at one year from February 2026. |
| Microsoft Store | free–$19 one-off | The only route with *no* warning at all: Store apps are re-signed by Microsoft. Costs a store listing and packaging work. |

Antivirus products are a separate, unrelated annoyance: PyInstaller one-file
executables unpack themselves at startup, which resembles what packed malware
does, so heuristic scanners sometimes object regardless of signing.

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

Seven scripts in `tools\` exist because of specific things that went wrong. They
are part of the project, not scratch work: a future session that needs to check
the build, look at the interface, or exercise the vision path should reach for
these rather than write them again.

```bash
py tools\measure_accuracy.py                # is the READING still as good?
py tools\verify_exe.py                      # does the BUILD work?
py tools\screenshot_pages.py --out shots    # what does it LOOK like?
py tools\seed_demo.py --data-dir C:\temp\demo --with-image
py tools\mock_anthropic.py --port 8899      # a fake API, for testing without a key
py tools\make_sample_receipt.py out.png     # a synthetic receipt image
```

- **`measure_accuracy.py`** and **`accuracy.py`** — the accuracy harness
  (1.10.0). Until it existed, every quality figure this project quoted lived in
  prose: "16 of 18 header fields", "$28.47 unaccounted", "12 of 20 names". Each
  was true when written, and none can be reproduced, because the set of
  photographs it covered was never recorded beside it. A figure nobody can
  recompute is a claim, not a measurement, and a claim cannot tell you whether
  the next change made things better or worse.

  `accuracy.py` is the scoring, kept pure — no OCR, no images, no Windows — so
  its tests run anywhere. `measure_accuracy.py` runs the real engine over
  `pictures\` and reports.

  Three design decisions are load-bearing, and none should be undone casually:

  1. **Ground truth and the baseline are separate files and are never merged.**
     `tests/fixtures/receipts_truth.json` is what a human confirmed the paper
     says, and measures *accuracy*. `tests/fixtures/accuracy_baseline.json` is
     what this code produced on some day, and measures *regression*. A harness
     that promotes its own last output to truth reports a clean pass for ever
     while drifting arbitrarily far from the receipt.
  2. **Invented lines are scored, not just missing ones.** Two rejected changes
     (§11.42) cut the unaccounted money from $44.02 to $15.57 and then $2.77 by
     inventing five and four lines that are not printed. On the money gap alone
     both were triumphs. `--check` therefore guards `lines_invented` and
     `items_read` as well as the gap, so a fabricating change must make the
     report worse.
  3. **A truth record carries the SHA-256 of its photograph**, so it cannot
     silently be scored against a different image. That is not hypothetical:
     `ALDI1.jpg` was re-photographed as `ALDI1_new.jpg` during 1.9.x.

  The header truth distinguishes *verified absent* (the key is present with a
  value of `null` — Walmart1 is photographed with its top out of frame, so a
  reader that supplies a merchant is **wrong**) from *unchecked* (the key is
  missing, and is not scored). Collapsing those two would silently require the
  reader to return nothing for every field nobody has looked at yet.

  **The corpus is one-sixth transcribed.** `Walmart1.jpg` has a human
  transcription, shared with `tests/test_real_receipt.py` and guarded against
  drift by a test. The other five photographs have hashes and self-checks only,
  and `verified_by` records that honestly rather than implying otherwise.
  Transcribing another receipt is the cheapest real improvement to this harness.
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
| `VERSION` | 1 | `1.10.0` |
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
| `app/ui/window.py` | 572 | the window: chrome, menus, navigation, poll loop, dialogs |
| `app/ui/receipts.py` | 720 | receipt list and the review pane |
| `app/ui/reports.py` | 356 | tiles, hand-drawn canvas charts, merchant table |
| `app/ui/theme.py` | 361 | four palettes, display scaling, ttk styling, shared widgets |
| `app/ui/settings_page.py` | 334 | recognition settings |
| `app/ui/rules.py` | 241 | categories and keyword rules |
| `app/ui/__init__.py` | 18 | the interface package's map |
| `app/pipeline.py` | 371 | scan orchestration, thread pool, engine fallback |
| `app/db.py` | 635 | schema, seed categories and rules, migrations, connections |
| `app/launcher.py` | 175 | data folder, logging, single-instance lock, error reporting |
| `app/categorize.py` | 131 | the precedence chain and rule matching |
| `app/validate.py` | 126 | arithmetic and sanity checks → review flags |
| `app/paths.py` | 103 | frozen vs. source paths; writable data folder with fallback |
| `app/money.py` | 66 | integer-cent money conversion |
| `app/images.py` | 66 | image normalisation (EXIF, downscale, PNG) |
| `app/settings_store.py` | 60 | settings read/write, secret masking |
| `app/extract/receipt_text.py` | 450 | shared: receipt text → `ExtractedReceipt` |
| `app/extract/windows_ocr.py` | 367 | Windows OCR engine + word-box row reconstruction |
| `app/extract/claude_vision.py` | 207 | Claude vision engine, pricing table, error mapping |
| `app/extract/base.py` | 181 | `ExtractedReceipt` schema + `Extractor` interface |
| `app/extract/tesseract_ocr.py` | 112 | Tesseract engine (parser now shared) |
| `app/extract/__init__.py` | 89 | engine registry and fallback order |
| `app/lookup/product_names.py` | 282 | Open Food Facts + UPCitemdb, paced, time-boxed, failure-tolerant |
| `app/lookup/__init__.py` | 132 | the barcode-name cache and the entry point the pipeline calls |
| `app/lookup/upc.py` | 70 | UPC-A check digit; the repair a Walmart receipt needs |
| `app/i18n.py` | 309 | interface language, the Chinese table, and the CJK font |
| `app/lookup/translate.py` | 241 | item names into Chinese, cached; Google then MyMemory |
| `tools/accuracy.py` | 237 | accuracy scoring: pure, no OCR, runs anywhere |
| `tools/measure_accuracy.py` | 218 | runs the engine over `pictures\`, reports, guards regressions |
| `tools/make_sample_receipt.py` | 121 | synthetic Walmart receipt with known values |
| `tools/verify_exe.py` | 356 | drives the built .exe and checks it behaves (§7) |
| `tools/seed_demo.py` | 139 | fills a set of books with plausible demo receipts |
| `tools/mock_anthropic.py` | 135 | stand-in for the Messages API, for testing without a key |
| `tools/screenshot_pages.py` | 124 | opens the window and screenshots every page |
| `tests/test_store.py` | 646 | the service layer, end to end with a stub engine |
| `tests/test_ui.py` | 498 | builds the real window and drives it |
| `tests/test_units.py` | 554 | money, validation, precedence, OCR-text parsing |
| `tests/test_real_receipt.py` | 291 | the one real receipt this project has been tested against |
| `tests/test_claude_engine.py` | 265 | Claude engine against a local mock of the Messages API |
| `tests/test_theme.py` | 160 | every palette's contrast and status-distinctness |
| `tests/test_i18n.py` | 242 | the language switch, and machine translation of item names |
| `tests/test_product_lookup.py` | 365 | barcode repair, both lookup sources, the cache, the rate-limit paths |
| `tests/test_windows_ocr.py` | 215 | row reconstruction, amount repairs, the real reading |
| `tests/test_desktop.py` | 143 | data-folder fallback, the single-instance lock, arguments |
| `tests/test_accuracy.py` | 253 | the harness itself: invented lines, multiplicity, the truth/baseline split |
| `tests/conftest.py` | 55 | temp-directory database fixtures |
| `tests/fixtures/receipts_truth.json` | 164 | ground truth: what a human confirmed each photograph says |
| `tests/fixtures/accuracy_baseline.json` | 115 | the baseline: what the code produced, for regression only |
| `tests/fixtures/walmart_ocr_words.json` | — | the 161 words Windows OCR really returned for the real receipt |

11 994 lines of Python across the 47 tracked `.py` files. Not in version control: `data/` (the user's books),
`dist/` and `build/` (regenerable from the above).

---

## 9. What has and has not been verified

Verified on this machine (Windows 11, Python 3.13.11, 3840×2160 at 150 %),
2026-08-23, again on 2026-08-29 for 1.3.0 and 1.4.0, and on 2026-08-31 for 1.5.0:

**Automated — 328 tests pass** (`pytest tests/ -q`, ~70 s):

- The **service layer** end to end against a stub engine with a known reading:
  schema validation, rule and model categorisation, arithmetic flags, storage,
  duplicate detection, engine fallback, listing filters, reports, CSV.
- The **Claude request and reply handling** against a local HTTP stand-in for the
  Messages API: the image block is attached, the generated JSON schema and
  `effort` both arrive inside `output_config`, the allowed category list is
  passed, and 401/403/404/429/500 plus `refusal`/`max_tokens` all become
  actionable messages.
- **The accuracy harness, 18 tests.** Mostly about the ways a scorer can
  flatter the code it scores: an invented line is counted as invented, two
  identical bread lines need two reads rather than one, a right amount under a
  mangled name still pairs, verified-absent and unchecked header fields are
  scored differently, and — the one that states the harness's purpose — a
  simulated change that reconciles the money by fabricating two lines is
  reported as a regression, not an improvement. One integration test runs the
  real engine over the real photographs and skips where they are absent.
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
- **Two more real Walmart receipts** (Scarborough ME, 2026-08-29) were supplied
  by the user and both are now permanent fixtures in `tests/test_units.py`.
  Each exposed a defect the first receipt could not: an item printed with no
  name at all, and an item sold by weight across two lines (11.29, 11.30). With
  those fixed, every printed amount on both receipts is read and the line items
  sum exactly to the printed subtotal -- 23.52 and 35.10. Of their usable
  barcodes, 7 of 9 resolved to product names, and 6 of 6 on the larger receipt.
- **All three real receipts have now been through the actual OCR engine**, as
  image files, via the app's own entry points (`create_from_image`, which
  EXIF-corrects and downscales, then `scan_now`). Not transcriptions this time.

  | Photo | Items | Subtotal | Tax | Total | Lines sum to |
  | --- | --- | --- | --- | --- | --- |
  | `Walmart1.jpg` 1280x1706 | 20 of 24 | exact | exact | exact | 131.61 of 141.94 |
  | `Walmart2.jpg` 1280x2681 | 3 of 3 | exact | exact | **not found** | 23.52, exact |
  | `Walmart3.jpg` 3111x1280 | 8 of 9 | **not found** | exact | exact | 32.13 of 35.10 |

  Every one came back `needs_review` with honest flags naming exactly what was
  missing, which is the behaviour that matters most: nothing was silently wrong.

- **Aldi, the first receipt from another chain, and the one that showed how
  Walmart-shaped this parser was.** Before any change it read **zero** real
  items from one of the two Aldi receipts -- the only "item" it found was the
  Mastercard line -- and 3 of 18 from the other. Five structural differences
  were responsible; all five are fixed, and 11.33 records them.

  | Photo | Items | Subtotal | Tax | Total | Lines sum to |
  | --- | --- | --- | --- | --- | --- |
  | `ALDI1.jpg` 18 lines | 11 of 18 | exact | exact | exact | 39.03 of 65.17 |
  | `ALDI2.jpg` 7 lines | **7 of 7** | not found | exact | not found | 17.43, exact |

  What is left on each is **OCR quality, not parsing**. `ALDI1.jpg` is deeply
  crumpled with diagonal shadows across the item block, and the engine returns
  `Lerge Eggs` with no price, `740saq` for an item number and `Cheesecake Sampl
  ert`; seven lines never arrive in a state any parser could use. On
  `ALDI2.jpg` every purchased line is read perfectly, and the two figures it
  misses are the engine dropping characters: `SUBTOTAL 17` with the `.43` gone,
  and the grand total -- printed as a large letter-spaced `T O T A L` -- not
  returned at all.

  The Walmart readings are unchanged by all of this, which was checked rather
  than assumed.
- **The same receipt photographed twice, which settles how much the photograph
  matters.** `ALDI1.jpg` is deeply crumpled with shadows across the item block;
  `ALDI1_new.jpg` is the identical receipt shot flat and evenly lit. Same
  camera, same engine, same parser:

  | | crumpled | flat |
  | --- | --- | --- |
  | Line items found | 11 of 18 | **17 of 18** |
  | Amounts exactly right | 11 of 18 | **17 of 18** |
  | Line items sum to | 39.03 | **60.59** of 65.17 |
  | Subtotal / tax / total | all exact | all exact |

  One line is missing from the flat photo -- 2% Milk at $4.58, which is exactly
  the remaining shortfall. **Photograph quality is the largest single lever on
  this application's accuracy, and it belongs to the user, not the code.** Flat,
  evenly lit, shot square on: that is worth more than any tuning available here.

  What it does *not* fix is the item names. Both photographs return 3 of 18
  names exactly; the flat one still gives `Bik Angs stew Meat`, `NFIGrk Yog
  yan` and `Whble Whitq Mushrm`. Amounts survive a bad photograph far better
  than words do, which is why categorisation had to be made noise-tolerant
  rather than relying on clean text (11.35).
- **Rotation is a solved problem, and the question raised in 1.5.1 is closed.**
  `Walmart3.jpg` is stored sideways -- 3111x1280 with EXIF orientation 8 -- and
  reads correctly anyway, because `app/images.py` calls `ImageOps.exif_transpose`
  when the image is imported. A photograph taken in portrait and stored rotated
  is the overwhelmingly common case, and it is handled. **Do not build
  try-every-orientation logic on the strength of a sideways-looking preview**;
  check for an EXIF orientation tag first. A genuinely rotated image with *no*
  EXIF tag remains untested.
- **The language switch and the item translation** (`tests/test_i18n.py`, 22
  tests): that English passes through untouched, that an untranslated string
  falls back to English rather than showing a key, and that an unknown language
  code does not raise. Three guard specific traps rather than behaviour — every
  translation key must still match a string constant somewhere in `app/`, so
  editing an English string cannot silently orphan its Chinese; the engine
  picker and the status filter must round-trip through their translated labels
  and still store English; and the glossary must outrank both the services and
  the cache. As with the lookup tests, nothing here touches the network.
- **The product-name lookup** (`tests/test_product_lookup.py`, 49 tests): the
  check-digit repair against all eight real receipt codes, both sources' parsers,
  the fallback from food database to catalogue, and — the ones that matter — that
  a refused request is never cached as "product unknown", and that one source
  running out of quota does not silence the other. No test touches the network;
  the single HTTP function is replaced with scripted answers.
- **Every theme's colours** (`tests/test_theme.py`, 60 tests): each palette is
  complete and well-formed; the accent clears 3:1 on that palette's own chart
  surface and every text pair clears its floor; the accent is at least ΔE 15
  from each status colour; and the cycle order crosses from dark to light
  exactly once. These run against *every* theme, so one added later cannot skip
  the check.
- **Windows OCR layout handling** (`tests/test_windows_ocr.py`, 22 tests): rows
  are rebuilt top-to-bottom and left-to-right from the stored word boxes; the
  grouping is proved scale-invariant (the same words at 4× the size group
  identically) and tolerant of a row that drifts downwards across the page; each
  of the three amount repairs is checked, and checked *not* to fire inside an item
  description; and the reading of the real receipt is asserted end to end.

**By hand, against the built `Bookkeeping.exe` copied to an empty folder** (no
Python, no venv, no source) — re-run for 1.9.4 with `tools\verify_exe.py`:

- It opens a window titled `Bookkeeping 1.9.4`, class `TkTopLevel`, with the
  receipt icon in the title bar and the File/View/Help menus.
- It creates `data\bookkeeping.db` (60 KB, seeded) and `data\bookkeeping.log`
  beside itself.
- **The frozen build really reaches Windows OCR.** Its own log records
  `Engine windows ready — Windows OCR (en-GB)`, which is the only way to know
  PyInstaller bundled the `winrt` bindings correctly — an import that works from
  source proves nothing about the .exe. This is what `_log_engines()` in
  `app/ui/window.py` exists for.
- A **second launch is refused** and exits 0. Since 1.8.1 the duplicate runs
  on a Windows desktop of its own, so its dialog never reaches the screen,
  and the refusal is confirmed from the log line rather than a window title
  (§11.36).
- **Closing the window exits cleanly** (code 0), and `--version` afterwards
  prints `1.9.4` — proving the lock was released.
- Startup measured at **3.4–3.6 s** to a visible window, over three runs.
- The four pages and every theme were screenshotted from the running program and
  inspected: list, review pane with the image and a real arithmetic flag, charts
  with correct proportions and value labels, 15 categories and 176 rules, and the
  settings controls.
- **The frozen build carries a working TLS stack**, which the online lookup
  needs: unpacking the running one-file .exe shows `_ssl.pyd`, `libssl-3.dll`,
  `libcrypto-3.dll` and `_socket.pyd`, and Python on Windows reads its
  certificate authorities from the operating system's own store rather than from
  a bundled file. **What has *not* been observed is a live lookup made by the
  .exe itself** -- the program has no command-line hook for scanning, so every
  measured lookup in this document was made from source. The packaging is
  verified; the round trip through the frozen binary is inferred. A `--self-test`
  flag would close that, and is the cheapest way to do so.
- **Both themes added in 1.4.0 were confirmed from the frozen build**, not only
  from source: the stored theme was set in a portable copy's own books, the .exe
  relaunched, and the window it drew photographed. Dracula and Solarized both
  rendered correctly at `Bookkeeping 1.4.0`.

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
  receipt. The .exe is unsigned and carries no Mark of the Web here, so the
  SmartScreen dialog is **predicted from the mechanism, not observed** — nobody
  has yet downloaded this build onto a machine that would show it (§7).
- **The product lookup has been measured against three receipts' barcodes.**
  The 12-of-20, 8-of-20 and 7-of-9 figures are those receipts', on two days,
  from one IP address. What the free services know about a *different* shop's goods is
  unmeasured, and both are third parties who may change their terms, their
  rate limits or their JSON at any time — the code treats every one of those as
  a non-answer rather than a crash, but the coverage figure would move.
- The pacing constants (1.2 s per host) were tuned empirically against these two
  services from this address. They are almost certainly conservative on a
  different connection and possibly still optimistic on a throttled one.

### Where to pick up

The state as of 1.9.5, for whoever reads this next:

- **The application works with nothing configured**, which is the single most
  important fact here. Before 1.3.0 a fresh copy could not read a receipt at all
  without an API key or a Tesseract install, and the first real receipt it was
  ever given failed with four red flags and no data. Windows' own OCR now covers
  that case on any Windows 10/11 machine.
- **Everything is committed. Nothing since 1.4.0 is pushed or tagged.** Thirteen
  commits sit on `main` locally, `e0235a5` (1.4.1) through `51a3560` (1.9.4);
  both `origin` (the private GitHub repository) and `mirror` are still at
  `1437d53`. `dist\Bookkeeping.exe` is built from `51a3560` and passes
  `tools\verify_exe.py`.

  The quality gate blocked the push twice, correctly both times, and each block
  found something a test run could not: a comment that stated a false reason
  (§11.44), and behind it a real defect that silently dropped a purchased line.
  Both are fixed. **A third review round was started and then stopped at the
  user's request**, so the gate is simply un-run at the current digest rather
  than failing — there is no known-outstanding finding. Pushing needs one clean
  round of safety-engineer and quality-engineer, then the thirteen tags.
- **The direction of travel is online, by the user's decision (August 2026):**
  "my ultimate goal for this software is for it to operate online, as internet
  connectivity is required to query certain information and provide accurate
  results." The product-name lookup in 1.5.0 is the first piece of that. Offline
  operation stays a supported fallback rather than the target — do not remove it,
  but do not treat "works offline" as a reason to reject a networked feature.
- **The API key verification is deliberately closed, not pending.** The user
  cannot supply a key at present and asked, in August 2026, that it be skipped.
  So the Claude vision engine remains exercised only against a local mock of the
  Messages API (`tests/test_claude_engine.py`) — the app is proven to handle a
  reply correctly, not to have received a real one. That is a known and accepted
  gap. Do not reopen it as a blocking item or plan work around closing it; if a
  key ever appears, the comparison to run is against the fixture in
  `tests/test_real_receipt.py` (merchant `null`, date `null`, subtotal 141.94,
  tax 7.50, total 149.44, 24 lines).
- **The one genuinely open verification: a receipt that is not a supermarket.**
  Five real receipts have now been through the whole pipeline as image files
  (section 9), across two chains, and every single one found a defect its
  predecessors could not -- 11.29 through 11.34 all came from that. Aldi alone
  found five, and one of them made a whole receipt read as zero items.

  Both chains are still grocery tills printing the same broad shape: a
  description, an item number, a right-aligned price, a tax flag. **A restaurant
  bill or a fuel receipt breaks that shape** -- amounts printed above the item,
  no item numbers at all, per-person subtotals, a tip line, litres at a price
  per litre. That is the next thing worth asking the user for, and on the
  evidence so far it will find something.

  The photographs live in `pictures\` as `Walmart1.jpg`, `Walmart2.jpg`, `Walmart3.jpg`, `ALDI1.jpg`,
  `ALDI1_new.jpg` and `ALDI2.jpg` and
  are gitignored, deliberately: a receipt is somebody's shopping and their
  payment method. Do not commit them, and do not paste their card or reference
  numbers into anything.
- **The known weaknesses**, if you are deciding what to build:
  1. **The barcode lookup is Walmart-shaped.** It resolves at best 12 of 20
     Walmart lines, and **0 of 18 at Aldi** -- Aldi prints six-digit internal
     article numbers, not barcodes, and no public database knows them. That is
     not a defect to fix: Aldi already prints readable names, so there is
     nothing to expand. But do not describe the feature as though it works
     everywhere.
  2. **A misread barcode digit yields a confidently wrong name** and cannot be
     detected (§11.31).
  3. **Item names survive a bad photograph far worse than amounts do** -- 3 of
     18 names exact even on a good photograph of an Aldi receipt. Categorisation
     was made tolerant of that (§11.35) rather than assuming clean text, and
     anything else built on the item name should assume the same.

  §13.2 (learn a rule from a reviewer's correction) remains the cheapest real
  improvement, because it turns each manual fix into a permanent one.
- **Do not** re-add a store-brand keyword rule (§11.18), reintroduce a web
  interface (§10), "simplify" the spec's excludes (§11.11), preprocess the image
  before Windows OCR (§10 — it was measured, and it makes the reading worse), let
  the amount repairs in `windows_ocr.py` fire outside the amount column (§11.21),
  add a theme without re-running the two colour checks (§11.26 —
  `tests/test_theme.py` runs them for you), parallelise the product lookups
  (§11.27), or scrape walmart.com (§3 — it answers a bot check, not a product).
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
25. **A tool must set `BOOKKEEPING_DATA` before it imports anything from `app`.**
    `app/db.py` resolves the data folder at *import* time, and importing any
    submodule of `app.ui` pulls in `app/ui/__init__.py` -> `window.py` -> `app.db`.
    Reading the theme list to build an argparse `choices=` list therefore
    imported the whole application before `--data-dir` had even been parsed, and
    silently pointed the run at the default books: the screenshots came out
    correct in every visible respect except that the reports were empty. The
    theme name is validated after the environment is set up instead
    (`tools/screenshot_pages.py`).
26. **A theme is not free to be any colour.** The accent fills the report bars,
    so each palette is checked for contrast on its own surface and for OKLab
    distance from the status colours. Do not judge the second with a contrast
    ratio: equally-dark colours of different hue score ~1:1 and look identical to
    that metric while being obviously different. `tests/test_theme.py` enforces
    both for every theme, so a palette added later cannot skip the check
    (`app/ui/theme.py`, `tests/test_theme.py`).

24. **A verification tool that cries wolf is worse than none.** `verify_exe.py`
    shelled out to PowerShell once per process-tree node *per poll*; under the
    disk load right after a build the loop ran so rarely that it missed an
    "Already running" dialog that was on screen the whole time, and reported a
    working build as broken. It now fetches the process table once and walks the
    tree in Python — the whole check went from a spurious failure to 15 seconds
    (`tools/verify_exe.py`).

27. **Do not parallelise the product lookups, and do not shorten the pacing.**
    Both free services answer `429` to a burst. Four concurrent workers — the
    obvious way to write it — made *both* refuse within a dozen calls and cut a
    receipt that resolves twelve names down to six. Serial requests at 0.7 s
    still drew refusals partway through; 1.2 s per host resolves all twelve. The
    reason this is worth a fixed note is that the failure is invisible: a refused
    barcode and an unknown product produce exactly the same empty result, so the
    feature looks merely mediocre rather than broken
    (`app/lookup/product_names.py`, `tests/test_product_lookup.py`).

28. **"I could not ask" must never be cached as "nobody knows".** The first
    version treated any non-200 as a miss and wrote it to `product_name`, so a
    single rate-limited moment would suppress a perfectly resolvable product for
    thirty days — and the cache would look identical to one holding a real miss.
    Only `200` and `404` are answers now; everything else raises, and raising
    means nothing is recorded. Related: one source running out of quota must set
    *that source* aside, not end the batch. When UPCitemdb's daily allowance was
    spent, a single refusal abandoned the whole receipt and the groceries Open
    Food Facts would happily have named came back blank — 5 of 20 rather than 8
    (`app/lookup/product_names.py`, `tests/test_product_lookup.py`).

29. **An item with no printed name must still be counted.** Some lines carry a
    barcode where the description belongs -- `756809105667 756809105660 5.88 X`
    on a real receipt. The guard that rejects a "description" with no letters,
    which exists to keep phone numbers and barcodes out of the item list, threw
    the whole line away. The only symptom was $5.88 missing from a $23.52
    receipt: no error, no warning, just a subtotal that would not reconcile.
    A description that is *nothing but* a 9-14 digit barcode is now accepted;
    everything else the guard rejected, it still rejects, and a test asserts a
    phone number is still not an item (`app/extract/receipt_text.py`,
    `tests/test_units.py`).

30. **Goods sold by weight print across two lines, and the parser must join
    them.** The name and barcode are on one line with no price at all, and the
    weighing is on the next:

        GINGER ROOT   000000004612 0 F
           0.42 lb @ 1.00 lb / 3.62         1.52 N

    Parsed a line at a time the money came out right and the name did not -- the
    item was called "0.42 lb @ 1.00 lb / 3.62", which is useless in a report and
    matches no categorisation rule. `_find_items` now carries a name that
    arrived without a price forward by exactly one line. One line, deliberately:
    carrying it further would attach a stale name to an unrelated item, and
    there is a test for that. The rate reads `<weight> lb @ 1 lb /<price>`, so
    the quantity and unit price are recovered too (`app/extract/receipt_text.py`,
    `tests/test_units.py`).

31. **A misread barcode digit produces a confidently wrong product name, and
    this cannot currently be detected.** Read the whole entry before trying to
    fix it, because the two obvious fixes were tried and measured and neither
    works.

    On `Walmart1.jpg`, 19 of 20 barcodes were read exactly right. The twentieth,
    `AIM TP 5.5OZ` (toothpaste), was read `063200000930` instead of
    `033200000930` -- one digit. The lookup returned **"Audi A5 8f7 3.0d
    Cylinder Head Gasket"**, and the review pane presented it as calmly as any
    correct name.

    The reason it cannot be caught is worth stating plainly: **a UPC's check
    digit exists precisely to detect a single misread digit, and this project
    throws that protection away by design.** Walmart does not print the check
    digit (11.28), so it has to be recomputed -- and a recomputed digit is
    consistent with whatever digits were read, right or wrong. The one piece of
    error detection barcodes have is unavailable here.

    Two mitigations were built and measured, and **both failed**:

    * **Checking the returned name against the printed abbreviation.** The idea
      is that `CLX PLNGR` should look like "Clorox Plunger". A subsequence score
      over 15 correct pairs and the one wrong pair gave the *wrong* pair 1.00 --
      "AIM" appears in order inside "Audi ... Cylinder Head Gasket" -- while the
      correct `PROTEINSUPPL` / "MUSCLE MILK GENUINE PROTEIN POWDER" scored 0.58.
      Short abbreviations match anything in a long enough string. `difflib` did
      no better: 0.18 for the wrong pair against 0.17 for a correct one.
    * **Agreement between OCR passes at different scales.** All three of 0.85,
      1.0 and 1.3 read the same wrong digits. The misread is stable, not noisy,
      so re-reading cannot vote it out.

    What shipped instead is honesty: the review pane labels the expansion
    **"from barcode: ..."**, so a reviewer reads it as a claim from a catalogue
    rather than as something printed on the receipt. The rate to quote is
    roughly one barcode in twenty on this camera and this receipt
    (`app/ui/receipts.py`, `app/lookup/upc.py`).

32. **Shrinking the image for OCR trades the total away for line items, so do
    it as a *second* pass rather than a replacement.** Windows OCR reads a
    smaller photograph better by some measures: at a 1176-pixel long edge
    instead of the stored 1568, `Walmart1.jpg` goes from 20 items to all 24 and
    its unexplained shortfall falls from $10.33 to $5.43.

    The first attempt simply replaced the full-size read with the smaller one,
    and had to be reverted: it **lost the TOTAL line on all three Walmart
    receipts** -- the single most important field on a receipt -- turned
    `Walmart2.jpg`'s exact 23.52 into 23.47, and made `Walmart3.jpg` drop its
    $19.97 line instead of its $2.97 one.

    1.8.0 keeps both readings instead of choosing between them (§3), which is
    what makes the smaller size usable at all. **Do not go back to a single
    pass at either size**; each is worse than the pair.

    A caution about how the first attempt was nearly shipped, and then nearly
    over-claimed. The measurement that made replacement look like a clear win
    counted "line amounts that match the printed ones", scoring the smaller
    image 34/36 against 30/36 -- a metric blind to *which* lines are missed and
    ignoring the header entirely. Later, a merge simulated outside the pipeline
    predicted 17/18 header fields and $25.47 unaccounted; run through the real
    pipeline it was 16/18 and $28.47. Both times the shortcut flattered the
    change. **Score a reading by what a user would notice -- the totals, and how
    far the lines are from the subtotal -- and measure it through the pipeline
    the user actually runs** (`app/extract/windows_ocr.py`).

33. **Five ways Aldi is not Walmart, and the parser assumed Walmart for all
    five.** This is the most useful entry in this section for anyone adding a
    third chain, because it is the list of things that turned out to be
    conventions rather than facts.

    1. **The tax flag is two letters** (`FA`, `NB`), and OCR sometimes splits
       one into `F A`. `_TRAILING_AMOUNT` allowed exactly one optional letter,
       so no Aldi line matched and a whole receipt read as zero items -- a total
       failure from a single character of pattern. The flag now allows one or
       two letters with an optional space, and **must be separated from the
       amount by whitespace**, or the weight line `(T) 0.02lb` parses as the
       amount 0.02 carrying the tax flag `lb`.
    2. **The item number is printed before the name** (`356387 Green Peppers`),
       where Walmart prints a barcode after it, and it is six digits rather
       than twelve so `_SKU` never saw it. `_LEADING_ITEM_NO` handles it, but
       only when no barcode was found, so Walmart's layout is untouched. The
       name may itself start with a digit -- `24ct Paper Bowl`, `2% Milk` --
       which is why the pattern does not require a letter after the number.
    3. **Weighed goods print the other way round.** Walmart puts the name on
       one line and the price on the next; Aldi puts the price on the first
       line and the weighing underneath. 11.30's carry-forward is unaffected
       because the Aldi continuation line has no amount, so it is simply
       skipped -- but do not assume the Walmart order is universal.
    4. **One tax line per band, and the zero band prints last.** Aldi prints
       `B-Taxable @5.500% 0.15` then `A-Taxable @0.00% 0.00`. The summary
       reader took the last match and reported no tax at all. A zero no longer
       displaces a figure already found. Two genuinely non-zero bands would
       still take the last; no receipt here does that.
    5. **`AMOUNT DUE` arrives clipped.** OCR returned `AMOUNT D 65.32`, and the
       grand total -- printed as a large letter-spaced `T O T A L` -- was not
       returned at all. Matching the prefix `AMOUNT D` recovers the total.
       Summary words are now also tested against a space-stripped copy of the
       line, so a letter-spaced heading would be recognised if the engine ever
       reads one; on these two receipts it never did
       (`app/extract/receipt_text.py`, `tests/test_units.py`).

34. **A payment line is recognised by its shape, not its name.** `Mastercard
    17.43` is not a purchase, and the word list catches it -- until OCR returns
    `Mas*ercard`, which it did, making the card total the only "item" on a
    seven-item receipt. What survives corruption is the shape: no item number,
    and an amount equal to one the receipt itself declares. Those amounts are
    gathered from every line already classified as summary -- which on Aldi
    includes a clean `Credit Card $17.43` printed further down, even when the
    brand line above it is mangled.

    **The guard that matters:** such a line is only dropped when other items
    were found. A genuine single-item receipt has one line equal to its own
    total, and emptying it would be far worse than keeping a stray one
    (`app/extract/receipt_text.py`, `tests/test_units.py`).

35. **The seeded rules were Walmart's vocabulary, not the language of shopping.**
    Two Aldi receipts categorised **1 item out of 18**. Nothing was broken; the
    rule list simply had no idea what a green pepper was. Every seeded pattern
    was either a Walmart abbreviation (`GV`, `SPGE`, `CLX`, `PLNGR`) or one of a
    dozen pantry staples, because every receipt the project had ever seen was
    from Walmart. Aldi prints plain English -- "Green Peppers", "Broccoli
    Crowns", "Flat Leaf Spinach" -- and matched none of it.

    Schema version 5 seeds 63 more: fresh produce, chilled and pantry staples,
    meat and fish, disposables, and merchant defaults for the supermarkets
    around the address on these receipts. Result: **17 of 17 and 7 of 7** on the
    two Aldi receipts, and Walmart improved from 12 of 20 to 15 of 20 as a side
    effect.

    Two things worth keeping in mind if this list grows again:

    * **The patterns have to survive OCR damage**, because the item names do
      not. `Whble Whitq Mushrm` still reaches Groceries -- partly on `MUSHRM`,
      partly on the merchant default, which exists precisely to catch what the
      keywords miss. Do not assume a rule will see clean text.
    * **11.22's substring trap applies harder to ordinary words than to brands.**
      `EGGS` is seeded and `EGG` is not, because LEGGINGS contains EGG. `BEANS`
      and not `BEAN`, because of BEANIE. `RICE`, `HAM`, `OATS` and `CREAM` were
      all wanted and all rejected -- they hide inside PRICE, SHAMPOO, COATS and
      SUNSCREEN, and a rule beats the model, so a false match is not a small
      thing.

    Seeding `SOURDOUGH` also broke two tests in `tests/test_store.py` that had
    used "SOURDOUGH BOULE" as their example of a description no rule matches.
    They are about precedence rather than about bread, so the example became
    "ARTISAN BOULE"; a test whose fixture quietly starts matching a rule has
    stopped testing what its name claims (`app/db.py`, `tests/test_store.py`).

36. **A refusal is not an error, and a verification must not look like a fault.**
    Two small things that together wasted real time. The single-instance guard
    reported "Bookkeeping is already open for these books" through
    `report_fatal`, which meant a red cross — the same icon as a crash — and an
    ERROR line in the log for something that had gone exactly right. And
    `verify_exe.py` proved that guard by launching a duplicate on the real
    desktop and closing its dialog a second later, so every build flashed what
    looked like an error past the user.

    The user asked what it was, and the honest answer took a while to reach
    because **an ERROR line in a log should mean the program broke.** Here it
    meant the opposite, which is precisely the noise that makes a log useless
    when something genuinely does go wrong.

    Both fixed. `report_startup(..., fatal=False)` uses an information icon and
    logs at INFO; the `--allow-second-window` hint moved out of the dialog,
    where it meant nothing to anyone who had simply double-clicked twice, and
    into the log. `verify_exe.run_unseen` starts the duplicate on a Windows
    desktop of its own, so it is refused and paints its dialog normally,
    somewhere nobody is looking. The check got sturdier in the process: it now
    confirms the refusal from the log line and the exit code rather than by
    matching a window title (`app/launcher.py`, `tools/verify_exe.py`).

    Two traps found while building the off-screen check, both worth keeping:

    * **A modal dialog moved out of sight still has to be dismissed.** The first
      version simply started the duplicate on the private desktop and waited for
      it to exit. It never did: `MessageBoxW` blocks until something clicks it,
      and on a desktop nobody is watching, nothing does. The check timed out and
      reported a perfectly good build as broken. `run_unseen` now finds the
      dialog with `EnumDesktopWindows` and posts `WM_CLOSE` to it there.
    * **`ctypes` truncates a 64-bit handle unless you declare the signature.**
      Every foreign function defaults to returning a C `int`, so `CreateDesktopW`
      returned a mangled handle and `CreateProcessW` rejected it — with the
      failure looking identical to "this machine will not allow a second
      desktop". Declaring `restype` and `argtypes` is not tidiness here; it is
      the difference between working and not.

37. **Do not add a third interface language.** The user asked for Chinese and
    said explicitly that nothing else should be added. `tests/test_i18n.py`
    asserts the set of languages is exactly `{en, zh}` so that adding one is a
    deliberate act rather than a drive-by; the cost is not the machinery but the
    200-odd strings somebody then has to keep correct for ever.

38. **A widget's own text cannot be the key it is looked up by.** The engine
    picker and the receipt status filter stored their English label in the
    combobox and then did `dict(ENGINES)[combobox.get()]`. In Chinese the
    combobox returns Chinese and the lookup finds nothing. `_value_for` and
    `_statuses_for` match on the *translated* label and hand back the English
    code, so the value written to the database is the same in either language —
    which everything else in the program depends on. Module-level tables that
    hold display text are the ones to check when adding to the interface: they
    are built at import, before a language is chosen.

39. **The Google Translate endpoint every snippet uses is blocked here.**
    `translate.googleapis.com/translate_a/single` answers `429 Too Many
    Requests` to the *first* request from this address — not after a burst, so
    pacing cannot help and retrying is wasted time. The endpoint Google's own
    Chrome extension uses,
    `clients5.google.com/translate_a/t?client=dict-chrome-ex`, works: twelve
    consecutive names at 0.5 s apart, no refusals. MyMemory is the fallback.

    The trap underneath is the same one as 11.28: **a refusal is not a missing
    translation.** Caching a 429 as "this name has no Chinese" would leave the
    item in English for ever, and the cache would look exactly like one holding
    a real answer. Only a service that actually replied may be recorded
    (`app/lookup/translate.py`, `tests/test_i18n.py`).

40. **A cash-rounding line is not a purchase.** Walmart prints `ROUNDING 0.04`
    between TOTAL and CHANGE DUE, in the item column, with an amount. It was
    read as a line item, which put four cents of money the receipt never spent
    into the books and broke the one check that says whether a reading hangs
    together.

    It surfaced only when a real scan was looked at line by line, which is worth
    noting: the figures reported for 1.8.0 counted it as a successful item, so
    "24 of 24 lines on Walmart1" was really 23 real lines plus this. The
    corrected totals are in §3 -- 69 line items rather than 70, and $28.47
    unaccounted rather than $28.43. **A count of items found is not a measure of
    items read correctly**, and this is the second time in this project that
    exact trap has flattered a change (see 11.32)
    (`app/extract/receipt_text.py`, `tests/test_units.py`).

41. **Closing the gap between the line items and the subtotal is not the same
    as reading the receipt better. Measured twice, rejected twice.** Both
    attempts made the headline number look much better while putting lines into
    the books that the receipt does not contain.

    * **Union of the two OCR passes**, keeping every line either found:
      $28.47 unaccounted → $15.57, and 72 of 79 printed amounts matched instead
      of 69. It also invented **5 lines**, because the passes read some
      descriptions slightly differently (`GV 1G SP` against `GV IG SP`) and
      nothing de-duplicates those.
    * **Greedy fill that never overshoots the subtotal** -- take the better
      pass, then add lines from the other only while they fit inside the
      remaining deficit. This looked like the careful version and produced the
      best number of all: **$2.77 unaccounted**. Of the six lines it added,
      **two were real and four were invented.** Walmart3 became perfect; on
      `ALDI1_new.jpg` it filled a $4.58 hole left by a missing milk with a $3.99
      line that is not on the receipt.

    Shipping either would have traded a property worth more than any of it:
    **the current reading invents nothing.** Across six photographs there are
    zero spurious lines. A missing line is visible -- the app says the items do
    not add up, and by how much -- while an invented line that makes the
    arithmetic work is invisible and wrong.

    This is the third time the same trap has caught this project (11.32, 11.40).
    The pattern is worth naming: **when a metric can be satisfied by adding
    something, it will eventually be satisfied by adding the wrong thing.** Any
    future attempt here has to be scored on lines matched *and* lines invented,
    never on the gap alone.

42. **A glossary sits in front of the translator, for words only a receipt
    explains.** `ME DEPOSIT` is Maine's bottle deposit; Google returns 我存款,
    "my deposit", reading ME as the pronoun. It is fluent, confident and wrong,
    and no tuning fixes it because the English genuinely is ambiguous -- only
    knowing the text came off a till roll resolves it.
    `translate.GLOSSARY` is checked before either service and short-circuits
    them entirely -- **and before the cache**, which is the part that is easy to
    get wrong. A term is usually added to the glossary *because* a wrong machine
    translation is already stored, so consulting the cache first keeps serving
    the wrong answer for ever. That is exactly what happened on the first
    attempt: the glossary was in place, the re-scan still showed 我存款, because
    it never got as far as asking. Keep the list to terms actually seen on a
    real receipt and actually mistranslated
    (`app/lookup/translate.py`, `tests/test_i18n.py`).

43. **Correcting an item's name drops its barcode expansion.** A looked-up name
    is only as trustworthy as the barcode the OCR read, and one misread digit
    produces a confident wrong answer nothing can detect (11.31) -- a toothpaste
    came back as an Audi cylinder head gasket, and once the interface was in
    Chinese it came back as 奥迪 A5 气缸盖垫片, which looks even more
    authoritative. Since the wrong name cannot be caught automatically, the
    reviewer editing that line is taken as the signal: they have said the
    machine misread it, so the machine's other guess about the same line goes
    too. An untouched line keeps its expansion, because it is usually right
    (`app/ui/receipts.py`, `tests/test_ui.py`).

44. **A summary word must not hide inside a product name.** `CASHEWS` contains
    `CASH`, so a bag of cashews was thrown away as a summary line and its money
    with it -- the receipt simply came up short with nothing to say why.
    `Q-TIPS` had the same fault through `TIP`. Matching is now on whole words.

    The whole-word rule immediately broke something, which is the useful half of
    this entry: Windows OCR reads Walmart's `TAX1` as **`TAXI`**, and `TAX` no
    longer covered it, so $7.50 of tax landed in the item list. `TAXI` is listed
    explicitly, with the trade written down -- a genuine taxi fare line would be
    read as summary, which is acceptable for something that reads shop receipts.

    This is the same substring trap as 11.22, in a different list, and the fix
    round walked straight into it a third time: `_PAYMENT_WORDS` was left on
    substring matching, so `CHICKEN TENDERS` matched `TEND`, `CASHEWS` matched
    `CASH` and `CARDAMOM` matched `CARD`. That one was not cosmetic either --
    a payment line's amount disqualifies an unnamed item (11.45), so a real
    `LOOSE PRODUCE 8.99` line disappeared because chicken tenders cost the same.
    Both lists now go through `_whole_words()`.

    Worth stating as a rule, since three lists have now had it: **any short word
    matched as a substring will eventually match inside a real product name.** A
    fourth list of this kind should use `_whole_words()` from the start
    (`app/extract/receipt_text.py`, `tests/test_units.py`).

45. **Only a payment line's amount may disqualify an item.** The rule that drops
    an unnamed line whose amount equals one the receipt declares (11.34) was
    taking that amount from *every* summary line, including the tax. An unnamed
    item costing the same as the tax would have been discarded for a
    coincidence. Only lines that actually hand over money contribute now
    (`app/extract/receipt_text.py`).

46. **`pictures/` is ignored as a directory, not by file extension.** It held
    six `.jpg` files and was covered only because an unanchored `*.jpg` rule
    happened to match them. A receipt saved as `.heic` -- the iPhone default --
    or `.webp`, or a scanned `.pdf`, would have been untracked *and* unignored,
    and the next `git add -A` would have published somebody's shopping along
    with their card digits and reference numbers. That is the one mistake in
    this project that cannot be undone, so it is now closed by path rather than
    by extension (`.gitignore`).

47. **A comment belongs to the line under it, and an insertion can steal it.**
    Two comment blocks ended up describing the wrong thing, both because
    something new was inserted between the prose and the constant it explained:
    docTR's half-median-word-height rule came to sit above `SECOND_PASS_EDGE`
    (a pixel count, nothing to do with word height) while `ROW_TOLERANCE = 0.5`
    was left bare, and the note about a tax flag needing whitespace came to sit
    above `_QTY_AT_PRICE`, which has no flag group. Neither comment was edited;
    the code under them was. **When inserting between a comment and its
    definition, move the comment or leave a blank line** -- and this is the
    class of finding the quality gate exists to catch, since nothing about it
    shows up in a test run (`app/extract/windows_ocr.py`,
    `app/extract/receipt_text.py`).

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
  Tags that exist: `v1.0.0`, `v1.0.1`, `v1.1.0`, `v1.2.0`, `v1.2.1`, `v1.2.2`,
  `v1.3.0`, `v1.4.0`. **1.4.1 through 1.9.4 are committed but untagged**,
  deliberately: a tag that is never pushed is a local-only fact, and the fix
  commits kept moving where the newest version belonged. Create them at the same
  time as the push, one per version, against the commit that bumped `VERSION`.
- Tracked: all source, `Bookkeeping.spec`, `build.bat`, `run.bat`, `make_icon.py`
  and `assets/icon.ico` (generated, but the build needs it).
- Ignored: `data/` (personal), `dist/` and `build/` (regenerable). **Because the
  .exe is not in Git, `build.bat` keeps the previous one as
  `dist\Bookkeeping.previous.exe` — that is the only way back from a bad build.**

`origin` is <https://github.com/Micheal-Jiaming/Bookkeeping>, created 2026-08-29.
**It is private and stays private** — that is a standing instruction from the
user, not a default to revisit, so do not offer to make it public.

Receipt photographs are gitignored (`*.jpg`, `*.jpeg`, `*.png`, unanchored so the
rules cover `shots\` output too, with `!assets/icon-preview.png` for the program
icon). A real receipt is somebody's shopping, their payment method and often
their address; the one this project is measured against lives in the tests as OCR
word boxes and a transcription instead of as an image.

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
10. **Code signing**, to stop the SmartScreen warning. §7 sets out the mechanism
   and the options with costs; the cheapest real answer is Azure Artifact
   Signing at about $10/month, and doing nothing is defensible.
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
| 1.4.0 | 2026-08-29 | **Two more themes.** Five candidate palettes were rendered in the real window and shown to the user, who chose **Dracula** (dark violet) and **Solarized** (warm cream) to sit alongside the existing dark and light. `View -> Theme` became a submenu marking the active theme, replacing a "Switch light / dark" command that no longer described what it did; the header button still cycles, now in an order that groups dark themes before light ones. The contrast and status-distinctness checks that were previously done by hand are now `tests/test_theme.py`, running against every theme including future ones — they caught a candidate whose teal accent sat ΔE 8.2 from its own green "good" status. Fixes §11.25–§11.26. 221 tests. |
| 1.10.0 | 2026-09-01 | **The accuracy figures become reproducible.** Every quality number this project quoted lived in prose and covered an unrecorded set of photographs, so no later reader could tell whether a number moved because the code changed or because the corpus did. `tools/measure_accuracy.py` now runs the real engine over `pictures\` and reports items read, money unaccounted, header fields, and -- against a human transcription -- lines matched, missed and **invented**. Ground truth and the baseline are separate files and are never merged, because a harness that promotes its own output to truth passes for ever while drifting. `--check` guards invented lines as well as the money gap, so the two changes rejected in 11.42 would now fail automatically rather than by hand. Confirmed the known `DOVE BW 11OZ` defect independently: Walmart1 scores 23 matched, 1 missed, 0 invented, and the 5.47 unaccounted is exactly that line. 328 tests. |
| 1.9.5 | 2026-08-31 | Documentation reconciled against the code (/md-renew-check, full mode). The handoff section claimed everything was pushed and tagged; nothing since 1.4.0 is either, and that claim would have sent the next session looking for work already done. Twenty stated line counts were stale after the 1.5.0–1.9.4 work, the .exe verification still quoted 1.5.1, the tag list did not say the later versions are deliberately untagged, and the 22 language tests had no entry in the verified list. No code changed. |
| 1.9.4 | 2026-08-31 | **The fix round repeated the bug it was fixing.** The gate blocked a second time, correctly. `_SUMMARY_WORDS` had been converted to whole-word matching but `_PAYMENT_WORDS` had not, so `CHICKEN TENDERS` matched `TEND` — and because a payment line's amount disqualifies an unnamed item, a genuine `LOOSE PRODUCE 8.99` line vanished because the tenders cost the same. `CASHEWS` and `CARDAMOM` did it through `CASH` and `CARD`. Both lists now share `_whole_words()`. The comment claiming the trap was closed had also been orphaned onto the one list where it was still open — the same insertion-steals-a-comment fault as §11.47, in the commit that fixed §11.47. 310 tests, six photographs unchanged. |
| 1.9.3 | 2026-08-31 | **What the quality gate caught.** The first push attempt was blocked, and the review was right to block it. Two comment blocks had been orphaned from the constants they explain by later insertions (§11.47), and two claims in `app/db.py` were false — one said all the new rules are priority 60 when 29 of them are merchant rules at 200, the other said CREAM was rejected for sitting inside SUNSCREEN, which it does not. Reading around those findings turned up a real defect: `CASHEWS` contains `CASH`, so a bag of cashews was discarded as a summary line and its money with it; `Q-TIPS` had the same fault through `TIP`. Summary words are matched on whole words now, which in turn exposed that OCR reads `TAX1` as `TAXI` (§11.44). Also narrowed which amounts can disqualify an item (§11.45) and closed a real publishing risk: `pictures/` was ignored only because today's files happen to be `.jpg` (§11.46). 308 tests. |
| 1.9.2 | 2026-08-31 | **Three problems from a real scan, two fixed and one deliberately not.** `ME DEPOSIT` was translating as 我存款 — "my deposit" — because ME is Maine and only a receipt knows that; a glossary now sits in front of the translator for words only a till roll explains (§11.42). Correcting an item's name now drops its barcode expansion with it: the wrong-name problem cannot be detected automatically (§11.31), so the reviewer editing the line is taken as the signal that the machine misread it (§11.43). The third — a line the OCR simply never read — was attacked twice and both attempts rejected: a union of the two passes, and a greedy fill that never overshoots the subtotal, cut the unaccounted money from $28.47 to $15.57 and $2.77 respectively, and invented 5 and 4 lines to do it. The reading currently invents nothing across six photographs, which is worth more than a better number (§11.41). 306 tests. |
| 1.9.1 | 2026-08-31 | **A cash-rounding line was being spent.** Re-scanning the real books after 1.9.0 showed `ROUNDING 0.04` sitting in the item list: Walmart prints it between TOTAL and CHANGE DUE, in the item column, with an amount, and it was read as a purchase. Four cents of money the receipt never spent, and it broke the check that says whether a reading adds up. It also means the 1.8.0 figures counted it as a success — "24 of 24" on Walmart1 was 23 real lines plus this — so the totals in §3 are corrected to 69 items and $28.47 unaccounted (§11.40). 301 tests. |
| 1.9.0 | 2026-08-31 | **The interface, and the receipts, in Chinese.** View → Language switches the whole interface between English and Chinese, and the item names read off a receipt are machine-translated to match. The English text is the translation key, so the source still reads as prose and a missing entry falls back to English rather than showing a bare key; a test compares every key against the string constants the code actually contains, so editing an English string cannot silently orphan its translation. Chinese only, asserted by a test (§11.37). Three things that had to be got right: a combobox hands back the text on screen, so settings looked up by their English label broke (§11.38); Segoe UI has no Chinese glyphs, so the font family follows the language; and switching language rebuilds every widget, exactly as switching theme does. Item names are translated during the scan and cached in a `translation` table (schema v6), never while drawing the review pane — a network request on the interface thread would freeze the window. The Google endpoint every snippet on the internet uses is blocked from this address on the very first request; the one its Chrome extension uses works (§11.39). 300 tests. |
| 1.8.1 | 2026-08-31 | **A refusal stops looking like a crash.** The single-instance guard announced itself with a red error icon and wrote an ERROR line to the log for something that had gone right, and `verify_exe.py` proved that guard by flashing the dialog across the real desktop during every build — which the user reasonably took for a fault. The message now uses an information icon and logs at INFO, the `--allow-second-window` hint moved from the dialog into the log, and the verification runs the duplicate on a private Windows desktop, confirming the refusal from the log and the exit code instead of a window title (§11.36). No behaviour under test was suppressed. 282 tests. |
| 1.8.0 | 2026-08-31 | **Every receipt is now read twice.** With no API key and no paid services on the table, the remaining accuracy had to come from what is already installed — so Windows OCR runs over each image at the stored size *and* shrunk to a 1176-pixel long edge, and the two readings are combined. Neither size wins outright: full size is better at the summary block, the reduced size is better at line items (§3). The one judgement — which set of line items to keep — is made by the receipt itself, whichever list lands closer to the printed subtotal, so a pass that invents lines is rejected by its own arithmetic. Measured through the real pipeline with the second pass off and on: summary figures found 14 of 18 → **16 of 18**, money the line items could not account for **$44.02 → $28.47**, line items found 66 → 69, and no receipt worse on any measure. Cost is about two tenths of a second. This supersedes the 1.5.2 decision to revert downscaling: replacing the full-size read was wrong, adding to it is right (11.32). |
| 1.7.0 | 2026-08-31 | **Categorisation learns the language of shopping, and a controlled test of photo quality.** The user re-photographed one Aldi receipt flat and evenly lit, which answered a question the project could not answer for itself: the identical receipt went from 11 of 18 line items to **17 of 18**, with the missing $4.58 being exactly the remaining shortfall. Photograph quality is the largest single lever on accuracy and it belongs to the user, not the code (section 9). What it did not fix was the item *names* — 3 of 18 either way — so the rules had to be made noise-tolerant instead. Schema v5 seeds 63 more keyword rules: fresh produce, chilled and pantry staples, meat and fish, disposables, and merchant defaults for the supermarkets near these receipts. Categorisation on the two Aldi receipts went from **1 of 18 to 24 of 24**, and Walmart improved from 12 of 20 to 15 of 20 as a side effect. Choosing those patterns re-applied 11.22's substring trap, which bites harder on ordinary words than on brands: EGGS and not EGG because of LEGGINGS, BEANS and not BEAN because of BEANIE, and RICE, HAM, OATS and CREAM all rejected outright (11.35). 282 tests. |
| 1.6.0 | 2026-08-31 | **A second chain: the parser stops assuming Walmart.** The user supplied two Aldi receipts, and they broke the reader badly -- one read as *zero* real items, its only "item" being the Mastercard line. Five structural assumptions were at fault, all recorded in 11.33: a two-letter tax flag (`FA`, `NB`) that a one-letter pattern rejected outright, an item number printed before the name instead of a barcode after it, weighed goods laid out in the opposite order, one tax line per band with the zero band last, and a clipped `AMOUNT D` where `AMOUNT DUE` was expected. Payment lines are now recognised by shape rather than by name, because OCR turned `Mastercard` into `Mas*ercard` (11.34). Result: `ALDI2.jpg` reads all 7 items with every amount exact and summing exactly to the printed subtotal; `ALDI1.jpg` -- a badly crumpled photo with shadows across the item block -- reads 11 of 18 with subtotal, tax and total all exact. What it still misses there is OCR quality, not parsing. The three Walmart readings are unchanged, which was checked rather than assumed. Photographs now live in `pictures\` and remain gitignored. 282 tests. |
| 1.5.2 | 2026-08-31 | **All three receipts through the real OCR engine, as images.** The user put the photographs on disk, so the pipeline was finally exercised end to end rather than on transcriptions. Results in section 9: the totals block is read exactly on two of three, every line item on `Walmart2.jpg`, and every reading is flagged honestly for what it missed. Two findings worth more than the numbers. **Rotation turned out to be a non-issue** -- the sideways photograph carries an EXIF orientation tag and `images.normalise` already honours it, so the try-every-orientation work floated in 1.5.1 is not needed (section 9). **A misread barcode digit produces a confidently wrong product name** -- a toothpaste came back as an Audi cylinder head gasket -- and it cannot be detected, because recomputing Walmart's missing check digit discards the only error detection a barcode has. Two mitigations were built and measured and neither worked; what shipped is the review pane labelling every expansion "from barcode:", so a reviewer weighs it rather than trusting it (11.31). Separately, shrinking the image for OCR was implemented, measured and reverted: it finds more line items but loses the TOTAL on all three receipts (11.32). 274 tests. |
| 1.5.1 | 2026-08-31 | **Two more real receipts, and the two defects they found.** The user supplied a second and third Walmart receipt, which between them broke the line parser in ways the first could not. An item printed with no name -- just its barcode where the description goes -- was thrown away entirely by the guard that keeps phone numbers out of the item list, losing $5.88 off a $23.52 receipt with no error of any kind. An item sold by weight prints its name on one line and its price on the next, so it was read with the right money and the name "0.42 lb @ 1.00 lb / 3.62". Both fixed, both now permanent fixtures; every amount on both receipts is read and sums exactly to the printed subtotal. The barcode repair was also tightened: it now rebuilds a check digit only when the printed code ends in the zero that marks a truncation, because the receipts contained two codes -- a bottle deposit and a produce PLU -- that are not barcodes at all, and a rebuilt code that happens to exist would put the wrong product on the line. One of those receipts also confirmed the whole zero-padding theory in a single line, printing `756809105667 756809105660` -- the true UPC beside its truncated form. Fixes 11.29-11.30. 274 tests. |
| 1.5.0 | 2026-08-31 | **Item names a person can read, and the first step towards online operation.** A till prints `CLX PLNGR`; the app now shows "Clorox Plunger & Toilet Brush with Carry Caddy" underneath it. The key was noticing that the twelve digits Walmart prints beside each line are *not a valid barcode* — it prints the first eleven and pads column twelve with a zero, so seven of eight codes on the real receipt fail UPC-A validation and every database refuses them. `app/lookup/upc.py` recomputes the check digit; Open Food Facts and UPCitemdb (both free and keyless) supply the names; a `product_name` table caches hits and misses alike (schema v4). Measured: 12 of 20 lines resolve with both services, 8 with one exhausted, and correct categories rise from 14/20 to 17/20 because the expansion feeds rule matching. Walmart's own site was tested and cannot be used — it answers a bot check, not a product. Added a Settings toggle stating exactly what leaves the machine, and a full written explanation of the SmartScreen warning (§7). Fixes §11.27–§11.28. 259 tests. |
| 1.4.1 | 2026-08-29 | Documentation reconciled against the code (/md-renew-check, full mode). Three errors in the categorisation section: it claimed 59 seeded rules when there are 113, cited `GREAT VALUE` as a seeded example after that rule was deliberately removed in schema v2, and said rules seed "only on a fresh database" when the guard is really "no built-in rule survives" and v3 migrates new rules into existing books. Also: the handoff section still described 1.3.0, the tag list and .exe verification figures were a release behind, the 60 theme tests were missing from the verified list, and the version-control section still said no GitHub remote existed. No code changed. |
