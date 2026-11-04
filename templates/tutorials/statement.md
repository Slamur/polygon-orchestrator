## Problem statement

A problem statement consists of 5 important parts:

- Legend

- Input format

- Output format

- Example tests

- Notes on the examples

## General information (for all parts of the statement)

- **Tex** markup is available;

 - For example, use `---` instead of an ordinary or long dash.

- Use **Tex** for formulas, variables and numbers ($ before and after a formula):

  - 10^{15}: $10^{15}$

  - a_i: $a_i$

  - 1 \le N \le 10^9: $1 \le N \le 10^9$;

  - a \ge b: $a \ge b$

  - k \cdot 10^5: $k \cdot 10^5$

- Lists — only with LaTeX environments, not with Markdown markup (LaTeX prints lines starting with `-`, `*` or `1.` as ordinary text):

  - a bulleted list: `\begin{itemize}`, items `\item ...`, `\end{itemize}`;

  - a numbered list: `\begin{enumerate}`, items `\item ...`, `\end{enumerate}`.

- Quotes in Russian text — `<<` and `>>` (in the statement they turn into guillemets «»): `<<YES>>`, not `"YES"`, `“YES”` or `«YES»`.

- Quotes in English text — ``` `` ``` and `''`: ``` ``YES'' ```, not `"YES"` or `“YES”`.

## Legend

- Text without information important for solving the problem

There should be no more than one paragraph of 2-3 lines of such text at the start of the problem.

Usually it's some introductory paragraph to create an "atmosphere".

- All important information must be clearly highlighted in the statement.

Either bold, or a separate paragraph, maybe even caps (if attention really must be drawn).

It's also sometimes useful to repeat the important details at the end of the statement in a formal form.

- Formal descriptions are mandatory

The statement must always contain descriptions from which the simplest program (not necessarily an efficient one) can be written.

**For example**, if some process in time is described, the phrase "at minute T" may mean two things:

1. Minutes are integer points on a line (essentially at XX:00);

1. A minute is the interval from XX:00 to XX:59.

Such things must be spelled out very clearly, and also have an example with an explanation.

- The statement doesn't have to be formal, but don't make a fairy tale that brings nothing to the problem.

Problems often describe situations or processes that are easiest to describe informally.

For example, one can describe movement on a plane from the point of view of a courier, rather than just a material point with strange rules of motion.

At the same time it's preferable to avoid situations where the fictional statement adds nothing to the problem setting but heavily overloads the text.

- Try to reduce the need for "life experience" to a minimum.

Not everyone thinks the same way as the problem's author(s).

A very common example: **indexing**.

In real life 1-indexing is used in most situations.

At the same time many programming languages use 0-indexing.

Inexperienced contestants get a dissonance from this, and the corresponding questions

It's often enough to state in one place that the indices go from 1 to N (or another value) to remove some of the questions in the future.

- Any constants and theorems from the real world must be spelled out clearly

For example, if you ask to print the names of the days of the week, describe all possible variants in the statement.

Preferably in a format that's easy to copy and paste into code.

If describing takes long — there must be a link to the corresponding material on the internet.

- Examples in the statement are optional, but often convenient.

Not to be confused with example tests.

An example often helps to reinforce the formal (or not so formal) description of a process / formula.

It's worth considering using the example from the statement as the first example test.

- Graphical content

Everything here is optional.

The main thing is that images don't overload the main part of the text.

One may consider using images in the notes, stating this explicitly in the statement.

## Input format

- The input description must be as formal as possible and contain all possible guarantees on the data.

- Each block of data is described with a separate line or paragraph.

- A good description format: "line such-and-such contains, separated by <separator>, <such-and-such data> (<limits on the data>) <long dash> <a brief definition of the data>".

- A brief definition of the data may repeat information from the legend — there's nothing wrong with that.

**Examples** of good descriptions:

- "The first line contains two integers separated by a space $N$ and $K$ $(1 \le N \le 3 \cdot 10^5; N - 1 \le K \le 2 \cdot N)$ — the number of intersections and roads in the city".

- "The i-th road is given by two integers and a string $F_i$, $T_i$, $S_i$ $(1 \le F, T \le N, F \neq T; 1 \le |S| \le 10)$ — the numbers of the intersections connected by the road, and also the road's name."

- "It's guaranteed that all $S_i$ consist only of lowercase Latin letters".

- "It's guaranteed that any intersection can be reached from any other by the given roads".

## Output format (of the result)

- In general everything is the same as for the input format.

- If there are some strict checking rules, they should be described separately: "The text is compared taking spaces and line breaks into account".

- For a **real-valued answer** the absolute and/or relative error must always be given: "The answer is accepted if the absolute and/or relative error doesn't exceed $10^{-6}$"

- All variants of the result must be spelled out.

**Example**: if the problem requires constructing an answer and it can be proven that it always exists.

There are two ways to describe this:

1. Write it directly: "It's guaranteed / it can be shown that (under the given limits) the answer always exists / is unique / doesn't exceed X / etc."

1. Offer an output variant for the absence of a result: "If there's no answer, print -1 on a single line."

## Example tests

- There must be at least one example test (if the problem's format allows it).

- Example tests must ALWAYS be the first tests in the judging system.

- Example tests are always made by hand.

- All examples from the statement must be included in the example tests (if they satisfy the problem's limits).

- Example tests are part of the statement, so part of resolving ambiguities / edge cases may be put on them (but not all of it).

- In most cases example tests must be computable "on paper" — don't make arrays of size 20 or strings of length 50 in the examples.

- Example tests with big numbers are possible as the last examples for checking the final solution (but they mustn't replace the basic examples).

## Notes on the examples

- All tests must have at least some explanation.

- At least two tests must have a very detailed explanation with all computations and answer variants.

- The notes mustn't be the only source of some important information (but may duplicate what's said above).

## Statement blocks in Polygon

- Legend;

- Input;

- Output;

- Notes;

- Scoring (optional, needed for problems with partial scores);

- Tutorial (not shown in the statement for obvious reasons).

All blocks are written in LaTeX.
