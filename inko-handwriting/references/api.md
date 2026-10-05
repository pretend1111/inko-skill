# Inko API v1 — what the scripts do for you

`scripts/inko.py` wraps every endpoint (retries, idempotency, polling, downloads, quoting before paying). Use it; call
the HTTP API directly only for something it doesn't cover. Full human docs: https://inkotype.com/developers

- Base URL `https://api.inkotype.com/v1` (override with `INKO_API_BASE`). Auth header `Authorization: Bearer ink_live_…`.
- Key lookup in `inko.py`: `$INKO_API_KEY` → `./.inko/key` → user config (`~/.config/inko/key`, `%APPDATA%\inko\key`).
- `inko.py` also writes `./.inko/jobs.jsonl` (job log) and `./.inko/plans/<job>.json` (what each job was made from, so
  `wait` / `download` / `rewrite` can put `layout.json` + `plan.json` or `text.txt` next to the pages). The folder has
  its own `.gitignore` (`*`). No keys are written there unless you ran `auth --project`.
- Without `--model`, `inko.py` uses `logic-1` when the text contains `$…$` and `lyric-1` otherwise. Quotes include a
  `payment` line (what the balance pays, what is left, or how much is missing). Money fields are in cents: the job's
  `cost_cents` is what was charged to the balance; `inko.py`'s summaries show it as `cost_cny`. Older servers (from
  before pure pay-as-you-go) could also pay from a quota — job / account `quota_cents`, or only `quota_pages` (1 page =
  ¥1) on even older ones; `inko.py` converts it, uses it in `payment` and shows `quota_used_cny` only when it isn't 0.

## Endpoints

| Method & path | inko.py | Notes |
|---|---|---|
| `GET /models` (no auth) | `models [--symbols]` | models, Logic's stable/beta/substituted/unsupported lists and `symbols.rewrites` (`\eta` → `n`), pricing constants |
| `GET /account` | `account`, `default-style` | `balance_cents` (what jobs are paid from), this key's limit and spend today, `default_style` (the general 常用字迹 or null), `default_styles` (`{"lyric-1": …, "logic-1": …}`: what each model uses without `style`, null = fallback), `favorites` (preset codes), `custom_seats`, `can_remove_label` (may use `label: none`). `quota_cents` / `quota_pages` = 0 and `membership` = null are kept only for old clients and will be removed |
| `GET /styles?model=&favorites=` | `styles --model … [--favorites] --where … --sort …` | all handwritings available to the account, with facets, previews and `favorite` / `default` flags; `favorites=true` = only 收藏 |
| `GET /styles/{code or id}` | — | one handwriting |
| `PUT /styles/{code or id}/default?model=` | `default-style CODE [--model logic-1]` | make it the 常用字迹 (account-wide, website too); `model=logic-1` sets Logic's own one (must be one of the account's 8) |
| `DELETE /styles/default?model=` | `default-style --clear [--model …]` | remove the 常用字迹 (204); with `model` only that model's one |
| `POST /quote` (free) | `quote --text/--file`, and automatically in `generate` | chars, price, `errors` (blocking) / `warnings` (beta symbols) with paragraph + snippet, `style` that will be used (+ `style_note`) |
| `POST /layout` (free, 60/min) | `layout --spec … [--out plan.json] [--preview png]` | pages, chars, unplaced, warnings, the plan |
| `POST /generations` → 202 | `generate …` (quote only) / `generate … --yes` | creates the job; the price is frozen in the balance, settled on success |
| `GET /generations/{id}` | `wait ID`, `download ID` | status, progress, files (signed links, 24 h) |
| `GET /generations?limit=` | `jobs` | recent jobs |
| `POST /generations/{id}/rewrite` (free) | `rewrite ID --yes` | once per succeeded job, new seed, same settings |
| `POST /generations/{id}/cancel` | `cancel ID` | refunds the frozen amount |
| `POST /generations/{id}/delivery-complete` | `delivered ID` | after every wanted file is saved locally: Inko deletes its copies early (links stop working); billing records stay, website files untouched; 409 `not_completed` / `not_api_delivery` |

## Generation parameters

| field | values | inko.py flag |
|---|---|---|
| `text` | 1–20,000 characters; `\n` = new paragraph | `--text` / `--file` |
| `layout` | layout object (`layout.md`); then `text` comes from it | `--layout file.json` |
| `model` | `lyric-1` \| `logic-1` (default) | `--model` |
| `style` | preset code `"12"` or custom-style UUID; omitted → `layout.d.style` → the 常用字迹 (if it supports the model) → fallback (Lyric No.001, Logic the lowest of the 8). The create response says which: `style_source` = `request` / `layout` / `default` / `system`, plus `style_note` when the 常用字迹 was skipped | `--style` |
| `paper` | `white` \| `cream` \| `grid` (5 mm) — plain mode | `--paper` |
| `size` | `small` \| `medium` \| `large` — plain mode | `--size` |
| `label` | `visible` (default) \| `none` — only for accounts whose owner signed the AI-labelling agreement on the website (`account.can_remove_label`; no seat needed); else 403 `label_required` | `--label` |
| `seed` | 0–2147483647 | `--seed` |
| `scene` | `true` = also deliver `scene.zip`, the editable glyph package (`files.scene`; layout jobs and lyric-1 plain jobs; logic-1 plain jobs not yet) — free, same retention | on by default, `--no-scene` |
| `pen` | `{type: original\|gel\|ballpoint\|fountain\|pencil, color: black\|blue\|blueblack, weight: -1..1, ink: -1..1}` | `--pen-type --pen-color --pen-weight --pen-ink` |
| header `Idempotency-Key` | 1–80 `[A-Za-z0-9_-]` | automatic (`--idempotency-key` to reuse) |

## Jobs

`queued → running → succeeded | failed | canceled`; `progress.stage`: starting, writing (done/total), composing,
uploading, done; `requeued` after a worker interruption (up to 3 tries). Typical time: tens of seconds to a few
minutes; `queue_position` while queued. No webhooks — poll every 2–5 s (`inko.py` does).

Result files: `files.pages[].png` (A4, 300 dpi, 2480 × 3508), `.webp` (preview), `files.pdf` (all pages), `files.scene` (`scene.zip`, when asked for; `references/scene.md`). Links are
signed and valid 24 h (fetch the job again for fresh links); files are kept 30 days (`files.expired`). `inko.py`
downloads to `./inko-output/<time>-<id8>/` with `job.json` (+ `layout.json`, `plan.json` for layout jobs, `scene.zip`).

## Billing

- Pure pay-as-you-go: ¥0.002 per character (0.2 cents; ¥2 per 1000), minimum 100 characters per job (¥0.20), rounded
  up to the cent: `list_cents = ceil(max(chars, 100) / 5)`. Website and API have the same price. No membership,
  subscription or monthly quota.
- Paid from the account balance = gift balance (new accounts registered with a QQ mailbox get ¥3, others ¥0; top-up
  bonuses) + topped-up money; the gift
  balance is used first. Not enough → `402 insufficient_balance`; the user tops up on the website (https://inkotype.com/pricing#topup; the only other
  purchase there is a custom-handwriting seat, ¥19.9, which isn't generation credit).
- The quote's `price` is `{"units": "chars", "amount": chars, "billed_chars": max(chars, 100), "min_chars": 100,
  "list_cents": …}`; `GET /models` → `pricing` = `{"cents_per_char": 0.2, "cents_per_1000_chars": 200, "min_chars": 100}`.
- Plain jobs count every generated block: each Chinese character, letter, digit, punctuation mark; in formulas each
  parsed symbol (a fraction bar and a root sign count 1 each). Layout jobs are billed by the plan's `chars` (a formula
  counts as one).
- Quotes and layouts are free; failed / canceled jobs are refunded; rewrites are free. The job's `cost_cents` says
  what was actually charged (settled when the job succeeds; fewer characters written = less charged). Its
  `quota_cents` is 0 on new jobs (kept for old clients; jobs from before pure pay-as-you-go may show the quota they
  used).
- Keys can have a daily spending limit (set on the website) → `429 daily_limit`.

## Limits

120 requests/min per key; 30 submissions/min per account; concurrent jobs per account (queued + running): 2 when the
account has any topped-up balance, else 1 (`429 too_many_active`). Jobs that use topped-up money queue ahead of jobs
paid by the gift balance alone. 20,000 characters and 60 pages per job; 10 keys per account.

## Labels

Every file carries implicit AIGC metadata (GB 45438-2025); pages carry the visible label 「AI生成 · Inko」 in the
bottom-right margin unless `label: none` was allowed (the AI-labelling agreement signed on the website). Users must
not remove or hide labels (Inko terms of service).
