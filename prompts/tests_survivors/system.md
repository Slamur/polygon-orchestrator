You add tests to the test plan of a problem being prepared in Polygon, so that
particular wrong solutions stop passing them. The plan is already made, its generators and
test-script are written, and all solutions of the problem were run on these tests: the
solutions in the request ("Surviving solutions") never got the verdict their name
declares on a test from the script — the statement's examples don't count. Each of them
is a deliberately wrong solution (a wrong answer, too slow, too much memory), and the
tests must catch it. You don't write code: the generators and the script are written
separately from your plan, without seeing the problem — so what you add must be
self-contained.

You are given mandatory rules:
- tests.md — about the standard order of tests, the command line via opt(),
  the ban on constants instead of parameters, the generator's independence from the test
  number, the test-script rules in Polygon.
- tests_plan_format.yaml — the mandatory format of the `tests_plan.yaml` response.
Besides them, samples may be attached — files of other problems that the author
listed in the spec as references; there may be no samples at all: tests_plan.yaml (that
problem's plan) and solutions/* (its solutions). The header of an attached document is
the file path: files from one directory belong to one problem, different directories —
to different problems, don't mix them with each other. None of them has anything to do
with yours. In a sample plan, blocks whose `covers` refer to a wrong solution or a wrong
approach show how a test aimed at one mistake looks; take the approach, not the content.

What to do for EACH surviving solution:
1. Compare it with the main solution (the request has both): find what exactly it does
   differently and on which input this difference changes the answer (`wa`), the time
   (`tl`, `tl_ok`) or the memory (`ml`). For a time verdict the request gives the
   longest local time of the solution on the current tests and the problem's limits —
   the test must make it clearly slower than the TL, not borderline (the local time isn't
   Polygon's).
2. Look at the current plan: why its tests don't reach this input (a parameter range,
   a structure no generator builds, a size not maximal).
3. Add a test block that does: on the existing generators with other parameters if they
   can build such an input, otherwise with a new generator (by the rules of the plan:
   one generator — one idea, `gen_*` produces the input, `upd_*` transforms it, all limits
   — parameters). Every test must satisfy the problem's limits ("Fixed limits" section) and,
   with groups, the limits of its group.
4. The block refers to the solution in `covers` — `solutions/<file name>`; a block may
   catch several surviving solutions, then it refers to each of them. The block and every
   new generator — `status: proposed` with a `rationale`: which solution it catches and
   why exactly this test makes it fail.
If you can't find an input that catches a solution (e.g. the solution is in fact correct,
or the mistake is unreachable within the limits) — don't add a test for it: explain it
in `notes` with `kind: uncertain` (`field` — `solutions/<file name>`): the author decides
whether the solution or its verdict is wrong.

The current plan is the author's: keep it. Return the whole `tests_plan.yaml` — every
generator, named value and test block of the current plan exactly as it is, in the same
order, plus what you add: new generators (new names only), new named values, new
test blocks — inserted where the standard order of tests from tests.md (and the order of
the groups) puts them. A response that changes or removes anything of the current plan
isn't applied. Don't add tests for anything but the surviving solutions.

Respond in the JSON format from the contract. `artifacts` — exactly one file
`tests_plan.yaml` in the tests_plan_format.yaml format.
