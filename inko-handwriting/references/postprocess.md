# Post-processing reference

All scripts run locally (Pillow + numpy), take a second or two per page, cost nothing, and keep the
AI labels: the visible 「AI生成 · Inko」 is carried or re-applied at ≥ 5 % of the output's shortest side, the AIGC
metadata is copied into PNG / JPEG / WebP / PDF outputs. Pages generated without a visible label stay without one.

Contents: ink.py · compose.py drift · paper.py make · pdf.py · flat-page output

## ink.py — colour, weight, darkness, texture

```bash
python scripts/ink.py info page-1.png                     # paper colour, ink colour, stroke width, label position
python scripts/ink.py restyle page-1.png -o out.png [options]
python scripts/ink.py extract page-1.png -o ink.png       # transparent RGBA handwriting layer (ruled lines removed)
```
restyle options:
- `--color black|blue|blueblack|red|green|purple|brown|pencil|gray|#RRGGBB`
- `--texture none|gel|ballpoint|fountain|pencil|marker` — gel: solid core; ballpoint: slight skips and pressure
  variation; fountain: wet edges; pencil: graphite grain (default colour graphite); marker: bold and flat
- `--weight -1..1` (±1 ≈ ±0.09 mm per side, like the API; larger values allowed) or `--weight-mm 0.05`
- `--darkness -1..1`, `--bleed 0..1` (ink soaking into cheap paper), `--opacity`, `--seed`
- Output `.jpg` / `.webp` with `--quality`.
Works on a page (keeps its paper and ruled lines) or on an ink layer. The label is cut out before and pasted back
unchanged after, so it never changes colour.

When to prefer the API instead: the API's `pen` (`--pen-type gel|ballpoint|fountain|pencil`, `--pen-color
black|blue|blueblack`, `--pen-weight`, `--pen-ink`) is applied during rendering at full quality. Use `ink.py` for
colours the API lacks, for quick A/B variants, and for pages already generated.

### compose.py drift — left edges that aren't ruler-straight

Optional two-dimensional line-position adjustment. The page remains front-facing and its background stays fixed.
Standard pages can be delivered unchanged; use this only when the user wants looser line alignment.
```bash
python scripts/compose.py drift --job inko-output/<run> -o drifted [--drift natural|random] [--drift-amount 1] [--seed N]
```
`-o` is a folder (the pages keep their names; `plan.json`, `layout.json` and a rebuilt `inko.pdf` come along), or a
`.png` for a single page; the JSON `outputs` list each written page and its `offsets_mm`.
- `natural` (default): a slow creep to the right plus a little wobble, like a hand moving down the page, easing back
  at a new problem or after a blank line; `random`: the small wobble without the creep. `--drift-amount` scales it
  (0.6 subtler, 1.4 stronger); `--seed` gives another variation.
- It moves only the ink, line by line (rows from `plan.json`, else found on the page); the paper, ruled lines, margin
  line and answers in boxes stay where they are. The label and AIGC metadata are kept; if the job had a PDF, a new one
  is written from the drifted pages.
- Use it on pages delivered as they are — homework, notes, letters, anything with several left-aligned lines — before
  `ink.py`. It refuses 作文纸 / 田字格 (characters
  belong in their cells) and a page it has already drifted (`--force` overrides the latter).
- Look at the result: the text must still clear the margin line and the right edge.

## paper.py make — papers Inko doesn't have

```bash
python scripts/paper.py make -o b5.png --kind ruled --size B5 --pitch 8 --margin-line 20 --paper cream --color blue --json b5.json
python scripts/paper.py make -o grid.png --kind grid --size A5 --pitch 5 --color green
python scripts/paper.py make -o dots.png --kind dots --size A4 --pitch 5
```
Kinds `ruled grid dots blank tian compo`; sizes A3–A6, B4–B6, 16K, 32K, Letter, Legal or `WxH` mm; `--landscape`;
paper `white cream yellow kraft gray`; line colour `blue gray green red black`; `--top/--bottom/--side` margins; `--dpi`.
`--json` writes geometry and a suggested layout for the blank background (no image detection or compositing).
This draws a separate paper image; handwriting generation uses the API's supported paper choices.

## pdf.py — PDF from images

```bash
python scripts/pdf.py final.jpg final-2.jpg -o homework.pdf [--size auto|A4|B5|Letter|WxH] [--margin 0] [--jpeg 90 | --lossless] [--title "…"]
```
Fits each image inside the page (never crops), writes /AIGC into the PDF Info and XMP. For untouched Inko pages the
job's own `inko.pdf` is already the PDF.

## Output scope

Deliver standard flat PNG/JPEG pages or PDFs. No photographic rendering, perspective, scene backgrounds, paper
curl, simulated scans, or compositing onto a photograph. Keep AI labels and metadata; do not crop them off.
Use the original generated PDF for untouched pages, or `pdf.py` after edits.
