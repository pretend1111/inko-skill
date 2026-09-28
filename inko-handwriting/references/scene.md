# Editable handwriting packages (scene.zip)

A job generated with `scene: true` delivers, next to the page PNG / PDF, a **scene package** `scene.zip` (API:
`files.scene`, same 30-day retention as the pages). It holds every handwritten glyph exactly as the model wrote it
(before any pen effect) plus where it sits on the page. With `scripts/scene.py` you can move glyphs, close up gaps,
make the left edges natural, change the pen and re-render the page, locally, in a second, for free. Nothing is
generated again, so the characters you liked stay exactly as they are.

Contents: when to use it · the package · workflow · commands · edit ops · the editor (GUI) · the label rule · limits

## When to use it (and when not)

Use it when the **characters are right but the page looks wrong**:
- a gap between two glyphs is too wide ("大空隙"), or a line is too loose
- left edges are ruler-straight (a real hand drifts), baselines wobble too much
- one glyph is too big / small / tilted, overlaps its neighbour, or sits outside the margin
- the pen should change (colour, gel / ballpoint / fountain / pencil, weight, darkness) for the page, a line or a glyph
- a glyph should go away (hide it)

Do **not** use it for a **wrong or badly written character**, a missing word or new text: the package cannot draw
new glyphs. Fix the text and generate again (only that part, if the layout allows). Also regenerate when the
whole style is off.

## The package

`scene.zip` is extracted once to a sibling folder (`scene.zip` → `scene/`) and used from there; every command also
accepts that folder or its `scene.json`.

```
scene.json            pages, glyphs, lines, pens, paper, label position, AIGC fields
sprites/page-N.png    glyph atlas of page N (8-bit gray, 255 = paper, ink as written)
paper/page-N.png      the paper when it isn't a plain colour (ruled lines, grid, uploaded background)
label.png             the visible label exactly as the server drew it
README.txt            format notes; the AI label must stay
edits.json            your edits (created by scene.py / the editor; scene.json is never modified)
```

Per glyph: `id` (`p0g12` = page index 0, glyph 12), `c` the character (a formula in a layout job is one glyph of
kind `math`), `i`/`j` its span in `text`, `m` the affine map sprite → page pixels (scale, slant, rotation, jitter),
`anchor` baseline anchor, `size` nominal glyph height, `adv` its advance box in the line, `ink` measured ink bounds,
`line`, `box` (glyphs in a name / date box), `cell` (`true` = centred in a grid cell of 稿纸 / 田字格 / 方格 paper: its
place is fixed by the paper, so `tighten` and `drift` never move it; older packages without the field are inferred
from the anchor sitting at the centre of the advance box), `pen` (null = the page's pen). Lines: `p0l3` = page index
0, line 3.
All geometry in scene.json is in page pixels (300 dpi); **all lengths on the command line are millimetres**.

An unedited package renders **the server's page itself** (layout jobs: pixel for pixel), so a render is always a safe
starting point.

## Workflow for the agent

1. `inspect` — read `anomalies` / `hints` (and `lines[].text` to find the line the user means).
2. Apply the smallest fitting command (`tighten`, `drift`, `gap`, `align-baseline`, `restyle`, or an atomic
   `move` / `scale` / `rotate` / `hide`). Use `--dry-run` first when unsure: it prints the ops without saving.
3. `render -o out/` and **look at the result** before handing it over. Not right → `undo` and try differently.
4. Can't get it right by commands (a few glyphs that need hand placement)? Start the `editor` and let the user drag.
5. Deliver the rendered PNG / JPG / PDF (they carry the label and the AIGC metadata). Continue with the other
   flat-page scripts (compose.py drift, ink.py, pdf.py) on the rendered pages as usual.

## Commands

```bash
S=scripts/scene.py
python $S inspect  scene.zip [--max-gap 0.35]            # pages, lines (ref, text, left_mm …), pens, anomalies, hints
python $S render   scene.zip -o out/ [--pages 1,2] [--format png|jpg] [--pdf]
python $S tighten  scene.zip [--line p0l3,p0l5 | all] [--max-gap 0.35] [--jitter 0.2] [--seed N]
python $S drift    scene.zip [--mode natural|random] [--amount 1] [--seed N] [--again]
python $S gap      scene.zip --a p0g12 --b p0g13 (--mm 0.8 | --size 0.12) [--only-b]
python $S align-baseline scene.zip [--line p0l3 | all] [--strength 1]
python $S restyle  scene.zip [--ids …|--line …|--page N] --type gel --color blue --weight 0.3 --ink 0.2   (alias: pen)
python $S restyle  scene.zip --ids p0g7 --reset          # back to the package's own pen
# restyle changes only the fields you give: `--line p0l3 --weight 0.3` makes a blue gel line bolder and keeps it blue gel
python $S move     scene.zip --ids p0g12,p0g13 --dx 1.2 [--dy -0.3]
python $S scale    scene.zip --ids p0g12 --k 0.9 [--about anchor|center|selection]
python $S rotate   scene.zip --line p0l4 --deg -1.5 [--about …]
python $S hide     scene.zip --ids p0g40          # show: the same with `show`
python $S note     scene.zip --text "why"
python $S undo     scene.zip [--n 1] [--ops]      # last n commands (a command = one batch); --ops: n atomic ops
python $S reset    scene.zip                      # drop all edits (edits.bak.json keeps the old ones)
python $S editor   scene.zip [--port 0] [--no-browser]
```
Selecting glyphs: `--ids p0g1,p0g2` or `--ids all`; `--line p0l3,p0l4` (a bare `3` means line 3 of `--page`, 1-based,
default 1); `--page 2`. All commands print JSON (`added`, `ops`, `edits`, per-line reports).

**inspect** anomalies (each with page, ids, chars and numbers):
- `gap` — ink gap between neighbours wider than `--max-gap` × glyph size (after a full-width 。，、 the limit is
  0.3 larger); `natural_size` is the line's own typical gap. Word spaces, gaps inside a group and glyphs written in
  grid cells (稿纸 / 方格: `lines[].grid` is true) are never flagged.
- `stroke` — stroke width (mm, incl. the pen's weight) ≥ 30 % off the same writer's glyphs of about the same size.
- `overlap` — two glyphs' ink boxes overlap by more than 30 %.
- `off_paper`, `near_edge` (< 5 mm from the edge), `outside_margin` (an edited glyph crosses the red margin line of
  ruled paper, or leaves the original text block), `under_label` (the label will cover it), `hidden`.
- `pages[].left_edges_aligned` ≥ 3 → a `drift` hint.

**tighten** shrinks every flagged gap on the chosen lines to the line's natural gap (±20 %, seeded) and moves the rest
of the line with it; word spaces (from the text, or reserved by the layout), formula/group gaps and glyphs in grid
cells (their place is fixed by the paper) are untouched.
Nothing to do → `added: 0`.

**drift** moves each written line sideways as a whole, like a real student: `natural` = a slow creep to the right
(0.03–0.08 × size per line) plus smoothed noise, capped at ~0.45 × size (× `--amount`, max 2), partly reset after a
blank line or at a new problem (`1.` `（2）` `第3题` …); `random` = the smoothed noise alone. Boxed glyphs (name /
date boxes) and glyphs in grid cells stay; a line never leaves the page or crosses a ruled paper's margin line. A second drift is refused
unless `--again` (undo the first instead).

**gap** sets the ink gap between two glyphs; by default the rest of the line follows (`--only-b` moves one glyph).
**align-baseline** puts every glyph's baseline anchor on the line's straight baseline (rotated box lines keep their
slope); `--strength 0.5` goes halfway (removes half of the natural wobble).

**render** writes `page-N.png` (or `.jpg`) and with `--pdf` `inko.pdf` into the output folder. The colour mode follows
the server (`scene.json` `gray`): a job it saved in grayscale (blank / white paper, black ink on every page) renders
grayscale until an edit gives any glyph a coloured pen; then every page is RGB. Pen texture is page-anchored: a moved glyph takes the grain of its
new place.

## Edit ops (edits.json)

```json
{"edits": [
  {"op": "move",   "ids": ["p0g12", "p0g13"], "dx": 1.2, "dy": 0, "batch": 1},
  {"op": "scale",  "ids": ["p0g12"], "k": 1.1, "about": "anchor", "batch": 2},
  {"op": "rotate", "ids": ["p0g12"], "deg": -2, "about": "anchor", "batch": 3},
  {"op": "pen",    "ids": "all", "pen": {"type": "gel", "color": "blue", "weight": 0.3, "ink": 0.2}, "batch": 4},
  {"op": "hide",   "ids": ["p0g40"], "batch": 5},
  {"op": "show",   "ids": ["p0g40"], "batch": 6},
  {"op": "note",   "text": "tighten p0l3 (auto)", "batch": 7}
]}
```
- `dx` / `dy` in mm (y down). `scale` `k` 0.1–10, `rotate` `deg` (+ = clockwise); `about`: `anchor` (each glyph's
  baseline anchor), `center` (each glyph's ink centre), `selection` (centre of all of them) or `[x, y]` page pixels.
- `pen`: `{type: original|gel|ballpoint|fountain|pencil, color: black|blue|blueblack, weight: -1..1, ink: -1..1}`;
  `null` = back to the package's pen. Pencil is always graphite.
- Ops apply in order; `batch` groups the ops of one command (undo removes whole batches); `src` is informational.
- Unknown ids are skipped with a warning (`inspect.warnings`); invalid ops are rejected when written.
You may write ops yourself (append to the list, new `batch` number); the renderer, the editor and the other commands
all read the same file.

## The editor (GUI)

```bash
python scripts/scene.py editor scene.zip            # opens the browser; --no-browser prints the URL only
```
A small local server on **127.0.0.1 only** (random free port; `--port` to fix it) serves a single offline page:
- the page with its paper, glyphs placed by their affine maps and the edits, ink tinted in the pen's colour (the
  pen's texture and weight are applied by the renderer on Export — the preview is an approximation), and the AI label
  in place (not editable)
- click / Shift+click / drag a box to select, double-click or `L` for the whole line, `Ctrl+A` all on the page
- drag to move, arrow keys to nudge (step 0.1 / 0.3 / 1 mm, Shift ×5), `+` / `−` size, `[` / `]` angle, the square
  handle at the selection's corner to scale freely, `Del` to hide ("show hidden glyphs" + Unhide to bring back)
- pen for the selection or for all; "Original" returns to the package's pen
- automatic: Tighten gaps (selected lines or all), Natural drift, Align baseline — the same code as the commands
- Find problems (= inspect, on the unsaved state) with a clickable list; wide gaps are shaded on the page
- Undo / Redo (`Ctrl+Z` / `Ctrl+Y`), **Save** (`Ctrl+S`) writes edits.json (atomic ops only; scene.json is never
  touched), **Export** saves and renders PNG + PDF into `<package>/render/` with the real pen

If a command changed edits.json while the editor was open, Save refuses (reload the page). Stop the server with Ctrl+C.
Tell the user where the exported files are, and re-run `inspect` / `render` yourself after they are done.

## The AI label

Everything rendered from a package is AI-generated content (GB 45438-2025):
- Every export carries the visible label 「AI生成 · Inko」: the server's own `label.png`, pasted where the server put
  it, checked to be ≥ 5 % of the short side (if the file is missing or too small the skill's standard label is
  applied instead), plus the implicit AIGC metadata (PNG iTXt + XMP, JPEG EXIF + XMP, PDF /AIGC + XMP): exactly the
  server's fields, `ProduceID` = the page's id. Once there are edits, the file also carries a separate marker
  `InkoEdited=true` — a PNG tEXt chunk, a JPEG comment, a PDF Info entry `/InkoEdited` — outside the AIGC fields,
  which stay unchanged.
- There is **no option to drop the label**. Only a job generated without a visible label (`label.mode: "none"`,
  decided by the server) renders without one.
- The sprite atlases have no visible label: never publish them, crops of them, or screenshots of the editor as
  finished work. Deliver rendered pages.

## Limits

- Weight is pixel-level (max / min filters, like the server): there are no stroke centre lines to re-draw.
- A formula in a layout job is one glyph: it moves / scales as a block (no symbol-level edits yet).
- Packages of plain (non-layout) Lyric pages are placed per glyph; their unedited render matches the server's page
  closely but not pixel for pixel (the server resized the whole page once). Plain Logic pages have no package yet.
- Large moves can collide with other lines or the label; run `inspect` after editing.
