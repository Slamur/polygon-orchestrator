You write an interactor (C++ with testlib) for an interactive problem being
prepared in Polygon.

You are given mandatory rules:
- interactive.md — what an interactive problem is, the interactor's streams
  (`inf` — the test, `ouf` — the solution's output, `cout` — the answers to the solution, `tout` —
  the file for the checker), the verdicts, distrust of the solution, flushing the buffer.
- problem_lib.h — shared types and helpers (`ll`, `vi`, `vll`, etc.).

Besides the rules, samples may be attached — files of other problems that
the author listed in the spec as references; there may be no samples at all. For this
step these are interactor.cpp — a sample interactor — and spec.yaml of the same problem.

How to use the sample interactor.
The header of an attached document is the file path: files from one directory
belong to one problem, different directories — to different problems, don't mix them
with each other. None of them has anything to do with yours. If a directory
has spec.yaml — it's the spec of that problem, and together with the interactor it gives a
"spec → expected result" pair; if there's no spec.yaml — only the
result is attached.
CARRY OVER from the sample (and follow even if there's no sample) the structure and the techniques
of working with testlib: `registerInteraction`, reading the test from `inf`, reading
queries from `ouf` right within the allowed bounds, answers to `cout` with flushing the
buffer, verdicts via `quitf` with a meaningful message. DON'T CARRY OVER
the sample problem's protocol, test format and interactor behavior — they come
only from your problem's spec.

THE MAIN CONSTRAINT: the sources of WHAT the interactor does are only the spec:
- the test format (`formal_input_sketch`, the indexing, the examples' input) — what and
  in which order the interactor reads from `inf`;
- the interaction protocol (`interaction_sketch`) — what the interactor tells
  the solution at the start, which queries it accepts, how it answers them, the
  query limit, the final answer;
- the interactor's behavior (`interactor.behavior_notes`) — whether it's adaptive,
  how the final answer is checked, what counts as a solution error, what the
  interactor writes to `tout` for the checker.
You don't derive the protocol and the behavior from the problem's plot and don't make them up.
If some case isn't covered by them (it isn't said what to write to `tout`;
whether the interactor is adaptive; what to do with a query beyond the limit or with
an invalid query; how to check the final answer if it may not be
unique) — `status: uncertain` stating exactly what's missing,
not an assumption "by the meaning of the problem". A mistake here is especially costly:
a too lenient interactor accepts a wrong solution, a too strict one or one
violating the protocol breaks all of the problem's tests.

Mandatory rules:
1. Read each value from `ouf` with testlib methods right within the allowed
   bounds (`ouf.readInt(min, max, "name")`, `ouf.readToken(pattern,
   "name")`) — don't write `if`/`quitf` by hand where testlib already can do it.
2. Check the query limit before answering a query beyond the limit.
3. After each answer to the solution flush the buffer (`cout << endl` or
   `cout.flush()`).
4. Read the test from `inf` without validating it again — it's already checked by
   the validator.
5. Write to `tout` exactly what's set in `behavior_notes`.
6. `#include "problem_lib.h"`, as in the project's other C++ artifacts.

If the spec is sufficient and unambiguous — `status: confirmed`. If you made
a decision on implementation details that the spec didn't literally stipulate, but
that don't change the interactor's behavior in essence (e.g. the text of the verdict
messages) — `status: proposed` with a justification in `notes`.

Respond in the JSON format from the contract. `artifacts` — one file `interactor.cpp`.
