# Writing text that looks hand-written

Inko writes exactly the characters you send. Most "this doesn't look real" complaints are really about the text:
typeset structure, markdown leftovers, odd punctuation. Clean the text first.

## From documents to handwriting

- Remove markdown and formatting: `#`, `**`, `>`, `---`, links (keep the words), emoji, footnote markers.
- Headings → their own line (optionally centred/larger with a layout mark). Bullets → `1.` / `(1)` / `·` or `-` at the
  start of the line, or turn them into prose.
- Tables → short lines ("姓名：张三  年龄：20") or a list; handwriting has no table grid.
- Code blocks → only if the user wants them; monospaced alignment won't survive.
- Very long paragraphs are fine; line breaks come from the page width, not from your newlines. Every `\n` starts a new
  paragraph (indented by default), and blank lines are ignored in plain mode — use a layout to keep empty lines.
- Spaces are narrow: a half-width space and a full-width space `　` both come out about a third of a character wide.
  Don't indent or align with spaces — use `indent` (layout `d` or a `line` mark) or `align`. Two half-width spaces
  between date / weekday / weather in a diary heading look natural.
- Every line starts at exactly the same x, which no hand does. Don't fake an uneven left edge with spaces or tiny
  indent marks; run `compose.py drift` on the finished pages (`postprocess.md`).

## Punctuation

- Chinese text: full-width `，。、；：？！“”‘’（）《》……——`. `「」` are not writable → use `“”` (see below).
- Math solutions and derivations are the exception: no full stops at all (`。．.`), commas at most, nothing at line
  ends (`writing-math.md` §1). Letters, essays and diaries keep their `。`.
- English text: ASCII punctuation; straight or curly quotes both work.
- Numbers and units: `3.5 kg`, `25℃` (℃ works; `°` works with logic-1 only — with lyric-1 write `25 度`), `50%` works.

## What can be written

Both models write common simplified Chinese characters, ASCII letters and digits, and the usual Chinese and English
punctuation; `①②③`, `℃` and `←↑→↓` work too. They **can't** write:

| Not writable | Write instead |
|---|---|
| traditional / most dialect characters (`個 國 係 廣 東 佢`) | simplified (`个 国 系 广 东`); dialect words the user must decide |
| letters with tone marks or accents (`ā á ǎ à ü ö ä ë ï`) | ASCII + tone digit: `pàng⁴ jäu⁵ → pang4 jau5`, `nǚ → nv3`, `café → cafe` |
| superscript / subscript digits (`¹ ² ³ ⁴ ₁ ₂`) | ordinary digits (`m2`, tone `4`), or `$m^2$` / `$x_1$` with logic-1 |
| `½ ¾`, Roman numerals `Ⅰ Ⅱ Ⅲ`, `「」 『』` | `1/2`, `I II` or `一、二、`, `“” ‘’` |
| `°` with lyric-1 | `25 度` (logic-1 writes `60°` directly) |

The free quote is the authority: it lists every character it can't write (`unsupported_char` / `unsupported_symbol`,
with paragraph and snippet), and anything it doesn't flag is writable. Nothing is dropped or replaced silently — and you
shouldn't either. When the content (a text to copy, an answer that repeats the question's notation) contains such
characters, ask the user **once** whether to use the writable equivalent, recommendation first, with one or two real
examples from their text. If they already said how ("用数字标调", "改成简体") or told you not to ask about such things,
apply the recommended form directly and mention it in one line.

## Forms that people recognise

- **Letter / 书信**: greeting on its own line without indent (称呼：), body paragraphs indented, 此致 (indented) /
  敬礼 (no indent, next line), name and date right-aligned at the end. Template: `assets/layouts/letter.json`.
- **Notes / 笔记**: short lines, headings, numbered points, keywords can be on their own lines; a date at the top right
  is a nice touch (`own` box).
- **Essay / 作文**: title centred, paragraphs indented two cells on 作文纸 (`compo`), no blank lines between paragraphs.
- **Diary / 日记**: first line date + weekday + weather ("9月27日  星期六  晴") flush left (`{"line": 0, "f": {"indent": 0}}`),
  then indented paragraphs. Check that date and weekday agree (and the year, if written); if the user's text disagrees,
  ask rather than silently "fixing" it.
- **Numbers in headings**: an Arabic digit inside a Chinese heading (第 3 课时) occasionally comes out oddly; if it
  does, 第三课时 reads naturally and writes more reliably.
- **Copying / 抄写**: every character, punctuation mark, capital and line break of the source exactly as given — no
  summarising, tidying or "correcting" (ask if something looks like a typo). Keep the source's line breaks if the
  teacher expects that (layout with `indent: 0`).
- **English**: both models were retrained on English handwriting on 2026-10-04 (word spacing, capitals, x-height,
  baseline); lyric-1 has the most handwriting choice. Words never break across lines and no hyphens are added, so a
  narrow box needs room for the longest word.

## English answers and prose

Preserve the required language, capitalization, apostrophes, decimal points and word spaces. English answers are
ordinary text: do not wrap words in `$...$`, transliterate them, add spaces between letters, or translate them unless
asked. Grammar exercises need the correct inflection (for example `walks`, `went`, `children`, `am`), not a copy of the
bracketed base word. Use ASCII punctuation when preparing new English text and keep deliberate source punctuation.
For longer sentences, inspect line endings and word spacing in the actual layout preview; do not assume that a long
word fits a narrow answer box. Adjust the box or size, never silently delete letters. Inspect the generated PNG as
well as the editable preview, especially `I/l`, punctuation and word boundaries.

## Content limits

- Up to 20,000 characters and 60 pages per job; split longer texts by chapter.
- Not allowed (blocked, not charged): IOUs, receipts, contracts, certificates, leave notes, signatures and other
  documents that could be used as forgeries, and illegal content. Don't circumvent the filter.
- Imitating a specific real person's handwriting needs that person's consent (custom handwriting is made by the user
  from their own samples on the website).
