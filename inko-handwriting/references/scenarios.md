# Standard flat-page scenarios

All deliverables are front-facing two-dimensional PNG pages and/or PDFs. Input pictures can provide questions or
text to transcribe, but are not used as output scenes. Do not add camera perspective, table backgrounds, lighting,
page curl, scan/photocopy effects, or paste handwriting into a real-paper photograph.

## Math homework

Read the questions from text or an input image. Solve them and prepare student-style steps using `writing-math.md`:
解 / 由…得 / 所以 / 答, formulas next to their lead-ins, supported symbols only. Use logic-1, usually a `ruled8`
layout with indent 0. Run the free quote and layout preview, confirm the cost, generate, inspect every answer,
and deliver the flat PNG pages and their PDF. For a worksheet input, number the answers on a separate standard page.

## Notes and handouts

Remove Markdown markers; keep headings and list numbering, and preserve supported math as LaTeX. Plain mode
`--paper grid --size small` gives squared paper. For a centred title or exact placement, use a built-in layout paper
such as blank or ruled8 and `assets/layouts/notes-blank.json`. There is no grid paper in custom layouts.
Deliver the generated PNGs and `inko.pdf`; use `pdf.py` to rebuild a PDF after local page edits.

## Letters and diaries

Use lyric-1 unless formulas are present. Choose white, cream, ruled or letter paper. Put greeting, paragraphs,
closing and date in their intended positions (`writing-text.md`, `assets/layouts/letter.json`). Output the page
itself, not an image of a card or notebook in a scene.

## Essays and writing practice

Use `paperId: compo` for 作文纸 or `tian` for 田字格. Each character belongs in its cell; do not apply line drift.
For repeated copying, repeat the text with clear paragraph breaks and quote the full character count. Split jobs
above 20,000 characters / 60 pages at paragraph boundaries and keep handwriting and pen consistent.

## Local corrections

Prefer `scene.py inspect` then `tighten`, `move`, `scale`, `pen` and `render` for spacing and appearance fixes.
`ink.py restyle` changes a whole page's colour, weight or pen texture; `ink.py extract` creates a transparent
handwriting layer for two-dimensional design. `compose.py drift` is an optional line-position edit only; it never
adds depth or a camera effect. Regenerate only for errors that these flat-page tools cannot fix, within the budget.
