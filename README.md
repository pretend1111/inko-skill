# Inko handwriting skill

**[简体中文 → README.zh-CN.md](README.zh-CN.md)**

**1.4.0 (2026-10-05): caught up with the website's assistant.** English handwriting retrained; long formulas wrap at
`=` / `+` instead of shrinking, and generated formulas match the text size; Logic can have its own 常用字迹
(`default-style CODE --model logic-1`); a table of what the models can't write and how the agent asks about it; math jobs
never stop to ask about model/handwriting compatibility; new `inko.py delivered` for the API's delivery confirmation;
label-free pages need only the signed agreement.

**1.3.0: standard flat page images and PDFs only.** Camera simulation, scene backgrounds, perspective, paper curl,
scan/photocopy effects and compositing onto photographs have been removed.

Turn notes, letters, essays and math solutions into naturally handwritten **two-dimensional pages** with
[Inko](https://inkotype.com). Choose standard paper, handwriting and pen; adjust layout, spacing and colour;
deliver PNG pages and PDFs. Questions may come from an input photo, but the output is a separate flat answer page.

> “Write these math solutions on ruled paper and give me PNG pages and a PDF.”
> “Turn notes.md into handwritten squared-paper notes in blue ink.”

Logic retains the 1.2.1 symbol update: `\eta` writes n, `\wedge` / `\vee` are supported.
See [math support](inko-handwriting/references/writing-math.md).

## Use it (for people)

1. Create an API key at **https://inkotype.com** → 账户 → API key. A separate key for your AI with a daily spending limit
   is a good idea.
2. Send your AI agent this repository link and the key, e.g.
   *"Install the handwriting skill from https://github.com/pretend1111/inko-skill — my Inko API key is ink_live_…"*
3. Ask for handwriting. The agent always shows you the price before anything is charged
   (¥0.002 per character, i.e. ¥2 per 1000, at least 100 characters per job; quotes and previews are free). It is pure
   pay-as-you-go from your Inko balance — no subscription or membership; accounts registered with a QQ mailbox get ¥3
   to try it out.
4. Optional: in the website's 字迹库, set a **常用字迹** (your default handwriting) and ★ favourites. The agent writes in
   your 常用字迹 unless you ask for another, and picks from your favourites when it has to choose. Say
   *"以后都用这个字迹"* and it saves the new one for you.
5. Optional: **your own handwriting** (专属字迹). Buy a custom-handwriting seat on the website (¥19.9, one-off, never
   expires; one seat = one custom handwriting) and enrol your samples there; the agent can then write in it (Lyric 1).

## For AI agents: how to install this skill

Do these steps in the user's project folder. Don't print the API key back to the user and don't write it into project
files or commits.

1. **Get the files** (a temporary folder is fine):
   ```bash
   git clone --depth 1 https://github.com/pretend1111/inko-skill.git inko-skill-src
   ```
   No git? Download and unzip `https://github.com/pretend1111/inko-skill/archive/refs/heads/main.zip`.
2. **Install** (needs Python 3.9+; installs Pillow + numpy if missing):
   ```bash
   python inko-skill-src/install.py                 # this project (use python3 if python is Python 2)
   python inko-skill-src/install.py --scope user    # or: for all of the user's projects
   ```
   It detects your agent and copies `inko-handwriting/` to `.claude/skills/` (Claude Code) or `.agents/skills/`
   (Codex, Cursor, GitHub Copilot, Gemini CLI, OpenCode, goose, Amp); `--agent claude|agents|both` overrides. It prints
   JSON with `installed_to` and the next steps. You may delete `inko-skill-src` afterwards.
3. **Save the key** the user gave you — piped on stdin so it stays out of the argument list:
   ```bash
   printf '%s' 'ink_live_…' | python <installed_to>/scripts/inko.py auth
   ```
   PowerShell: `'ink_live_…' | python <installed_to>\scripts\inko.py auth`. It is verified and stored in the user's
   config folder (`~/.config/inko/key`, `%APPDATA%\inko\key`); `--project` stores it in `./.inko/key` (git-ignored)
   instead. Alternatively the user can set the environment variable `INKO_API_KEY`.
4. **Check**: `python <installed_to>/scripts/inko.py doctor` → `"ok": true`, key valid, balance shown.
5. **Start**: read `<installed_to>/SKILL.md` now and follow it — some agents only list a new skill after a restart,
   but you don't need to wait. Tell the user it's ready, where the key is stored, and suggest a daily spending limit on
   the key (website → 账户 → API key).

Updating later: run steps 1–2 again (the installed copy is replaced; the key is not touched).

## What's included

- `SKILL.md`, `references/`: flat-page workflow, content, symbols, layouts, styles, API and troubleshooting.
- `inko.py`: API client, quote, preview, generate, download.
- `scene.py`: editable glyph packages, spacing and pen changes, browser editor, flat-page export.
- `ink.py`: ink colour, weight and transparent layers.
- `paper.py make`: standard flat paper backgrounds; no image detection.
- `compose.py drift`: optional two-dimensional line-position edits only.
- `pdf.py`: images to PDF at the selected paper size.

Image processing uses Pillow and numpy locally. Reinstalling replaces the previous skill folder, including removal
of retired scripts, without touching API keys.

## Money, keys, labels

- **Nothing is charged without a confirmed quote**: `inko.py generate` only quotes unless the agent adds `--yes`, and the
  skill tells the agent to get your OK (or stay within a budget you gave) first. Failed/canceled jobs are refunded; one
  free re-write per job.
- **Your key** stays on your machine (user config folder or `./.inko/key`, git-ignored). Revoke it on the website any time.
- **AI labels**: every Inko page carries a visible 「AI生成 · Inko」 label and hidden AIGC metadata as required by Chinese
  law (GB 45438-2025). The scripts keep both on every derived image and PDF and re-apply the visible label at the
  required size; please don't remove them. Pages without the visible label are only available to accounts that
  signed the AI-labelling agreement on the website (the metadata stays).
- Inko refuses IOUs, receipts, contracts, certificates, leave notes and signatures (not charged).

## Development

```bash
python tests/run_tests.py          # offline flat pages, ink, layout, PDF, metadata and feature boundaries
python tests/test_logic_symbols.py # Logic client contract
```

`evals/` covers math from an input image and notes to flat pages/PDF, with an outcome grader.

MIT licensed. Inko API usage requires an account and follows its service terms.
