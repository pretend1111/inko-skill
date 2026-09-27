# Inko API v1 — what the scripts do for you

`scripts/inko.py` wraps every endpoint (retries, idempotency, polling, downloads, quoting before paying). Use it; call
the HTTP API directly only for something it doesn't cover. Full human docs: https://inkotype.com/developers

- Base URL `https://api.inkotype.com/v1` (override with `INKO_API_BASE`). Auth header `Authorization: Bearer ink_live_…`.
- Key lookup in `inko.py`: `$INKO_API_KEY` → `./.inko/key` → user config (`~/.config/inko/key`, `%APPDATA%\inko\key`).
- `inko.py` also writes `./.inko/jobs.jsonl` (job log) and `./.inko/plans/<job>.json` (what each job was made from, so
  `wait` / `download` / `rewrite` can put `layout.json` + `plan.json` or `text.txt` next to the pages). The folder has
  its own `.gitignore` (`*`). No keys are written there unless you ran `auth --project`.
- Without `--model`, `inko.py` uses `logic-1` when the text contains `$…$` and `lyric-1` otherwise. Quotes include a
  `payment` line (member quota vs balance). Money fields: the job has `cost_cents` / `quota_pages`; `inko.py`'s own
  summaries show them as `cost_cny` / `quota_pages_used`.

## Endpoints

| Method & path | inko.py | Notes |
|---|---|---|
| `GET /models` (no auth) | `models [--symbols]` | models, Logic's stable/beta symbol lists, pricing constants |
| `GET /account` | `account` | balance (cents), quota pages, membership, this key's spend today |
| `GET /styles?model=` | `styles --model … --where … --sort …` | all handwritings available to the account, with facets and previews |
| `GET /styles/{code or id}` | — | one handwriting |
| `POST /quote` (free) | `quote --text/--file`, and automatically in `generate` | chars, price, `errors` (blocking) / `warnings` (beta symbols) with paragraph + snippet |
| `POST /layout` (free, 60/min) | `layout --spec … [--out plan.json] [--preview png]` | pages, chars, unplaced, warnings, the plan |
| `POST /generations` → 202 | `generate …` (quote only) / `generate … --yes` | creates the job; money/quota is frozen, settled on success |
| `GET /generations/{id}` | `wait ID`, `download ID` | status, progress, files (signed links, 24 h) |
| `GET /generations?limit=` | `jobs` | recent jobs |
| `POST /generations/{id}/rewrite` (free) | `rewrite ID --yes` | once per succeeded job, new seed, same settings |
| `POST /generations/{id}/cancel` | `cancel ID` | refunds the frozen amount |

## Generation parameters

| field | values | inko.py flag |
|---|---|---|
| `text` | 1–20,000 characters; `\n` = new paragraph | `--text` / `--file` |
| `layout` | layout object (`layout.md`); then `text` comes from it | `--layout file.json` |
| `model` | `lyric-1` \| `logic-1` (default) | `--model` |
| `style` | preset code `"12"` or custom-style UUID | `--style` |
| `paper` | `white` \| `cream` \| `grid` (5 mm) — plain mode | `--paper` |
| `size` | `small` \| `medium` \| `large` — plain mode | `--size` |
| `label` | `visible` (default) \| `none` (members/permanent custom handwriting who signed the labelling agreement; else 403 `label_required`) | `--label` |
| `seed` | 0–2147483647 | `--seed` |
| `pen` | `{type: original\|gel\|ballpoint\|fountain\|pencil, color: black\|blue\|blueblack, weight: -1..1, ink: -1..1}` | `--pen-type --pen-color --pen-weight --pen-ink` |
| header `Idempotency-Key` | 1–80 `[A-Za-z0-9_-]` | automatic (`--idempotency-key` to reuse) |

## Jobs

`queued → running → succeeded | failed | canceled`; `progress.stage`: starting, writing (done/total), composing,
uploading, done; `requeued` after a worker interruption (up to 3 tries). Typical time: tens of seconds to a few
minutes; `queue_position` while queued. No webhooks — poll every 2–5 s (`inko.py` does).

Result files: `files.pages[].png` (A4, 300 dpi, 2480 × 3508), `.webp` (preview), `files.pdf` (all pages). Links are
signed and valid 24 h (fetch the job again for fresh links); files are kept 30 days (`files.expired`). `inko.py`
downloads to `./inko-output/<time>-<id8>/` with `job.json` (+ `layout.json`, `plan.json` for layout jobs).

## Billing

- ¥1.8 per 1000 characters, rounded up to the cent, minimum 100 characters per job; or member quota at 500
  characters = 1 page (0.01-page precision). Order: quota → gift balance → paid balance.
- Plain jobs count every generated block: each Chinese character, letter, digit, punctuation mark; in formulas each
  parsed symbol (a fraction bar and a root sign count 1 each). Layout jobs are billed by the plan's `chars` (a formula
  counts as one).
- Quotes and layouts are free; failed / canceled jobs are refunded; rewrites are free. `cost_cents` / `quota_pages`
  in the job say what was actually charged.
- Keys can have a daily spending limit (set on the website) → `429 daily_limit`.

## Limits

120 requests/min per key; 30 submissions/min per account; concurrent jobs per account: free 1, with balance 2, Pro 3,
Max 5 (`429 too_many_active`); 20,000 characters and 60 pages per job; 10 keys per account.

## Labels

Every file carries implicit AIGC metadata (GB 45438-2025); pages carry the visible label 「AI生成 · Inko」 in the
bottom-right margin unless `label: none` was allowed. Users must not remove or hide labels (Inko terms of service).
