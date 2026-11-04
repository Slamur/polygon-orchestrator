You write a custom checker (C++ with testlib) for a problem being prepared in
Polygon — following the read_and_check pattern.

You are given mandatory rules:
- checker.md — the read_and_check concept: the answer
  is read AND validated by one function parameterized by the stream (jury/
  participant); the data is checked in the mode of maximal distrust of
  the participant's stream; the function returns information sufficient for
  comparing, and does NOT check optimality by itself — the comparison
  is done separately, after both streams have been read successfully.
- problem_lib.h — shared types and helpers (`ll`, `vi`, `vll`, etc.).

The checker's structure: `read_answer(InStream&)`, `read_answer_from`
(wraps `read_answer` with a `stream.seekEof()` check — extra data
after the answer → `_pe`, not `_wa`), then `ja`/`pa` are read from `ans`/`ouf`
with this function and compared.

Besides the rules, samples may be attached — files of other problems that
the author listed in the spec as references; there may be no samples at all. For this
step these are checker.cpp — a sample checker with the same structure — and spec.yaml of the
same problem.

How to use the sample checker.
The header of an attached document is the file path: files from one directory
belong to one problem, different directories — to different problems, don't mix them
with each other. None of them has anything to do with yours. If a directory
has spec.yaml — it's the spec of that problem, and together with its other files it
gives a "spec → expected result" pair; if there's no spec.yaml — only
the result is attached.
Match the `checker.custom_comparison_notes` of the sample's spec against its checker
to see how the author's description turns into code, and transform
your problem's description the same way. CARRY OVER from the sample (and follow even if there's no
sample) the structure and the techniques of working with testlib: reading the input from `inf` with testlib methods (`readInt`, `readLong`,
`readInts(n)`); reading the answer with stream methods checking right while reading
(`readToken("A|B", "name")` for a word from a fixed set,
`readInt(min, max, "name")` for a number in a range); checking the answer for
consistency with the input via `stream.quitif(condition, _wa, "message", ...)` with
a meaningful message; a type alias for the answer (`answer_t`); the verdict on the
stream that turned out worse (`quit_answer(..., ans)` gives `_fail` when
the participant is better than the jury). DON'T CARRY OVER the sample problem's answer format, its
check and its comparison rule — they come only from
`custom_comparison_notes`.

THE MAIN CONSTRAINT: the only source of WHAT to compare and HOW is
`checker.custom_comparison_notes` from the spec. You don't derive the answer format and
the comparison rule from the legend/statement yourself — if
`custom_comparison_notes` doesn't cover some case (e.g. it isn't
said whether the comparison is case-sensitive, whether repeats are allowed,
what to do with empty input) — `status: uncertain` stating exactly what's
missing, not an assumption "by the meaning of the problem". A mistake here is especially
costly: a too lenient checker accepts a wrong solution, a too strict one
rejects a correct one, and both cases may go unnoticed for a long time.

Mandatory rules:
1. Use the read_and_check pattern: one function `read_answer(InStream&)`
   for both streams, don't duplicate the reading logic separately for `ans` and `ouf`.
2. The `read_answer_from` wrapper with a `seekEof()` check after reading the answer
   is mandatory, extra data at the end of the stream — `_pe`.
3. If the problem isn't about "optimality" (not about finding the best answer
   among several acceptable ones) — don't use `left_better`/similar
   logic from the sample, compare the results of `read_answer` directly for
   equality and give `_wa` on a mismatch.
4. All bound/format checks on the participant's stream (`ouf`) must go
   through testlib methods (`readInt(min, max, ...)`, etc.), which themselves
   give `_pe`/`_wa` on a violation — don't write `if`/`quitf` by hand where
   testlib already can do it.
5. `#include "problem_lib.h"`, as in the project's other C++ artifacts.

If `custom_comparison_notes` is sufficient and unambiguous — `status:
confirmed`. If you made a decision on implementation details that
`custom_comparison_notes` didn't literally stipulate, but that don't change
the comparison in essence (e.g. a particular way of reading a token) — `status:
proposed` with a justification in `notes`.

Respond in the JSON format from the contract. `artifacts` — one file `checker.cpp`.
