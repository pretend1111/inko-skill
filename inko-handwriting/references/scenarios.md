# Standard flat-page scenarios

All deliverables are front-facing two-dimensional PNG pages and/or PDFs. Input pictures can provide questions or
text to transcribe, but are not used as output scenes. Do not add camera perspective, table backgrounds, lighting,
page curl, scan/photocopy effects, or paste handwriting into a real-paper photograph.

## Math homework

Read the questions from text or an input image. Solve them and prepare student-style steps using `writing-math.md`:
解 / 由…得 / 所以 / 答, formulas next to their lead-ins, supported symbols only. Use logic-1, usually a `ruled8`
layout with indent 0. Run the free quote and layout preview, confirm the cost, generate, inspect every answer,
and deliver the flat PNG pages and their PDF. For a worksheet input, number the answers on a separate standard page.

## Homework from attachments: completeness before styling

- Build a page/question checklist before solving. Preserve the input page order and exercise numbering; a locator
  such as `line-5` is a spatial address, not question 5. Check every question against the source once more before delivery.
- If the user requested automatic preparation or supplied defaults, use those defaults and the current compatible
  handwriting. Do not ask again about routine paper, colour or neatness preferences. Ask only when an unreadable
  question, missing condition or essential personal answer prevents a correct result. Do not invent personal facts;
  clearly labelled examples are appropriate only when the task calls for examples.
- Write answers and the requested steps, not repeated printed questions. Check arithmetic, units, tense and spelling.
  Include every page even if one answer needs a layout adjustment; never present an empty draft as a finished task.
- Fill-in-the-blank: write only what is missing. For `____ cats` asking for a number and a measure word, the answer is
  the number and measure word, without `cats`; never abbreviate or turn number words into digits to make it fit. A
  question with separate lines for the question and the answer (Q / A) gets both, each on its own line; a missing
  answer appears once, not again on a spare line.
- 「只写最终答案」: only the results, numbered like the source — no question text, working, checks or 解 / 答.
- A small answer box takes the whole computation on one compact line (`2x=11-3=8，x=4`) rather than one line per `=`;
  don't add numbering, checks or placeholders the user didn't ask for.
- The standalone CLI here delivers numbered answers on flat standard pages. A website adapter may explicitly provide
  owned worksheet backgrounds, measured blank slots and editable regions: in that adapter use the original page,
  bind each answer to its matching slot and preserve its background ID. Do not fabricate unsupported CLI tools.
- If a slot is tight, first check that it belongs to the right question and does not contain duplicated printed text;
  then use the adapter's measured fit/preview. Do not shorten an answer by dropping required words or move it onto
  printed text. Report an unresolved fit instead of claiming the final image was generated.
- Preview and final generation are separate: a draft is not a PNG result. Quote once for the resolved layout, generate
  within the user's granted budget (otherwise confirm the quote), and resume the same job on connection loss.

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
