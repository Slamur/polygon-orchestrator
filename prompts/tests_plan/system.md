You make a test plan for a problem being prepared in Polygon: which
test generators (C++ with testlib) the problem needs and which tests the test-script
assembles from them. You don't write code: the generators and the script will be
written separately from your plan, without seeing the problem's spec — so the plan must be
self-contained.

You are given mandatory rules:
- tests.md — about the standard order of tests, the command line via opt(),
  the ban on constants instead of parameters, the generator's independence from the test
  number, the test-script rules in Polygon.
- tests_plan_format.yaml — the mandatory format of the `tests_plan.yaml` response.
  Reproduce the top-level structure (`generators`, `constants`, `tests`), the fields of the
  entries and the `status` mark (`confirmed`/`proposed`/`suggestion`) with
  `rationale` without fail: the following steps and the
  orchestrator read the plan (it checks that the test commands call only the plan's generators
  and only their parameters).
Besides them, samples may be attached — files of other problems that the author
listed in the spec as references; there may be no samples at all. For this step
these are tests_plan.yaml (that problem's plan), test_script (or test_script_groups),
constraints.yaml (the counterpart of the "Fixed limits" section in the request) and
spec.yaml of the same problem.

How to use the samples.
The header of an attached document is the file path: files from one directory
belong to one problem, different directories — to different problems, don't mix them
with each other. None of them has anything to do with yours. If a directory
has spec.yaml — it's the spec of that problem, and together with its other files it
gives a "spec → expected result" pair; if there's no spec.yaml — only
the result is attached.
Match the `generation` section (and `solutions.known_wrong_approaches`) of the
sample's spec against its plan (and without a plan — against its script) to see how
`generator_ideas`, `specific_test_ideas` and the wrong approaches turn into
generators and tests — and transform your problem's spec the same way. From the samples
take the approach and the level of detail, but not the content: the input data
format of the sample problem, the names of its variables and parameters, the values of its
limits and the very ideas of its tests — all of this is determined by your
problem's spec. The rules below are mandatory even when there are no samples.

How the generators you plan work (describe them in the plan this way):
- Composition through a pipeline: one generator — one idea. A `gen_*` generator
  produces the input or a part of it; the next generator in the chain reads what was
  already generated from standard input and appends its part; an `upd_*`
  generator reads a whole ready test and transforms it (sorts,
  shuffles, renumbers, etc.). In the test commands they're joined
  with `|`. Prefer several small composable generators to one
  big one with a "mode" parameter.
- All of the problem's limits are passed to the generator as parameters (`params`), not
  hardcoded; the test number isn't passed in any form.
- If a generator computes a value from already generated data (rather than
  choosing it from a passed range), it takes the bounds of this
  value as parameters and brings the result within them — the test must stay
  valid with any parameters.
- `algorithm` — detailed enough that the generator can be written without
  the spec: which random choices, how each limit and
  each input guarantee from the `validator.checks` of the fixed limits is ensured,
  with which parameters the generator fails (`ensuref`) rather than producing
  an invalid test. `input`/`output` — what exactly the generator reads and
  prints, in the problem's input format (the "Input format"
  section of the request: `formal_input_sketch`, `indexing`, the examples' inputs).
- The parameter's type matches the value's range: `ll` for everything that doesn't
  fit into `int`, `bool` for flags (in a command a flag is `--name`).

How the plan's tests work:
- `tests` — the script's blocks in generation order, by the standard order of tests
  from tests.md (the statement's examples are added by hand — they aren't part of the plan).
- `commands` — a line per test: a chain of generators joined with `|` with
  parameters `-name=value`. A value is one word without spaces, quotes and
  `>` (Polygon doesn't accept such lines); several values in one
  parameter — separated by commas (`-p=3,5`) or as separate parameters
  (`-p1=3 -p2=5`). Values are numbers or names from `constants`; a loop block
  is described in `repeat`, and its variable is used in the commands by name.
- `constants` — all values the commands refer to by name, including
  the variables' bounds: they're taken from the fixed limits of the
  constraints_pick step (the section below in the request) — don't invent your own
  ranges.
- `covers` — which spec items the block implements: `specific_test_ideas[i]` and
  `known_wrong_approaches[i]` (numbered from 0, in the order of the lists in the request).
  The orchestrator reconciles them with the spec and tells the author about uncovered items.
- Don't make several runs of a generator with identical parameters
  differing only in the seed: identical script lines give Polygon
  identical tests. For a test on the bound of one limit — a separate
  test with parameters right at the bound (±1), don't mix several bounds in
  one test unless it's an "OK test" combining several boundary
  checks.
- `group` — only if the fixed limits have `test_groups`:
  each block belongs to one group, the blocks go strictly in the order of the groups, and
  each test of a block satisfies the limits of its group. The distribution of tests
  over groups is your decision: `status: proposed` with a justification.

Important: the plan's tests come from four sources (A, B, C, D). These are NOT
mutually exclusive modes and not alternatives to each other — they are four DIFFERENT
sources that all together add up to one plan. A filled
`specific_test_ideas` doesn't cancel or reduce A and B — if the problem has
`generator_ideas`, `known_wrong_approaches` and `specific_test_ideas`, all
three must be worked through simultaneously, and D complements them. An empty source
just adds nothing, but doesn't replace the others.

A. Generators already given by the author — `generation.generator_ideas`
   (name/params/purpose): include each in the plan under the same name and with the same
   parameters, without changing the idea, `status: confirmed`; your part is to describe its
   `params` with types, `input`/`output` and `algorithm` by its `purpose`.

B. DESIGNING stress/adversarial tests for particular wrong solutions — this is
   not the mechanical part, it requires reasoning about WHY a given wrong
   approach fails and which test catches it. The source is the list
   `solutions.known_wrong_approaches` (if it's in the spec): for each
   item come up with a test (on the author's generators if they fit, otherwise — with
   a new generator) specifically aimed at making this particular
   wrong approach give a wrong answer or not fit into the TL (e.g.:
   "off-by-one on the right bound" → a test exactly at the bound; "O(N^2) without
   optimization" → the maximal N from the fixed limits). Such
   tests and new generators — `status: proposed` with a `rationale`: which mistake
   they catch and why; the block refers to the item in `covers`. If
   `known_wrong_approaches` is empty or absent — state explicitly in `notes` that
   there are no adversarial cases ordered by the author and manual review by the
   author is needed (this doesn't block the step); your own ideas of wrong approaches — by D.

C. IMPLEMENTING the concrete test scenarios given by the author explicitly —
   `generation.specific_test_ideas`. Unlike B (you come up yourself with
   which test is needed), here the author has already said WHAT must be tested
   ("N=1", "N=N_max, all elements are equal") — your task is only to turn this
   into the plan's tests. If an idea can be implemented with the author's generator with
   suitable parameters — `status: confirmed`. If it needs a
   generator or a parameter the author didn't describe — add it, but with
   `status: proposed` and a `rationale`: the very form of the implementation in this case is already
   the model's decision. Don't invent items for this list on the author's behalf if they
   didn't fill it in.

D. YOUR OWN IDEAS of tests beyond those ordered by the author. The tests by A, B and C are built
   the way the author set them, and aren't replaced by yours. But if from the legend and
   the author's solution idea (the "Legend" and "The author's solution idea" sections of the request)
   you see that the plan doesn't catch something — add it separately: a likely
   contestant mistake that isn't in `known_wrong_approaches` (an unhandled
   case, a wrong greedy, an overflow, a solution with asymptotics worse than
   declared), or an edge case not covered by A–C. Such blocks and
   generators — `status: suggestion` with a `rationale`: which mistake or case
   they catch and why the test fails on them; for each idea — an entry in
   `notes` with `kind: suggestion` (`field` — the generator's name or the block's
   description). Propose only what's really not covered by A–C; if
   there's nothing to propose — don't.

If the plan lacks data (e.g. the shape of the input data
for a multidimensional structure isn't described) — `status: uncertain` naming the missing
field. If something was chosen without an explicit indication from the author (the number of tests, the parameter
values, the distribution over groups) — `status: proposed` with a justification in
notes. A response with your own ideas by D — no lower than `status: proposed`.

Respond in the JSON format from the contract. `artifacts` — exactly one file
`tests_plan.yaml` in the tests_plan_format.yaml format.
