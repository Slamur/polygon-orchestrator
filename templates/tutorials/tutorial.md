## The problem's tutorial

The tutorial is written from the author's draft. The draft is the only source of content: the tutorial expands the author's thoughts, it doesn't present the problem's solution anew.

## What's allowed and what isn't

- It's allowed to expand a thought already written in the draft: unfold a point into connected text, write down with formulas what's said in words, finish a reasoning step the author outlined, and translate the draft's notation into the statement's notation.

- The order of thoughts — as in the draft. Paragraphs and sections appear only where the text itself requires it.

- If the author explicitly asks to do something ("prove", "finish", "spell out", "explain in more detail here" and the like; in a Russian draft — «докажи», «допиши», «распиши», «тут объясни подробнее»), this piece is written in full, at the place where the request stands. The request itself doesn't get into the tutorial.

- Without such a request it's not allowed to add what isn't in the draft:

  - proofs and justifications;

  - the asymptotics, time and memory estimates;

  - edge cases and warnings about typical mistakes;

  - other solutions and optimizations;

  - code and implementation details;

  - an introduction, a conclusion and a retelling of the statement.

  Even if it seems important or that the tutorial is incomplete without it, that's no reason to add it.

## The author's solutions

- The solutions are attached only to understand what the author meant in the draft: which algorithm stands behind a short point and in what order the actions go.

- Implementation details that aren't in the draft aren't carried over from a solution into the tutorial. A solution's code is inserted only if the author explicitly asked for it.

## Addressing the reader

- The person is taken from the draft's text: where the author writes "we" (in Russian — «мы») — there "we", where "you" («вы») — there "you". The forms of address aren't unified if they differ in the draft.

- If the person isn't visible at this place of the draft, choose "we" or "you" by the neighboring text of the draft. The author's impersonal constructions may be kept as is.

## Formatting

- **Tex** markup is available. Use `---` instead of an ordinary or long dash.

- Formulas, variables and numbers — in **Tex** (`$` before and after a formula): `$a_i$`, `$10^{15}$`, `$1 \le N \le 10^9$`, `$k \cdot 10^5$`.

- The draft may be written in Markdown, but the tutorial is LaTeX. Markdown markup (`**`, `#`, backticks) doesn't get into the tutorial.

- Lists — only with LaTeX environments, not with Markdown markup (LaTeX prints lines starting with `-`, `*` or `1.` as ordinary text):

  - a bulleted list: `\begin{itemize}`, items `\item ...`, `\end{itemize}`;

  - a numbered list: `\begin{enumerate}`, items `\item ...`, `\end{enumerate}`.

- Quotes in Russian text — `<<` and `>>`: `<<YES>>`, not `"YES"`, `“YES”` or `«YES»`.

- Quotes in English text — ``` `` ``` and `''`: ``` ``YES'' ```, not `"YES"` or `“YES”`.