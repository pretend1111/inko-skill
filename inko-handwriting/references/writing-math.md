# Writing math the way students do (Inko Logic)

Contents: 1. What makes a solution look student-written · 2. Detail levels · 3. Chinese conventions · 4. Layout of a
solution page · 5. What Logic can write (symbol table) · 6. Substitutions · 7. LaTeX pitfalls · 8. Example

## 1. Student-written, not typeset

A handwritten solution that looks real is shaped like the student's thinking, not like a textbook:

- One step per line. Short lines. Nobody hand-writes a 60-symbol equation on one line.
- The problem number and `解：` share the first line: `1. 解：…` (not `1.` alone on a line).
- Reasons in words, not symbols-only: 因为… 所以…, 由①得, 代入得, 移项得, 两边同除以 2 得.
- Equalities chained down the page: first line `原式 $= …$`, next lines start with `$=…$`.
- No typographic luxury: no `\left( \right)` sizing, no aligned environments, no boxed answers, no colour.
- Numbering like the assignment: `1.` / `(1)` / `第 3 题`; sub-questions `(1)`, `(2)` on their own lines.
- A final answer line when the question is a word problem or asks for a value: `答：…` (or `∴ …` for pure
  computations at 简洁). Units in the answer, not inside every step.
- Occasional natural shortcuts at the 简洁 level (skip the obvious expansion), never wrong shortcuts.
- Don't restate the whole problem; at most the equation being solved.

## 2. Detail levels (ask the user; default 适中)

| Level | For | What goes in |
|---|---|---|
| 详细 | handing in / showing understanding / someone learning it | every transformation, the reason for it, checks (检验), final 答 |
| 适中 | normal homework | the key transformations and the result; routine arithmetic done in one step |
| 简洁 | answers-only style, quizzes, scratch work | the essential equation(s), the result, very few words |

For proofs: 证明： … 所以 … 得证 / 证毕. For geometry: name the figure in words (在 △ABC 中), cite reasons in
parentheses after the step: `∠1 = ∠2（对顶角相等）`.

## 3. Chinese school conventions

- Solve: `解：` then steps. Word problems: `解：设宽为 $x\,cm$，…，根据题意得 …`, end with `答：…`.
- Equations: `去分母得`, `移项得`, `合并同类项得`, `系数化为 1 得`, `所以 $x=…$`.
- Inequalities: final `所以不等式的解集为 $x>2$` (`>` is beta — fine, just check the result).
- Functions: `令 $f'(x)=0$，得 …`, monotonic intervals `在 $(0,1)$ 上单调递增`.
- Geometry reasons in （） after the statement; ∵ / ∴ are fine as **text** characters (outside `$…$`).
- Multiple answers: `$x_1=2$，$x_2=3$` (Chinese comma between formulas).
- **Units belong inside the formula** (`$x\,cm$`, `$40cm^2$`) or in Chinese (`5 厘米`). Latin letters written as plain
  text right after a formula (`$x$ cm`) are placed like a superscript and look like an exponent.
- **Numbers that matter go inside `$…$`** with logic-1 (`答：5 小时行驶 $375$ 千米`): Logic writes digits inside formulas
  more reliably than digits in the Chinese text around them.
- **Beta symbols** (`>`, `\neq`, `\div`, `\in`, `!`, `\lambda`, `\mu`) are sometimes misdrawn (`>` can look like a 7): use
  as few as the solution needs — explain steps in words (两边同时加 3，得 …) and keep the symbol for the result — and
  check each one in the output.
- English-medium homework: `Solution:` / `Therefore` / `Answer:` — same principles.

## 4. Page layout

- A `ruled8` layout looks most like an exercise book: `{"text": …, "paperId": "ruled8", "d": {"indent": 0}}`. Plain mode
  (`--paper white|grid`) indents every line by two characters, which looks odd for solutions.
- A blank line between problems (`\n\n`) — keeps them visually separate (layouts keep empty lines).
- Continuation lines aligned under the text after `1. 解：` look very student-like: mark those lines with an indent
  measured in character sizes (decimals allowed), e.g. `{"line": [2, 5], "f": {"indent": 3}}` for lines 2–5 under
  `1. 解：`; about `5.4` under `2. 解：原式 `. Check the free preview and adjust.
- `$$…$$` puts an equation centred on its own line (and takes about two ruled lines). Use it for the key equation or a
  long fraction, not for every step — students mostly write inline, left-aligned.
- Formula symbols are written smaller than Chinese characters (about 70 %), inline fractions about half a character
  tall — normal for handwriting. Put an important fraction in `$$…$$` if it must be big.
- Several lines of stacked fractions in a row (`$=\frac{9}{12}+\frac{10}{12}$` …) need more room: in an answer box give
  `"form": {"line": 2.2}` (default 1.7) so the fractions of neighbouring lines don't touch.
- About 30 characters per ruled-8 mm line at the default size; a long formula that doesn't fit is shrunk. The free
  `inko.py layout --preview` shows how it breaks.
- Date in the exercise-book header (`ruled8` / `ruled7`): see the recipe in `layout.md`.
- The user's own exercise book: see `paper-matching.md`.

## 5. What Logic can write inside `$…$`

`python scripts/inko.py models --symbols` lists the live symbols as **glyphs** (stable = well trained, beta = allowed
but sometimes weaker); in LaTeX you write the usual commands: `\times`→×, `\pm`→±, `\leq`/`\le`→≤, `\geq`/`\ge`→≥,
`'`/`\prime`→′, `\to`/`\rightarrow`→→, `\int`→∫, `\sum`→∑, `\infty`→∞, `\Delta`→Δ, `\alpha`→α … Structures and
function names aren't in that list but work. Verified with the quote (2026-09):

- Digits, all lowercase latin letters, uppercase **A B C E F G H I L M N P R S T V X Y** (not D J K O Q U W Z)
- `+ - = < ( ) [ ] \{ \} | / , . '`, `\prime`, `\pm`, `\times`, `\leq \le`, `\geq \ge`, `\infty`, `\to \rightarrow`,
  `\ldots \cdots`, `\quad`, `\,`
- Structures: `\frac \dfrac \tfrac`, `\sqrt{…}`, `\sqrt[3]{…}`, `^`, `_`, `\int`, `\int_a^b`, `\sum_{i=1}^{n}`,
  `\lim_{x \to 0}`, `\text{cm}` / `\mathrm{d}` (latin letters only)
- Functions: `\sin \cos \tan \cot \arcsin \log \log_a \ln \max \min`
- Greek: `\alpha \beta \gamma \theta \pi \sigma \phi \Delta` (beta: `\lambda \mu`)
- Beta: `> \gt`, `\neq \ne`, `\div`, `\in`, `!`

Outside `$…$` (plain text) much more is available in **both** models: Chinese, English, digits, full/half-width
punctuation, and characters like `∵ ∴ ∠ △ ⊥ ∥ ≈ ≠ ≥ ≤ × ÷ ± √ π ∞ ∈ ∪ ∩ ⊂ ⊆ ∅ ∀ ∃ ≡ → ⇒ α β θ λ μ ρ ω Σ Ω ℃ % ‰ ①②③ ……`.
Not writable anywhere: `°`, `² ³` (superscript digits), `½`, `Ⅰ Ⅱ Ⅲ`, `⑴`, `㈠`, `「」`.

Always run the free quote — its `errors` list names the exact symbol and paragraph; it is the source of truth.

## 6. Substitutions

| Wanted | Write instead |
|---|---|
| `90°`, `^\circ` | `$90^{o}$` (a small raised o looks exactly like a handwritten degree sign), or `90 度` |
| `\cdot` | `\times`, or just juxtapose (`2ab`) |
| `\because`, `\therefore` | text `∵` / `∴` outside `$…$`, or 因为 / 所以 |
| `\angle ABC` | text `∠ABC` |
| `\triangle ABC` | text `△ABC`, or `$\Delta ABC$` |
| `\perp`, `\parallel` | text `⊥`, `∥` (e.g. `AB ⊥ CD`) |
| `\approx`, `\sim` | text `≈`; `\sim` → 约 / 相似 in words |
| `\Rightarrow`, `\Leftrightarrow` | text `⇒`, or 所以 / 等价于; `\rightarrow` works in math |
| `\cup \cap \subset \emptyset` | text `∪ ∩ ⊂ ∅` between formulas: `$A$ ∪ $B$` |
| `\vec{a}`, `\overrightarrow{AB}` | 向量 $a$ / 向量 AB in words (arrows over letters aren't supported) |
| `\overline{AB}` | 线段 AB |
| `\bar{x}` (mean), `\bar{v}` (average velocity) | name it: `平均速度 $= \frac{v_0+v}{2}$`, or a subscript `$v_{avg}$` — not `$v = …v…$`, which changes the meaning |
| `\hat{y}` | drop the hat and say it in words |
| `\%` | text `%` (e.g. `50%` outside math) |
| `\binom{n}{k}` | `$C_n^k$` |
| `\omega \rho \delta \epsilon \tau …`, uppercase Greek except Δ | text `ω ρ δ ε` outside math, or rename the variable |
| uppercase D J K O Q U W Z in math | rename (`O` for a circle centre → text `O` outside math: `圆 O`), or lowercase |
| `\sum\limits` | `\sum` |
| Chinese inside `\text{}` | move the Chinese outside the `$…$` |
| matrices, `cases`, `align` | write each row / case / line as its own line |
| `x²`, `m/s²` (unicode superscripts) | `$x^2$`, `$m/s^2$` |
| chemistry `H₂O`, `CO₂` | plain text `H2O`, `CO2` (the letter O can't be written inside math) |

## 7. LaTeX pitfalls

- JSON needs doubled backslashes: `"$\\frac{1}{2}$"`. Write files with a file tool, never a shell heredoc.
- `$` must pair up on every line; a formula can't span lines.
- Keep spaces out of the way: `$x^{2n}$` not `$x^2n$` (the latter is x² n).
- `$$…$$` must be alone on its line.
- Chinese punctuation goes outside the formula: `$x=2$，` not `$x=2，$`.
- A period right after a formula that ends in a superscript (`$40cm^2$。`) is sometimes written up at superscript
  height — look at it in the result; leave the period out if it isn't needed.

## 8. Example (适中)

Problem: 解方程 $\frac{x}{x-1}-\frac{2}{x}=1$.

```
3. 解：方程两边同乘 $x(x-1)$，得
$x^2-2(x-1)=x(x-1)$
$x^2-2x+2=x^2-x$
$-x=-2$
$x=2$
检验：当 $x=2$ 时，$x(x-1)=2\neq 0$
所以 $x=2$ 是原方程的解。
```
(In the JSON `text`, lines are joined with `\n` and every backslash is doubled. With `"d": {"indent": 0}` and
`{"line": [1, 6], "f": {"indent": 3}}` the steps line up under the text after `3. 解：`.)
