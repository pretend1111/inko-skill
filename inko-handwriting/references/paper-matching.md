# Writing on the user's own paper

Goal: the user sends a photo or scan of their paper (exercise book, notebook, letter pad, worksheet) and gets back
that same photo with handwriting sitting exactly on its lines — same tilt, same perspective, same lighting.

Contents: 1. Getting a good photo · 2. Analyse · 3. Confirm with the user · 4. Generate to match · 5. Compose ·
6. Check and fix · 7. Several sheets · 8. Special papers · 9. Worksheets (blank areas) · 10. How it works

## 1. A good photo (tell the user if theirs is poor)

- The whole sheet, or at least the whole writing area, in frame; phone roughly above the page.
- Even light, no hand or phone shadow across the lines, no glare on glossy paper.
- Sharp: lines must be visible when zoomed in. 1500–4000 px on the long side is plenty.
- A darker table around the sheet helps: the script finds the sheet's edges and straightens it.
- A scan works best of all.

## 2. Analyse

```bash
python scripts/paper.py analyze paper.jpg -o paper.json --overlay paper-check.png
#   --paper-size B5      only if the photo shows the WHOLE sheet (gives true mm)
#   --line-mm 8          if you know the line spacing (6 / 7 / 8 / 9 / 10 are common)
#   --corners x1,y1,…    sheet corners TL,TR,BR,BL if the automatic straightening picked the wrong edges
#   --no-rectify         for flat scans
```

Output (stdout summary, full detail in `paper.json`):
- `kind`: ruled / grid / blank; `pitch_mm`, `px_per_mm`, `scale_from` (how the mm scale was known — if it says
  ASSUMED, the mm values are a guess; positions are still exact because everything is matched to the lines).
- `lines`: every detected line as a curve `y = c0 + c1·x + c2·x²` (px of `analyzed_image`), its extent, whether it
  already has writing (`written`, `written_x1` = where that writing ends), `regular` (fits the spacing) or `inferred`
  (hidden line filled in).
- `first_free_line`, `free_lines`, `written_lines`, writing area `write_x0_px … write_x1_px` (inside the margin line).
- `suggested_layout`: character size, line spacing and margins for generating text that fits these lines exactly,
  plus `chars_per_line_est`, `capacity_chars_est`.
- If the sheet was straightened: `analyzed_image` is the straightened copy (`*-flat.png`) and `flat_to_source` /
  `source_to_flat` map between it and the original photo.

## 3. Confirm with the user (one message)

Show `paper-check.png` (green = free lines, red = already written, grey = ignored, blue = writing area) and ask only
what's open: start line (default: first free line), every line or every other line, left margin OK, pen colour (their
existing writing is usually blue or black — match it), and the price. If your text is longer than
`capacity_chars_est`, say how many sheets it needs.

**When the page already has writing, the new writing must look like the same person wrote it.** `paper.json` →
`existing_writing` gives the pen colour as it appears on this paper (`ink_rgb`), how high the writing sits above the
line (`lift`) and how tall it is (`height`, both in line spacings). Pick a handwriting close to it (neat vs casual,
upright vs slanted, compact — look at the photo; e.g. `--where "neat>=70 slant<=35"` for tidy upright writing) instead
of an arbitrary default.

## 4. Generate to match

Use `suggested_layout` as is:
```json
{"text": "…your text…", "paperId": "blank", "d": {"size": 4.96, "line": 1.6129, "margins": [22, 45.0, 26, 20], "indent": 2}}
```
- Keep `size`, `line` and the left/right margins — they make one generated line exactly as long as one line of the
  paper. You may change `indent` (0 for solutions / lists) and add `marks` (titles). Top/bottom margins only decide how
  many lines fit on each generated page; they don't matter for the result.
- Write on every other line (`--every 2` later)? Then nothing changes here.
- `scale` < 1 in the suggestion means the paper's lines are longer than an A4 frame allows; the text is generated
  smaller and scaled back up when composing — nothing to do.
- Pen: the API pen gives the stroke character (`--pen-type gel|ballpoint|fountain|pencil`) and the colour
  (`--pen-color black|blue|blueblack`); compose.py uses that colour at real-pen darkness. Want the same colour as the
  writing already on the page? Leave the colour to compose (`--color match`, or `auto`, which does this when the job
  has no colour of its own).
- Then quote → confirm → `inko.py generate --layout layout.json … --yes`. The run folder gets `plan.json` (exact
  baselines of every line) which compose.py uses.

## 5. Compose

```bash
python scripts/compose.py lines --job inko-output/<run> --paper paper.json -o final.jpg
```
Useful options:
- `--start-line N`, `--every 2`, `--over-writing` (also use lines that already have writing — rarely wanted)
- `--color auto` (default): the job's pen colour (blue / blue-black / black / pencil) at the darkness real ink has in a
  photo; if the job used the original pen, the colour of the writing already on the page; else the page's ink darkened.
  Also `match` (always the existing writing), `page` (the generated ink exactly — usually too light on a photo),
  `black|blue|blueblack|red|#hex`. `--texture gel|ballpoint|fountain|pencil|marker`, `--weight ±x`, `--darkness ±x`.
- `--lift` baseline height above the line in line spacings — default: like the writing already on the page, else 0.14
  (lower it to sit closer to the line). The output JSON says which colour and lift were used and why.
- `--x-offset 0.5` shift right by half a line spacing; `--scale 0.95` slightly smaller writing
- `--soften`, `--grain` match blur/noise of the photo (auto by default)
- Several sheets: `--paper p1.json p2.json`; text continues on the next sheet (or another copy of the last one).
- Pages generated without `plan.json`: rows are found from the page itself (`--plan` if you have one saved).

The result is the original photo (full resolution) with the handwriting multiplied into the paper, the label
「AI生成 · Inko」 in a corner without new handwriting under it (`--label-corner auto`; bottom-right first) at ≥ 5 % of the
shortest side, and the AIGC metadata.

## 6. Check and fix

Open `final.jpg` and zoom into the first, a middle and the last line.
- Text floats above the lines → `--lift 0.08`; sits on or below the lines → `--lift 0.2`.
- Starts too far left/right → `--x-offset ±0.3`. Runs past the right edge → the layout's right margin is too small:
  use the suggested margins, or `--scale 0.95`.
- Wrong lines used → `--start-line` (numbers are on the overlay).
- Lines missing on the overlay (very faint print) → re-run analyze with `--line-mm`, a sharper photo, or a scan.
- Ink looks too clean/sharp for a blurry photo → `--soften 1.0`; too grey → `--color black` or `--darkness 0.3`.

## 7. Several sheets / front and back

Analyse each photo, then `compose.py lines --paper sheet1.json sheet2.json …`. Outputs: `final.jpg`, `final-2.jpg`, ….
With one photo of an empty sheet, extra text continues on copies of it (fine for blank paper; the script warns if that
photo already has writing, which would then appear twice).

## 8. Special papers

- **Grid / squared paper** (`kind: grid`): lines are the horizontal grid lines; compose works the same (text sits on the
  lines). For one-character-per-cell writing, generate on Inko's `compo` / `tian` paper instead.
- **Squared paper with a layout** (Inko's own `--paper grid` exists only in plain mode, which indents every line):
  `paper.py make -o grid.png --kind grid --pitch 5 --json grid.json` (one text line per two 5 mm squares; `--row-step`
  changes it), generate on `blank` with its `suggested_layout` plus your marks, then `compose.py lines --paper grid.json`,
  then `photo.py` if it should look photographed.
- **作文纸 / 田字格 the user owns**: generate with `paperId: "compo"` / `"tian"` (the cells match most standard pads)
  and use `compose.py page` onto a straightened photo if the cell grid lines up; otherwise plain `compo` pages.
- **Dotted / blank paper**: no lines to snap to → generate normally and use `compose.py page` (same paper size) or
  `compose.py place` (into a box).
- **Paper you don't have a photo of** (B5 notebook, yellow legal pad): draw it:
  `python scripts/paper.py make -o b5.png --kind ruled --size B5 --pitch 8 --margin-line 20 --paper cream --json b5.json`,
  then compose onto it (`--paper b5.json`), then `photo.py` for a photographed look.

## 9. Worksheets: answers into blank areas

```bash
python scripts/paper.py blanks worksheet.jpg -o blanks.json --overlay blanks-check.png --paper-size A4
```
Each blank has `x_mm, y_mm, w_mm, h_mm` on the (straightened) sheet — the whole empty band — and `suggested_box_mm`
(a little in from the edges and clear of the question above), which is where an answer looks natural. Check in the
output that the sheet was straightened (`analyzed_image` is a `*-flat.png`); a photo whose sheet fills almost the whole
frame is handled, but if the corners were missed, pass `--corners`. Put one box per answer (shorthand in `layout.md`;
`"form": {"line": 2.2}` for boxes with several lines of fractions), generate on `blank` paper, and compose the page onto
the sheet:
```bash
python scripts/compose.py page --job inko-output/<run> --sheet blanks.json -o answered.jpg
```
One answer came out badly but the others are good? A rewrite redraws the whole page. Instead put each answer on the
sheet from whichever version wrote it best:
`compose.py place page-1.png --from-mm x,y,w,h --onto answered.jpg --box …` takes just that box's region of a page.
The sheet's size must be the size you planned in (A4 → A4). For a B5 or Letter worksheet pass
`--paper-size B5` to both `blanks` and `compose.py page`, and plan the boxes in that sheet's mm (they stay inside
210 × 297, so they fit on Inko's A4).

## 10. How it works (for debugging)

`analyze` finds the sheet (Otsu + contour), recovers its true aspect from the perspective, straightens it, removes the
residual tilt, then measures thin dark horizontal structures in ~20–40 vertical strips (median per strip, so handwriting
doesn't count as a line), estimates the spacing from their autocorrelation, and links strip peaks into curves.
`compose lines` crops each generated row by its plan baseline, scales it to the local line spacing, bends it along
the paper line (mesh warp) and multiplies it into the paper; for straightened photos the ink is warped back into the
original photo with the inverse perspective.
