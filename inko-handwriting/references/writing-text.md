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

## Punctuation

- Chinese text: full-width `，。、；：？！“”‘’（）《》……——`. `「」` are not writable → use `“”`.
- English text: ASCII punctuation; straight or curly quotes both work.
- Numbers and units: `3.5 kg`, `25℃` (℃ works; `°` doesn't — write `25 度` or `$25^{o}$` with logic-1), `50%` works.
- Circled numbers `①②③` work; roman numerals `Ⅰ Ⅱ` don't → `I, II` or `一、二、`; superscripts `²` don't → `m2` in
  text or `$m^2$` with logic-1; `½` → `1/2`.
- Run the free quote; it lists anything that can't be written (`unsupported_char`) with its paragraph.

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
- **Copying / 抄写**: keep the source's line breaks if the teacher expects that (layout with `indent: 0`).
- **English**: lyric-1 has the most handwriting choice; keep lines shorter (English words don't break).

## Content limits

- Up to 20,000 characters and 60 pages per job; split longer texts by chapter.
- Not allowed (blocked, not charged): IOUs, receipts, contracts, certificates, leave notes, signatures and other
  documents that could be used as forgeries, and illegal content. Don't circumvent the filter.
- Imitating a specific real person's handwriting needs that person's consent (custom handwriting is made by the user
  from their own samples on the website).
