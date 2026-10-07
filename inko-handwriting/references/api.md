# Inko API v1 — what the scripts do for you

`scripts/inko.py` wraps every endpoint (retries, idempotency, polling, downloads, validation before generation). Use it; call
the HTTP API directly only for something it doesn't cover. Full human docs: https://inkotype.com/developers

- Base URL `https://api.inkotype.com/v1` (override with `INKO_API_BASE`). Auth header `Authorization: Bearer ink_live_…`.
- Key lookup in `inko.py`: `$INKO_API_KEY` → `./.inko/key` → user config (`~/.config/inko/key`, `%APPDATA%\inko\key`).
- `inko.py` also writes `./.inko/jobs.jsonl` (job log) and `./.inko/plans/<job>.json` (what each job was made from, so
  `wait` / `download` / `rewrite` can put `layout.json` + `plan.json` or `text.txt` next to the pages). The folder has
  its own `.gitignore` (`*`). No keys are written there unless you ran `auth --project`.
- Without `--model`, `inko.py` uses `logic-1` when the text contains `$…$` and `lyric-1` otherwise. Quotes include a
  `payment` line that identifies free handwriting inference. New quotes / jobs have `list_cents` / `cost_cents` = 0;
  `cost_cny` is the displayed amount. Historical paid jobs retain their actual costs and quota fields; do not rewrite
  their history or mistake those records for the current price.

## Endpoints

| Method & path | inko.py | Notes |
|---|---|---|
| `GET /models` (no auth) | `models [--symbols]` | models, Logic's stable/beta/substituted/unsupported lists and `symbols.rewrites` (`\eta` → `n`), pricing constants |
| `GET /account` | `account`, `default-style` | `balance_cents` (account balance, not required for free handwriting), this key's limit and spend today, `default_style` (the general 常用字迹 or null), `default_styles` (`{"lyric-1": …, "logic-1": …}`: what each model uses without `style`, null = fallback), `favorites` (preset codes), `custom_seats`, `can_remove_label` (may use `label: none`). `quota_cents` / `quota_pages` = 0 and `membership` = null are kept only for old clients and will be removed |
| `GET /styles?model=&favorites=` | `styles --model … [--favorites] --where … --sort …` | all handwritings available to the account, with facets, previews and `favorite` / `default` flags; `favorites=true` = only 收藏 |
| `GET /styles/{code or id}` | — | one handwriting |
| `PUT /styles/{code or id}/default?model=` | `default-style CODE [--model logic-1]` | make it the 常用字迹 (account-wide, website too); `model=logic-1` sets Logic's own one (must be one of the account's 8) |
| `DELETE /styles/default?model=` | `default-style --clear [--model …]` | remove the 常用字迹 (204); with `model` only that model's one |
| `POST /quote` (free) | `quote --text/--file`, and automatically in `generate` | chars, price, `errors` (blocking) / `warnings` (beta symbols) with paragraph + snippet, `style` that will be used (+ `style_note`) |
| `POST /layout` (free, 60/min) | `layout --spec … [--out plan.json] [--preview png]` | pages, chars, unplaced, warnings, the plan |
| `POST /generations` → 202 | `generate …` (quote only) / `generate … --yes` | creates a free handwriting job; no balance deduction |
| `GET /generations/{id}` | `wait ID`, `download ID` | status, progress, files (signed links, 24 h) |
| `GET /generations?limit=` | `jobs` | recent jobs |
| `POST /generations/{id}/rewrite` (free) | `rewrite ID --yes` | once per succeeded job, new seed, same settings |
| `POST /generations/{id}/cancel` | `cancel ID` | cancels the job; returns any historical held amount |
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

- Lyric 1 and Logic 1 handwriting inference is free on the website and API: no per-character fee, minimum charge,
  membership or top-up required. `list_cents`, `cost_cents` and new-job quota usage are 0.
- `GET /models` provides live `pricing`; `/quote` and `/layout` still validate character support and placement.
  Keep server-returned fields, including an explicit 0. Never calculate a charge using the retired character tariff.
- `--yes` means submit, not permission to spend. `--max-cents` defaults to 0, blocking unexpected positive quotes.
  Report a nonzero quote or `insufficient_balance` as a service / endpoint discrepancy instead of demanding a top-up.
- The website's DeepSeek assistant charges actual API token usage separately. This handwriting CLI does not call
  DeepSeek; the external AI agent's provider may charge its own model fees. Do not call all AI usage free.
- Every user gets one free custom-handwriting seat (two training attempts); enrol it on the website, not by purchasing.
- New accounts receive a one-time ¥0.10 DeepSeek trial credit. This is unrelated to eligibility for free inference;
  use `/account` for the actual balance, never infer a gift from the email domain.
- Failed / canceled current jobs have no inference charge. Historical paid jobs retain their original billing and
  refund records. A rewrite is free and limited to once per successful original job; fresh generation is also free.

## Limits

120 requests/min per key; 30 submissions/min per account; concurrent jobs per account (queued + running): 2 when the
account has any topped-up balance, else 1 (`429 too_many_active`). Free generation does not consume topped-up or gift
money; do not promise a paid queue priority. 20,000 characters and 60 pages per job; 10 keys per account.

## Labels

Every file carries implicit AIGC metadata (GB 45438-2025); pages carry the visible label 「AI生成 · Inko」 in the
bottom-right margin unless `label: none` was allowed (the AI-labelling agreement signed on the website). Users must
not remove or hide labels (Inko terms of service).
