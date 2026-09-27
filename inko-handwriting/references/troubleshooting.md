# Troubleshooting

`inko.py` errors print `{"ok": false, "error": {"code", "message", "hint", "request_id"?}}` and exit non-zero.
Scripts print `error: …` on stderr. Start with `python scripts/inko.py doctor`.

## API error codes

| code | what happened | do this |
|---|---|---|
| `no_api_key` / `unauthenticated` (401) | key missing, wrong or revoked | ask the user for a key (website → 账户 → API key), `inko.py auth` |
| `insufficient_balance` (402) | not enough quota/balance | tell the user the price; they top up or buy membership on the website |
| `invalid_text` (400) | unwritable characters/symbols, unpaired `$`, formula in lyric-1, > 60 pages | run the quote, fix what `errors` lists (`writing-math.md` → substitutions), split long texts |
| `broken_backslash` (inko.py) | LaTeX backslashes were eaten by a shell/JSON | rewrite the file with a file tool; double backslashes in JSON |
| `invalid_request` (400/422) | a parameter is wrong; 422 message names the field | fix the flag / layout JSON |
| `content_blocked` (400, not charged) | document-forgery risk (IOU, receipt, contract, certificate, leave note, signature) or illegal content | explain; don't try to get around it |
| `style_not_found` (404) | no such handwriting for this account | `inko.py styles --model …` |
| `style_model_mismatch` (400) | handwriting doesn't support the model (Logic: only the account's 8; custom: lyric-1 only) | pick from `styles --model` |
| `label_required` (403) | `--label none` without eligibility | use the default visible label |
| `rate_limited` (429) | too many requests | inko.py waits `Retry-After` and retries |
| `too_many_active` (429) | concurrent job limit | wait for running jobs (`inko.py jobs`), then submit |
| `daily_limit` (429) | this key's daily spending limit | tomorrow, or the user raises the limit on the website |
| `free_paused` / `queue_full` (503) | peak time | retry later (same idempotency key is safe) |
| `no_rewrite` (409) | rewrite already used / job not succeeded / is a rewrite | a new generation instead |
| `not_cancelable` (409) | job already finished | — |
| `link_expired` (404) | download link older than 24 h | `inko.py download ID` fetches fresh links |
| `files_expired` | results older than 30 days are deleted | regenerate |
| `network` | no connection / proxy / TLS (e.g. repeated `SSL: UNEXPECTED_EOF`) | inko.py retries transient errors; if it keeps failing through a local proxy, try without it: `NO_PROXY=inkotype.com` (or fix `HTTPS_PROXY`) |
| `wait_timeout` | job still running after `--timeout` | `inko.py wait ID` later — the job keeps running |

Failed jobs are refunded automatically; say so when reporting a failure.

## Script problems

| symptom | fix |
|---|---|
| `this script needs Pillow and numpy` | `python -m pip install -r scripts/requirements.txt` |
| boxes/text in previews show as squares | no CJK font found: set `INKO_FONT=/path/to/NotoSansCJK.ttc` (only affects previews/overlays, not the handwriting) |
| `paper.py analyze` finds no lines (`kind: blank`) | faint or dotted lines: better light / a scan, `--line-mm N`; dotted paper has no lines to snap to — use `compose.py page` |
| sheet straightened wrongly (odd crop in `*-flat.png`) | pass `--corners x1,y1,x2,y2,x3,y3,x4,y4` (TL, TR, BR, BL) or `--no-rectify` |
| lines found but numbered from a header rule | lines marked grey (not regular) are ignored automatically; use `--start-line` |
| text too high/low on the lines | `compose.py lines --lift 0.08` (lower) / `0.2` (higher) |
| lines too long for the paper | generate with `suggested_layout` margins; or `--scale 0.95` |
| "text continues on another copy" | more text than free lines: give another sheet `--paper a.json b.json`, `--every 1`, or shorter text |
| label lands on handwriting | `--label-corner bl` (or tl/tr); never remove it |
| composite looks too crisp/clean in a blurry photo | `--soften 1.0`, `--grain 4` |
| ink on the photo looks faded / greyer than real pen | don't use `--color page`; default `auto` uses real-pen darkness; or `--color match` / `--darkness 0.3` |
| new writing looks like a different person than the lines already written | pick a handwriting closer to it (`paper-matching.md` §3); `--color match` |
| units / letters after a formula float like an exponent | put the unit inside the formula (`$5\,cm$`) or write it in Chinese |
| need to check a PDF but can't open it | `inko.pdf` = the same pages as the PNGs — check those; for `pdf.py` output, render with PyMuPDF if installed (`python -c "import fitz; …"`) |
| huge PDF pages | images without dpi use A4; set `--size A4` explicitly |
| Windows console shows garbled Chinese | output is UTF-8 JSON; set `PYTHONIOENCODING=utf-8` or read the files instead of the console |
