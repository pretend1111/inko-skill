# Custom layout (`--layout layout.json`)

Plain generation flows paragraphs onto A4. A layout decides exactly where and how every character goes. It's the same
engine as the website's 创作 → 书写 page; what the preview shows is what gets written.

Contents: 1. Minimal spec · 2. Papers · 3. Page settings `d` · 4. Styling characters `marks` · 5. Boxes ·
6. Pens · 7. Recipes · 8. Checking: preview and plan · 9. Gotchas

## 1. Minimal spec

```json
{"text": "第一段\n第二段", "paperId": "ruled8", "d": {}}
```
- `text`: the whole document, one paragraph per line; `$…$` / `$$…$$` only with `logic-1`.
- Everything else is optional. Units are **mm**, origin top-left of the A4 sheet (210 × 297).
- Model and handwriting are not part of the layout: pass `--model` / `--style` / `--pen-*` to `inko.py generate`.
- `inko.py` also accepts a full request body `{"model": …, "layout": {…}}`.

## 2. Papers (`paperId`)

| id | what | writing area (default margins top/right/bottom/left) | character size |
|---|---|---|---|
| `blank` | plain A4 | 22 / 20 / 32 / 20 mm | `d.size`, default 7 mm |
| `ruled8` | exercise-book lines 8 mm apart, red margin at 24 mm, No./Date header | first line y = 34, last 274, x 24–194 | follows the lines (≈ 5 mm) |
| `ruled7` | lines 7 mm apart | first 33, last 276, x 24–194 | ≈ 4.3 mm |
| `letter` | letter paper, red lines 9 mm, header | first 52, last 268, x 22–188 | ≈ 5.6 mm |
| `compo` | 作文纸, 20 × 20 = 400 cells of 9 mm, 3 mm between rows | cells from y = 34 | one character per cell |
| `tian` | 田字格, 12 × 15 cells of 14 mm | cells from y = 30 | one per cell |

On ruled/grid papers the size follows the paper (writing sits on the lines); set `d.size` only on `blank`.
There is **no squared (5 mm grid) paper** in layouts — plain mode has `--paper grid`. For a squared page with a
layout (centred title, no indent, boxes): generate on `blank` with the layout from
`paper.py make -o grid.png --kind grid --pitch 5 --json grid.json` (its `suggested_layout`), then
`compose.py lines --paper grid.json` (one line of text per two squares; `--row-step` changes that).

The exercise-book header of `ruled8` / `ruled7` reads "No. ________   Date ____ / ____", right end at x = 192 mm,
baseline y = 24 mm; the Date blanks are x ≈ 175.9–182.5 and 185.3–191.9 mm (see the recipe below).
`background: {id, w, h}` uses a paper photo uploaded on the website (id from there); through the API the skill
instead composes onto the user's paper locally (`paper-matching.md`).

## 3. Page settings `d`

| field | meaning | default |
|---|---|---|
| `size` | character height, mm (blank paper) | 7 |
| `line` | line spacing as a multiple of `size` (blank paper) | 1.7 |
| `letter` | extra letter spacing, fraction of the size | 0 |
| `align` | `left` / `center` / `right` | left |
| `indent` | first-line indent, in character sizes (decimals allowed, e.g. 3.4) | 2 |
| `margins` | `[top, right, bottom, left]` mm; `null` entries follow the paper | paper's |
| `cols` / `rows` | characters per line / lines per page; given → size, spacing are derived | 0 (auto) |
| `skip` | leave the first N lines of page 1 empty | 0 |
| `lineStep` | ruled paper: 1 = every line, 2 = every other line | 1 |
| `shear` | slant of the characters, degrees | 0 |
| `style` | default handwriting for the layout (else `--style`) | — |
| `perPage` | at most N characters per page (0 = full) | 0 |
| `autoFlow` | text overflowing a box continues in the next box of the chain | true |
| `fillRest` | text not placed in boxes is written in the page's writing area | true |
| `mathPolicy` | a formula wider than the line: `auto` / `shrink` / `expand` | auto |
| `pen` | default pen for the page, `type|color|weight|ink` (see 6) | original |

`pageDefaults: [null, {"size": 6}, …]` pins settings for single pages (index = page).

## 4. Styling characters: `marks`

`{"id": "M1", "start": 0, "end": 5, "f": {…}}` styles characters `start … end-1` of `text`:
`scale` (size multiplier), `align`, `indent`, `letter`, `shear`, `style` (another handwriting — must be available to
the account/model), `pen`. `align` / `indent` are read from the first character of each line, so mark the **whole
paragraph** when you align or indent it.

**Skill shorthand — don't count characters by hand.** `inko.py` computes `start`/`end` for you:
- `{"line": 0, "f": {…}}` — the whole paragraph 0 (first line of `text`); `-1` = last; `[2, 4]` = paragraphs 2–4;
- `{"match": "此致", "f": {…}}` — the first occurrence of that text (`"nth": 2` for the second);
- `id` may be omitted.

If you do write `start`/`end` yourself: positions in `text` as sent (after JSON unescaping, `\frac` is 5 characters),
counted in UTF-16 units — like Python `len()` except emoji and very rare characters count double.

## 5. Boxes

Put text anywhere. Common fields: `id`, `kind` (`rect` / `area` / `path`), `page` (0-based), `order` (sequence within a
chain), `form` (per-box `size`, `letter`, `line`, `cols`, `rows`, `align`, `indent`, `shear`, `style`, `pen`),
`count` (how many characters this box takes; default fill).

What a box writes — one of:
- `"block": "B1"` → a range of `text` defined in `blocks: [{"id": "B1", "start": s, "end": e}]` (flows through all boxes
  of that block by `order`);
- `"own": "李明"` → its own extra text (name, date, a caption), not part of `text`;
- `"flow": true` → whatever `text` wasn't placed elsewhere, before the page body;
- nothing (`"block": null`) → an empty placeholder that just keeps the page body away.

Kinds:
- `rect`: `x, y, w, h` (top-left, size), `rot` (degrees around the centre).
- `area`: painted region, `strokes: [{"pts": [[x, y], …], "r": radius, "erase": false}]`; lines of text fill it.
- `path`: writing along a curve, `paths: [{"pts": [[x, y], …]}]`.
- `wrap: true` lets the page body use the space beside the box (default: body skips the box's rows).

**Skill shorthand** (expanded by `inko.py` before sending): give a box `"text"` and leave out what you don't need —
`{"id": "Q1", "text": "$x=3$", "x": 30, "y": 60, "w": 80, "h": 18}`. The texts become blocks of `text`; without a
top-level `text`, nothing else is written on the page and paragraphs get no first-line indent (`d.indent` 0 — set it
if you want one). Missing fields default to `kind` rect, `page` 0, `rot` 0, `order` 1, `form` {}. Box texts may have
several lines (`\n`); `line` marks count the lines of all box texts in order, one after another (box 1's lines, then
box 2's …).

## 6. Pens inside a layout

A pen string is `type|color|weight|ink`: `gel|blue|0.3|0`, `pencil||0|-0.2`, `""` = original.
Types `original gel ballpoint fountain pencil`; colours `black blue blueblack` (pencil ignores colour); weight and ink
−1 … 1. Precedence for a character: its mark → its box → the page's `pageDefaults` → `d.pen` → the request's `--pen-*`.
Other colours (red corrections, green…) aren't available from the API: recolour afterwards with
`ink.py restyle` (whole page) or generate those parts separately and `compose.py place` them.

## 7. Recipes

Centred, larger title (the first line):
```json
"marks": [{"line": 0, "f": {"scale": 1.3, "align": "center"}}]
```
Right-aligned closing line: `{"line": -1, "f": {"align": "right", "indent": 0}}`. Letter: `此致` indented,
`敬礼` flush left: `{"match": "敬礼", "f": {"indent": 0}}` (on its own line).

Name and date bottom-right (letters, notes):
```json
"boxes": [
  {"id": "N", "own": "小林", "x": 140, "y": 200, "w": 45, "h": 10, "form": {"align": "right"}},
  {"id": "D", "own": "2026年9月27日", "x": 120, "y": 210, "w": 65, "h": 10, "form": {"align": "right"}}]
```
Solutions without paragraph indent: `"d": {"indent": 0}`. Start lower on the page: `"d": {"skip": 3}`.
Date in the exercise-book header (`ruled8`/`ruled7`):
```json
"boxes": [{"id": "mon", "own": "9", "x": 175.9, "y": 20, "w": 6.6, "h": 5.4, "form": {"size": 4, "align": "center"}},
          {"id": "day", "own": "27", "x": 185.3, "y": 20, "w": 6.6, "h": 5.4, "form": {"size": 4, "align": "center"}}]
```
Continuation lines lined up under the text after `1. 解：`: `{"line": [1, 4], "f": {"indent": 3}}` (see
`writing-math.md` §4).
Every other line on ruled paper: `"d": {"lineStep": 2}`. Exactly 20 characters per line: `"d": {"cols": 20}`.
Match a real paper: use `suggested_layout` from `paper.py analyze` (blank paper, `size`, `line`, `margins`).
Two columns: two `rect` boxes side by side with `"flow": true`, `order` 1 and 2, and `"d": {"fillRest": false}`.
A different handwriting for a quotation: a mark with `{"style": "205"}` (Lyric) on that range.
A tilted sticky-note: a `rect` box with `"rot": -6`.

Ready-made templates: `assets/layouts/` (`letter.json`, `homework-ruled8.json`, `notes-blank.json`,
`essay-compo.json`, `answers-boxes.json`). Copy, replace the text, keep the structure.

## 8. Checking: preview and plan (free)

```bash
python scripts/inko.py layout --spec layout.json --model logic-1 --out plan.json --preview preview.png
```
- `pages`, `chars` (billing for layout jobs; each formula counts once), `unplaced` (> 0 → some text doesn't fit:
  enlarge boxes, add pages or shorten), `warnings`.
- `preview.png` (and `preview-2.png` …): the paper, boxes (dashed), every character at its place in a font, formulas as
  blue boxes, the label corner. Show it to the user when placement matters.
- `plan.json` → `plan.pages[k]` = list of characters `{c, x, y, s, w, r, h, st, row, box, m}`: `x, y` is the
  character's left end of the **baseline** (mm), `s` height, `r` rotation, `row` line index, `box` its box.
  `inko.py generate` saves it next to the pages; `compose.py` uses it.

## 9. Gotchas

- `text` can't be empty (use the box shorthand for boxes-only pages).
- Logic handwriting: only the account's 8 (see `styles.md`); a mark with another style fails with `style_not_found` /
  `style_model_mismatch`.
- Very small `size` (< 3.5 mm) or huge (> 12 mm) looks unnatural; 5–8 mm is typical handwriting.
- 60 pages per job at most; big documents → several jobs.
- The visible label is drawn in the bottom-right margin; keep boxes out of the bottom-right 60 × 18 mm so nothing
  touches it.
