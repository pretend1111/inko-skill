---
name: inko-handwriting
description: >-
  Real-looking handwriting from text, notes, letters, essays and math solutions via the Inko API (inkotype.com), plus
  local post-processing: write exactly on the lines of the user's own ruled paper (from a photo), make it look photographed
  or scanned, change ink colour / pen / stroke weight, fill answers into a worksheet, export PDF. Use this skill whenever
  the user wants anything hand-written — 手写, 手写体, 手写字, 写成手写, 帮我写/抄到纸上, 手写作业, 手写笔记, 手写信, 字迹,
  写在横线纸/作文纸上, 做完题出手写图片, 拍照效果, "make it look handwritten", "write this on my notebook paper", "solve this
  and hand-write the steps", "handwritten PDF" — even if they never say "Inko".
---

# Inko handwriting

Inko writes text the way a person does — real pen strokes, not a font — and returns A4 pages (PNG + PDF, 300 dpi).
This skill makes you the expert operator: you prepare the content, pick paper / size / handwriting / pen with the user,
pay only after they agree, and then turn the white pages into whatever they actually need (their own notebook page,
a phone photo, a worksheet with answers filled in, a PDF).

All scripts are in `scripts/` next to this file (call them with the path you installed to, e.g.
`python .claude/skills/inko-handwriting/scripts/inko.py …`). They print JSON on stdout — read it, don't guess.

## First time in a session

```bash
python scripts/inko.py doctor          # Python deps, API reachable, key valid, balance
```

- No key: ask the user to create one at https://inkotype.com (账户 → API key), ideally a separate key for you with a
  daily spending limit. Save it with `python scripts/inko.py auth` (reads the key from stdin; `--project` stores it in
  `./.inko/key` instead of the user config dir). Never repeat the key back, never write it into project files or commits.
- Missing Pillow/numpy: `python -m pip install -r scripts/requirements.txt`.
- `inko.py` keeps a small git-ignored `./.inko/` folder in the working directory: `jobs.jsonl` (job log), `plans/`
  (what each job was made from), optionally `key`. Results go to `./inko-output/<time>-<id>/`. You may keep the user's
  choices in `./.inko/prefs.json` yourself (write it with your file tool, e.g. `{"style": "205", "pen": "gel|blue"}`):
  read it at the start of later sessions and reuse those choices — no script reads it for you.

## Rules that matter (and why)

1. **Money: quote first, submit only after a yes.** `inko.py generate` without `--yes` only quotes (free) and prints the
   price. Tell the user the price, character count, model, handwriting, pen and paper in one short message and wait for
   agreement; then re-run with `--yes`. Price: ¥1.8 per 1000 characters, at least 100 characters per job
   (≈ ¥0.18). Members pay with quota first (500 characters = 1 page): the quote's `payment` line says what will
   actually be used ("0.20 pages of member quota, cash untouched") — tell the user that, not just the ¥ figure.
   If the user already gave you a budget ("anything under ¥5 is fine"), pass `--max-cents 500 --yes` (compared with the
   list price) and don't ask again. `--max-cents` checks one job; across several jobs keep the running total yourself.
2. **Keep the AI labels.** Every page carries a visible label 「AI生成 · Inko」 and hidden AIGC metadata, required by
   Chinese law (GB 45438-2025) and Inko's terms. Never crop, cover, blur, recolour or paint over the label, never strip
   metadata, never re-save through tools that drop it. The scripts here re-apply the label (≥ 5 % of the shortest side,
   readable contrast) and copy the metadata on every output — use them rather than ad-hoc image code. If the user asks
   to remove the label, explain it's a legal requirement; members who signed the labelling agreement can request
   label-free pages themselves (`--label none`), and still have to disclose AI generation when sharing.
3. **No forgeries.** Inko refuses IOUs, receipts, contracts, certificates, leave notes, signatures and similar documents
   (`content_blocked`, not charged). Don't try to work around it (splitting text, renaming things, compositing a
   signature). Don't imitate a specific real person's handwriting without their consent.
4. **Look before you hand over.** Open the PNG / final image yourself (you can view images; zoom into a few lines) and
   check it: all text present, nothing overlapping, lines aligned, label intact, and the usual weak spots — punctuation
   right after a formula, digits in dates, units. `inko.py rewrite JOB --yes` re-writes the **whole** job once for free
   with a new seed: other characters change too, so compare both versions and keep the better one; a rewrite can't be
   rewritten. If a character that matters is still wrong after that, rephrase the text around it (a digit in a heading →
   Chinese numeral, drop a stray period after a formula) and generate again — paid, so within the user's budget and at
   most once or twice; otherwise deliver the best version and say which character is off. `inko.pdf` is made of the
   same pages as the PNGs — check the PNGs, and copy the PDF to a meaningful name when delivering.
5. **Content quality is your job.** Inko writes exactly what you give it. Typos, a wrong answer or a clumsy line break
   will be faithfully hand-written — proofread the text first.

## Workflow

1. **Understand the goal**: what content, for what (homework to hand in, notes, a letter, a mock-up…), on what paper,
   and the final form (image of their paper, photo-like, PDF, plain pages).
2. **Prepare the content** (the part users value most — see the scenario guides below). For math, solve it and write
   the solution the way a student writes it, not the way a textbook typesets it.
3. **Choose** model, handwriting, size, paper, pen. Defaults are fine for anything the user doesn't care about.
4. **Confirm in one message** — see "Ask the user" below. Don't interrogate; ask only what changes the result.
5. **Free checks**: `inko.py quote` (plain) or `inko.py layout --preview` (custom layout) → fix any unsupported
   characters/symbols (the output says where), show the preview if placement matters.
6. **Generate** (`inko.py generate … --yes`) → pages land in `./inko-output/<time>-<id>/` with `job.json` (and
   `plan.json` for layout jobs).
7. **Post-process** as needed (table below) → **look at the result** → deliver file paths, cost, and what the user can
   tweak next ("bluer ink? bolder strokes? a different handwriting?").

## Pick the model

| Content | Model | Notes |
|---|---|---|
| Chinese / English prose, letters, essays, notes without formulas | `lyric-1` | Hundreds of preset handwritings (+ the user's own custom handwriting). No `$…$`. |
| Anything with math: `$…$` inline, `$$…$$` display, Chinese mixed in | `logic-1` | Each account gets 8 curated handwritings. Only the symbols in `inko.py models --symbols`. |

Formulas decide it: one `$x^2$` means `logic-1`. Chemistry like H₂O can be plain text in either model (write `H2O`).
Without `--model`, `inko.py` picks this way automatically and says so in its output.

**Choose the handwriting on purpose.** Without `--style` Inko falls back to Lyric No.001, one of the most casual,
joined-up hands (Logic: the lowest-numbered of the account's 8). Unless the user truly doesn't care, filter by what they
want (`references/styles.md`) — and when writing on their own paper, pick one that resembles the writing already on it.

## Plain pages or a custom layout?

- **Plain** (`--text/--file`, `--paper white|cream|grid`, `--size small|medium|large`): paragraphs flow on A4. Fast,
  good for essays and "just write this". Every line of your text is a paragraph and gets a 2-character first-line
  indent (no switch) — for lists, notes and solutions use a layout with `"indent": 0`. Squared (5 mm grid) paper exists
  only here.
- **Layout** (`--layout layout.json`): exact size in mm, line spacing, margins, ruled / 作文纸 / 田字格 / letter paper,
  titles centred and bigger, no indent, text boxes anywhere (answers, name, date), writing along a curve,
  per-character pen or handwriting. Required whenever the result must match real paper. Read `references/layout.md`;
  templates are in `assets/layouts/`; `inko.py` accepts shorthands so you never count character positions. Check with
  `inko.py layout --spec layout.json --preview preview.png` (free, local preview). Squared paper with a layout: generate
  on `blank` and compose onto `paper.py make --kind grid --json` (see `references/paper-matching.md` §8).

## Ask the user (only what's missing)

Put the open questions in **one** message, with your suggested default for each, in the user's language. Typical:

- **Detail level** (math/homework): 详细（每步都写，适合交作业/给人讲懂）/ 适中（关键步骤）/ 简洁（像草稿，跳步）
- **Handwriting**: offer 2–4 candidates as a contact sheet (`inko.py styles …` + `inko.py previews CODES`), or ask
  "工整 / 自然 / 潦草，偏圆还是偏方？"; the user's own custom handwriting if they have one (lyric-1).
- **Paper & size**: white / cream / grid / ruled 8 mm / 7 mm / 作文纸 / 田字格 / letter, or **their own paper** (photo);
  character size (small ≈ 6 mm, medium ≈ 7 mm, large ≈ 8.5 mm tall).
- **Pen**: original / gel 中性笔 / ballpoint 圆珠笔 / fountain 钢笔 / pencil 铅笔; black / blue / blue-black; thinner–bolder.
- **Output**: plain pages / on their paper photo / photo-like (desk, straight-down, scan, photocopy) / PDF.
- **Price** (always, before submitting).

If the user said "you decide" or the request is clear, decide, state your choices in one line, and only confirm the price.

## Post-processing (all local, instant, free)

| Need | Command |
|---|---|
| Write on the user's own ruled paper (photo or scan) | `paper.py analyze` → generate with its `suggested_layout` → `compose.py lines` (see `references/paper-matching.md`) |
| Answers into blanks of a worksheet photo | `paper.py blanks` → layout boxes at those mm → `compose.py page` (or `compose.py place` per answer) |
| Looks photographed / scanned / photocopied | `photo.py page.png -o photo.jpg --preset desk\|flat\|notebook\|scan\|copy` |
| Other ink colour, pen texture, bolder/thinner, lighter/darker | `ink.py restyle page.png -o out.png --color blue --texture ballpoint --weight 0.4` |
| Transparent handwriting layer (for design tools) | `ink.py extract page.png -o ink.png` |
| A paper Inko doesn't offer (B5 notebook, yellow legal pad, dotted) | `paper.py make … --json paper.json` then `compose.py lines` |
| PDF of edited/composed images | `pdf.py a.png b.jpg -o out.pdf --size A4` |

Prefer the API's own `pen` options (`--pen-type/--pen-color/--pen-weight/--pen-ink`) when generating — they are applied at
full quality; use `ink.py` for colours the API doesn't have (red, green, purple, #hex), textures like marker, or quick
"what if" variants without paying again. When composing onto a photo, `compose.py` picks the ink colour itself
(`--color auto`: the job's pen colour, else the colour of the writing already on the paper, else real-pen darkness) —
Inko's page inks are rendered lighter than ink looks in a photo, so don't force `--color page` there. It also puts the
label in a corner without new handwriting. Details and recipes: `references/postprocess.md`.

## Scenario guides

Read the one that matches before you start — they contain the judgement calls that make results look genuinely real.

- `references/scenarios.md` — end-to-end recipes: math homework from a photo, notes → handwritten PDF, own notebook
  paper, worksheet answers, letter/card, essay on 作文纸, copying (抄写) practice, "make it look like a phone photo".
- `references/writing-math.md` — how students actually write solutions (解/由…得/所以/答), detail levels, the Logic
  symbol table, substitutions for unsupported symbols, LaTeX pitfalls.
- `references/writing-text.md` — paragraphs, punctuation, letters, essays, English text, line breaks that look natural.
- `references/styles.md` — choosing a handwriting from descriptions ("工整好看", "像男生写的", "潦草"), facets, previews.
- `references/layout.md` — the layout JSON (papers, `d`, boxes, marks, pens), recipes, reading the plan.
- `references/paper-matching.md` — the user's own paper end to end, photo tips, fixing misalignment.
- `references/postprocess.md` — ink / photo / pdf / compose options with examples.
- `references/api.md` — endpoints, parameters, limits, billing, job states, files and expiry.
- `references/troubleshooting.md` — every error code and what to do; common script problems.

## Writing JSON and LaTeX safely

LaTeX backslashes die in shells and JSON (`\frac` → form-feed + `rac`, `\neq` → newline + `eq`). Write text and layout
files with your file-writing tool (not `echo`/heredoc), double every backslash inside JSON strings (`"\\frac{a}{b}"`),
and pass them with `--file` / `--layout`. `inko.py` refuses input that shows swallowed backslashes and says why.
