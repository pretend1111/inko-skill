# Choosing a handwriting

## What's available

- **Lyric 1** (`lyric-1`): the whole preset library (several hundred handwritings, `No.001` …) for every account, plus the
  user's own **custom handwriting** (专属字迹) if they made one on the website (id = UUID; Lyric only; it can only
  write common Chinese characters — the quote flags anything else).
- **Logic 1** (`logic-1`): 8 curated handwritings per account, assigned at random and fixed. `inko.py styles --model
  logic-1` lists exactly those 8; any other code fails with `style_model_mismatch`.
- Default when `--style` is omitted: Lyric `No.001`, Logic the lowest-numbered of the account's 8.

## Facets (percentiles 0–100 among all handwritings)

| facet | low → high |
|---|---|
| `neat` | scribbly → neat (工整) |
| `beauty` | ordinary → beautiful (an estimate) |
| `compact` | spread out → compact |
| `joined` | stroke by stroke → joined-up (连笔) |
| `slant` | upright → slanted right |
| `aspect` | wide/flat → tall/narrow |
| `round` | angular → rounded |
| `weight` | thin → thick strokes (as written; the pen can change it) |
| `ink` | light → dark (as written; the pen can change it) |

`neighbors` lists similar handwritings — useful to offer "like this one but …", or to avoid showing near-duplicates.

## From a description to candidates

| The user says | Filter (`--where`) and sort |
|---|---|
| 工整 / 好看 / 字写得漂亮 | `"neat>=70 beauty>=70"`, `--sort beauty` |
| 像楷体 / 很规整 / 像印出来的 | `"neat>=85 joined<=20 slant<=20"`, `--sort beauty` (a short list instead of hundreds) |
| 普通学生 / 自然 / 不要太完美 | `"neat>=35 neat<=75 beauty>=40"` |
| 潦草 / 随意 / 草稿 / 连笔 | `"neat<=30 joined>=55"` |
| 圆润 / 可爱 / 小清新 | `"round>=70"`, `--sort beauty` |
| 方正 / 硬朗 / 有力 | `"round<=30 weight>=50"` |
| 紧凑 / 小字 | `"compact>=70"` ; 舒展 / 大气 → `"compact<=30"` |
| 斜着写 | `"slant>=70"` ; 端正 → `"slant<=25"` |
| 细笔 / 粗笔 | `"weight<=30"` / `"weight>=70"` (or keep any handwriting and use `--pen-weight`) |
| 像小学生 | `"neat>=40 round>=55 beauty<=70"`, larger size, pencil |
| 像老师写的批注 | `"neat>=60 joined>=50"`, red via `ink.py restyle --color red` |
| 像男生 / 像女生写的 | there's no such facet — show 3–4 contrasting candidates (e.g. angular & loose vs rounded & neat) and let them pick by eye |
| 像我自己的字 | their custom handwriting (made on the website) — `inko.py styles --kind custom` lists it; ask before using one (on a shared or test account it may not be theirs; custom styles have no facets or previews). Custom styles are Lyric-only: no `$…$` formulas — simple arithmetic still works as plain text (`3/4+5/6=19/12`, `2x-3>5`); for real formulas pick the Logic handwriting closest to theirs and say so |

```bash
python scripts/inko.py styles --model lyric-1 --where "neat>=70 beauty>=70" --sort beauty --limit 12
python scripts/inko.py previews 205 345 147 88 --model lyric-1        # downloads cards + contact-sheet.png
```
Pick 3–4 candidates that differ from each other (check `neighbors`), make the contact sheet, **show it** and let the
user choose. `--page` downloads full sample pages instead of cards.

## Keep it consistent

Once the user picks, reuse the same `--style` (and pen) for everything in the conversation and later jobs of the same
document. Remember choices across sessions in `.inko/prefs.json` (git-ignored), e.g.
`{"model": "logic-1", "style": "59", "pen": {"type": "gel", "color": "blue"}, "paper": "ruled8"}` — read it at the start,
mention "using your saved handwriting No.059" and pass the flags explicitly.

## Size, pen and the handwriting together

- Plain mode sizes: small ≈ 6 mm, medium ≈ 7 mm, large ≈ 8.5 mm character height.
- Exercise books (8 mm lines) look right at ≈ 5 mm characters — the ruled papers and `suggested_layout` do that for you.
- A heavy handwriting + `gel` + weight 0.5 looks like a marker; tone down with `--pen-weight -0.3` instead.
- Pencil suits primary-school work and drafts; fountain suits letters; gel/ballpoint are the everyday defaults.
