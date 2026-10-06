---
name: inko-handwriting
description: >-
  Generate standard flat handwritten PNG pages and PDFs from text, notes, letters, essays and math solutions via
  Inko (inkotype.com). Choose paper, handwriting and pen; edit layout and ink locally. Use for 手写、手写作业、
  手写笔记、手写信、二维手写图片、handwritten pages or handwritten PDF. Input images may be read for their content;
  output is always a flat page, without camera effects or compositing into real-world scenes.
---

# Inko handwriting

Inko writes text the way a person does — real pen strokes, not a font — and returns A4 pages (PNG + PDF, 300 dpi).
This skill makes you the expert operator: you prepare the content, pick paper / size / handwriting / pen with the user,
pay only after they agree, and deliver standard flat page images or PDFs. Preserve the natural handwriting,
not a simulated camera: no perspective, desk scenes, shadows, page curl, scanner effects or photo compositing.

All scripts are in `scripts/` next to this file (call them with the path you installed to, e.g.
`python .claude/skills/inko-handwriting/scripts/inko.py …`). They print JSON on stdout — read it, don't guess.

What the models can write changes as they learn: this skill never lists it. Get it live — `inko.py charset` (plain
text: Greek, pinyin tones, superscripts, `½`, `⑪`, `「」` … per model, and what custom handwritings can't write) and
`inko.py models --symbols` (LaTeX commands inside `$…$`) — and let the free quote have the last word. For Logic math,
read `references/writing-math.md` §5 before preparing formulas. When either command reports `skill_update`, tell the
user once that a newer version of this skill is available (`git pull` the inko-skill clone, then `python install.py`).

What changed in skill 1.5.0 (2026-10-06): the lists of writable characters and LaTeX commands moved out of these files
into the live `inko.py charset` / `models --symbols`, so the skill stays right as the models learn (Greek letters,
pinyin tones, superscripts, `½`, `⑪`, `「」` … were added on 2026-10-06).

What changed on 2026-10-04 (skill 1.4.0): both models were retrained on English handwriting (word spacing, capitals,
baseline); the layout engine breaks a formula wider than a whole line at `=`, `≤`, `+` … instead of shrinking it;
generated formulas keep the same size as the text around them; Logic can have its own 常用字迹; removing the visible
label needs only the signed agreement (no seat).

## First time in a session

```bash
python scripts/inko.py doctor          # Python deps, API reachable, key valid, balance, the user's 常用字迹 / favourites
```

- No key: ask the user to create one at https://inkotype.com (账户 → API key), ideally a separate key for you with a
  daily spending limit. Save it with `python scripts/inko.py auth` (reads the key from stdin; `--project` stores it in
  `./.inko/key` instead of the user config dir). Never repeat the key back, never write it into project files or commits.
- Missing Pillow/numpy: `python -m pip install -r scripts/requirements.txt`.
- `inko.py` keeps a small git-ignored `./.inko/` folder in the working directory: `jobs.jsonl` (job log), `plans/`
  (what each job was made from), optionally `key`. Results go to `./inko-output/<time>-<id>/`. You may keep the user's
  other choices in `./.inko/prefs.json` yourself (write it with your file tool, e.g. `{"pen": "gel|blue", "paper":
  "ruled8"}`): read it at the start of later sessions and reuse them — no script reads it for you. Their preferred
  handwriting lives on their Inko account instead (常用字迹, below), so every agent and the website share it.

## Rules that matter (and why)

1. **Money: quote first, submit only after a yes.** `inko.py generate` without `--yes` only quotes (free) and prints the
   price. Tell the user the price, character count, model, handwriting, pen and paper in one short message and wait for
   agreement; then re-run with `--yes`. Price: ¥0.002 per character (¥2 per 1000), at least 100 characters per job
   (= ¥0.20); same price as the website. Pure pay-as-you-go: it comes out of the account balance (the gift balance
   first — new accounts registered with a QQ mailbox get ¥3, others start at ¥0 — then topped-up money); there is no
   membership, subscription or monthly quota. The quote's
   `payment` line says what the balance pays and whether it is enough ("¥0.20 from the balance (¥3.00 available, ¥2.80
   left after this job)") — tell the user that, not just the ¥ figure. Not enough → they top up on inkotype.com first.
   If the user already gave you a budget ("anything under ¥5 is fine"), pass `--max-cents 500 --yes` (compared with the
   list price) and don't ask again. `--max-cents` checks one job; across several jobs keep the running total yourself.
2. **Keep the AI labels.** Every page carries a visible label 「AI生成 · Inko」 and hidden AIGC metadata, required by
   Chinese law (GB 45438-2025) and Inko's terms. Never crop, cover, blur, recolour or paint over the label, never strip
   metadata, never re-save through tools that drop it. The scripts here re-apply the label (≥ 5 % of the shortest side,
   readable contrast) and copy the metadata on every output — use them rather than ad-hoc image code. If the user asks
   to remove the label, explain it's a legal requirement. Only accounts whose owner signed the AI-labelling agreement
   (《AI 生成内容标识协议》) on the website can request label-free pages themselves (`--label none`, see
   `account.can_remove_label`; anyone else gets 403 `label_required`; no seat or purchase is involved), and they still
   have to disclose AI generation when sharing. Don't bring up label removal unless the user asks.
3. **No forgeries.** Inko refuses IOUs, receipts, contracts, certificates, leave notes, signatures and similar documents
   (`content_blocked`, not charged). Don't try to work around it (splitting text, renaming things, compositing a
   signature). Don't imitate a specific real person's handwriting without their consent.
4. **Look before you hand over.** Open the PNG / final image yourself (you can view images; zoom into a few lines) and
   check it: all text present, nothing overlapping, no odd wide gaps (around formulas especially), writing sitting on
   the ruled lines (not crossing them), left edges not ruler-straight (drifted — see post-processing), label intact, and
   the usual weak spots — punctuation right after a formula, digits in dates, units. Layout / spacing / pen problems
   are fixed locally from `scene.zip` without paying again (post-processing); only wrong characters need a rewrite. `inko.py rewrite JOB --yes` re-writes the **whole** job once for free
   with a new seed: other characters change too, so compare both versions and keep the better one; a rewrite can't be
   rewritten. If a character that matters is still wrong after that, rephrase the text around it (a digit in a heading →
   Chinese numeral, drop a comma right after a formula) and generate again — paid, so within the user's budget and at
   most once or twice; otherwise deliver the best version and say which character is off. `inko.pdf` is made of the
   same pages as the PNGs — check the PNGs, and copy the PDF to a meaningful name when delivering.
5. **Content quality is your job.** Inko writes exactly what you give it. Typos, a wrong answer or a clumsy line break
   will be faithfully hand-written — proofread the text first.
6. **Do what was asked, exactly.** "Copy this" means every character, line break, capital and punctuation mark kept —
   no summarising, tidying or "fixing" the source. "Only the final answers" means only the results: no question text,
   no working, no checks, no 解 / 答 prefixes. A complete document is delivered as one document that may run over several
   pages, never shortened or split into repeated copies. If a question is unreadable or a condition is missing, ask —
   don't guess an answer.

## Workflow

1. **Understand the goal**: what content, for what (homework to hand in, notes, a letter, a mock-up…), on what paper,
   and the final form (flat PNG pages, PDF, or both). Images supplied as questions are input material only.
2. **Prepare the content** (the part users value most — see the scenario guides below). For math, solve it and write
   the solution the way a student writes it, not the way a textbook typesets it: each formula on the same line as its
   lead-in (`所以 $x=4$`, `移项得 $2x=8$`), never `$$…$$` (a centred line of its own — textbook look), no full stops
   (`。．.`) anywhere in a solution, commas at most. Long `=` chains simply run on: with logic-1 `inko.py` cuts them
   into pieces that continue on the next line (`formula_splits`; `--keep-formulas` turns it off), and the layout engine
   breaks anything still wider than a line at `=` / `≤` / `+` — never break a chain into lines yourself. Prose keeps
   its `。`. Check every character against what the models can write (`references/writing-text.md`, "What can be
   written") before the quote does it for you.
3. **Choose** model, handwriting, size, paper, pen. Defaults are fine for anything the user doesn't care about.
4. **Confirm in one message** — see "Ask the user" below. Don't interrogate; ask only what changes the result.
5. **Free checks**: `inko.py quote` (plain) or `inko.py layout --preview` (custom layout) → fix any unsupported
   characters/symbols (the output says where) and any `math_style` warnings (`$$`, full stops, a lead-in cut off from
   its formula), show the preview if placement matters.
6. **Generate** (`inko.py generate … --yes`) → pages land in `./inko-output/<time>-<id>/` with `job.json` (and
   `plan.json` for layout jobs).
7. **Post-process** only as needed (table below). Keep the generated page flat; optional line-position adjustment
   uses drift (`scene.py drift` when the job came with `scene.zip`, else `compose.py drift`) →
   **look at the result** → deliver file paths, cost, and what the user can tweak next ("bluer ink? bolder strokes?
   a different handwriting? move something yourself in the editor?").

For image/PDF homework, also follow the completeness checklist in `references/scenarios.md`.
Automatic preparation means reusing the user's defaults, not repeatedly asking style questions; it does not create
spending permission. A website adapter's provided tools and layout rules take precedence over CLI-only instructions.

## Pick the model

| Content | Model | Notes |
|---|---|---|
| Chinese / English prose, letters, essays, notes without formulas | `lyric-1` | Hundreds of preset handwritings (+ the user's own custom handwriting). No `$…$`. |
| Anything with math: `$…$` formulas, Chinese mixed in | `logic-1` | Each account gets 8 curated handwritings. Only the symbols in `references/writing-math.md` §5 — unknown LaTeX commands are refused. |

Formulas decide it: one `$x^2$` means `logic-1`. Chemistry like H₂O can be plain text in either model (whether the
subscript ₂ is written as-is or as `H2O`: see `inko.py charset`).
Answers that are only numbers ("只写最终答案": `x=4`, `-3`, `40`) need no formulas and can stay with `lyric-1`.
Without `--model`, `inko.py` picks this way automatically and says so in its output.

**Never ask the user to choose a model or to resolve a handwriting/model clash.** When the job needs `logic-1` but the
handwriting they picked (or their 常用字迹) is Lyric-only — every custom handwriting and most presets are — use their
Logic 常用字迹 (`default-style` shows `default_styles["logic-1"]`), else one of their Logic favourites, else leave out
`--style` (Inko uses the account's Logic default), and say so in one line: 「含公式，公式部分用 Logic 1 的字迹写」.
Only a handwriting they explicitly named for this job and that can't do Logic is worth a sentence before going on.

**Choose the handwriting — the user's own choices come first.** Go down this list and stop at the first that applies:

1. They name one for this job (No.205, 「用我自己的字」, 「这次写工整点」) → `--style`, or filter by that description.
2. Their **常用字迹** (default handwriting, saved on their account): leave out `--style` and Inko uses it. There can be
   two: a general one and, optionally, Logic's own (one of the account's 8) — `inko.py default-style` lists what each
   model really uses (`default_styles`). The quote says which handwriting will be used (`style.source`: `default` = their
   常用字迹; `system` = Inko's fallback). When no 常用字迹 can do this job's model — custom handwritings are Lyric-only,
   Logic only has the account's 8 — the quote's `style_note` says so; then continue with 3.
3. Their **favourites** (收藏): `inko.py styles --favorites --model <the job's model>` — pick the one that fits the job
   (neat for homework to hand in, relaxed for notes), or offer two or three.
4. Nothing saved: filter by what the job needs (`references/styles.md`). Never settle for the fallback: Lyric's is No.001,
   one of the most casual, joined-up hands.

When the user says 「以后都用这个」,
save it for them: `inko.py default-style CODE` (add `--model logic-1` for a Logic handwriting meant only for math; it
changes their account, the website included — only on request).

## Plain pages or a custom layout?

- **Plain** (`--text/--file`, `--paper white|cream|grid`, `--size small|medium|large`): paragraphs flow on A4. Fast,
  good for essays and "just write this". Every line of your text is a paragraph and gets a 2-character first-line
  indent (no switch) — for lists, notes and solutions use a layout with `"indent": 0`. Squared (5 mm grid) paper exists
  only here.
- **Layout** (`--layout layout.json`): exact size in mm, line spacing, margins, ruled / 作文纸 / 田字格 / letter paper,
  titles centred and bigger, no indent, text boxes anywhere (answers, name, date), writing along a curve,
  per-character pen or handwriting. Use when exact page placement matters. Read `references/layout.md`;
  templates are in `assets/layouts/`; `inko.py` accepts shorthands so you never count character positions. Check with
  `inko.py layout --spec layout.json --preview preview.png` (free, local preview). For squared paper,
  use plain `--paper grid`; custom layouts currently use the built-in flat paper types.

## Ask the user (only what's missing)

Put the open questions in **one** message, with your suggested default for each, in the user's language. Never ask in
pieces over several turns, never re-ask what they already told you (in this request, earlier, or in `./.inko/prefs.json`),
and never treat your recommendation as their answer. Typical:

- **Detail level** (math/homework): 详细（每步都写，适合交作业/给人讲懂）/ 适中（关键步骤）/ 简洁（像草稿，跳步）
- **Handwriting**: if they have a usable 常用字迹, just say you'll use it (no question needed). Otherwise offer 2–4
  candidates as a contact sheet — favourites first (`inko.py styles --favorites` / `inko.py styles …` + `inko.py previews
  CODES`) — or ask "工整 / 自然 / 潦草，偏圆还是偏方？"; their own custom handwriting if they have one (lyric-1).
- **Paper & size**: white / cream / grid / ruled 8 mm / 7 mm / 作文纸 / 田字格 / letter;
  character size (small ≈ 6 mm, medium ≈ 7 mm, large ≈ 8.5 mm tall). This is the one question worth asking for a
  first job when the user gave only the content: recommend 「A4 竖版白纸」 first. "A4" alone is already an answer
  (portrait, white, no lines); an uploaded page to write on is one too.
- **Pen**: original / gel 中性笔 / ballpoint 圆珠笔 / fountain 钢笔 / pencil 铅笔; black / blue / blue-black; thinner–bolder.
- **Output**: standard flat PNG pages / PDF / both.
- **Characters the models can't write** (the live list: `inko.py charset`; the quote flags each one): ask once whether
  to use the writable equivalent the list suggests, with your recommendation first and one or two real examples from
  their text. If they already said how, or told you
  not to ask about such things, just apply the recommended form and mention it.
- **Price** (always, before submitting).

If the user said "you decide" / 「随便」 / 「默认」 / 「不要问」 or the request is clear, decide, state your choices in one line,
and only confirm the price. Don't stop for optional layout preferences (margins, line spacing, alignment).

## Post-processing (all local, instant, free)

| Need | Command |
|---|---|
| Fix spacing / position / size / pen of glyphs or lines, natural left edges — without regenerating (job has `scene.zip`, the default) | `scene.py inspect RUN/scene.zip` → `tighten` / `drift` / `move` / `pen` … → `scene.py render RUN/scene.zip -o final --pdf`; let the user drag things themselves: `scene.py editor RUN/scene.zip` (a Chinese page in their browser). See `references/scene.md` |
| Natural left edges when there is no `scene.zip` | `compose.py drift --job RUN -o drifted` before `ink.py`; not on 作文纸 / 田字格 |
| Everything is saved locally and the user wants Inko's copies gone early (otherwise kept 30 days) | `inko.py delivered JOB` — only after every file they want, `scene.zip` included, is saved |
| Other ink colour, pen texture, bolder/thinner, lighter/darker | `ink.py restyle page.png -o out.png --color blue --texture ballpoint --weight 0.4` |
| Transparent handwriting layer (for design tools) | `ink.py extract page.png -o ink.png` |
| PDF of edited flat-page images | `pdf.py a.png b.jpg -o out.pdf --size A4` |

Prefer the API's own `pen` options (`--pen-type/--pen-color/--pen-weight/--pen-ink`) when generating — they are applied at
full quality; use `ink.py` for colours the API doesn't have (red, green, purple, #hex), textures like marker, or quick
"what if" variants without paying again. All edits stay on the flat page. `paper.py make` can draw a separate
standard paper background; it does not paste handwriting into a photo. Details: `references/postprocess.md`.

## Scenario guides

Read the relevant guide for content and flat-page layout decisions.

- `references/scenarios.md` — math answers, notes, letters, essays and copying practice as flat pages or PDF.
- `references/writing-math.md` — how students actually write solutions (解/由…得/所以/答), detail levels, the Logic
  symbol table, substitutions for unsupported symbols, LaTeX pitfalls.
- `references/writing-text.md` — paragraphs, punctuation, letters, essays, English text, line breaks that look natural.
- `references/styles.md` — choosing a handwriting from descriptions ("工整好看", "像男生写的", "潦草"), facets, previews.
- `references/layout.md` — the layout JSON (papers, `d`, boxes, marks, pens), recipes, reading the plan.
- `references/postprocess.md` — ink / PDF / optional flat-page drift options with examples.
- `references/scene.md` — the editable glyph package (`scene.zip`): inspect, tighten gaps, drift, move, restyle, the
  browser editor for the user, rendering with the label.
- `references/api.md` — endpoints, parameters, limits, billing, job states, files and expiry.
- `references/troubleshooting.md` — every error code and what to do; common script problems.

## Writing JSON and LaTeX safely

LaTeX backslashes die in shells and JSON (`\frac` → form-feed + `rac`, `\neq` → newline + `eq`). Write text and layout
files with your file-writing tool (not `echo`/heredoc), double every backslash inside JSON strings (`"\\frac{a}{b}"`),
and pass them with `--file` / `--layout`. `inko.py` refuses input that shows swallowed backslashes and says why.
