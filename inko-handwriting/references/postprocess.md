# Post-processing reference

All scripts run locally (Pillow + numpy; OpenCV optional), take a second or two per page, cost nothing, and keep the
AI labels: the visible 「AI生成 · Inko」 is carried or re-applied at ≥ 5 % of the output's shortest side, the AIGC
metadata is copied into PNG / JPEG / WebP / PDF outputs. Pages generated without a visible label stay without one.

Contents: ink.py · photo.py · compose.py · paper.py make · pdf.py · recipes · what not to do

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

## photo.py — photographed / scanned look

```bash
python scripts/photo.py page.png -o photo.jpg --preset desk|flat|notebook|scan|copy [overrides]
```
Overrides: `--background desk.jpg` (their own table photo), `--tilt 0–25` (camera tilt), `--turn` (sideways angle),
`--roll` (sheet rotation), `--fill 0.5–0.98` (how much of the frame the sheet fills), `--light lamp|warm|daylight|cloudy|even`,
`--shadow 0–1`, `--curl 0–1`, `--blur px`, `--noise grey-levels`, `--size long-side-px` (0 = keep), `--seed`, `-q`.

Input may be an Inko page, a restyled page or a composed page. For photos of the user's own paper, compose onto that
photo instead (it already has real light) — photo.py is for generated paper. `flat` is the clearest, `desk` the most
casual (far edge slightly soft). The label is lifted off the page (the paper under it — ruled lines, grid — is rebuilt)
and put on the finished photo at ≥ 5 % of its shortest side, never straddling the sheet's edge.

## compose.py — onto real paper and images

What `lines` does and doesn't carry over: it follows the text flow line by line; characters in text boxes (name/date
boxes) are left out — put such extras on their own lines of the text instead (a right-aligned `line` mark), or place
them with `place`. It writes with one ink colour per run, so per-character pens from the layout don't survive (run it
once per colour, or keep the generated page). Its JSON `check` lists, per new line, how high the ink sits above the
line and how tall it is (in line spacings); compare with `existing_writing` when the page already had writing.

```bash
python scripts/compose.py lines --job RUN --paper paper.json -o final.jpg      # snap to ruled lines (paper.py analyze)
python scripts/compose.py page  --job RUN --sheet blanks.json -o out.jpg        # whole page onto a same-size sheet
python scripts/compose.py place page-1.png --onto img.jpg --box x,y,w,h -o out.jpg   # into a box of any image
```
Common options: `--color auto|match|page|black|blue|blueblack|red|#hex` (auto = the job's pen colour at real-pen
darkness, else the writing already on the paper, else the page's ink darkened; `page` keeps Inko's rendered ink, which
looks faded on photos), `--texture`, `--weight`, `--darkness`, `--soften px` (match photo blur), `--grain` (match sensor
noise; measured automatically), `--label-corner auto|br|bl|tr|tl` (auto: the first corner without new handwriting),
`-q`. lines: `--start-line`, `--every 2`, `--over-writing`, `--lift` (default: like the existing writing, else 0.14),
`--x-offset`, `--scale`, several `--paper`s. page: `--paper-size`, `--offset-mm dx,dy`. place: `--fit
contain|width|height|scale`, `--align`, `--valign`, `--rotate`, `--from-mm x,y,w,h` (only that region of the page). The JSON output says which colour / lift / corner were
used and why. Details and troubleshooting: `paper-matching.md`.

## paper.py make — papers Inko doesn't have

```bash
python scripts/paper.py make -o b5.png --kind ruled --size B5 --pitch 8 --margin-line 20 --paper cream --color blue --json b5.json
python scripts/paper.py make -o grid.png --kind grid --size A5 --pitch 5 --color green
python scripts/paper.py make -o dots.png --kind dots --size A4 --pitch 5
```
Kinds `ruled grid dots blank tian compo`; sizes A3–A6, B4–B6, 16K, 32K, Letter, Legal or `WxH` mm; `--landscape`;
paper `white cream yellow kraft gray`; line colour `blue gray green red black`; `--top/--bottom/--side` margins; `--dpi`.
`--json` writes the exact paper.json for `compose.py lines` (no detection).

## pdf.py — PDF from images

```bash
python scripts/pdf.py final.jpg final-2.jpg -o homework.pdf [--size auto|A4|B5|Letter|WxH] [--margin 0] [--jpeg 90 | --lossless] [--title "…"]
```
Fits each image inside the page (never crops), writes /AIGC into the PDF Info and XMP. For untouched Inko pages the
job's own `inko.pdf` is already the PDF.

## Recipes

| Request | Commands |
|---|---|
| 蓝色圆珠笔、像拍照 | generate with `--pen-type ballpoint --pen-color blue` → `photo.py page-1.png -o p.jpg --preset flat` |
| 字再粗一点 / 细一点 | `ink.py restyle page-1.png -o bold.png --weight 0.5` / `--weight -0.4` |
| 颜色淡一点，像铅笔 | `ink.py restyle page-1.png -o p.png --texture pencil` (or regenerate with `--pen-type pencil`) |
| 红笔批改 | generate the comments separately, `ink.py restyle … --color red`, then `compose.py place` them onto the page |
| 写在我的本子上 | `paper.py analyze` → layout from `suggested_layout` → generate → `compose.py lines` |
| 像扫描件 PDF | `photo.py page-N.png --preset scan` for each page → `pdf.py *-scan.jpg -o scan.pdf` |
| 米黄色信纸 | generate with `--paper cream`, or `paper.py make --paper cream …` + compose |
| 透明底 PNG 给设计用 | `ink.py extract page-1.png -o ink.png` |

## What not to do

- Don't crop pages, paint over the label corner, or re-save images with tools that drop metadata; use these scripts.
- Don't "clean up" generated handwriting with filters that make it look typeset (sharpening, thresholding to pure
  black) — it removes exactly the natural variation users pay for.
- Don't run `photo.py` on a photo of the user's real paper (double perspective, double lighting).
