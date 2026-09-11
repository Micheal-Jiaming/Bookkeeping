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
- **Version:** 1.17.0 (see `VERSION`)
- **Ships as:** two builds from one spec — `dist\Bookkeeping.exe` (30.2 MB, Windows OCR, the portable one) and `dist\BookkeepingFull.exe` (125.5 MB, adds RapidOCR and reads markedly better). Windows x64, no installer either way
- **Stack:** Python 3.13 · **Tkinter** · SQLite · PyInstaller
- **Recognition:** Claude vision (`claude-opus-5`) primary; **RapidOCR** offline in the full build; Windows' built-in OCR offline in both, needing no key or install; Tesseract optional
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
4. Document everything in `Bookkeeping_record.md`, and keep it updated in step with the
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


### Naming a Costco line, where no barcode can (1.13.0)

The barcode route above is the better answer wherever it works, and at Costco it
cannot work at all. Costco prints a five- to seven-digit **item number** of its
own devising beside each line — 96716, 1199652 — not a UPC. `barcode_for`
correctly declines every one of them, so there is nothing to query and no
catalogue on earth that could answer if there were. This is Aldi's problem
(§9) again, and worse: Aldi at least prints readable names.

**Costco's own website is not the way round it, and this was measured rather
than assumed.** `costco.com` serves its home page to a program quite happily
(HTTP 200, 3.9 MB), and then answers both

* the catalogue search, `/CatalogSearch?keyword=1199652`, and
* any product page, `/….product.NNNNNNNNN.html`

with **403 Forbidden** and an Akamai "Access Denied" body — not a missing
product, a refusal. `search.costco.com`'s query API answers 403 as well. It is
the same wall walmart.com puts up, and it does not come down for a different
User-Agent, because it is not a User-Agent check. Do not spend another session
on it.

So the expansion has to come from what is already printed, which is `app/lookup/
shorthand.py`. Less than a catalogue gives, and not nothing: most of what makes
a Costco line unreadable is a handful of abbreviations the chain uses the same
way on every receipt it prints. `KS` is Kirkland Signature, `ORG` is organic,
`CROISS` is croissants, `500CT` is a pack size, and `KSDAILY` is the first of
those run into the next word.

**Why bother, when the printed name is right there on screen.** Two things
downstream read the name rather than showing it. `resolve_category` searches the
expansion as well as the printed text, and the Chinese translation is made from
it — and a general-purpose translator handed till shorthand produces confident
nonsense. Measured, printed against expanded:

| printed | Chinese from the printed name | expanded | Chinese from the expansion |
| --- | --- | --- | --- |
| `DRUMSTICKS` | 鼓槌 — the thing you hit a drum with | *(needs no expansion)* | 鸡腿, via the glossary |
| `BUTER CROISS` | 黄油克罗斯 | Butter Croissants | 黄油牛角面包 |
| `KS CAGE FREE` | KS 笼子免费 — "cage" and "free of charge" | Kirkland Signature Cage Free | 柯克兰招牌无笼 |
| `KS COFFEE` | KS咖啡 | Kirkland Signature Coffee | 柯克兰招牌咖啡 |
| `KSBLUEDISH` | 凯斯蓝迪什 — sounded out letter by letter | Kirkland Signature Bluedish | 柯克兰签名 Bluedish |

`DRUMSTICKS` is the one that shows where the boundary between the two mechanisms
falls. It needs no expansion — it is already complete English — and *that* is
exactly why it mistranslates. Complete English is what can be ambiguous, so it
belongs in `translate.GLOSSARY` beside `ME DEPOSIT`, not in the expander.

**What may go in the expander's tables, and what may not.** Only an abbreviation
with one meaning in a shop, which no ordinary word is spelled like. The rule the
rest of this project works to applies here with more force than usual, because
the expansion is shown to a reviewer as though it were the product: a guess that
fails is harmless, and a guess that succeeds puts somebody else's goods on the
line. So `GP WINGS`, `KSBLUEDISH` and `KS CAL 500CT` keep their `GP`,
`BLUEDISH` and `CAL`. Chicken wings, dish soap and calcium are each the obvious
reading and none of them is printed anywhere on the paper, and being *nearly*
right about what somebody bought is the failure this declines to risk.

`KS` is expanded only when the merchant is Costco, for the same reason: it is
Kirkland Signature there and two letters anywhere else. `ORG` and `CROISS` are
chain-neutral and apply everywhere.

**This is why the merchant matters twice.** Getting the shop wrong does not just
mislabel one field — it silently switches off the chain-specific half of the
expander, and it forfeits the merchant categorisation rule. Measured on this
receipt: with the merchant unread, 8 of 15 distinct lines land in
`Uncategorized`; with it read as Costco, none do.

### Reading a logo that OCR cannot read (1.13.0)

The store name is printed as a logo, and a logo is the hardest thing on a till
roll for OCR: large, stylised, and on the part of the paper that curls. The
Costco receipt gave the merchant field as `455 Scarborough Downs Rd` — the
street address printed underneath it — because an exact search for "COSTCO"
found nothing.

**How badly it is misread depends on the size the image happens to be**, which
is the finding that shaped the fix. The same six letters, from the same
photograph:

| read at | comes back as |
| --- | --- |
| the original file, 1280px long edge | `Cosrco` |
| the stored copy, 1176×1568 | *nothing at all* |
| the stored copy downscaled again — **what the app actually sees** | `Cesrco` |
| 1.25× | `=WHOLESAZE` — the logo's second word instead |
| 2.5× | `=WHOLESALE` — finally correct, and only that word |

There is no scale at which `COSTCO` is read correctly, and no two readings
agree, so corroborating one against another does not work either. A third OCR
pass at a larger size was measured and does not help.

`_find_merchant` therefore runs three passes of decreasing strictness, each
looking at fewer lines than the one before, because the weaker the test the
closer to the top of the receipt its evidence must come from:

1. **Exact substring**, over the header block — unchanged, and still what
   answers on every other photograph in `pictures\`.
2. **One character wrong** (`_within_one_edit`), first six lines, single-word
   names of six characters or more. Six is the floor because one edit away from
   a short word is simply another word: SHELL and SHELF differ by one, and a
   line reading SHELF must never be filed under Shell.
3. **Two characters wrong** (`_same_shape`), first three lines, and only when
   the candidate is the same length as the name and starts and ends with the
   same letter. MARKET is two substitutions from TARGET and Market Basket is a
   supermarket in the same state as this receipt — the first letter is what
   refuses it. SUBWAY against SAFEWAY is refused on length.

**Why a weaker match is acceptable here and nowhere else in this project.**
Everywhere else — rebuilding a barcode, expanding an abbreviation — a wrong
answer is invisible: it names a product the reviewer cannot check against the
paper. The merchant is the opposite. One field, at the top of the review pane,
beside a photograph of the receipt, wrong in a way anybody spots and fixes in a
second. The cost of being wrong is a visible field to correct; the cost of
refusing is a street address in the Merchant box and every line the shop would
have categorised left blank.

The merge between the two OCR passes needed the same idea. It filled a header
field from the second pass only when the first had left it empty, and the
merchant is never empty — when the logo is unreadable the fallback returns the
street address, which is a value, so it would keep the address and discard the
`Costco` the other pass really did recognise. `is_known_merchant` distinguishes
a recognised shop from a guess, and a recognised shop now wins whichever pass
found it.

### Looking up what the reviewer typed (1.16.0)

Expansion and translation used to run in exactly one place: during a scan. A
line added or renamed in the review pane afterwards therefore went through
neither, ever — and the user reported it as the application ignoring what they
had entered. It was not ignoring it. It had never been asked.

`pipeline.enrich_now` runs the same two passes over the stored rows instead of a
fresh reading, and `_save` queues it in the scan pool after every save. It
**fills blanks and nothing else**: amounts, categories, descriptions and the
receipt's status are left exactly as they are, so a confirmed receipt stays
confirmed and a category the reviewer chose is never second-guessed.

**Two different failures produced the same blank line**, which is why the report
was one complaint and the fix is two:

| line | what had happened |
| --- | --- |
| `DOVE BW 11OZ` | Added by hand, so nothing had ever looked at it. It translates perfectly well once asked — 多芬 BW 11OZ. |
| `EQJELLUBE80Z` | Asked twice, weeks earlier. The barcode resolved to nothing and both translators returned the input unchanged. Genuinely unnameable, and the miss was cached. |

The second is the one worth designing for. **A blank line reads as the program
not having bothered**, and that is exactly how it was read, so a question that
was asked and came back empty now says so: `no translation found` / 未找到译文
under the line, where the answer would have gone.

**Only where a question was actually put.** `FRENCH BREAD` needs no expansion and
carries a number-system-2 code that `barcode_for` declines, so nothing was asked
and nothing is said — a note on every plain-English line would be noise that
teaches the reader to stop looking. Two records make the distinction:

* `line_item.name_source = 'notfound'`, set when a barcode *was* resolvable and
  the catalogues had nothing. `NULL` still means nobody asked.
* a row in `translation` with a `NULL` zh, which the cache has always written.
  `lookup.cached_state` is new only in exposing it: `chinese_for` returns hits
  alone, so the pane could not tell a miss from a silence.

**A service that could not be reached is not a "not found"**, and the test suite
caught the first version of this getting it wrong. When `names_for_skus` raises,
the lookup has learned nothing about whether the product exists; writing
`notfound` would state a fact about the catalogue on the strength of a network
failure, and would stop the next save from trying again. The mark is made only
when the services actually answered.

**Refreshing the pane cannot be unconditional.** The pass is network-bound and
can take seconds, and the reviewer may well carry on typing while it runs. The
pane snapshots what it holds immediately after the save — which is what was
saved, with no edits on top — and refreshes when the pass finishes *only if that
snapshot still matches*. Otherwise it leaves the screen alone: a stale grey
subtitle costs nothing next to throwing away somebody's typing.

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

### A better offline reader, and what it costs (1.17.0)

Windows OCR is what makes this application work with nothing installed, and it
is the weakest part of it. The user put it plainly: *"the current visual
recognition model is not up to the mark"* — and was right, though not about the
model they had in mind. **Claude vision has never once run on their receipts**:
there is no API key, so every reading they have ever seen came from the offline
fallback.

**What was surveyed.** Against the constraints that actually bind here —
offline, CPU only, no key, and small enough to be a portable executable — most
of the strong engines disqualify themselves on weight. docTR, EasyOCR and Surya
all pull in PyTorch, roughly two gigabytes; the VLM readers (GOT-OCR2.0,
dots.ocr, PaddleOCR-VL) expect a GPU; PaddleOCR itself is the accuracy leader
and brings the PaddlePaddle runtime with it.

[RapidOCR](https://github.com/RapidAI/RapidOCR) is the one that fits: Baidu's
PP-OCR models converted to ONNX and run on ONNX Runtime, so neither PaddlePaddle
nor PyTorch is involved. Apache 2.0, models shipped in the wheel, nothing
downloaded at runtime, nothing sent anywhere.

**Measured, through the same parser and against the receipts the user confirmed
by hand** (which is what made this measurable at all — see 1.15.0):

| | Windows OCR | RapidOCR |
| --- | --- | --- |
| header fields correct | 30/30 | 30/30 |
| printed lines matched | 63 | **73** |
| lines missed | 14 | **4** |
| lines invented | 3 | **0** |
| item names exactly right | 37 | **59** |

The gains land where the failures were. Costco goes from 10 matched to 14 and
from 5 names to 10; on a full scan through the pipeline it reads **15 of the 16
items** where the Windows engine read 12, leaving 4.69 unaccounted instead of
59.22. Aldi's names go from 3 of 18 to 9. And it reads `COSTCO` off the logo on
the first try — the thing the three-pass merchant match in this section exists
because Windows OCR cannot do at *any* scale.

**The cost, measured rather than estimated.** `onnxruntime`, `numpy`, `opencv`
and 32 MB of weights take the frozen executable from 30 MB to **125 MB** and its
start-up from 3.5 to **5.2 seconds**, because a one-file build unpacks its whole
payload to a temp folder on every launch. Reading a receipt goes from about 0.4
to 1.6 seconds.

That collides with the promise this project has made since day one — one file
you can copy onto a USB stick — so **both builds exist**: `build.bat` gives the
30 MB portable one, `build.bat --full` the 125 MB accurate one, and a single
spec produces both so they cannot drift apart. The sizes, the commands and the
spec traps are set out in §7 and deliberately not repeated here: a fact kept in
two places is a fact corrected in one of them and missed in the other.

#### Three traps, all of which cost a build to find

**1. Loading WinRT first stops onnxruntime loading at all.** Build a
`WindowsOcrExtractor` and then a `RapidOcrExtractor`, and the second fails every
time with *"DLL load failed while importing onnxruntime_pybind11_state"*;
reverse the order and both work. WinRT initialises the thread's COM apartment,
and onnxruntime's extension module will not initialise underneath that.

This is not a laboratory curiosity: `engine_status` asks every engine whether it
is available at start-up, and Windows OCR answering that question is enough to
poison RapidOCR for the rest of the session. `rapid_ocr.py` therefore loads
onnxruntime at module import, and `app.extract` imports that module before
anything touches WinRT. **Do not make it lazy "for consistency" with the other
engines.**

**2. Answering "is this engine available?" must not build the engine.** The
other engines construct themselves in `available()` because doing so is
instant. RapidOCR loads three ONNX models and takes the better part of a second
— and `available()` runs on every draw of the Settings page and at every
start-up. Building it there took the test suite from 90 seconds to 377. It is an
import check now, and the cost is that a corrupt model file reads as available
until the first scan, which then fails with a message naming the real problem.

**3. Keeping something *out* of a build is harder than putting it in.** The
slim build came out at 97 MB — cv2 alone was 29 MB of it — because PyInstaller
follows an ordinary import even inside a function body, and then follows a
literal module name handed to `importlib.import_module` as well. Neither of the
usual tricks hides a dependency from it. The slim build names the exclusions
outright in `Bookkeeping.spec`, which is the honest place for them anyway: that
file is what decides what each build contains.

A fourth, smaller one: both builds come from one spec, and PyInstaller names its
work folder after the spec, so without an explicit `--workpath` each build threw
away the other's analysis and re-ran from scratch.

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

179 keyword rules and 15 categories are seeded on first run (`app/db.py`),
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
vs. total**, subtotal + tax ≠ total, **the receipt's own item count vs. the lines
read**, implausible tax (> 50 % of the total), engine confidence < 0.6, and
duplicate image (same SHA-256 already in the books). Tolerance is 5 cents,
because real receipts disagree with their own arithmetic by a cent or two on
weighted goods.

#### Counting the lines, not just the money (1.14.0)

The money check says how much is unaccounted for. It does not say how many lines
to go looking for, and that is the number a reviewer actually needs: it is what
tells them when they have finished. *"Off by 59.22"* leaves them counting; *"at
least 4 lines are missing"* does not.

**Every chain here prints the figure**, which is what makes the check general
rather than a Costco special case — and each prints it differently, so
`_find_items_sold` carries one pattern per dialect:

| chain | prints | shape |
| --- | --- | --- |
| Walmart | `ITEMS SOLD 21`, `# ITEMS SOLD 3` | count after the words |
| Aldi | `18 ITEMS` | count first |
| Costco | `TOTAL NUMBER OF ITEMS SOLD = 16` | OCR destroys both keywords and returns `TOTAL NUMBER OF 1 EMS sot-c 16`, so the pattern anchors on the surviving `NUMBER OF` and takes the number at the end of the line |

**A bare `SOLD 16` is deliberately not matched.** The second OCR pass of the
Costco receipt returns `sold 6` for the line that reads 16 on the paper. A
pattern loose enough to catch it would import a wrong count, and a wrong count is
worse than none: the flag it raises sends the reviewer hunting for lines that are
not missing. Every pattern therefore requires a surviving keyword, and the first
pass's 16 stands.

**Two kinds of line are not "items sold", and the tills agree.** Discounts, and
container deposits — Walmart prints 21 sold against 24 printed lines, and 7
against 9, the difference being three and two Maine bottle deposits. Excluding
both, the printed count agrees **exactly with all six of the user's confirmed
receipts**, which is the evidence the rule rests on. Deposits are matched on the
word `DEPOSIT`, the same way the seeded categorisation rule matches them, because
the wording is state-specific and the word is not.

**Only a shortfall is reported, and it is reported as "at least".** Reading more
lines than the receipt sold is real evidence of an invented line, but it is also
what a misread count looks like, so that direction is left alone. And because the
comparison is a net one — an invented line hides a missed one — the figure is a
floor rather than an exact count. On the Costco photograph the flag says four,
and six are genuinely absent.

The count is stored on the receipt row rather than recomputed, so the flag
survives a save: it is the receipt's own statement, not a field the reviewer
edits, and clearing it the first time a draft is saved would be worse than not
having it.

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
at 8**; migrations live in `_migrate` and each is written to be a no-op on a
database that already has the change, so they are safe to re-run. v2 removed the
`GREAT VALUE` rule (§11.18), v3 added the abbreviation rules the offline engine
needs, v4 added the `product_name` cache, v5 added the grocery vocabulary
(§11.35), v7 added `line_item.name_source` and the Costco nouns (§11.49),
and v8 added `receipt.items_sold` (§3).
v4 and v6 have no migration body because their pieces arrive through paths that
run on every open (`CREATE TABLE IF NOT EXISTS`, and `_seed_settings` inserting
any missing key). v3, v5 and v7 share `_add_missing_rules`, which skips any
pattern the user already has so a rule they deleted stays deleted.

**v7 was the first migration to add a column**, and v8 does the same, and why it needs a body at all is
worth keeping: `CREATE TABLE IF NOT EXISTS` leaves an existing table exactly as
it is, so the `name_source` line in `SCHEMA` only ever reaches a database
created after this version. Without the explicit `ALTER TABLE`, every older set
of books would keep a `line_item` with no such column and fail on the next scan.
`_has_column` guards it, because `ADD COLUMN` raises rather than shrugging when
the column is already there.

| Table | Purpose | Notes |
| --- | --- | --- |
| `receipt` | one row per receipt | status, image path + sha256, merchant (+ raw as printed), date, currency, subtotal/tax/tip/total in cents, payment method, `items_sold` (the count the receipt prints for itself), header category, engine/model/confidence, `raw_text` + `raw_response` for audit, `review_flags` JSON, timing, tokens, `cost_usd`, `error` |
| `line_item` | purchased lines | description (+ `raw_description` = the plain-English expansion and `name_source` = what filled it: `barcode`, `shorthand`, `model`, or `notfound` for a barcode looked up and not found), sku, quantity, unit price, amount, category, `category_source`, `is_discount`, `taxable` |
| `category` | expense categories | name (unique), colour chip, `is_builtin`, sort order |
| `category_rule` | keyword rules | field (`description`/`merchant`), match type (`contains`/`regex`), pattern, category, priority (lower first), enabled |
| `translation` | English name → Chinese | the machine translation cache; a `NULL` means the services were asked and had none |
| `product_name` | barcode → readable name | the online lookup's cache: repaired UPC, name, which source knew it, when. A `NULL` name is a real answer ("asked, nobody knew"), not a gap — see §3 |
| `setting` | key/value settings | engine preference, API key, model, effort, Tesseract path, auto-confirm, **sensitive-detail masking**, online lookup, item translation, **plus the interface's own state**: language, theme, window geometry, last page |

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
    rapid_ocr.py        RapidOCR: PP-OCR on ONNX Runtime (full build only)
    windows_ocr.py      Windows' own OCR + word-box row reconstruction
    tesseract_ocr.py    Tesseract, if the user installed it
    receipt_text.py     shared: receipt text → ExtractedReceipt
app/lookup/             readable product names, online and off
    upc.py              the check-digit repair a Walmart receipt needs
    product_names.py    Open Food Facts + UPCitemdb, paced and time-boxed
    shorthand.py        till abbreviations expanded locally, for chains with no
                        barcode to look up (Costco)
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

**There are two builds, and which one to use is a real choice** (§3, and the
build section below):

| | `Bookkeeping.exe` | `BookkeepingFull.exe` |
| --- | --- | --- |
| size | 30.2 MB | 125.5 MB |
| start-up | 3.5 s | 5.2 s |
| offline reader | Windows OCR | RapidOCR, falling back to Windows OCR |
| reads (of the confirmed lines) | 66 matched, 41 names exact | 81 matched, 76 names exact |

The slim one is the portable promise: small enough to send or carry. The full
one is the accurate one, and it is what to run on a machine you work at. They
share a data folder format, so the same books open in either.

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
build.bat --full
```

| command | produces | size | offline engine |
| --- | --- | --- | --- |
| `build.bat` | `dist\Bookkeeping.exe` | 30.2 MB | Windows OCR |
| `build.bat --full` | `dist\BookkeepingFull.exe` | 125.5 MB | RapidOCR, then Windows OCR |

Both come out of **one** `Bookkeeping.spec`, selected by the
`BOOKKEEPING_FULL_BUILD` environment variable that `build.bat --full` sets. One
spec rather than two, because two would drift and the comparison between the
builds would stop meaning anything. `--full` also pip-installs `rapidocr` and
`onnxruntime` if they are missing; the plain build neither installs nor bundles
them.

Each installs PyInstaller if missing, regenerates `assets\icon.ico` if missing,
**copies the previous build of that kind to `*.previous.exe`**, then builds.
About 90 seconds for the slim one, rather longer for the full. The backup copy
matters: build output is not in Git, so if a new build is broken that file is
the only way back.

Things in the spec that must not be "tidied up":

- **The slim build's `excludes` list is load-bearing, not tidiness.** It names
  `rapidocr`, `onnxruntime`, `cv2`, `numpy` and the rest outright. Two gentler
  ways of keeping them out were tried and neither works: PyInstaller follows an
  ordinary import even inside a function body, *and* it resolves a literal
  module name handed to `importlib.import_module`. Without the exclusions the
  slim build comes out at 97 MB instead of 30 — `cv2.pyd` alone is 29 MB of it.
- **`--workpath` per target.** PyInstaller names its cache after the spec file,
  and both builds share one spec, so without this each build discards the
  other's analysis and re-runs from scratch.

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
py tools\measure_accuracy.py --engine windows   # ...and for the slim build's engine
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

  **Which engine it measures (1.17.0).** `--engine auto`, the default, takes the
  first installed of RapidOCR → Windows OCR → Tesseract, which is what the
  application itself would pick. Until 1.17.0 the harness built a
  `WindowsOcrExtractor` unconditionally, so from 1.16.0 onwards `--check` was
  guarding an engine the full build had stopped using — silently, which is the
  worst way for a gate to be wrong. Claude is excluded from `auto`, and the
  deciding reason is not the money: its reading is not deterministic, so it
  cannot back a regression baseline. Name it explicitly to see what it does.

  Both engines, same eight photographs, same parser, one command each:

  | | matched | missed | invented | names exact | unaccounted |
  |---|---|---|---|---|---|
  | `--engine windows` | 66 | 19 | 3 | 41/66 | $101.95 |
  | `--engine rapid` | **81** | **4** | **0** | **76/81** | **$0.00** |

  **All eight photographs, every one checked line by line by the user on
  2026-09-09** and exported to truth by `tools/export_truth.py`. This is the
  first time the whole corpus has carried a human transcription, and the first
  to include a restaurant bill. `ALDI2` and `Walmart1` are read exactly — line
  for line and name for name — and Walmart1 is the receipt this project started
  on at 20 of 24.

  **Every difference that remains, against what the user confirmed:**

  | photograph | difference | cause |
  | --- | --- | --- |
  | `ALDI2`, `Walmart1` | none — exact, line for line and name for name | — |
  | `ALDI1_new` | `Chill Beans` read as `Chili Beans` | recogniser — see below |
  | `ALDI1_new` | `Green Onions` read as `Green Onionis` | recogniser |
  | `ALDI1_new` | `24 ct Paper Bowl` read as `24ct Paper Bow1` | recogniser: `l` read as `1`, and the space lost. The stray `356387` that used to trail it was a parser gap, closed in 1.17.2 (§11.76) |
  | `Walmart4` | `GV RY RD IC` read as `GVRY RD IC` | recogniser, lost space |
  | `Walmart2` | `756809105667` read as `...660` | recogniser, one digit |
  | `KFC1` | four combo components missed; subtotal blank | agreed to build, deferred for a second sample -- §12a item 6 |

  **Five are the recogniser and four are one agreed feature not yet built.
  The parser column is empty for the first time**, the three that stood there
  having been closed in 1.17.2 (§11.76). No amount is wrong anywhere,
  nothing is invented, and every receipt reconciles to the cent.

  **One of them is worth more than its score.** The user confirmed `Chill
  Beans`, which is what Aldi prints; the model returned `Chili Beans`. It is not
  misreading the ink, it is *correcting* it to the dictionary word — the same
  instinct that earlier turned Aldi's `Jalpeno` into `Jalapeno`, where the paper
  really did say `Jalapeno` and the older transcription was the thing at fault.
  Harmless on a bag of beans. Worth remembering on a product code, where the
  plausible correction is the dangerous one, and worth checking first if a name
  ever comes back subtly wrong in a way no OCR error explains.

  Four design decisions are load-bearing, and none should be undone casually:

  1. **Ground truth and the baseline are separate files and are never merged.**
     `tests/fixtures/receipts_truth.json` is what a human confirmed the paper
     says, and measures *accuracy*. `tests/fixtures/accuracy_baseline.<engine>.json`
     is what this code produced on some day, and measures *regression*. A harness
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
  4. **One baseline per engine, and a cross-engine comparison is refused rather
     than reported** (1.17.0). RapidOCR matches ten more lines per corpus than
     Windows OCR, so scored against the other's baseline every metric moves —
     a landslide improvement one way, a catastrophe the other, and neither is a
     measurement of anything the code did. `--check` reads the `engine` key the
     baseline records and declines outright when it does not match. Both files
     are kept because both builds ship: the slim build has no RapidOCR at all,
     so its gate has to guard Windows OCR. A baseline written before 1.17.0
     carries no key and is read as `windows`, which is a record of fact — that
     was the only engine the harness could run.

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
| `Bookkeeping_record.md` | this file | the whole documentation |
| `VERSION` | 1 | `1.12.1` |
| `README.md` | 207 | the public landing page: what it is, the measured accuracy, and the honest network boundary |
| `docs/screenshots/*.png` | 4 files | the README's images, from demo books via `--tight`; `receipts.png` has its Payment field blurred after capture (see the note in `.gitignore`) |
| `requirements.txt` | 54 | pinned to the versions actually installed and tested |
| `bookkeeping.py` | 24 | the entry point PyInstaller freezes |
| `run.bat` | 27 | run from source (development) |
| `build.bat` | 72 | build `dist\Bookkeeping.exe`, keeping the previous one |
| `Bookkeeping.spec` | 127 | PyInstaller build definition, with the reasoning inline |
| `make_icon.py` | 128 | draws `assets/icon.ico` (a receipt with a torn edge) |
| `assets/icon.ico` | — | 8 sizes, 16–256 px; generated but tracked, because the build needs it |
| `.gitignore` / `.gitattributes` | 95 / 1 | `data/`, `dist/`, `build/`, `.venv/`, caches and **every plausible receipt format** ignored — not just `.jpg`/`.png` but `.heic`, `.heif`, `.jfif`, `.webp`, `.avif`, `.bmp`, `.tif`, `.tiff`, `.pdf` — with the four README screenshots re-included **by name, not by directory**; `* -text` |
| `app/__init__.py` | 22 | package docstring / layout map |
| `app/store.py` | 777 | the service layer: receipts, categories, rules, reports, CSV |
| `app/ui/window.py` | 669 | the window: chrome, menus, navigation, poll loop, dialogs |
| `app/ui/receipts.py` | 820 | receipt list and the review pane |
| `app/ui/reports.py` | 356 | tiles, hand-drawn canvas charts, merchant table |
| `app/ui/theme.py` | 361 | four palettes, display scaling, ttk styling, shared widgets |
| `app/ui/settings_page.py` | 352 | recognition settings |
| `app/ui/rules.py` | 241 | categories and keyword rules |
| `app/ui/__init__.py` | 18 | the interface package's map |
| `app/pipeline.py` | 520 | scan orchestration, thread pool, engine fallback |
| `app/db.py` | 710 | schema, seed categories and rules, migrations, connections |
| `app/launcher.py` | 175 | data folder, logging, single-instance lock, error reporting |
| `app/categorize.py` | 131 | the precedence chain and rule matching |
| `app/validate.py` | 172 | arithmetic and sanity checks → review flags |
| `app/paths.py` | 103 | frozen vs. source paths; writable data folder with fallback |
| `app/money.py` | 66 | integer-cent money conversion |
| `app/images.py` | 66 | image normalisation (EXIF, downscale, PNG) |
| `app/settings_store.py` | 60 | settings read/write, secret masking |
| `app/extract/receipt_text.py` | 947 | shared: receipt text → `ExtractedReceipt` |
| `app/extract/rapid_ocr.py` | 325 | RapidOCR: better offline reading, full build only |
| `app/extract/windows_ocr.py` | 404 | Windows OCR engine + word-box row reconstruction |
| `app/extract/claude_vision.py` | 207 | Claude vision engine, pricing table, error mapping |
| `app/extract/base.py` | 189 | `ExtractedReceipt` schema + `Extractor` interface |
| `app/extract/tesseract_ocr.py` | 112 | Tesseract engine (parser now shared) |
| `app/extract/__init__.py` | 109 | engine registry and fallback order |
| `app/lookup/product_names.py` | 282 | Open Food Facts + UPCitemdb, paced, time-boxed, failure-tolerant |
| `app/lookup/__init__.py` | 135 | the barcode-name cache and the entry point the pipeline calls |
| `app/lookup/upc.py` | 70 | UPC-A check digit; the repair a Walmart receipt needs |
| `app/i18n.py` | 323 | interface language, the Chinese table, and the CJK font |
| `app/privacy.py` | 66 | masking the personal details a receipt carries — **display only** |
| `app/lookup/shorthand.py` | 136 | till shorthand expanded offline; the only route that works at Costco |
| `app/lookup/translate.py` | 277 | item names into Chinese, cached; Google then MyMemory |
| `tools/accuracy.py` | 298 | accuracy scoring: pure, no OCR, runs anywhere |
| `tools/measure_accuracy.py` | 323 | runs the chosen engine over `pictures\`, reports, guards regressions |
| `tools/make_sample_receipt.py` | 121 | synthetic Walmart receipt with known values |
| `tools/verify_exe.py` | 356 | drives the built .exe and checks it behaves (§7) |
| `tools/seed_demo.py` | 139 | fills a set of books with plausible demo receipts |
| `tools/mock_anthropic.py` | 135 | stand-in for the Messages API, for testing without a key |
| `tools/screenshot_pages.py` | 145 | opens the window and screenshots every page; `--tight` clips to the client area, which is **mandatory** for anything published |
| `tests/test_store.py` | 771 | the service layer, end to end with a stub engine |
| `tests/test_ui.py` | 803 | builds the real window and drives it |
| `tests/test_units.py` | 1196 | money, validation, precedence, OCR-text parsing |
| `tests/test_real_receipt.py` | 291 | the one real receipt this project has been tested against |
| `tests/test_claude_engine.py` | 265 | Claude engine against a local mock of the Messages API |
| `tests/test_theme.py` | 160 | every palette's contrast and status-distinctness |
| `tests/test_i18n.py` | 242 | the language switch, and machine translation of item names |
| `tests/test_product_lookup.py` | 539 | barcode repair, both lookup sources, the cache, the rate-limit paths |
| `tests/test_rapid_ocr.py` | 261 | the RapidOCR engine: box conversion, the load-order trap, the real photograph |
| `tests/test_shorthand.py` | 97 | till-shorthand expansion, and every abbreviation it refuses to guess |
| `tests/test_windows_ocr.py` | 298 | row reconstruction, amount repairs, the real reading |
| `tests/test_desktop.py` | 143 | data-folder fallback, the single-instance lock, arguments |
| `tests/test_accuracy.py` | 465 | the harness itself: invented lines, multiplicity, the truth/baseline split, engine matching |
| `tests/conftest.py` | 55 | temp-directory database fixtures |
| `tests/fixtures/receipts_truth.json` | 164 | ground truth: what a human confirmed each photograph says |
| `tools/export_truth.py` | 174 | turns receipts confirmed in the app into ground truth, into a gitignored file |
| `tests/fixtures/accuracy_baseline.rapid.json` | 141 | the RapidOCR baseline: what the code produced, for regression only |
| `tests/fixtures/accuracy_baseline.windows.json` | 141 | the same for Windows OCR, which is all the slim build has |
| `tests/fixtures/walmart_ocr_words.json` | — | the 161 words Windows OCR really returned for the real receipt |

13 227 lines of Python across the 51 tracked `.py` files. Not in version control: `data/` (the user's books),
`dist/` and `build/` (regenerable from the above).

---

## 9. What has and has not been verified

Verified on this machine (Windows 11, Python 3.13.11, 3840×2160 at 150 %),
2026-08-23, again on 2026-08-29 for 1.3.0 and 1.4.0, and on 2026-08-31 for 1.5.0:

**Automated — 447 tests pass** (`pytest tests/ -q`, ~92 s):

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

  **The table below was measured by hand at 1.6.0 and is kept for the record. It
  is superseded by `py tools\measure_accuracy.py`, which recomputes all of it
  and is the figure to trust** -- a hand-written table is a snapshot of one
  afternoon, and two of these cells silently stopped being true (see the note
  under it).

  | Photo | Items | Subtotal | Tax | Total | Lines sum to |
  | --- | --- | --- | --- | --- | --- |
  | `ALDI1.jpg` 18 lines | 11 of 18 | exact | exact | exact | 39.03 of 65.17 |
  | `ALDI2.jpg` 7 lines | **7 of 7** | not found | exact | not found | 17.43, exact |

  **Remeasured 2026-09-02, and two cells have moved since 1.6.0:**

  | Photo | Items | Subtotal | Tax | Total | Unaccounted |
  | --- | --- | --- | --- | --- | --- |
  | `ALDI1.jpg` | 11 | 65.17 | **0.00 -- wrong** | 65.32 | 15.45 |
  | `ALDI1_new.jpg` | 15 | 65.17 | 0.15 | 65.32 | 11.28 |
  | `ALDI2.jpg` | 7 | **17.43** | 0.00 | **17.43** | 0.00 |

  - **`ALDI2.jpg` now reads its subtotal and total**, which the 1.6.0 table
    records as "not found". Two-pass reading (1.8.0) is the likely cause; it was
    never written down, so the older table understates the parser.
  - **`ALDI1.jpg`'s tax is read as `0.00` and is not exact.** `65.17 + 0.00` does
    not equal the printed `65.32`, and the harness flags the arithmetic as BAD.
    The same receipt re-photographed as `ALDI1_new.jpg` reads `0.15`, which
    reconciles exactly, so **0.15 is what the paper says and `ALDI1.jpg` misreads
    it.** Whether that is a regression introduced after 1.6.0 or an error in the
    original hand-written table was **not determined** -- it needs a run against
    the 1.6.0 code to settle, which is a code investigation rather than a
    documentation one. Do not assume either answer.
  - **`ALDI2.jpg`'s "7 of 7" is a hand-verified claim from 1.6.0, not a harness
    measurement.** It has no transcription in `receipts_truth.json`, so the
    harness reports it as "nothing verified yet" and scores it on
    self-consistency only. Its items summing exactly to the printed subtotal is
    strong evidence and is not the same thing as a transcription. Transcribing
    this receipt is the cheapest way to turn the claim into a measurement.

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

### How the three chains differ, measured against a human-confirmed reading (1.13.0)

**Where these numbers come from, and why they are better than the ones above.**
In September 2026 the user went through all six receipts in the application by
hand, corrected every field and line, and confirmed each one. That makes the
books themselves a ground truth for those six photographs — established by
somebody holding the paper, not by a second model reading the same image — and
this table is the current engine scored against it. **This is now wired into the
harness** — `tools/export_truth.py` (1.15.0) writes those confirmations out as a
gitignored truth file, so `measure_accuracy.py` scores six of the seven
photographs against a transcription rather than against itself. The tracked
`tests/fixtures/receipts_truth.json` still holds only Walmart1, which is
deliberate and explained in §12a: aggregate figures may live in git, an itemised
list of one person's shopping may not.

Both columns of numbers are the same engine against the same confirmed books:
**before** is what the comparison found, **after** is the same measurement once
the four defects below were fixed in 1.13.1.

| photograph | header right (before → after) | amounts matched | missed | invented (before → after) | names exact (before → after) |
| --- | --- | --- | --- | --- | --- |
| `Walmart1.jpg` | 3/5 → 3/5, and the other two are correct as absences (the top is out of frame) | 23/24 | 1 | 0 | 22/24 |
| `Walmart2.jpg` | 4/5 → **5/5** | 3/3 | 0 | 0 | 2/3 |
| `Walmart3.jpg` | 5/5 | 8/9 | 1 | 0 | 8/9 |
| `ALDI1_new.jpg` | 5/5 | 17/18 | 1 | 0 | 3/18 |
| `ALDI2.jpg` | 4/5 | 7/7 | 0 | 0 | 6/7 |
| `COSTCO1.jpg` | 3/5 → **5/5** | 10/16 | 6 | 3 → **2** | 0/16 → **5/16** |

Nothing in the Aldi or Walmart3 rows moved, which is the point of showing them:
the Costco work did not disturb the chains that already read correctly.

**The single most useful thing in that table is that names and amounts fail
independently, and by chain.** Aldi returns every amount and almost no name;
Walmart returns both; Costco returns neither reliably. Anything built on top of
an item name must assume Aldi's 3-in-18, not Walmart's 22-in-24.

#### The line layout, which is where Costco actually breaks

`0/16` names looks like total failure and is not. Eight of the ten Costco lines
that were read came back as **the correct text with a left-hand column glued to
the front** — the confirmed name preceded by a single letter and the item
number. Costco prints a one-letter code in a column to the *left* of the item
number, and the parser has never seen that:

```
Walmart   NAME .......... 012345678901   12.34 X      flag on the right
Aldi      NAME .......... 123456          12.34       flag on the right
Costco    E  1234567  NAME ........       12.34 F     flag on the LEFT
```

Because the line does not begin with the item number, the sku is not split off
either, so the number ends up inside the description and every name scores as
wrong. **This is one structural fix, not eight name fixes**, and it is the first
thing to do for Costco. It is also the pattern to look for at any new chain: find
out which side the tax flag is printed on before assuming a line begins with its
item number.

Only four Costco names are genuinely mangled beyond that, and they are ordinary
character confusion of the kind Aldi produces too.

#### The summary block: three chains, three vocabularies

| chain | subtotal | total | how the amount sits |
| --- | --- | --- | --- |
| Walmart | `SUBTOTAL` | `TOTAL`, and on the card slip **`TOTAL PURCHASE` with the amount printed first** — `24.81 TOTAL PURCHASE` | usually right of the label, sometimes left |
| Aldi | `SUBTOTAL` | **no `TOTAL` at all** — `AMOUNT DUE` | right of the label |
| Costco | `SUBTOTAL` | `**** TOTAL` | right of the label |

`Walmart2.jpg` reported no total because of the second row of that table: its
`TOTAL` line came back as `TOT AL 24 . a-I` and the only legible statement left
was `24.81 TOTAL PURCHASE`, which the parser could not use because it looked for
the amount at the end of the line. Fixed in 1.13.1.

**`ALDI2.jpg` is not the same problem, and the first version of this note said
it was.** `AMOUNT DUE` has been in the vocabulary all along and works on
`ALDI1_new.jpg`, which prints `AMOUNT DUE 65.32` on one row. On ALDI2 the till
put the amount on the row below, *and* OCR lost the decimal point from it: both
passes return `$ 17 - 43`. So there is no amount there to read, at either scale,
and a lookahead to the next row would find nothing. Repairing ` - ` into a
decimal point was considered and rejected — the existing amount repairs are all
gated on the `.dd` of a price (§11.21) and this one could not be, so it would be
the first repair in the file able to fire on arbitrary text. **ALDI2's total is
an OCR limit, not a parser bug**, and it stays unfixed.

A fourth chain should still be assumed to have a fourth vocabulary.

#### Two defects this comparison found (both fixed in 1.13.1)

1. **The Costco tax is read one cent high, and the receipt itself says so.**
   The summary line comes back as `TAX 5.16` where the paper reads `5.15` — a
   plain digit misread. What makes it worth fixing rather than shrugging at is
   that the same reading contains the correct value three times over: the two
   rate components are read exactly (`A 5.500% TAX 2.86`, `F 8.00% TAX 2.29`,
   summing to 5.15), the `TOTAL TAX 5.15` line is read exactly, and
   subtotal + 5.15 = the total that was also read exactly. The parser takes the
   first, wrong one and never revisits it.

   **This is §12a item 2, no longer latent.** That entry describes a bare
   `TAX 0.00` arriving before the rate lines and blocking the rate sum from ever
   being written. The mechanism here is identical with a non-zero value, and the
   consequence is visible in the user's own confirmed books: they reconcile to
   the cent on all five other receipts and are one cent out on this one.

   Fixed by `_settle_tax`, which does not prefer either reading on principle —
   it asks which of them makes the receipt's own arithmetic add up, and only
   acts when subtotal, total and a breakdown are all present and the two
   candidates actually differ. **A consequence for the books already on disk:**
   receipt #18 was confirmed with the wrong `5.16` and will not reconcile until
   it is re-scanned or the field is corrected by hand. The application will not
   change a confirmed figure underneath its owner.

2. **A date typed without a leading zero sorts wrongly.** The Walmart1 header is
   out of frame, so its date was entered by hand as `2026-8-18` rather than
   `2026-08-18`. Dates are stored and sorted as text, so that row sorts above
   `2026-09-06` — it appears at the top of the Receipts list as though it were
   the newest. The review pane labels the box `YYYY-MM-DD` and then accepts
   anything.

   Fixed in `store.normalise_date`, which pads a date already of that shape and
   leaves everything else exactly as typed. Deliberately not a validator: the
   field belongs to the reviewer, and a value the code cannot parse is better
   shown back to them unchanged than silently reinterpreted.

#### Notes on reading this comparison

- **`ALDI` against `Aldi` is not an error.** The user typed the name as printed;
  the parser returns the canonical spelling from `_KNOWN_MERCHANTS`. The strict
  string comparison counts it as a header miss, which is why Aldi shows 5/5 in
  one row and the merchant is worth discounting in the other.
- **Walmart1 having no merchant and no date is the correct answer**, not a
  failure, and §11 records the fix that made it so. It is counted as 3/5 above
  because the user has since filled both in by hand.
- The photographs and the confirmed line items stay out of git. Only the counts
  and the layout facts are recorded here.

### Where to pick up

The state as of 1.17.0, for whoever reads this next:

- **The application works with nothing configured**, which is the single most
  important fact here. Before 1.3.0 a fresh copy could not read a receipt at all
  without an API key or a Tesseract install, and the first real receipt it was
  ever given failed with four red flags and no data. Windows' own OCR now covers
  that case on any Windows 10/11 machine.
- **1.12.0 through 1.17.0 are neither pushed nor tagged.** `origin` (now a
  public GitHub repository) and `mirror` both sit at `005a59a` (1.11.7), and
  tags stop at `v1.11.7`. Pushing needs one clean round of safety-engineer and
  quality-engineer, then the nine tags.

  The quality gate has blocked this project's pushes twice, correctly both
  times, and each block found something a test run could not: a comment that
  stated a false reason (§11.44), and behind it a real defect that silently
  dropped a purchased line. Both are long fixed. The gate is simply un-run at
  the current digest rather than failing — there is no known-outstanding
  finding.
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
- **A receipt that is not a supermarket: asked for, and it paid off exactly as
  predicted.** This entry stood open for weeks saying a restaurant bill would
  break the parser's assumptions and would find something. In 1.17.1 the user
  supplied one -- a KFC/TB carry-out ticket -- and it did, twice over:

  - It prints **no line saying TOTAL at all**. The amount charged sits against
    `CARRY OUT`, with the tax *above* it rather than below, and no subtotal line
    anywhere. The receipt read with no total (§11.71).
  - It prints **`Cashier: Zackariah`** near the top, which matched `CASH` and
    exposed a defect that had been silently mis-recording three of the other
    receipts as cash purchases all along (§11.70).

  It also prints four combo components with no price under one priced line, and
  the app records none of them although the user's own verification does. **On
  2026-09-10 the user decided both should be captured** -- the priced combo line
  and the constituent items beneath it -- so this is agreed work not yet built,
  rather than an open question (§12a item 6). It waits on a second sample: how a
  combo is printed varies by chain, and a rule fitted to this one receipt would
  read KFC and nobody else.

  **The prediction generalises, so keep the entry alive in a narrower form.**
  Every new *shape* of receipt has found a defect its predecessors could not,
  without exception, across four chains now. Still untried: **a fuel receipt**
  (litres at a price per litre), a sit-down bill with a **tip line** and
  per-person subtotals, something faded or folded, and anything **not in USD** --
  every amount pattern in `receipt_text` assumes a `.` decimal separator.

  The photographs live in `pictures\` as `Walmart1.jpg` through `Walmart4.jpg`,
  `ALDI1_new.jpg`, `ALDI2.jpg`, `COSTCO1.jpg` and `KFC1.jpg`, and are
  gitignored, deliberately: a receipt is somebody's shopping and their payment
  method. Do not commit them, and do not paste their card or reference numbers
  into anything. (`ALDI1.jpg` was deleted by the user in 1.17.1; the file the
  older tables call `ALDI1.jpg` is not on disk, and `ALDI1_new.jpg` is the
  re-photograph that replaced it.)
- **The known weaknesses**, if you are deciding what to build:
  1. **The barcode lookup is Walmart-shaped.** It resolves at best 12 of 20
     Walmart lines, **0 of 18 at Aldi** and **0 of 16 at Costco** -- both print
     their own internal article numbers, not barcodes, and no public database
     knows them. Aldi needs no fix, because it already prints readable names.
     Costco does not, which is what `app/lookup/shorthand.py` exists for. Do not
     describe the barcode feature as though it works everywhere.
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
  (§11.27), scrape walmart.com (§3 — it answers a bot check, not a product), or
  scrape costco.com (§3 — measured, and it answers 403 on search and on every
  product page).
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
48. **A merchant the OCR misreads costs far more than one field.** The Costco
    receipt reported its merchant as `455 Scarborough Downs Rd`, the street
    address printed under the unreadable logo. That is not just a wrong box:
    the merchant gates the chain-specific half of the shorthand expander *and*
    the merchant categorisation rule, so 8 of the receipt's 15 distinct lines
    sat in `Uncategorized` as a consequence. Fixed with the three-pass match in
    §3, and — separately — in the merge between the two OCR passes, which filled
    a field from the second reading only when the first had left it blank. The
    merchant is never blank, because the fallback supplies the address, so the
    merge was discarding a recognised `Costco` in favour of a guess. A named
    shop now wins over a guess whichever pass found it
    (`app/extract/receipt_text.py`, `app/extract/windows_ocr.py`).
49. **A locally expanded name must not claim it came from a barcode.** The
    review pane labelled every expansion `from barcode:`, which was already
    untrue of names the vision model supplied and would have become untrue of
    every Costco line. The three differ in how much they can be trusted — a
    barcode name is only as good as the digits OCR read off a photograph, while
    a shorthand expansion cannot be wrong about *which* product it is — so the
    label is the reviewer's cue for how hard to look. `line_item.name_source`
    records which, rather than the pane inferring it
    (`app/db.py`, `app/pipeline.py`, `app/store.py`, `app/ui/receipts.py`).
50. **A lookup failure must not also skip the offline expansion.**
    `_expand_item_names` returned early when `names_for_skus` raised, which was
    right when a barcode was the only source. The shorthand pass never touches
    the network, so it still has something to offer on exactly the machine where
    the lookup could not run — the early return is now a caught exception and an
    empty result (`app/pipeline.py`).

51. **A per-line flag column can be printed on the left.** Walmart and Aldi
    both put their tax flag after the price, so a line had never begun with
    anything but its item number. Costco prints a single letter in the left
    margin, and the whole of `E 96716 ORG SPINACH` therefore stayed in the
    description — flag, item number and all. It is one fault, not the fifteen
    wrong names it looked like: with the column recognised, the same photograph
    went from 0 of 16 names exact to 5, and the rest are ordinary character
    misreads. The letter is dropped rather than interpreted, because nothing on
    the receipt says what Costco means by it (`app/extract/receipt_text.py`).
52. **`SUBTOTAL` read as `SUBT TAL` became the receipt's largest purchase.** OCR
    split the word at the O. It matched neither the literal nor the letter-spaced
    form, so the line was not treated as summary at all and its amount — the
    subtotal itself — was booked as an item. Recognised now by comparing the
    leading run of letters against `SUBTOTAL` within one edit, the same budget
    used for a misread shop logo, and the same helper
    (`app/extract/receipt_text.py`).
53. **A stated tax and a rate breakdown that disagree are settled by the
    receipt's own arithmetic.** `TAX 5.16` was read where the paper says 5.15,
    while the two rate components were read exactly and sum to 5.15. The old
    rule — a stated line beats the breakdown — took the misread digit and never
    revisited it. `_settle_tax` prefers neither: it takes whichever candidate
    satisfies subtotal + tax = total, and does nothing at all unless all three
    are present and the two candidates differ. **This is the bug recorded as
    §12a item 2**, which described the same mechanism with a bare `TAX 0.00` and
    called it latent; it was not latent, it was costing a cent on every scan of
    this receipt (`app/extract/receipt_text.py`).
54. **A total printed before its own label was invisible.** Walmart's card slip
    states `24.81 TOTAL PURCHASE`, and on one photograph that is the only
    legible statement of the total — the `TOTAL` line itself came back as
    `TOT AL 24 . a-I`. The parser looked for the amount at the end of the line
    only. A leading amount is now accepted, but exclusively on a line that
    already names a summary field, so an item priced before its name is not
    swept up as the total (`app/extract/receipt_text.py`).
55. **A hand-typed date without a leading zero sorted as the newest receipt.**
    Dates are stored and compared as text, so `2026-8-18` sits above
    `2026-09-06`. `store.normalise_date` pads a date already of that shape and
    passes anything else through untouched (`app/store.py`).

56. **A remembered window position was checked for its corner, not its size.**
    `_geometry_is_on_screen` asked whether the top-left of a saved geometry
    landed somewhere visible and said nothing about the rest of the window, so
    `2461x1733+421+1034` passed: the corner is on screen, and everything below
    it is not. The window opened with most of itself, including the row of
    buttons that saves a receipt, off the bottom of the display.

    Replaced by `fit_to_screen`, which **clamps rather than accepts or
    rejects** — rejecting threw away the size the user had chosen and reverted
    to the default, where clamping keeps their size wherever it fits and
    corrects only what does not. It matters more here than in most applications
    because this one is meant to be copied onto a USB stick: the books travel
    with the program and `window_geometry` travels inside them, so a geometry
    saved on a 4K desktop arrives on a laptop that cannot show it.

    Clamping to `winfo_screenheight` was not enough on its own, and the first
    attempt at this fix proved it: that figure counts the taskbar's pixels as
    available, so the window came back on screen and sat with its last 75 rows
    behind the taskbar. `usable_screen` asks Windows for the real work area
    through `SPI_GETWORKAREA` — 3840×2088 against a reported 3840×2160 on this
    machine, a 72-pixel taskbar — and falls back to a fraction of the screen
    when the call is unavailable (`app/ui/window.py`).

57. **Real card and transaction identifiers were in tracked test files, and
    had already been pushed.** Found by auditing before deciding item 1 of §12a
    rather than by anybody reporting it. `tests/test_units.py` carried a
    transcribed Walmart receipt complete with its transaction certificate
    (`TC# …`), its reference number (`REF # …`) and the last four digits of the
    card that paid, all present at `origin/main`. Nothing in those tests needed
    the real values: what is being tested is that a line of that *shape* reads
    as summary rather than as a purchase, so they are now zeros, with a comment
    saying they must stay invented.

    Worth noting how it happened, because it was not carelessness about
    security -- it was a transcription made to test the parser honestly, by
    someone thinking about parsing. The masking feature (§11.44) and the whole
    `pictures/` policy were already in place; neither covers a value typed into
    a test file by hand. **The residue is still in the published history**, and
    removing it means a force-push, which is the user's call rather than
    something to do quietly (`tests/test_units.py`).
58. **A placeholder in the truth file was mistaken for evidence.** Five of the
    six entries in `receipts_truth.json` name a photograph and assert nothing
    about it -- they exist so the harness can say "nothing verified yet" rather
    than "no record", which are different things. The first cut of
    `export_truth.py` treated any existing entry as coverage and so exported
    one receipt out of six, silently. `Truth.says_anything` is the distinction,
    and both the exporter and `load_truth` use it (`tools/accuracy.py`,
    `tools/export_truth.py`).
59. **The merchant was scored letter by letter.** A reviewer types the shop as
    the sign prints it, `ALDI`; the parser returns the canonical `Aldi`. Both
    name the same shop, and comparing them exactly held two receipts at 4/5 for
    ever -- a column that can never reach full marks is one a reader learns to
    ignore. Compared through `normalise_name` now, the same way item names
    already were (`tools/accuracy.py`).

60. **Nothing ever looked at a line the reviewer typed.** Expansion and
    translation ran during a scan and nowhere else, so a line added or renamed
    in the review pane was never offered a product name or a translation --
    for the life of the receipt. Reported as the application ignoring what had
    been entered, which is a fair reading of the evidence. `enrich_now` now
    runs both passes over the stored rows after every save
    (`app/pipeline.py`, `app/ui/receipts.py`).
61. **A question that came back empty looked identical to one never asked.**
    Both render as a line with nothing under it. `name_source = 'notfound'` and
    a `NULL` zh in the translation cache are the two records that tell them
    apart, and `lookup.cached_state` exposes the second, which `chinese_for`
    had always hidden by returning hits only (`app/lookup/translate.py`,
    `app/ui/receipts.py`).
62. **A lookup that could not run must not be recorded as a lookup that found
    nothing.** The first version of §11.60 marked `notfound` whenever a
    resolvable barcode produced no name -- including when `names_for_skus` had
    thrown, which says nothing whatever about the catalogue. It would have told
    the reviewer "no product name found" because their network was down, and
    cached that judgement against the line so no later save retried it. Caught
    by `test_a_lookup_failure_never_breaks_a_scan`, which is worth noting: the
    test predates the bug and failed for the right reason (`app/pipeline.py`).

63. **Initialising WinRT stops onnxruntime from loading, for the rest of the
    process.** Build a `WindowsOcrExtractor` and then a `RapidOcrExtractor` and
    the second raises *"DLL load failed while importing
    onnxruntime_pybind11_state"* every time; reverse them and both work. WinRT
    initialises the thread's COM apartment and onnxruntime's extension module
    will not initialise underneath it. `engine_status` asks every engine about
    itself at start-up, so the bad order was the *normal* one. Fixed by loading
    onnxruntime at import of `rapid_ocr`, which `app.extract` imports before
    anything reaches WinRT. Found by a test that passed alone and failed in its
    own file (`app/extract/rapid_ocr.py`).
64. **Asking whether RapidOCR is available must not load RapidOCR.** The other
    engines build themselves in `available()` because it is instant; this one
    loads three ONNX models. `available()` runs on every Settings draw and every
    start-up, and building there took the suite from 90 seconds to 377. It is an
    import check now (`app/extract/rapid_ocr.py`).
65. **Two ways of hiding an import from PyInstaller, neither of which works.**
    The slim build is supposed to contain no RapidOCR at all and came out at
    97 MB instead of 30 -- cv2 alone was 29 MB. PyInstaller follows an ordinary
    import inside a function body, and it also resolves a literal module name
    passed to `importlib.import_module`. The exclusions are named outright in
    the spec now, which is where a decision about what a build contains belongs
    (`Bookkeeping.spec`).
66. **One spec, two builds, one work folder.** PyInstaller names its cache after
    the spec file, so the slim and full builds overwrote each other's analysis
    and each re-ran from scratch. `build.bat` passes `--workpath` per target
    (`build.bat`).
67. **The harness measured an engine the application had stopped using.**
    `measure_accuracy.py` built a `WindowsOcrExtractor` unconditionally, so from
    1.16.0 the `--check` gate guarded the slim build's reader while the full
    build ran RapidOCR — and said nothing about it. It now takes `--engine`,
    defaulting to the first installed of RapidOCR → Windows OCR → Tesseract, and
    keeps one baseline per engine. Two things found while fixing it, both now
    pinned by tests: `build_engines` answers an unrecognised name with the whole
    fallback list, so taking its first entry would have run **Claude** in
    response to a typo; and resolving the engine before looking for photographs
    turned a fresh clone's "none found" into "no engine available", which is the
    less true of the two answers (`tools/measure_accuracy.py`).
68. **A capital O and a zero are the same ink, and no model can fix that.** The
    largest single defect left after RapidOCR landed: five of the fourteen wrong
    item names were this one confusion — `PR0TEINSUPPL`, `GVC0RNSTARCH`,
    `DOVE BW 110Z`, `AIM TP 5.50Z`, `EQJELLUBE80Z`. It is worth knowing *why the
    obvious fix is the wrong one*: a larger recogniser meets exactly the same
    ambiguous glyph, so this was never going to be bought with model size. Two
    narrow rules break the tie on what the token is instead — a zero inside an
    otherwise all-capital word is an O, and `0Z` after a digit is the unit `OZ`.
    Both refuse anything carrying a digit other than zero, which is what exempts
    barcodes, item numbers and codes like `WD40`; the asymmetry is deliberate,
    since a missed repair leaves a name a reviewer can see is wrong while a
    wrong one invents a plausible name nobody will question. Names exact went
    59 → 64 of 73, with Walmart1 reaching 23 of 23. One existing test recorded
    the misreading as expected output and was corrected — the fixture beneath it
    is untouched, so it is still exactly what OCR returned
    (`app/extract/receipt_text.py`, `tests/test_windows_ocr.py`).
69. **A photograph is never quite square, and on a receipt that separates the
    columns.** Three item names on ALDI1_new carried the flag letters of the
    line below (`FP Chicken Drums` read as part of `Blk Angs Stew Meat`), one
    line was lost, and Costco's `ORG SPINACH` was missing entirely. None of it
    was recognition: the page is tilted by 1.5°, the columns sit 800px apart, so
    the left-hand column of a line drifts 31px against a row pitch of 51 and
    lands nearer the row above. `group_rows` now takes a `skew` and groups along
    that baseline.

    **Where the angle comes from is the whole story.** Two estimators were
    written and both failed, for a reason worth recording: a receipt is a table
    with evenly spaced columns, so a shear that slides one column onto the row
    *below* projects just as sharply as the true angle, and a search for the
    "clearest" projection walks straight into that resonance — it returned
    −0.080 for three different receipts, pinned at the edge of its own search
    range. Fitting slopes inside grouped rows failed differently: the rows it
    had to learn from were the mis-grouped ones.

    The angle needed no estimating at all. RapidOCR's detector returns a
    **rotated quadrilateral** per line and `_as_words` was flattening it to a
    bounding box and discarding the tilt. Taking the median of those polygon
    angles gives +0.0254 for ALDI1_new — inside the 0.02–0.05 plateau that fixes
    every affected row — and exactly 0.0 for the four photographs that are
    square. Windows OCR and Tesseract report no angle and pass no skew, so they
    are untouched, which their own baseline confirms.

    Effect across the seven photographs: 73 → **75** lines matched, 4 → **2**
    missed, still none invented, 64 → **68** names exact, and money unaccounted
    **$14.87 → $0.95**. Costco and ALDI1_new now reconcile to the cent
    (`app/extract/windows_ocr.py`, `app/extract/rapid_ocr.py`).
70. **Three receipts recorded a card purchase as cash, and had done all along.**
    Surfaced by the first KFC receipt, which prints **`Cashier: Zackariah`** near
    the top. `_find_payment` was the one place that never got the whole-word
    treatment of §11.9.4, so `CASHIER` matched `CASH`, and because the function
    returns on its first hit it never reached `Card Type: Mastercard` at the
    bottom. Aldi's `Your cashier today was Ismail` did the same. **A confidently
    wrong value is worse than a blank one**: nothing in the books contradicts
    "CASH", so nobody would ever have questioned it. Now matched with
    `_whole_words`, and ALDI, ALDI2 and KFC1 all report the card.

    The same audit found a second wrong value in the same function. The last
    four digits were taken from `(\d{4})\s*$` -- the end of the line, whatever
    was there -- so a line shaped `MASTERCARD- 0000 I 1 APPR#009999` reported the
    card as **ending 9999, which is the approval code**. (Invented digits: the
    real ones are exactly what §11.57 is about, and writing them into this row
    while describing their removal is a mistake this session actually made and
    caught on the pre-push scan.) Only digits sitting against the
    brand are trusted now, bridged across whatever the terminal masks with, and
    a line with nothing there yields a brand and no number rather than a guess.
    Both Walmart receipts now report the right four digits.

    First-match-wins was deliberately kept rather than preferring a brand over a
    generic word, so KFC reports `CREDIT` from `ETender Credit` although
    `Mastercard` is printed below it -- less specific, but true. Preferring the
    brand would read the wrong answer off any receipt whose footer advertises
    the cards a shop accepts (`app/extract/receipt_text.py`).
71. **Fast food names the total after the counter, not after the word TOTAL.**
    KFC1 has no line saying TOTAL anywhere: the amount charged sits against
    **`CARRY OUT $12.84`**, with the tax printed *above* it instead of below,
    and no subtotal line at all. The receipt read with no total and was flagged
    for hand entry. `CARRY OUT`, `TAKE OUT`, `DINE IN` and `DRIVE THRU` are now
    total labels -- **anchored to the start of the line and requiring the amount
    to follow the words immediately**, which is the whole safety of the rule: a
    bag charged as `CARRY OUT BAG 0.10` keeps a word in between and stays a
    purchase. Matching loosely would let a ten-cent bag overwrite the total.

    `TO GO` is deliberately absent: two of the commonest short words in English,
    and it survives the space-stripped comparison as `TOGO`. Only `CARRY OUT` is
    confirmed against a photograph; the siblings are the same label in the same
    slot and are unverified. KFC1 now reads 11.89 + 0.95 = 12.84 and validates
    clean (`app/extract/receipt_text.py`).
72. **A zero in the tax-flag column, and what the older engine knew.** Walmart
    flags a non-taxable line with the letter `O`, and RapidOCR ran it into the
    amount: `0.05 O` arrived as **`0.050`**, which is not a two-decimal amount,
    so the whole line was discarded and its money with it. Three receipts lost a
    bottle deposit that way -- the last money unaccounted for anywhere.

    **The user pointed out that the previous model handled this correctly, and
    that is what identified the cause.** Windows OCR returns one box per *word*,
    so `0.05` and `O` are separate detections and `rows_to_text` rebuilds the
    space from their geometry -- it produced `0.05 O` on all eleven deposit
    lines across three receipts, every time. RapidOCR returns one box per
    *line*, so the space survives only if the recogniser chose to emit it, and
    on the first deposit line of each receipt it does not. Nothing downstream
    can recover a gap that was never in the string, so the parser now accepts a
    lone `0` in the flag column and reads it as the letter.

    Narrow on purpose: **only `0`**, never another digit, so `123.456` is still
    not an amount carrying a flag, and the two-letter rule that keeps `0.02lb`
    out is untouched. `_tax_flag` translates the zero before the lookup --
    without that the flag reads as unknown rather than non-taxable, which loses
    the only statement the receipt made about it.

    Effect: **$0.00 unaccounted across all eight photographs**, Walmart1 at 24
    of 24 names, and for the first time every line of every transcribed receipt
    is read with none invented (`app/extract/receipt_text.py`).
73. **A confidence cap that promised the opposite of what it did.** Found by the
    pre-push comment review, and it was a behavioural defect rather than a wrong
    comment. `rapid_ocr.MAX_CONFIDENCE` was 0.75, described as *"deliberately
    capped below the auto-confirm threshold ... for the same reason Windows OCR
    is"*. `validate.LOW_CONFIDENCE` is **0.6**, so 0.75 clears it: a RapidOCR
    reading whose arithmetic happened to balance raised no flag at all and
    `auto_confirm_clean` signed it off with nobody having looked at it. Windows
    OCR was doing the right thing at 0.5 next door, which is exactly what made
    the false claim easy to believe.

    Now 0.55 -- above Windows OCR's 0.5 because this engine really is more
    accurate, below 0.6 because the promise has to be true. The test that was
    supposed to guard this compared the cap against **its own constant**, which
    is true of any value whatsoever; it now compares against `LOW_CONFIDENCE`,
    so the two cannot drift apart again. A second test had been quietly
    measuring the cap rather than the mean it was named for, because its sample
    scores averaged 0.6 (`app/extract/rapid_ocr.py`, `app/validate.py`).
74. **Two accounts of PyInstaller, flatly contradicting each other.** Also from
    the review. `rapid_ocr.py` said a module name passed as a string to
    `importlib.import_module` is invisible to PyInstaller's analysis, so the
    slim build stays slim; `Bookkeeping.spec` said PyInstaller resolves the
    literal exactly as it resolves an import, which is why the spec carries an
    `excludes` list at all. **The spec is right** -- §11.65 records the slim
    build reaching 97 MB proving it. The hazard was concrete: a maintainer
    trusting `rapid_ocr.py` could delete the excludes and triple the file the
    build exists to keep small. The importlib form stays for the other half of
    its reason -- staying loadable when the package is genuinely absent -- and
    the comments now say so.

    The same review found quoted figures that had gone stale and, in one
    docstring, **two different scores for Windows OCR at once**, measured on
    corpora of different sizes with neither named. All regenerated from the
    committed baselines with the corpus stated, which is the discipline
    `tools/accuracy.py` was written to enforce and this violated
    (`app/extract/rapid_ocr.py`, `app/extract/__init__.py`).
75. **Removing a secret in a follow-up commit does not remove it from the
    history you are about to publish.** The most useful entry in this section,
    because the mistake survived a scan that was looking directly for it.

    Having reintroduced the real identifiers (§11.73's neighbour), they were
    taken out again in a second commit, and a grep of the working tree came back
    clean -- so the push looked safe. It was not. The first commit's *tree* still
    carried all three values, that commit was one of twenty-six the push would
    publish, and a later deletion changes nothing about what an earlier commit
    contains. Caught by the pre-push safety review, not by me, and not by the
    scan in the publish script either: **that scan grepped `refs/heads` and
    `refs/tags`, which resolve to tip trees, and never walked the commits in
    between.** A check that only inspects the tip is not a check.

    The fix has to be a rewrite, not another edit. The two pending commits were
    collapsed into one whose tree never contained the values -- chosen over
    `filter-repo` deliberately, because the resulting tree is then literally the
    one that can be grepped rather than one a rewriting tool is trusted to have
    cleaned. Both are legitimate; only one is directly verifiable.

    The same review found a **zero-byte file named `1649$` committed to the
    repository root** -- debris from a shell typo where `$$` expanded to the
    process id. It had been looked for under the name `$$$`, not found, and
    declared absent; `git add -A` then swept it in. Harmless, and it would have
    been the first thing a visitor to a public repository saw.

76. **The last three defects section 9 blamed on the parser, and none of them
    needed a new idea.** Each was a pattern that did not allow for how the ink
    actually reads.

    **Costco's margin flag, doubled by the reader.** `_LEADING_ITEM_NO` allowed
    one letter before the item number and `EEE 9218 RED ONIONS` has three: the
    margin prints a single `E`, and the detector split a hairline glyph into
    three copies. Because the pattern then failed *entirely* rather than
    partially, flag and item number both stayed inside the name. Widened to a
    repeated letter through a backreference rather than `[A-Za-z]{1,3}`, and
    the distinction matters: a run of the same letter is the duplication being
    modelled, where three arbitrary letters would eat the opening words of any
    description that happened to be followed by a number. Costco went 15 to
    **16 of 16 names exact**.

    **`lb` read as `1b` cost an item its entire name.** The two are the same
    strokes in this font and, unlike the O-for-zero confusion, there is no
    surrounding word for the recogniser to break the tie with. `_WEIGHED`
    matched `lb|lbs|kg|g|oz`, so the weighing line failed the pattern, the name
    on the line above was never merged in, and the item was booked as
    `0.42 1b. @ 1.00 1b. / 3.62`. The unit now matches `[l1i]b`. Walmart3 went
    8 to **9 of 9 names exact**.

    **Some Aldi lines carry a second number between the description and the
    price.** `343415 24ct Paper Bowl 356387 2.69 NB` has its item number in the
    left margin *and* a second code before the price, belonging to no column
    the receipt labels.

    **Where that code comes from is not established, and an earlier draft of
    this entry claimed that it was.** 356387 appears exactly once in the
    reading, which argues for its being printed on that line; but the hand
    transcription of the same 2026-08-21 receipt in `tests/test_units.py`
    records 356387 as another row's item number, which argues for the section 9
    note's original reading -- a neighbouring row tipped in by the 1.5 degree
    skew (§11.69). The files cannot settle it; the paper could. Caught by
    the quality review, and it was right to insist: the draft used a fact about
    the OCR *text* to overturn a claim about the *receipt*, which it cannot do.

    None of that needs settling to ship the fix. A bare number at the tail of a
    description is not part of the name whichever row printed it, and the rule
    is bounded so that being wrong about the cause costs nothing: only once a
    leading item number has been found, so Walmart's layout never reaches it;
    only on five to eight digits, so a description ending in a size or a year
    keeps its number; and never on the whole description. **This one does not show in the
    score**: the same line also reads `Bow1` for `Bowl` and loses a space, both
    the recogniser, so the name is still not exact. Worth doing regardless --
    a stray six-digit number inside an item name is rubbish the user can see.

    **Names exact 74 to 76 of 81** across the eight photographs, no line lost,
    none invented, still $0.00 unaccounted. **Windows OCR did not move at all**
    -- 66/19/3 and 41 names, unchanged -- and that is the corroboration worth
    having. It boxes words rather than lines, so it reads `lb` correctly, and
    its Costco reading is too poor to reach the flag rule. A change to a shared
    parser that moves one engine and not the other is aimed at a real reading
    defect rather than at the harness.

77. **A hand-added line had no category at all, and the reports disagreed with
    the pane about it.** Found by the user from the review pane on the KFC
    receipt: the four combo components they had typed in themselves showed a
    bare dash in the category column, while the priced line above them showed
    Uncategorized.

    Both readings were true and they contradicted each other. The categoriser
    assigns something to every line it reads, so `category_id` is null only for
    a row a reviewer added by hand -- the Add line button starts from an empty
    item. `store.py` then reads those rows back through
    `COALESCE(c.name, 'Uncategorized')`, so the figures counted the line as
    Uncategorized while the pane it was entered in showed nothing.

    The default is **Other**, chosen by the user over Uncategorized, and the
    distinction is worth keeping. "Uncategorized" is a *marker* meaning nobody
    has decided -- `category_names()` withholds it from the candidate list
    offered to the model for exactly that reason, though rule creation is a
    separate path that does offer it -- whereas "Other" is a decision. A
    reviewer who has
    typed the name in has identified the item; it simply belongs to no named
    category.

    **The subtle half is the provenance, not the category.** `_collect`
    promotes a line to `manual` whenever the chosen category differs from the
    one it loaded with, and a manual category is excluded from rule backfill.
    Had the fallback been recorded that way, every hand-added line would have
    been pinned to Other for good and no rule written later could reach it. So
    the fallback records as `default`, and the row's `original_category_id` is
    the *resolved* id rather than the stored null, so the application's own
    choice is not mistaken for the reviewer's.

    Two deliberate limits. Scoped to hand-added rows, at the user's choice:
    changing the categoriser's own fallback would have moved every line
    currently sitting in Uncategorized, including ones in receipts already
    confirmed. And selecting the blank entry by hand still stores null, because
    that is a reviewer's decision rather than a default. Rows already saved with
    no category show Other the next time the receipt is opened and take it
    permanently when that receipt is saved, so existing books correct
    themselves as they are touched rather than by a migration.

---

## 12. Version control

One repository in the project folder, named after this document, exactly like the
sibling projects under `D:\claude`:

- `core.autocrlf=false` locally, plus `.gitattributes` with `* -text`, so files
  are stored byte for byte (this machine has `core.autocrlf=true` system-wide,
  which would otherwise rewrite every text file to CRLF on the first checkout).
- `user.name = xu.jiamin`, `user.email = 318819920+Micheal-Jiaming@users.noreply.github.com`,
  per repository. **Do not set this back to a personal address.** The repository
  is public, GitHub shows commit author emails, and a single commit made with a
  personal address would put it into public history permanently. See the
  publication note below.
- Remote `mirror` → `D:\claude\repos\Bookkeeping.git` (a local bare second copy;
  its `HEAD` points at `main` so a clone checks out).
- Baseline **1.0.0**. A functional change adds **0.1**, a fix or docs change adds
  **0.0.1**, updated in `VERSION` in the same commit and tagged `v<number>`.
  Tags: **`v1.0.0` through `v1.9.5`, 22 of them**, all pushed to both remotes.
  1.10.0, 1.10.1 and 1.10.2 were committed untagged and are tagged as part of the
  1.11.0 publication push. **The 1.11.0 history rewrite changed every commit SHA**,
  so all earlier tags were deleted and recreated against the rewritten commits — a
  tag still pointing at a pre-rewrite SHA would dangle, and `git push --tags` would
  silently keep publishing the old object. Tag one per version, against the commit
  that bumped `VERSION`.
- Tracked: all source, `Bookkeeping.spec`, `build.bat`, `run.bat`, `make_icon.py`,
  `assets/icon.ico` (generated, but the build needs it), `README.md`, and the four
  PNGs in `docs/screenshots/`. Those four are re-included past the blanket
  `*.png` rule **by name, one line each — not by directory**.
  `!docs/screenshots/*.png` is the obvious way to write it and is worse: it would
  re-include any PNG later dropped in that folder, and in a project whose whole
  image policy exists because real receipt photographs land in the working tree,
  that is the one place a receipt could become publishable with nobody editing
  `.gitignore`. Verified by check-ignore — a fifth PNG in that exact directory is
  ignored, and the four named ones are tracked.
- **`README.md` is a deliberate, permitted exception to the one-document-per-project
  rule, and must not be "merged and deleted" as a duplicate.** `/md-renew-check`
  reports it as a second Markdown document, because that is what the workspace
  convention normally forbids. The exception exists because GitHub renders
  `README.md` as the repository's landing page and will not render
  `Bookkeeping_record.md`; without it a public repository presents as a bare file listing
  with no entry point. The division of labour is strict, and keeping it strict is
  what stops this becoming a genuine duplicate: **`README.md` is a landing page
  for a human visitor** — what the project is, the measured numbers, how to run
  it, what it cannot do — and **`Bookkeeping_record.md` remains the single source of
  truth** for architecture, rejected alternatives, fix history and handoff. When
  the two disagree, this document wins and the README is the one to correct.
- Ignored: `data/` (personal), `dist/` and `build/` (regenerable). **Because the
  .exe is not in Git, `build.bat` keeps the previous one as
  `dist\Bookkeeping.previous.exe` — that is the only way back from a bad build.**

`origin` is <https://github.com/Micheal-Jiaming/Bookkeeping>, created 2026-08-29.

**It is public as of 2026-09-03.** It was private until then, and that was a
standing instruction rather than a default; the reversal was an explicit decision
by the user, who wants interviewers to read and run the project. The earlier
instruction is recorded here rather than deleted, because the reasoning behind it
still applies to everything the repository does *not* contain. Three consequences
to carry forward:

- **The commit author identity was rewritten before publication.** Every commit
  and tag in history is now authored as
  `xu.jiamin <318819920+Micheal-Jiaming@users.noreply.github.com>`. The personal
  address previously used locally appears in no commit header and no longer in
  this document. This was done *before* the repository went public, while nothing
  had been cloned, which is the only moment it is cheap.
- **Publishing is one-way in practice.** Anything public can already have been
  cloned, forked, cached or indexed. Treat a leak discovered after this date as
  disclosed, not as something that making the repository private again would undo.
- **Receipt photographs and `data/` remain the things that must never be
  committed**, and the stakes are now higher rather than lower. The `.gitignore`
  reasoning below is the control that matters most.

Receipt photographs are gitignored (`*.jpg`, `*.jpeg`, `*.png`, unanchored so the
rules cover `shots\` output too, with `!assets/icon-preview.png` for the program
icon). A real receipt is somebody's shopping, their payment method and often
their address; the one this project is measured against lives in the tests as OCR
word boxes and a transcription instead of as an image.

---

## 12a. Open, deferred by the user

Items 1-5 were raised by review during the 1.12.x work and item 6 by the user in
1.17.1; all are **deliberately not acted on yet** -- the user asked for them to
be recorded and picked up on request. None blocks the app; all were reported
rather than found by accident, so they are worth keeping in one place rather
than rediscovering.

**Item 5 was partly overtaken by 1.13.0**, which was about naming and
categorising Costco's lines rather than reading more of them. The seven missed
items and the lost subtotal are untouched and still open. One consequence is now
visible rather than latent: at the size the app stores the image, `SUBTOTAL
188.37` reads as `SL'BT TAL 188.37` and is parsed as a *purchased line*, which
is what produces the "items plus tax come to 315.37 but the total reads 5.15"
flag on screen. Worth folding into item 5 rather than treating as new.

**That decision point is now closed.** The harness used to score six of seven
photographs on self-checks alone, because only Walmart1 had a transcription.
`tools/export_truth.py` now writes one for every receipt confirmed in the app,
into a gitignored file -- so the figures never reach the repository and the
measurement still works. Six of seven are scored against real truth as of
1.15.0.

| # | Where | What |
| --- | --- | --- |
| 1 | ~~`Bookkeeping_record.md` 1.12.0 row, `README.md`~~ | **Decided in 1.15.0, and the decision drew a line worth keeping.** *Aggregate* figures -- a subtotal, a tax, a total -- stay: they are not identifying on their own and they are the substance of a changelog documenting a real defect. *Itemised* data does not go into git at all, because a list of what one person bought, for how much, on what day is the thing the image policy exists to protect. That is why the exported truth file is gitignored (§3). The audit that came with the decision found something worse than the figures and fixed it -- see §11.57. |
| 2 | ~~`app/extract/receipt_text.py`, `_find_summary_amounts`~~ | **Closed in 1.13.1 (§11.53), and it was never latent.** The entry described a bare `TAX 0.00` arriving before the rate lines and blocking the rate sum. The same mechanism with a non-zero value was reading Costco's tax a cent high on every scan; comparing against the user's confirmed books is what surfaced it. `_settle_tax` now decides between the two by the receipt's own arithmetic. |
| 3 | `app/ui/receipts.py`, `toggle_raw` docstring | Says the pane shows "exactly what the engine returned". With masking on -- the default -- every digit is an asterisk, so "exactly" is wrong on the default path. The truth is stated three lines below and on screen, so it misleads only briefly. |
| 4 | `Bookkeeping_record.md` 1.12.1 row | Does not record that a real membership number reached a code comment, the test and that row, and was removed by amending the unpushed commit rather than by a follow-up. The 1.11.2 row is the precedent for recording that kind of decision without reproducing the value. |
| 5 | Costco recognition (still open) | Seven of sixteen line items are still missed and the subtotal is lost in OCR (`SUBTOTAL 188.37` reads as `SUBT TRL`, no amount). Agreed scope at 1.12.0 was the summary block and false lines only. The gap is those seven items (66.26) plus one `5.99` read as `5.93`. |
| 6 | `app/extract/receipt_text.py`, and how the review pane shows a group | **Combo meals: agreed 2026-09-10, deferred for want of a second sample.** KFC1 prints one priced combo line at 11.89 with four component lines beneath it that carry no price of their own, and no subtotal anywhere. The app records the priced line and drops the four; the hand transcription records all five and supplies the 11.89 subtotal the paper never prints, so the reading is incomplete rather than wrong -- and those four are the whole of the corpus's four missed lines, every other line of every other receipt being read. **The user wants both the combo and its constituent items captured.** Not built yet, on purpose: one receipt cannot show how the shape varies between chains -- indentation, a leading marker, an empty price column, or no cue at all -- and a rule fitted to a single sample would read KFC and no one else. Two decisions travel with it: a component at 0.00 is not a purchase, so it must not dilute the category totals or the reconciliation in §10; and a component needs to belong to its parent line rather than sit beside it as a sibling. Revisit when the next combo receipt arrives -- the user has none to hand as of 2026-09-10 and asked to compare shapes before anything is built. |

---

## 13. Ideas not built

Ranked by how much they would improve the daily experience:

1. **More real receipts.** **All eight** photographs now carry a transcription
   and are scored per field, which is what 1.15.0 made cheap — confirming a
   receipt in the app is the whole of the work. The restaurant bill this entry
   used to ask for arrived in 1.17.1 and found two defects (section 9). Most
   wanted now is **a second combo or set meal**, which is what §12a item 6
   waits on before the feature can be designed against more than one sample.
   Also missing: a **fuel** receipt, a sit-down bill with a **tip line**,
   something faded or folded, and a **non-USD** one — every amount pattern in
   `receipt_text` assumes a `.` decimal separator, so a comma-decimal receipt is
   the one most likely to fail outright rather than partially.
2. **Learning from corrections.** When a reviewer re-categorises the same item
   name twice, offer to create the keyword rule. The rules table already supports
   it; only the suggestion is missing. This matters more since 1.3.0: an offline
   reading leaves ~40 % of lines uncategorised, and those corrections are exactly
   the signal that would fix it permanently.
3. **Use the arithmetic residual to re-read ambiguous rows.** When the items are
   short by exactly 0.9 × a parsed amount, a leading digit was lost and which row
   it was is usually determinable. Would need care: see §10 on not guessing, so
   this should propose a correction in the review pane rather than apply one.
4. **Drag and drop onto the window.** Tk cannot do it without `tkdnd`, a
   non-stdlib dependency; the file dialog and clipboard paste cover the same need
   for now.
5. **Budgets and month-over-month deltas** on the reports page.
6. **A date picker** in the review pane instead of a typed `YYYY-MM-DD` box.
7. **PDF and emailed receipts** (the Anthropic API takes PDFs as document blocks,
   so the engine change is small).
8. **Multi-page or multi-receipt images** — currently one image is one receipt.
9. **Code signing**, to stop the SmartScreen warning. §7 sets out the mechanism
   and the options with costs; the cheapest real answer is Azure Artifact
   Signing at about $10/month, and doing nothing is defensible.
10. **Batch scanning via the Message Batches API** at half price, for someone
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
| 1.17.2 | 2026-09-10 | **The last three parser defects, closed.** None needed a new idea -- each was a pattern that did not allow for how the ink actually reads (§11.76). Costco's margin flag is one letter the detector splits into three, so `EEE 9218 RED ONIONS` failed `_LEADING_ITEM_NO` outright and kept flag and item number inside the name; a backreference now accepts a repeated letter, deliberately not `[A-Za-z]{1,3}`, which would eat the opening of any description followed by a number. `lb` reads as `1b` -- the same strokes, with no surrounding word to break the tie -- so `_WEIGHED` missed the weighing line, never merged the name from the line above, and booked the item as `0.42 1b. @ 1.00 1b. / 3.62`. And some Aldi lines carry a second code between the description and the price. Where it comes from is **not** established -- it appears once in the reading, but the hand transcription of the same receipt records it as another row's item number -- and an earlier draft of §11.76 claimed otherwise; the strip is bounded so that being wrong about the cause costs nothing. **Names exact 74 → 76 of 81** over the eight photographs -- Costco 15 → 16 of 16, Walmart3 8 → 9 of 9 -- with no line lost, none invented and still $0.00 unaccounted. The Aldi fix does not show in the score, because the same line also reads `Bow1` for `Bowl`; it is worth doing because it removes a stray six-digit number from an item name in the user's books. **Windows OCR did not move at all** (66/19/3, 41 names), which is the corroboration worth having: a shared-parser change that moves one engine and not the other is aimed at a real reading defect rather than at the harness. **Then the user found, in the review pane of the KFC receipt, that the four combo components they had typed in by hand carried no category at all** while the priced line above them read Uncategorized (§11.77). Only a hand-added row can reach that state, and `store.py` was already counting it as Uncategorized through a `COALESCE`, so the pane and the figures disagreed about the same line. Such a row now falls into **Other** -- the user's choice over Uncategorized, and the right one: Uncategorized is a marker meaning nobody has decided, while a reviewer who typed the name in has identified the item. The trap was the provenance rather than the category: `_collect` treats any category differing from the loaded one as the reviewer's own and excludes it from rule backfill, so recording the fallback that way would have pinned every hand-added line to Other for good. It records as `default` instead. Scoped to hand-added rows only, also at the user's choice, so nothing already in the books moves until its receipt is next saved. 523 tests, both baselines re-recorded. |
| 1.17.1 | 2026-09-10 | **What the pre-push reviews caught.** Three wrong comments, one of which was a real defect rather than wrong prose (§11.73-74). `MAX_CONFIDENCE` was 0.75 while `LOW_CONFIDENCE` is 0.6, so a RapidOCR reading with balanced arithmetic raised no flag and `auto_confirm_clean` signed it off unreviewed -- while the comment promised the exact opposite, citing Windows OCR, which was doing it correctly at 0.5. Now 0.55, and the test that was meant to guard it compares against the real threshold instead of against its own constant. `rapid_ocr.py` and `Bookkeeping.spec` also gave flatly opposite accounts of whether PyInstaller resolves a literal name passed to `importlib`; the spec is right, and believing the other one would have meant deleting the excludes that keep the slim build at 30 MB. Quoted figures regenerated from the committed baselines with the corpus named -- one docstring had been carrying two different Windows OCR scores at once. **Separately, the pre-push scan caught me reintroducing the very identifiers §11.57 exists to remove**: writing up the card-digit fix put a real last-four and a real approval code into a comment, a test literal and this document. All replaced with invented values, kept distinct from each other so the test still proves what it claims. 515 tests. |
| 1.17.0 | 2026-09-08 | **A second offline engine, and a second build to carry it.** The user judged the recognition not good enough and asked for a better open-source model. First the correction: Claude vision had never run on their receipts at all -- no API key -- so everything they had seen was Windows OCR. Surveyed the field against the constraints that bind here (offline, CPU, no key, portable): docTR, EasyOCR and Surya need PyTorch, the VLM readers need a GPU, PaddleOCR needs its own runtime. **RapidOCR** fits -- PP-OCR models on ONNX Runtime, Apache 2.0, weights in the wheel. Measured through the same parser against the confirmed receipts: 73 lines matched against 63, 4 missed against 14, none invented against 3, and 59 item names exact against 37; on Costco 15 of 16 items instead of 12, and it reads the logo Windows OCR cannot read at any scale. It costs 95 MB and 1.7 seconds of start-up, so there are now two builds from one spec: `build.bat` gives the 30 MB portable one and `build.bat --full` the 125 MB one. Three traps on the way, all in §3 and §11.63-66, the sharpest being that touching WinRT first stops onnxruntime loading at all. 447 tests. **This document was also renamed from `Bookkeeping.md` to `Bookkeeping_record.md`** — the workspace convention since August 2026 — with `git mv` so the history follows, and the 18 references to it across the code, tests and README updated. Folded into this version rather than given a patch bump of its own, because 1.17.0 is not published. **The accuracy harness was then pointed at the new engine**, which it had never been: `measure_accuracy.py` built a `WindowsOcrExtractor` unconditionally, so from the moment RapidOCR became the default the `--check` gate was guarding an engine the full build no longer ran — and saying nothing about it. It now takes `--engine` and defaults to `auto`, the first installed of RapidOCR → Windows OCR → Tesseract, which is the application's own order minus Claude; Claude is excluded because its reading is not deterministic and so cannot back a regression baseline, not because of the cost. Baselines are per engine (`accuracy_baseline.rapid.json`, `accuracy_baseline.windows.json`) and a cross-engine `--check` is **refused rather than reported**, since every metric moving at once measures the change of engine and nothing the code did. Two defects found while building it: an unrecognised engine name fell through `build_engines` to the head of the fallback list, which would have answered a typo by running Claude; and resolving the engine before looking for photographs turned a clean clone's "none found" into "no engine available". Both are pinned by tests. The headline figures are now reproducible from one command each rather than quoted from a one-off run — windows 63/14/3 and 37 names, rapid 73/4/0 and 59 names, $117.40 against $14.87 unaccounted across seven photographs. **That measurement then paid for itself**: attributing all 18 remaining defects line by line showed the model was no longer the constraint. Five of the fourteen wrong names were one confusion — a capital O printed as a zero — which a larger recogniser could not have fixed, since the ambiguity is in the ink rather than the reading. Two narrow parser rules put them back (§11.68) and names exact went **59 → 64 of 73**, Walmart1 reaching 23 of 23, with no line lost and none invented. What remains is three genuine detection misses, three rows where Aldi's FP/NF column bleeds into its neighbour, two parser gaps, and two names where the reader spelled the word correctly and the paper did not. On the open question of whether to move to PP-OCRv6 medium: it would cost about 100 MB of weights for perhaps three of the thirteen defects left, so it stays untested rather than adopted. **Then the largest of those remaining clusters turned out not to be a model problem either** (§11.69): the page is photographed 1.5° off square, which on a receipt's widely spaced columns is enough to tip a line's left-hand end into the row above. The angle was already in the data — RapidOCR's detector returns rotated polygons and the code was flattening them to bounding boxes — so grouping along the measured baseline took 73 → 75 lines matched, 4 → 2 missed, 64 → 68 names, and **$14.87 → $0.95 unaccounted** across seven photographs, with Costco and ALDI1_new reconciling exactly. Of the eighteen defects this version started with, nine remain and none of them is the recogniser's fault. **Then the user supplied a KFC receipt and a fourth Walmart** -- the first restaurant bill ever tested here, and the variety gap section 13 has ranked first for weeks. It read the new chain's merchant, date and tax first time and correctly refused to invent prices for the four unpriced combo components, but it exposed two defects (§11.70-71), one of them long-standing and serious: `Cashier: Zackariah` matched CASH, so **three of the eight photographs had been recording a card purchase as cash**, and the card's last four digits were being read off the approval code. Both fixed, plus `CARRY OUT` as a total label for a receipt that never says TOTAL. All eight now report the right tender; KFC1 reconciles 11.89 + 0.95 = 12.84; nothing on the older six moved. Money unaccounted across eight photographs is **$0.15**, all of it the 5-cent bottle deposit that the O-for-zero flag still swallows. **That last gap then closed too** (§11.72), and the user's own observation is what found it: the previous engine handled the deposit line correctly, because Windows OCR boxes *words* and rebuilds the lost space from geometry while RapidOCR boxes *lines* and simply omits it. The parser now reads a lone zero in the flag column as the letter O. **$0.00 unaccounted across all eight photographs**, Walmart1 at 24 of 24 names, and for the first time every line of every transcribed receipt read with none missed and none invented -- 59/0/0 against Windows OCR's 49/10/2 on the same corpus. 515 tests. **The user then checked every photograph line by line** and exported them to truth -- the first time the whole corpus has carried a human transcription, and the first to include a restaurant bill. `ALDI.jpg` was renamed back to `ALDI1_new.jpg` to match the name its receipt is confirmed under, since the exporter finds photographs by filename and the earlier rename had orphaned 18 verified lines. Scored against the full set: **81 matched, 4 missed, none invented, 74 names exact, $0.00 unaccounted**, against Windows OCR's 66/19/3 and 41 names. ALDI2 and Walmart1 are exact line for line and name for name. Of the twelve differences left, five are the recogniser, three the parser (§9), and four are a single scope question -- KFC's unpriced combo components and its absent subtotal, both of which the user *did* record by hand, so the app is not capturing something they want. That is the next thing to decide rather than a defect to fix quietly. |
| 1.16.0 | 2026-09-08 | **A line you type is now looked up too, and a dead end says so.** Reported from a screenshot of two lines with nothing underneath them. They had failed for opposite reasons: one was added by hand and had never been asked about at all, the other had been asked weeks earlier and genuinely has no name and no translation. So the fix is two. `enrich_now` runs the expansion and translation passes over the stored rows after every save -- filling blanks only, never touching amounts, categories or a confirmed status -- and a question that came back empty is now recorded and shown as `no translation found` / 未找到译文 rather than left blank. Nothing is said where nothing was asked, so plain-English lines stay quiet. A lookup that *failed* is deliberately not recorded as a lookup that found nothing (§11.62). The pane refreshes when the pass finishes only if the reviewer has not started typing again. Measured on the real receipt: `DOVE BW 11OZ` becomes 多芬 BW 11OZ, `EQJELLUBE80Z` says 未找到译文, and the receipt stays confirmed. 432 tests. |
| 1.15.0 | 2026-09-07 | **Hand-verification stops being disposable.** The most expensive thing anybody does with this application is check a receipt line by line, and until now the result went into the books and nowhere else -- the next parser change could undo it and no test would notice. `tools/export_truth.py` turns every receipt confirmed in the app into a ground-truth record, so the accuracy harness scores **six of seven photographs against a real transcription instead of one**, and `--check` can fail on a regression that previously nothing could see. The output is gitignored, which is the answer to §12a item 1: aggregate figures may stay in the prose, an itemised list of one person's shopping may not go into git at all -- and it would be useless there anyway, since the photographs are ignored too. A tracked transcription still outranks an exported one, because a reviewer fills in fields the photograph does not show and Walmart1's header is out of frame. Auditing for that decision turned up real card and transaction identifiers already published in the test files (§11.57). 425 tests; baseline refreshed, because invented-line counts went from unmeasurable to measured rather than from good to bad. |
| 1.14.1 | 2026-09-07 | **A saved window geometry could reopen off the screen.** Noticed while screenshotting: the stored geometry was `2461x1733+421+1034` and the window came up with most of itself below the bottom edge. The guard checked only that the top-left corner was visible and never that the window fitted, so it passed. Now clamped rather than accepted or rejected, and clamped against the desktop's **work area** rather than its full height — the first cut of the fix used `winfo_screenheight`, which counts the taskbar's pixels, and left 75 rows of the window behind it. `SPI_GETWORKAREA` gives the honest number. 418 tests. |
| 1.14.0 | 2026-09-07 | **The receipt's own item count is now a check.** First step of the approach the user set out: feed in receipts, verify them, and turn each round of verification into something permanent. The money check says how much is unaccounted for; it never said how many lines to look for, and that is the number that tells a reviewer when they have finished. Every chain here prints the figure and all three print it differently — `ITEMS SOLD 21`, `18 ITEMS`, and Costco's `TOTAL NUMBER OF ITEMS SOLD` which OCR reduces to `TOTAL NUMBER OF 1 EMS sot-c 16` — so `_find_items_sold` carries a pattern per dialect and anchors the Costco one on the surviving `NUMBER OF`. A bare `SOLD 16` is deliberately unmatched: the second Costco pass returns `sold 6` for that line, and a wrong count is worse than none. Discounts and container deposits are excluded — Walmart prints 21 sold against 24 lines, the difference being three Maine deposits — and with that exclusion the printed count agrees exactly with all six of the user's confirmed receipts, which is the evidence the rule rests on. Only a shortfall is flagged, phrased as *at least*, because an invented line hides a missed one. Stored on the row (schema v8) so the flag survives a save. 407 tests, no accuracy regression; the flag now fires correctly on four of the six photographs and stays silent on the two read completely. |
| 1.13.1 | 2026-09-07 | **Five parsing defects, all found by comparing the engine against the user's own corrections.** They went through all six receipts in the application by hand and confirmed each one, which made the books a ground truth and made the differences measurable (§9). (1) Costco prints a per-line flag in the *left* margin where Walmart and Aldi print it on the right, so `E 96716 ORG SPINACH` kept flag and item number inside the name — one fault wearing fifteen wrong names, and fixing it took that photograph from 0 of 16 names exact to 5. (2) `SUBTOTAL` read as `SUBT TAL` was not recognised as summary at all, so the subtotal was booked as the receipt's largest purchase. (3) A stated `TAX 5.16` beat a rate breakdown that summed to the correct 5.15; `_settle_tax` now lets the receipt's own arithmetic decide, closing §12a item 2, which had been recorded as latent and was not. (4) `24.81 TOTAL PURCHASE` — a total printed before its label — was invisible, which is why one Walmart receipt reported no total. (5) A hand-typed `2026-8-18` sorted above `2026-09-06`, putting the oldest receipt at the top of the list. Measured after: Costco header 3/5 → 5/5 and invented lines 3 → 2, Walmart2 header 4/5 → 5/5, the Aldi and Walmart3 rows unmoved. 393 tests, no accuracy regression. ALDI2's missing total was diagnosed and deliberately left: OCR loses the decimal point from `$ 17 - 43` at both scales, so there is no amount to read. |
| 1.13.0 | 2026-09-06 | **Costco receipts are named, categorised and translated properly.** Reported from a screenshot: the merchant read as the street address and the Chinese item names were nonsense. Both had the same root -- Costco prints its own item numbers rather than barcodes, so the lookup that carries Walmart has nothing to query, and the shorthand went to the translator raw (`BUTER CROISS` came back as 黄油克罗斯). The user suggested searching costco.com; it was tried and measured, and the site answers 403 to the catalogue search and to every product page, exactly as walmart.com does. So `app/lookup/shorthand.py` expands what the paper already prints -- `KS` to Kirkland Signature (only at Costco), `ORG`, `CROISS`, `500CT`, and a store-brand prefix run into the next word -- and deliberately leaves `GP`, `CAL` and `BLUEDISH` alone, because their obvious readings are guesses about somebody's shopping. The merchant needed three passes of decreasing strictness: the logo reads as `Cosrco`, `Cesrco`, `=WHOLESAZE`, `=WHOLESALE` or nothing depending only on the size it is read at, and never correctly. Also `DRUMSTICKS` to the glossary (鼓槌 is a drum stick), three Costco keyword rules, `line_item.name_source` so the pane stops labelling every expansion `from barcode:`, and a lookup failure no longer skips the offline expansion too. Measured on the real photograph: merchant `455 Scarborough Downs Rd` becomes `Costco`, uncategorised lines 8 of 15 become 0, 380 tests, no accuracy regression on any of the seven photographs. |
| 1.12.1 | 2026-09-03 | **The masking option promised more than it covered, and the review caught it.** The settings text said it hides *“a card or **membership number**”*, but `SENSITIVE_FIELDS` held one entry, `payment_method`, and a membership number is not a field at all — `MEMBER` is in `_SUMMARY_WORDS`, so the line is discarded before anything stores it. The one place it *does* reach the screen is the **Engine output window**, which inserts the raw engine text verbatim; a Costco reading carries the membership number in its first five lines, with the card trailer just below (not reproduced here, for the reason the receipt photographs are gitignored). So the promise was false precisely where it mattered, and a user who read it, switched the option on and handed over the laptop got exactly the disclosure it undertook to prevent. **The worst class of wrong comment: trusted while false, about a privacy control.** Fixed by making the claim true rather than by weakening it — the raw pane now masks its digits and says so, with the full text one keystroke away. Masking every digit there costs the amounts too, accepted because it is an audit view rather than a working one. Also from the same review, and found independently by both reviewers: **`_cents_text` had the sign bug its own sibling documents**. `_amount_cents` carries a four-line docstring about `int("-0")` being 0, and then `_cents_text` rendered –15 cents as `-1.85`, because Python floors so `-15 // 100` is `-1` and `-15 % 100` is `85`. Reachable through a rate-breakdown line carrying a negative amount, which a refunded receipt prints. **Documenting a trap in one direction is not the same as handling it in both.** Plus four comment corrections: `privacy.py` pointed at `ReceiptsPage._collect` when the method is on `ReviewPane`; the surviving zero-tax guard lost its explanation when the special case around it was replaced; `_find_summary_amounts`'s “last matching line wins” needed its new tax exception; and `toggle_masking`'s “nothing stored changes” was ambiguous in a method whose first statement saves the setting. 344 tests. |
| 1.12.0 | 2026-09-03 | **A third chain, and an option to hide what a receipt says about its owner.** Costco is structurally unlike Walmart and Aldi in one way that broke the parser outright: it charges **two tax rates on one receipt** (Maine's 5.5% general and 8% prepared food) and prints a component line for each. The rule that handled Aldi's zero-rate line only stopped a zero displacing a real figure, and its own comment predicted the gap — *“two genuinely non-zero rates would still take the last; no receipt seen here does that”*. COSTCO1 is that receipt, and it read the tax as **2.29 when 5.15 was charged**. Rate components are now summed, and a line that states the tax outright beats any breakdown. Two more failures in the same block: OCR drops the second word of `TOTAL TAX 5.15`, leaving a bare `TOTAL` that read as the grand total and reported a **$193.52 purchase as $5.15** — told apart now by arithmetic, since a TOTAL equal to the sum of the rate components above it is those components' total; and `AMOUNT: $193.52` from the approval block is accepted as a total, which matters because the grand-total line on this receipt was scribbled out on the paper. Separately, Windows OCR reads Costco's `Visa` tender line as `Vise`, so it escaped the payment words and was counted as a purchase carrying the grand total — **$193.52 of nothing, more than the receipt's own subtotal**. Net on COSTCO1: tax and total now correct, items 10 → 9 and their sum 315.57 → 122.05. Aldi and Walmart read identically to before, checked figure by figure. **Seven line items are still missed and the subtotal is still lost** — OCR returns `SUBT TRL` with no amount — which was deliberately left for a second pass; the remaining 66.32 gap is those seven items (66.26) plus one `5.99` misread as `5.93`. **The new option** shows the digits of a card or membership number as asterisks: `VISA ****4471` becomes `VISA ********`, keeping the brand while dropping the number. Costco prints the member number on every copy, so this is not hypothetical. On by default, because a privacy control that must be discovered protects only those who already knew to look, and the cost runs one way. Toggled from **View → Hide sensitive details**, **Ctrl+M**, or the Settings checkbox. It is **display only**, and the trap it had to avoid is specific: the review pane's entry boxes are the same widgets the save path reads back, so rendering a mask into one would have written asterisks into the database and destroyed the value the mask exists to protect. A masked field is shown read-only and its true value passes through the save untouched, pinned by `test_saving_a_masked_receipt_never_writes_the_mask_into_the_books`. Full Chinese for the new strings. 342 tests. |
| 1.11.7 | 2026-09-03 | **Pinned a referent.** The 1.11.6 row said “its predecessor” without saying what that was. The thing 1.11.5 audited was the `.gitignore` comment block, which 1.11.4 had rewritten — not the 1.11.4 changelog row, which is what “predecessor” most naturally points at in a table of rows. A reader following the wrong referent would search the row above for a phrase that was never in it. Named explicitly instead. No code changed; 328 tests. |
| 1.11.6 | 2026-09-03 | **Deleted a count.** The 1.11.5 row said the `.gitignore` note rewritten one round earlier was untrue of its own prose because “three present-tense clauses follow it”. That holds only if *clause* is read as *sentence*; by the ordinary sense there are more. Removed rather than corrected, because the row's point stands without a number and the number was the only part that could be wrong. Ends a run in which each fix explained itself into the next defect — the edit that stopped it was a deletion. No code changed; 328 tests. |
| 1.11.5 | 2026-09-03 | **Two wording fixes in the note 1.11.4 rewrote.** Neither was a false statement about behaviour; both were the note describing *itself* inaccurately. “Past tense throughout” was not true of its own prose, and is now “Describes a hole that is now closed”, which says the thing that matters and claims nothing about grammar. “See the block beneath the extension rules” pointed past its target, which sits *between* the two extension groups rather than below both; now “the next comment block”, which is positionally exact. Recorded once because it is the fourth consecutive round in which review found imprecision in prose the previous round had written: **each fix was adding more explanation than it was correcting, and the new explanation was the new defect.** The fix here was to write less. No code changed; 328 tests. |
| 1.11.4 | 2026-09-03 | **Three stale or miscounted claims, two found by review and one found while fixing them.** (1) The receipt-photograph note at the top of `.gitignore` still warned, in the present conditional, that a `.heic`, `.webp` or scanned `.pdf` “would be untracked *and* unignored” — false since 1.11.3 closed exactly that hole seven lines below it. The classic stale-comment shape: the comment stood still while the thing under it changed. Rewritten in the past tense and **kept rather than deleted**, because the reason the extension list has to be generous is easier to understand from the gap it used to have than from the list itself. (2) The 1.11.3 row claimed “all eight now ignored” while naming only seven — `.heif` was added to `.gitignore` and never spelled out, so a reader counting the row's own list got seven and could not tell what the eighth was. `.heif` named, and the count reconciled with `.jfif` explicitly, since that made nine in total. (3) Found while fixing those: **the 1.11.3 row's own summary of the correction it was describing repeated the error being corrected** — it said `make_sample_receipt.py` has a payment line with “no digits at all”, the precise false absolute that 1.11.3 had just fixed one row below. Corrected to “no *card* digits — it reads `VISA TEND` against the receipt total.” A correction is not automatically true because it is a correction, and a summary of a fix is a fresh claim that needs checking like any other. No code changed; 328 tests. |
| 1.11.3 | 2026-09-03 | **Closed the receipt-format hole, and corrected three things the 1.11.2 row got wrong.** `.gitignore` protected receipt photographs with `*.jpg`, `*.jpeg` and `*.png` only — a gap its own comment had named for weeks without anybody acting on it. A receipt is far more likely to arrive as `.heic` (the iPhone default) than as `.jpg`, and `.heif`, `.webp`, `.avif`, `.bmp`, `.tif`, `.tiff` and a scanned `.pdf` were all equally unprotected: untracked *and* unignored, so one `git add -A` would have published somebody's shopping. **All eight now ignored — nine counting `.jfif`, added later in this same commit and described below.** None is tracked anywhere, so the rules cost nothing. The three corrections, all in the 1.11.2 row above and all found by review rather than by me: it credited the blurred string to `make_sample_receipt.py` as well as `seed_demo.py`, but that file's only match is a `TC#` transaction code ending in the same four digits, and its payment line carries no *card* digits — it reads `VISA TEND` against the receipt total; it said the input box's border was left intact, when the blur eats every edge but the bottom; and it said “five fixtures” when there are four occurrences across three files, a miscount from treating `grep` hits as occurrences of the string searched for. Worth recording as a pattern rather than three separate slips: **every one is a claim that sounded authoritative because it carried a number or a filename, and none had been checked against the thing it described.** The review of this very commit then caught a fourth of exactly that kind, which is the most useful thing in this row: correcting the attribution, I wrote that `make_sample_receipt.py` has a “payment line carrying no digits at all”. It renders `VISA TEND` against `TOTAL`, so the line reads `VISA TEND    68.46` — digits, just not card digits. The point I was making survived; the absolute I reached for to make it did not. **Writing about a failure mode is not protection against it.** Two further review findings taken in the same edit: `*.jfif` added, which is what Chrome on Windows saves a JPEG as and was the one plausible receipt format still uncovered; and two real limits of these rules written down rather than left to be discovered — the patterns are case-sensitive where the filesystem is, so an iPhone's upper-case `IMG_0001.HEIC` would be unignored again on a Linux clone, and a `!` line cannot re-include a file inside an excluded directory, so nothing can rescue a `.pdf` from `pictures/` or `data/`. No code changed; 328 tests. |
| 1.11.2 | 2026-09-03 | **The Payment field in `receipts.png` is blurred.** The user asked for it after the repository went public. The value was a card-shaped string masked to last-4 — not reproduced here, for the same reason it was blurred there — which I had checked the provenance of and deliberately left: it is a hardcoded literal in `tools/seed_demo.py`, the real receipt behind the project was paid in cash, and it is masked to last-4 anyway. That reasoning was beside the point. **A published image is judged on how it looks, not on where its data came from** -- a reader cannot audit `seed_demo.py` to reassure themselves, and a card-shaped string sitting beside real merchant names and the author's own name reads as real. Blurred with a radius large enough that no glyph survives magnification — checked at 5×, and again at 8× nearest-neighbour with a full-range histogram stretch, which raises only broad luminance blobs at a spatial frequency far below character pitch. **The blur also eats most of the input box's own border**, which an earlier version of this row wrongly said was left intact: only the bottom edge survives crisply, against all four edges on an unblurred neighbour such as Subtotal. It still reads as deliberate redaction rather than a rendering fault, because the smudge stays inside the field's rectangle under its own label — but that is the field's position in the layout and its label doing the work, not a surviving border. Recorded in `.gitignore` beside the capture instructions, because re-shooting the screenshot removes the blur and the next person to run `--tight` needs to know to redact again. Note that the string is still present as source text — **four occurrences across three files**, not the five this row first claimed: `tests/test_claude_engine.py`, `tests/test_store.py`, and `tools/seed_demo.py` twice. The miscount came from grepping the masked last-4 digits and reading the hit count as occurrences of the full string — the digits are deliberately not written out here either; the fifth hit is a `TC#` transaction code ending in the same four digits, in a file whose payment line reads `VISA TEND` against the receipt total — digits, but not card digits. The user was told and scoped this change to the image. |
| 1.11.1 | 2026-09-03 | **What the pre-publication review caught.** The quality gate blocked, correctly, on a comment that contradicted its own code. `compare()` guards the money gap with `abs(now) > abs(before)`, which fires only when the gap GROWS, and the comment claimed it watched movement “in either direction by more than a cent” — wrong twice, and wrong in the direction that matters: it told a future maintainer the `lines_invented` guard was redundant when it is in fact the only thing catching a gap closed by fabrication. There is a test pinning the one-directional behaviour, so the code was right and the comment was the defect. Also corrected: the changed-photo note implied the score was quarantined when it is scored and compared like any other, so a re-photographed receipt can read as a regression; and a docstring I had just written enumerated three of the capture box's four margins, understating the exposure it exists to warn about. Separately, the safety review's one surviving finding was taken: the screenshots were re-included by `!docs/screenshots/*.png`, which protects a *location* rather than four known files, so any PNG later dropped there would be publishable — now an allowlist of four names. Four stale line counts. No behaviour changed; 328 tests. |
| 1.11.0 | 2026-09-03 | **Published.** The repository went public so interviewers can read and run the project, reversing a standing private-only instruction. Prepared for that in four ways. (1) A `README.md` landing page, because GitHub renders `README.md` and not `Bookkeeping_record.md`, so the repository previously presented as a bare file listing with no entry point; it leads with the measured accuracy and states the network boundary explicitly. (2) Four screenshots in `docs/screenshots/`, taken against demo books — and this exposed a real hazard in `screenshot_pages.py`, whose capture box deliberately reaches ~46px above and 10px either side of the window to include the title bar. `ImageGrab` grabs the *screen*, so that margin captured fragments of other windows behind the app; the first set of images was discarded and a `--tight` flag added that clips to the client area exactly. A second set leaked the Windows username through the settings page's data-folder path and was also discarded. (3) The commit author email was rewritten across all history to a GitHub noreply address before publication. (4) `pydantic` was declared in `requirements.txt` — `app/extract/base.py` imports it directly, and it had been arriving only transitively through `anthropic`. Also corrected the tag list (22 exist, the doc claimed 8) and three stale line counts, including a Python total measured just before the 1.10.0 harness files were staged. No application code changed; 328 tests. |
| 1.10.1 | 2026-09-02 | Documentation reconciled (/md-renew-check, fast mode plus targeted verification of the 1.10.0 additions). The mechanical check was clean, but remeasuring the Aldi accuracy table against the code found two cells that had silently stopped being true: ALDI2 now reads its subtotal and total, which the 1.6.0 table records as not found, and ALDI1 tax reads 0.00 where the paper says 0.15 -- the harness flags that arithmetic as BAD, and whether it is a regression or an error in the original table is explicitly left undetermined. Also noted that ALDI2 7 of 7 is a hand-verified claim rather than a harness measurement, since that receipt has no transcription. One stale line count fixed, caused by accuracy_baseline.json lacking a trailing newline so wc -l and splitlines disagree by one. No code changed. |
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
