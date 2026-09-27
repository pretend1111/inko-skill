# Scenario recipes

Each recipe: what to find out, what to prepare, the commands, and the judgement calls. `S=scripts` below means the
`scripts/` folder of this skill. Always: quote → confirm price → `--yes` → look at the result.

Contents
1. Math homework from a photo ("做完这些题，出手写图片")
2. The user's own lined paper (text + photo of their paper)
3. Worksheet: write answers into the blanks of a printed sheet
4. Notes / lecture handout → handwritten PDF
5. Letter, card, postcard
6. Essay on 作文纸 / 田字格 practice
7. "Make it look like a phone photo" / scan / photocopy
8. Copying practice (抄写) and long texts
9. Change the look afterwards (colour, pen, thickness) without paying again
10. English or mixed-language pages

---

## 1. Math homework from a photo

**Find out** (ask in one message, with defaults): detail level (详细 / 适中 / 简洁), paper (their own exercise book photo?
ruled 8 mm? grid? blank A4?), handwriting (neat student? casual?), pen (blue gel? black?), output (image of their page /
photo-like / PDF). If they only said "帮我做完出手写图片", default to: 适中, ruled 8 mm, a neat Logic handwriting, black
gel pen, plain PNG pages — and say so.

**Prepare**
1. Read every problem from the photo carefully (numbers, signs, exponents). If something is unreadable, ask.
2. Solve. Double-check arithmetic; a wrong answer written beautifully is worse than none.
3. Write the solution like a student (see `writing-math.md`): `1. 解：` on the first line, one step per line, reasons
   in Chinese words (因为/所以/由…得), final 答：, units inside the formula (`$40cm^2$`) or in Chinese. Don't copy the
   problem statement unless the user wants it.
4. Only symbols Logic can write (`inko.py models --symbols`); replace the rest (`writing-math.md` → substitutions).

**Handwriting**: Logic has 8 handwritings per account — `inko.py styles --model logic-1 --sort neat` lists yours; a
neat, medium-weight one (not the heaviest) reads best for homework. Show a contact sheet if the user cares.

**Layout** (`ruled8`, one step per line, a blank line between problems; see `assets/layouts/homework-ruled8.json`):
```json
{"text": "1. 解：$x^2-4x+3=0$\n$(x-1)(x-3)=0$\n所以 $x_1=1$，$x_2=3$\n\n2. 解：…", "paperId": "ruled8",
 "marks": [{"line": [1, 2], "f": {"indent": 3}}], "d": {"indent": 0}}
```
(the mark lines steps up under the text after `1. 解：`). Write it with your file tool as `hw.json`, then:
```bash
python $S/inko.py layout --spec hw.json --preview hw-preview.png          # free: pages, unplaced chars, preview
python $S/inko.py generate --layout hw.json --style <code> --pen-type gel   # quote only (model picked: formulas → logic-1)
python $S/inko.py generate --layout hw.json --style <code> --pen-type gel --yes
```
**If they want it on their own exercise book**: do recipe 2 with this text instead of a ruled8 layout.
**Photo-like**: `photo.py page-1.png -o hw-photo.jpg --preset flat` (clearest, homework-app look) or `--preset desk`.

Judgement calls: students skip trivial algebra at 简洁, never at 详细; long display fractions take two ruled lines — fine;
keep each line short enough to fit (about 30 characters on ruled 8 mm A4 at default size; the preview shows overflow).
Look closely at digits, punctuation right after formulas and units in the result — the usual weak spots.

---

## 2. The user's own lined paper

The user gives text (or asks you to write it) and a photo/scan of their paper, and wants the writing on its lines.

```bash
python $S/paper.py analyze paper.jpg -o paper.json --overlay paper-check.png   # + --paper-size B5 or --line-mm 8 if known
```
- Open `paper-check.png` and show it to the user: lines found (green free / red already written), writing area (blue).
  Confirm: spacing, which line to start on, skip lines (write on every other line?), pen colour.
- If some lines are already written on, pick a handwriting that resembles that writing (`existing_writing` in
  paper.json; look at the photo) — the new lines must look like the same person continued.
- `scale_from` says "ASSUMED 8 mm": ask the line spacing or paper size if stroke thickness matters; for position it
  doesn't (everything is matched to the lines themselves).
- Build the layout from `suggested_layout` in the JSON (it matches character size, line spacing and line length to the
  paper): `{"text": "...", "paperId": "blank", "d": <suggested d>}`. Check `capacity_chars_est` against your text — too
  long means a second sheet (ask for another photo, or it continues on another copy of the same paper).
- Generate (quote → confirm → `--yes`), then:
```bash
python $S/compose.py lines --job inko-output/<run> --paper paper.json -o final.jpg
# colour, height above the line and the label corner are chosen automatically (the JSON says what and why);
# options: --start-line 5  --every 2 (every other line)  --color match|blue|black  --lift 0.1  --x-offset 0.5
```
- Look at `final.jpg` zoomed in on two or three lines. The text should sit on the lines, not cross them.
- A straightened copy of the photo was used for detection; the ink is written back into the ORIGINAL photo, so the
  result looks like their photo with writing on it.

More (photo tips, grid paper, curled pages, fixing offsets): `paper-matching.md`.

---

## 3. Worksheet answers into blanks

```bash
python $S/paper.py blanks worksheet.jpg -o blanks.json --overlay blanks-check.png --paper-size A4
```
- Show the overlay; map blank #n → question with the user.
- Layout on `blank` paper with one box per answer at the blank's `x_mm, y_mm, w_mm, h_mm` (A4 worksheet → same mm;
  another size: see `paper-matching.md`). The skill accepts a shorthand — a `text` inside each box; `inko.py` turns it
  into the API's blocks for you and writes nothing else on the page:
```json
{"paperId": "blank", "boxes": [
  {"id": "Q1", "text": "$x=3$", "x": 32, "y": 64, "w": 80, "h": 18, "form": {"size": 6}},
  {"id": "Q2", "text": "面积是 $12\\pi$ 平方厘米", "x": 32, "y": 121, "w": 120, "h": 18, "form": {"size": 6}}]}
```
- Check with `inko.py layout --spec answers.json --preview answers-preview.png` (`unplaced` must be 0: enlarge a box
  or shrink `form.size` otherwise), generate, then write the page onto the worksheet photo:
```bash
python $S/compose.py page --job inko-output/<run> --sheet blanks.json -o answered.jpg
```
For one or two answers, generating a small page and `compose.py place page-1.png --onto worksheet.jpg --box x,y,w,h`
is quicker than a layout.

---

## 4. Notes / handout → handwritten PDF

- Read the notes file. Clean it for handwriting: drop markdown symbols (#, *, tables → short lines), keep headings as
  their own lines, number lists as "1. ", keep formulas as `$…$` (then model = logic-1).
- Long material: say the page count and price from the quote before generating (20,000 characters / 60 pages per job;
  split bigger ones into chapters).
- Plain mode is quickest: `python $S/inko.py generate --file notes.txt --paper grid --size small` → `inko.pdf` is already
  the PDF (copy it to a good name). But plain mode indents **every line** by two characters, so headings, numbered points
  and short notes all start indented. For a tidy notes page use a layout (`assets/layouts/notes-blank.json`: centred
  title, `"indent": 0`) — on `blank`/`ruled8`, or on squared paper via `paper.py make --kind grid --json` + compose
  (`paper-matching.md` §8). `small` fits 5 mm squares better than `medium`.
- Want it to look photographed page by page: `photo.py` each PNG with `--preset scan` or `flat`, then
  `pdf.py page-*-photo.jpg -o notes.pdf`.

---

## 5. Letter, card, postcard

- lyric-1 (unless formulas). Paper `letter` (red lines, header) or `ruled8`/`blank`; cream paper feels warmer.
- Title centred and larger: `marks` with `{"scale": 1.3, "align": "center"}` on the title's character range.
- Greeting on its own line with no indent (`marks` `{"indent": 0}`), closing phrases right-aligned, name and date in
  right-aligned `own` text boxes near the bottom (see `assets/layouts/letter.json`).
- A name at the end of a letter is fine; reproducing a signature is not (content filter + ethics).
- Card / postcard: write the text in a box of the card's size on blank A4, then put it on a photo of the card with
  `compose.py place page-1.png --onto card.jpg --box x,y,w,h` (the label is re-applied on the card image). Don't crop
  the A4 page down to the card — that would cut the label off.

---

## 6. Essay on 作文纸, 田字格 practice

- `paperId: "compo"` (400-cell grid, 20 × 20, one character per cell) or `"tian"` (田字格, 14 mm cells, for practice).
- Title: `marks` `{"align": "center"}` on the title; paragraphs start with two empty cells automatically (`d.indent` 2).
- Count: 400 cells per page; tell the user how many pages the quote says.
- Grade-school feel: a neat, rounder handwriting (`styles.md`), pencil pen type for younger students.

---

## 7. Photo-like, scanned, photocopied

```bash
python $S/photo.py page-1.png -o photo.jpg --preset desk      # angled phone photo on a wooden desk, lamp light
python $S/photo.py page-1.png -o photo.jpg --preset flat      # from straight above, homework-app style
python $S/photo.py page-1.png -o photo.jpg --preset notebook  # bound notebook page: spine shadow and curl
python $S/photo.py page-1.png -o scan.jpg  --preset scan      # scanner look
python $S/photo.py page-1.png -o copy.jpg  --preset copy      # grey photocopy
```
- Which one: `flat` is the clearest (every word readable — good for homework apps and sending to someone who must read
  it); `desk` looks most like a casual snapshot (far edge slightly soft); `scan` for documents; `notebook` when the page
  should look bound.
- `--background their-desk.jpg` uses the user's own table photo; `--seed N` gives a different variation; `--tilt`,
  `--roll`, `--light warm|daylight|lamp|cloudy`, `--shadow`, `--curl`, `--noise` fine-tune.
- For the most convincing result, compose onto a photo of the user's real paper first (recipe 2) — that photo already has
  real lighting and texture — and skip `photo.py`.
- The label is taken off the paper (the paper under it is rebuilt, ruled lines and grid included) and placed on the
  photo at ≥ 5 % of its shortest side — in the image corner, or just inside the sheet's corner when the corner would
  straddle the sheet's edge. That is intended.

---

## 8. Copying practice (抄写) and long texts

- Split long texts at paragraph boundaries into jobs of ≤ 20,000 characters; one job per chapter keeps rewrites cheap.
- 抄写 N 遍: repeat the text N times in `text` with a blank line between copies (priced per character, so say the total).
- Same handwriting across jobs: pass the same `--style`; for the same "hand on the same day" look also keep `--pen-*`.

---

## 9. Change the look without paying again

```bash
python $S/ink.py restyle page-1.png -o blue.png --color blue --texture ballpoint
python $S/ink.py restyle page-1.png -o bold.png --weight 0.6            # thicker; negative = thinner
python $S/ink.py restyle page-1.png -o light.png --darkness -0.4        # lighter ink
python $S/ink.py restyle page-1.png -o red.png --color "#b3261e" --texture gel
```
Offer 2–3 variants side by side when the user is unsure; they're instant. For a completely different handwriting or
fixing badly written characters, use `inko.py rewrite JOB_ID` (free once per job) or a new generation.

---

## 10. English or mixed-language pages

- Both models write English letters, digits and punctuation; lyric-1 has the widest choice of handwriting.
- Keep straight quotes and ASCII punctuation in English passages; Chinese punctuation in Chinese sentences.
- Very long English words don't break across lines; prefer shorter lines or a smaller `size`.
