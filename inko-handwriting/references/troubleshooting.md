# Troubleshooting

`inko.py` errors print `{"ok": false, "error": {"code", "message", "hint", "request_id"?}}` and exit non-zero.
Scripts print `error: …` on stderr. Start with `python scripts/inko.py doctor`.

## API error codes

| code | what happened | do this |
|---|---|---|
| `no_api_key` / `unauthenticated` (401) | key missing, wrong or revoked | ask the user for a key (website → 账户 → API key), `inko.py auth` |
| `insufficient_balance` (402) | the balance doesn't cover the price (there is no membership or quota — every job is paid from the balance) | tell the user the price and what's missing; they top up on the website (价格 → 充值余额, https://inkotype.com/pricing#topup), then submit again |
| `invalid_text` (400) | unwritable characters/symbols (an unsupported symbol or an unknown LaTeX command such as `\geqslant`, `\because`, `\binom`: `unsupported_symbol` in the quote), unpaired `$`, formula in lyric-1, > 60 pages | run the quote, fix what `errors` lists (`writing-math.md` §5 symbol table, §6 substitutions), split long texts |
| `broken_backslash` (inko.py) | LaTeX backslashes were eaten by a shell/JSON | rewrite the file with a file tool; double backslashes in JSON |
| `invalid_request` (400/422) | a parameter is wrong; 422 message names the field | fix the flag / layout JSON |
| `content_blocked` (400, not charged) | document-forgery risk (IOU, receipt, contract, certificate, leave note, signature) or illegal content | explain; don't try to get around it |
| `style_not_found` (404) | no such handwriting for this account | `inko.py styles --model …` |
| `style_model_mismatch` (400) | handwriting doesn't support the model (Logic: only the account's 8; custom: lyric-1 only) | pick from `styles --model` |
| `style_not_ready` (409) | `default-style` with a custom handwriting that isn't finished on the website yet | pick another one, or wait until it's ready |
| `no_slot` (403) | a custom handwriting that is no longer in one of the account's seats (a seat was refunded or taken back; each seat holds one custom handwriting) | pick another handwriting; to use this one again the user buys a seat (¥19.9) or deletes another custom handwriting on the website |
| quote/job says `style.source: system` with a `style_note` | the user's 常用字迹 can't do this job (custom → Lyric only; not among the Logic 8; still training) | choose from their favourites (`styles --favorites`) or by description, and tell the user why |
| `label_required` (403) | `--label none` without eligibility | the user signs the AI-labelling agreement (《AI 生成内容标识协议》) on inkotype.com first (no seat or purchase needed); until then generate with the label |
| `style_gone` (409) | rewriting a job whose handwriting is no longer available | generate again with another handwriting (paid, after a quote) |
| `idempotency_conflict` (409) | the `Idempotency-Key` was already used for a different request | drop `--idempotency-key` or use a new one |
| `not_completed` / `not_api_delivery` (409) | `delivered` on a job that hasn't succeeded, or wasn't made through the API | wait for the job; website jobs need no confirmation |
| `account_disabled` (403) | the account is disabled | the user contacts Inko support on the website |
| `invalid_model` (400) | model other than `lyric-1` / `logic-1` | fix `--model` |
| `rate_limited` (429) | too many requests | inko.py waits `Retry-After` and retries |
| `too_many_active` (429) | concurrent job limit (1 job queued or running; 2 when the account has any topped-up balance — `api.md` → Limits) | wait for running jobs (`inko.py jobs`), then submit |
| `daily_limit` (429) | this key's daily spending limit | tomorrow, or the user raises the limit on the website |
| `free_paused` / `queue_full` (503) | peak time (`free_paused`: jobs paid by the gift balance alone, on accounts with no topped-up money, are paused) | retry later (same idempotency key is safe); for `free_paused`, topping up also helps |
| `no_rewrite` (409) | rewrite already used / job not succeeded / is a rewrite | a new generation instead |
| `not_cancelable` (409) | job already finished | — |
| `link_expired` (404) | download link older than 24 h | `inko.py download ID` fetches fresh links |
| `files_expired` | results older than 30 days are deleted | regenerate |
| `network` | no connection / proxy / TLS (e.g. repeated `SSL: UNEXPECTED_EOF`) | inko.py retries transient errors and, when a local proxy (`HTTPS_PROXY=127.0.0.1:7890` …) breaks the connection, switches to a direct connection by itself (it says so). Still failing: check the internet connection; `NO_PROXY=inkotype.com,api.inkotype.com` skips the proxy from the start |
| `wait_timeout` | job still running after `--timeout` | `inko.py wait ID` later — the job keeps running |

Failed jobs are refunded automatically; say so when reporting a failure.

## Script problems

| symptom | fix |
|---|---|
| wide gaps between glyphs, around formulas or between formula pieces (a layout reserves more room than the writing needs) | `scene.py inspect RUN/scene.zip` shows them; `scene.py tighten` closes them, then `scene.py render` — no new generation (`references/scene.md`) |
| a glyph slightly too big / low / tilted, one line looser than the rest, the user wants to nudge things | `scene.py move` / `scale` / `rotate` / `tighten --line`, or let the user do it in `scene.py editor` |
| no `scene.zip` next to the pages | the job was generated with `--no-scene`, by a logic-1 plain job (not supported yet), or on an older server: regenerate with a layout, or use `compose.py drift` / `ink.py` on the PNG |
| `this script needs Pillow and numpy` | `python -m pip install -r scripts/requirements.txt` |
| boxes/text in previews show as squares | no CJK font found: set `INKO_FONT=/path/to/NotoSansCJK.ttc` (only affects previews/overlays, not the handwriting) |
| lines too long for the paper | generate with `suggested_layout` margins; or `--scale 0.95` |
| units / letters after a formula float like an exponent | put the unit inside the formula (`$5\,cm$`) or write it in Chinese |
| `math_style` warnings in the quote / layout output (logic-1) | not blocking, but fix them before paying: `display_formula` → write the `$$…$$` formula inline after its lead-in; `full_stop` → delete every `。．.` from the solution (commas at most); `orphan_lead` → join the lead-in (所以 / 得 / `：` …) and the formula into one line. Follow each warning's `fix`, then run the free check again (`writing-math.md` §1). Prose that merely contains a formula (notes, a letter) keeps its `。` — ignore `full_stop` there |
| `formula_splits` in the output | inko.py cut a long `=` / `≤` … chain into pieces so it can continue on the next line — intended. To keep formulas in one piece, `--keep-formulas` (a long one then moves whole or is squeezed) |
| `formula_note` says long formulas were NOT split | the layout uses raw `start`/`end` / `blocks` / box `range` positions, or a `match` mark cuts through a long formula: use `line` marks and `match` whole formulas, or cut the chain yourself (`$A=B$ $=C$`) |
| `所以` left alone at a line end, or layout warnings `mshrink` / `mtiny` | a formula didn't fit and wasn't cut: no top-level relation to cut at (only one at its very start, or all inside brackets / fractions), `--keep-formulas`, or see `formula_note`. Start that step on a line of its own, or cut it yourself before a relation (`$A$ $=B$`). `mshrink` on a formula you scaled up on ruled paper is expected (the line spacing caps its height) |
| a centred formula on its own line in the result (`mexpand` in the layout warnings) | `$$…$$` was used — make it inline after its lead-in; for a bigger fraction use a mark with `f.scale` |
| `compose.py drift` refuses: page already drifted | the pages were drifted before — drift the job's original pages instead; `--force` only if you really mean to drift again |
| drift looks too strong / too weak | `--drift-amount 0.6` / `1.4`; `--drift random` for a small wobble without the steady creep; another `--seed` for a new variation |
| left edges still ruler-straight | the pages weren't drifted: `compose.py drift --job RUN -o drifted` (not spaces or tiny indent marks) |
| need to check a PDF but can't open it | `inko.pdf` = the same pages as the PNGs — check those; for `pdf.py` output, render with PyMuPDF if installed (`python -c "import fitz; …"`) |
| huge PDF pages | images without dpi use A4; set `--size A4` explicitly |
| Windows console shows garbled Chinese | output is UTF-8 JSON; set `PYTHONIOENCODING=utf-8` or read the files instead of the console |
