# Writing math the way students do (Inko Logic)

Contents: 1. What makes a solution look student-written · 2. Detail levels · 3. Chinese conventions · 4. Layout of a
solution page · 5. What Logic can write (symbol table) · 6. Substitutions · 7. LaTeX pitfalls · 8. Example

## 1. Student-written, not typeset

A handwritten solution that looks real is shaped like the student's thinking, not like a textbook:

- One step per line, and **the lead-in shares the line with its formula**: `所以 $x=4$`, `移项得 $2x=8$`,
  `故 $S=12$`, `由①得 $y=2x$`. A formula never sits alone on the next line after 所以 / 得 / 故 / 即 / 则 / `：` —
  a student keeps writing after the word, and when the formula doesn't fit, simply carries on at the left of the next
  line. Not `…，得` with `$x^2-2x=0$` on the next line, but `…，得 $x^2-2x=0$`.
- **Never `$$…$$`** in student work: the engine puts it on a line of its own (centred in layouts) — textbook look (§4).
- **Long formulas run on.** Don't break a chain into lines yourself or give it a line of its own: with logic-1,
  `inko.py` cuts a long `=` chain into pieces so the engine continues it on the next line like a student (§4).
- **No full stops** in solutions, homework and derivations — no `。`, no `．`, no `.` at the end of a sentence, not even
  after the `答` line. Commas (`，`) between clauses at most; line ends carry no punctuation. Numbering (`1.`) and decimals
  (`$3.5$`) are fine. Ordinary prose (letters, essays, diaries) keeps its `。` as usual.
- The problem number and `解：` share the first line: `1. 解：…` (not `1.` alone on a line).
- Because / therefore the way students write them: `∵ … ∴ …` (text characters, outside `$…$`) — the usual form in
  geometry proofs and step-by-step derivations: `∵ $OD=OE$`, `∴ △ODC ≌ △OEC`. 因为 / 所以 in words only in prose-like
  explanations. Other lead-ins stay in words: 由①得, 代入得, 移项得, 两边同除以 2 得.
- Equalities chained down the page: first line `原式 $= …$`, next lines start with `$=…$`.
- No typographic luxury: no `\left( \right)` sizing, no aligned environments, no boxed answers, no colour.
- Numbering like the assignment: `1.` / `(1)` / `第 3 题`; sub-questions `(1)`, `(2)` on their own lines.
- A final answer line when the question is a word problem or asks for a value: `答：…` (or `∴ …` for pure
  computations at 简洁), without a full stop. Units in the answer, not inside every step.
- Left edges that wander a little: real pages aren't ruler-straight. Run `compose.py drift` on the finished pages
  (`postprocess.md`) — not spaces or tiny indent marks.
- Occasional natural shortcuts at the 简洁 level (skip the obvious expansion), never wrong shortcuts.
- Don't restate the whole problem; at most the equation being solved.

## 2. Detail levels (ask the user; default 适中)

| Level | For | What goes in |
|---|---|---|
| 详细 | handing in / showing understanding / someone learning it | every transformation, the reason for it, checks (检验), final 答 |
| 适中 | normal homework | the key transformations and the result; routine arithmetic done in one step |
| 简洁 | answers-only style, quizzes, scratch work | the essential equation(s), the result, very few words |
| 只写答案 | "只写最终答案" | the results only, numbered like the source: no question, working, checks or 解 / 答 prefixes; plain numbers can use lyric-1 |

For proofs: 证明：∵ … ∴ … 得证 / 证毕. For geometry: name the figure in words (在 △ABC 中), cite reasons in
parentheses after the step: `∴ $AB=AC$（等角对等边）`. Angles: `∠` if `inko.py charset` lists it as writable, otherwise
in words, `角 1 = 角 2（对顶角相等）` (§6). Degrees: type `60°` where the live list allows `°` (see §6).

## 3. Chinese school conventions

- Solve: `解：` then steps. Word problems: `解：设宽为 $x\,cm$，…，根据题意得 $…$`, end with `答：…`.
- Equations: `去分母得`, `移项得`, `合并同类项得`, `系数化为 1 得`, `所以 $x=…$`.
- Inequalities: final `所以不等式的解集为 $x>2$`; `\geq` / `\leq`, not the slanted `\geqslant` / `\leqslant` (§6).
- Functions: `令 $f'(x)=0$，得 …`, monotonic intervals `在 $(0,1)$ 上单调递增`.
- Geometry reasons in （） after the statement; ∵ / ∴ as **text** characters (outside `$…$`), preferred over 因为 / 所以.
- Multiple answers: `$x_1=2$，$x_2=3$` (Chinese comma between formulas).
- **Units belong inside the formula** (`$x\,cm$`, `$40cm^2$`) or in Chinese (`5 厘米`). Latin letters written as plain
  text right after a formula (`$x$ cm`) are placed like a superscript and look like an exponent.
- **Numbers that matter go inside `$…$`** with logic-1 (`答：5 小时行驶 $375$ 千米`): Logic writes digits inside formulas
  more reliably than digits in the Chinese text around them.
- **Weaker symbols** (whatever the quote lists under `warnings`, the fallback ones `\{ \} \lambda \forall \exists`, the
  untested uses mentioned in §5) are sometimes misdrawn: use as few as the solution needs — explain steps in words
  (两边同时加 3，得 …) and keep the symbol for the result — and check each one in the output.
- English-medium homework: `Solution:` / `Therefore` / `Answer:` — same principles.

## 4. Page layout

- A `ruled8` layout looks most like an exercise book: `{"text": …, "paperId": "ruled8", "d": {"indent": 0}}`. Plain mode
  (`--paper white|grid`) indents every line by two characters, which looks odd for solutions.
- A blank line between problems (`\n\n`) — keeps them visually separate (layouts keep empty lines).
- Continuation lines aligned under the text after `1. 解：` look very student-like: mark those lines with an indent
  measured in character sizes (decimals allowed), e.g. `{"line": [2, 5], "f": {"indent": 3}}` for lines 2–5 under
  `1. 解：`; about `5.4` under `2. 解：原式 `. Check the free preview and adjust. An indent moves only the first
  line of a paragraph; when a step wraps, its second line starts at the margin — as a student's does.
- No `$$…$$`: in a layout, display math is always centred on its own line (`align` / `indent` marks can't move it)
  and takes about two ruled lines (warning `mexpand`); plain mode gives it a line of its own too. Every formula goes
  inline, right after its lead-in.
- Formula symbols are written smaller than Chinese characters (about 70 %), inline fractions about half a character
  tall — normal for handwriting. If one fraction must be bigger, scale that formula (matched as written, `$` included)
  with a mark instead of using `$$`: `{"match": "$\\frac{a+b}{2}$", "f": {"scale": 1.3}}`, and check the preview. On
  ruled paper the preview caps its height by the line spacing (it warns `mshrink`, expected there). The generated page
  writes every formula at its natural handwritten size, the same as the text around it (since 2026-10-04; at most about
  2.2 character heights), placed right after the preceding word — so a tall formula can come out a little bigger than
  in the preview. Give stacked fractions room with `line` (below).
- Several lines of stacked fractions in a row (`$=\frac{9}{12}+\frac{10}{12}$` …) need more room: in an answer box give
  `"form": {"line": 2.2}` (default 1.7) so the fractions of neighbouring lines don't touch.
- About 30 characters per ruled-8 mm line at the default size. A formula that doesn't fit the rest of the line moves
  **whole** to the next line (leaving `所以` alone at the end of the line). Since 2026-10-04 one wider than a **whole**
  line is broken by the engine itself at its top level — before `=` `≤` `≥` … first, then before `+` / `−` — and each
  piece keeps the normal size (the operator starts the next piece, as a student writes it). Only a formula with
  nothing top-level to break at (one very wide fraction) is still squeezed (warnings `mshrink` / `mtiny`). A space
  between two formulas (`$a=b$ $=c$`) is an ordinary break point.
- **Automatic split (logic-1).** `inko.py` therefore cuts a formula wider than about 8 characters into as FEW pieces
  as possible and joins them with a space: a statement `LHS = …` is cut once before its first relation, so the left
  side follows the lead-in and the rest starts the next line — `所以 $I(a)=\int_0^\pi \ln(…)dx=\int_0^\pi \ln(…)dt=I(-a)$`
  → `所以 $I(a)$ $=\int…dx=\int…dt=I(-a)$`; a piece still longer than a line is cut again as late as possible (after a
  `,` between statements, else before `=` `≤` … , else before a top-level `+`/`−`). Few cuts on purpose: the space
  between pieces stays visible when they end up on one line. Nothing inside `{}`, `\frac`, `\sqrt`, scripts, brackets or
  environments is ever cut; a formula with nothing top-level to cut at stays whole — if it jumps to the next line and
  leaves 所以 alone, start that step on a line of its own. The output's `formula_splits` lists what was
  split; `--keep-formulas` turns it off. Layouts that position text by raw numbers (`start`/`end` marks, `blocks`, box
  `range`) or with a `match` mark cutting through a long formula are not split — `formula_note` says why; use `line`
  marks and `match` whole formulas. The free `inko.py layout --preview` shows where lines break.
- Style check: the free checks (`quote`, `layout`, `generate` without `--yes`) list `math_style` warnings —
  `display_formula` (`$$` used), `full_stop` (`。．.` in math), `orphan_lead` (a paragraph ending in 所以 / 得 / 故 /
  即 / 则 / `：` / `，` followed by one that starts with a formula). They don't block; fix the text as each `fix` says
  and run the check again. The check runs on every logic-1 text: prose that merely contains a formula (notes, a
  letter) keeps its `。` — ignore `full_stop` there.
- Date in the exercise-book header (`ruled8` / `ruled7`): see the recipe in `layout.md`.

## 5. What Logic can write inside `$…$`

Which LaTeX commands Logic writes changes whenever the model is retrained, so the lists are **not** copied here.
`python scripts/inko.py models --symbols` prints them live (`logic-1` → `symbols`), as LaTeX commands and single
characters (`\alpha`, `\frac`, `\sin`, `+`, `A`):

- `stable` — writable, including the structures `\frac \sqrt \bar \overline \mathbb`. Of these, `substituted` are drawn
  with a real person's glyph, sized, placed and weighted like the chosen hand (each handwriting always gets the same
  one): fine to use, but the shape isn't the writer's own and may look slightly different from the rest of the formula.
  The rest are written by the model in the chosen hand — use them freely.
- `beta` — experimental: may come out wrong; look at them in the result (free rewrite if needed).
- `unsupported` — refused by the quote (`unsupported_symbol`). Substitutions in §6.
- `rewrites` — deliberate notation changes, e.g. `\eta` → `n`: the command is accepted but written as the other symbol.
  Make this visible when preparing content; if the distinction matters (a formula already using n), settle the
  notation with the user before submitting.
- `environments` — the `\begin{…}` names that work.

<!-- inko:live:logic-symbols -->
Run `python scripts/inko.py models --symbols` and read `logic-1` → `symbols` before writing formulas.
<!-- /inko:live -->

Always run the free quote — its `errors` name every symbol it refuses (with paragraph and snippet), its `warnings` the
weaker ones. It is the source of truth: where it disagrees with anything here, follow the quote.

**Unknown commands are refused.** Logic knows only the commands in the live lists. Anything else (an unknown command, or
the `&` of an `align` environment) is refused by the quote (`unsupported_symbol` in `errors`) and by `generate`
(`invalid_text`); without that check the whole formula would silently vanish from the page. Replace it (§6) before
paying. Functions not in the list are written letter by letter as `\operatorname{…}`; a lone point or circle name
(`圆 O`, `点 D`) as a plain-text letter outside `$…$` stays in the writer's own hand.

**Structures**
- `\frac \dfrac \tfrac` (nested, in exponents too), `\sqrt{…}`, `\sqrt[3]{…}`, `^`, `_` (`x_i^2` too)
- Limits: `\sum_{i=1}^{n}`, `\int_a^b`, `\prod_{k=1}^{n}`, `\lim_{x \to 0}`; `\limits` / `\nolimits` are accepted
  and change nothing (`\sum\limits_{i=1}^{n}` is written like `\sum_{i=1}^{n}`)
- `\bar{x}`, `\overline{AB}`: a short bar above
- `\left( \right)` and the like: written as ordinary brackets; `\left.` / `\right.` draw nothing
- Matrices: `matrix pmatrix bmatrix vmatrix Bmatrix smallmatrix`, and `array{lcr}` (columns aligned as given, vertical
  rules not drawn); the brackets are stretched, `\cdots \vdots \ddots` inside are laid out as three dots. Tested up to
  4 columns; more columns, a matrix inside a matrix and small inline matrices are untested — check them.
- Piecewise functions and systems of equations: `cases`, `dcases`, or `\left\{ \begin{array}{l} … \end{array} \right.`
- Ignored (nothing drawn): `\displaystyle`, `\big \Big \bigg \Bigg`, spacing `\, \; \! \quad \qquad`. `\text{}`,
  `\mathrm{}`, `\operatorname{}`, `\mathbf{}`, `\boldsymbol{}` write just their content: `\text{cm}`, `\mathrm{d}x`,
  `\operatorname{rank}` (r a n k, letter by letter)

```
解方程组得 $\begin{cases}x=2\\y=1\end{cases}$
所以 $f(x)=\begin{cases}x^2, & x\geq 0\\-x, & x<0\end{cases}$
$A^{-1}=\begin{pmatrix}1&-2\\0&1\end{pmatrix}$
```
(In JSON every backslash doubles, the row break included: `"\\begin{cases}x=2\\\\y=1\\end{cases}"`.) A matrix or
`cases` block is several rows tall, like stacked fractions (§4): look at it in the preview and in the result.

Outside `$…$` (plain text) the characters each model writes — Greek letters, `°`, superscripts, `∵ ∴ △ ≈ ℃ ①` … —
are in the live `inko.py charset` (`references/writing-text.md`). Prefer `$…$` for Greek letters and math symbols in
logic-1: substituted glyphs and fallbacks are only used in formulas.

## 6. Substitutions

For everything the quote refuses and everything the live lists (§5, `inko.py charset`) mark unsupported or don't list.
A symbol the live lists say is writable needs no substitution, even if it appears below — the models keep learning and
this table only says what to do *if* something is refused. Keep the meaning: when the only
substitute is a different letter or notation the user might not accept, ask first (a teacher's variable names matter).

Same sign, another command — use the one Logic knows:
- `\geqslant \leqslant` (common in Chinese LaTeX) → `\geq \leq`
- `\implies` → `\Rightarrow`; `\varnothing` → `\emptyset`; `\ast` → `*`; `\bot` → `\perp`; `\lnot` → `\neg`;
  `\bullet` (as a product) → `\cdot`
- `\lvert x \rvert` → `|x|`; `\|x\|`, `\Vert x \Vert` → `||x||`; `\langle a, b \rangle` → `<a, b>`
- `\vartheta \varrho \varsigma \varpi` → `\theta \rho \sigma \pi` (same letter, other shape)
- `\lg x`, `\gcd`, `\deg`, `\dim`, `\ker`, `\arg`, `\sup`, `\inf`, any function name not in §5 → `\mathrm{lg}\,x`,
  `\mathrm{gcd}` … (written letter by letter, like `\ln`); `\pmod{n}` → `(\mathrm{mod}\ n)`
- `\mathcal{F}`, `\mathscr{F}`, `\mathfrak{g}` → the plain letter

| Wanted | Write instead |
|---|---|
| `90°`, `^\circ`, `\degree` | plain-text `90°` where `inko.py charset` lists `°` for the model (logic-1 writes it as `$90^{o}$`: a small raised o in the writer's own hand); otherwise `90 度`; `^\circ` works too, with a substituted glyph |
| `\because`, `\therefore` | text `∵` / `∴` outside `$…$` (preferred in proofs and derivations), or 因为 / 所以 |
| `\angle ABC`, `∠1` | while `inko.py charset` lists `∠` as not writable: 角 in words, `角 ABC`, `角 1 = 角 2` |
| `\triangle ABC` | text `△ABC`, or `$\Delta ABC$` |
| `\cong` (全等) | 全等 in words: `△ABC 全等于 △DEF` |
| `\simeq`, `\triangleq` | `=` with 记作 / 定义为 in words |
| `\gg` | turn it round, `\ll` works: `$b \ll a$` for a ≫ b; or 远大于 in words |
| `\supseteq`, `\ni` | turn it round: `$B \subseteq A$`, `$x \in A$` |
| `\subsetneq` (真子集) | in words: `$A$ 是 $B$ 的真子集`; `\subset` only if the user's course writes proper subsets as ⊂ |
| `\leftrightarrow`, `\longrightarrow` | `\Leftrightarrow` / `\iff` when it means 等价, `\rightarrow` for a plain arrow; else words (对应) |
| `\bigcup_{i=1}^{n} A_i`, `\bigcap` | `A_1 \cup A_2 \cup \cdots \cup A_n` (∩ likewise) |
| `\otimes`, `\ominus`, `\bigoplus` | `\times` when the meaning allows (an ordinary product), else words |
| `\lfloor x \rfloor` | `[x]`, as Chinese textbooks write 取整, with the meaning said once: `[x] 表示不超过 x 的最大整数` |
| `\lceil x \rceil` | words: `不小于 x 的最小整数` |
| `A^\top`, `A^\dagger` | `A^T`; `\dagger` in words (共轭转置), or `A^{*}` if the user's course writes it so |
| `\hat{y}` | words (`y 的估计值`, `回归方程为 $y=…$`) — not `\bar{y}` in statistics, where ȳ is the mean; elsewhere `\bar` only if the user agrees |
| `\vec{a}`, `\overrightarrow{AB}` | 向量 $a$ / 向量 $AB$ in words (arrows over letters aren't supported; `\mathbf{a}` writes a plain a) |
| `\dot{x}`, `\ddot{x}` (time derivatives) | `x'`, `x''`, or `\frac{dx}{dt}` |
| `\tilde{x}` | another name agreed with the user, or words |
| `\eta \zeta \kappa \chi \psi \iota \upsilon \Pi \Xi \Upsilon` | another letter only if the user agrees; otherwise name the quantity: `机械效率为 80%` instead of `$\eta=80\%$` |
| `\hookrightarrow`, `\rightleftharpoons`, `\models`, `\vdash`, `\Vdash`, `\aleph`, `\bigvee`, `\bigwedge` | words |
| `\%` | text `%` outside math (`50%`); inside math only if the live lists have `\%` |
| `\binom{n}{k}` | `$C_n^k$` |
| Chinese inside `\text{}` | move the Chinese outside the `$…$` |
| `align`, `aligned`, `gather` | write each line as its own line (`&` outside a matrix isn't supported); matrices and `cases` work (§5) |
| `x²`, `m/s²` (unicode superscripts) | `$x^2$`, `$m/s^2$` |
| chemistry `H₂O`, `CO₂` | logic-1: `$H_2O$`, `$CO_2$` (the O is a substituted glyph); lyric-1: plain text `H2O`, `CO2` |

## 7. LaTeX pitfalls

- JSON needs doubled backslashes: `"$\\frac{1}{2}$"`. Write files with a file tool, never a shell heredoc.
- `$` must pair up on every line; a formula can't span source lines (long `=` chains are cut into pieces that wrap,
  §4).
- Keep spaces out of the way: `$x^{2n}$` not `$x^2n$` (the latter is x² n).
- Chinese punctuation goes outside the formula: `$x=2$，` not `$x=2，$`.
- Punctuation right after a formula that ends in a superscript (`$40cm^2$，`) is sometimes written up at superscript
  height — look at it in the result; leave the comma out if it isn't needed (there are no full stops anyway, §1).

## 8. Example (适中)

Problem: 解方程 $\frac{x}{x-1}-\frac{2}{x}=1$

```
3. 解：方程两边同乘 $x(x-1)$，得 $x^2-2(x-1)=x(x-1)$
$x^2-2x+2=x^2-x$
$-x=-2$
解得 $x=2$
检验：当 $x=2$ 时，$x(x-1)=2\neq 0$
所以 $x=2$ 是原方程的解
```
(In the JSON `text`, lines are joined with `\n` and every backslash is doubled. With `"d": {"indent": 0}` and
`{"line": [1, 5], "f": {"indent": 3}}` the steps line up under the text after `3. 解：`. The first line's formula
follows 得 on the same line (it fits on ruled 8 mm at the default size; a longer `=` chain would be cut by inko.py and
carry on at the start of the next line). No full stop anywhere.
Optionally use `compose.py drift` to adjust line positions on the flat pages.)
