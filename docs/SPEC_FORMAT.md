# Problem input spec format

The format is YAML. One file per problem, lying in the `authoring/` subdirectory of its
directory: `problems/<problem path>/authoring/spec.yaml` (next to it —
`constraints.yaml`, the result of `constraints_pick`, and the draft copy from
`init --draft`; the results of the other steps — in the problem directory itself).

A problem directory can lie at any depth — intermediate directories only
group problems (`problems/contest/2026/round-1/my-task/`). The spec's `problem_id`
is derived from the directory path: in `problems/` — the name of the innermost directory
(`my-task`), for sample problems in `examples/` — all path components joined with `--`
(`examples/array/prefix-sums/` -> `array--prefix-sums`). The orchestrator checks
the spec's `problem_id` against the path; `problem_id` is also the problem's name on Polygon, so
it is unique among all problems of `problems/` and `examples/`. More — CLAUDE.md,
"Running".

The main principle: **everything that requires coming up with asymptotics or math is a field
the author must fill in**. The model works only with what is explicitly
written in the spec, and never derives a solution's complexity from the legend text
on its own. If a needed field is empty — the corresponding orchestrator step must fail
with an "insufficient input data" error rather than guess.

This doesn't forbid the model from thinking on its own: the author's decisions (asymptotics, algorithm,
the ordered solutions and tests) it implements as given and doesn't substitute, but
beyond them it may propose its own — another or a faster solution,
a test for a mistake that isn't in the spec, a remark about the limits —
as separate files or notes with `kind: suggestion` (see
`docs/PROMPTS.md`). The exceptions are the statement and the comparison rule in the checker: they
are set only by the author.

A draft with all fields (empty, with hints in comments) is created by
`orchestrator <problem> init` — in `problems/<problem path>/authoring/spec.yaml`, from
the `templates/spec_template.yaml` template. The template lists all fields of the
format — this is checked by `tests/test_spec_init.py`, so when editing the
format the template has to be edited along with it.

The spec doesn't have to be filled in from scratch:
- `orchestrator <problem> init --ref <base problem>` — a copy of another
  problem's spec (`problems/<path>` or `examples/<path>`) with a new `problem_id`;
  the base problem is added to the shared `references`;
- `orchestrator <problem> init --draft NAME` — the spec from a free-form draft
  from `drafts/` is written by the model (the base — the template or, together
  with `--ref`, the base problem's spec). Everything from the draft it keeps, and
  fills in what's missing itself — including fields that are otherwise set
  only by the author (`intended_complexity`, `algorithm_hint`,
  `custom_comparison_notes`) — marking each such value with a
  `# PROPOSED, REVIEW ME: ...` or `# SUGGESTION: ...` comment. The steps treat the spec as
  the author's decisions, so the marks must be checked (and removed) before
  `llm generate` — while they are in the spec, `llm generate` and `llm status`
  warn about them (without stopping the run);
  for a problem that already has a spec, the same command revises the spec by
  what changed in the draft, keeping manual edits.

Below are the fields by section. Marked `[required]` / `[optional]` for the MVP
(the generative part without a judging loop).

```yaml
problem_id: unique-slug            # [required] Latin letters; derived from the problem directory path (see below)
language: ru                       # [optional] the main statement language: ru (default) | en, see "Statement languages"
title:
  ru: "Title in Russian"           # [required with language: ru] the initial name on Polygon; afterwards the source is problems/<problem path>/statement/ru/name.txt
  en: "English title"              # [required with language: en, otherwise optional]; the same for statement/en/
references:                        # [optional] shared references — samples for ALL steps, see "References" below
  - "examples/array/prefix-sums"

# ---------------------------------------------------------------------------
# 1. STATEMENT DRAFT — input for the statement_draft step
# ---------------------------------------------------------------------------
# The input/output format from here is also passed to the following steps:
# formal_input_sketch, indexing and the examples' input — to constraints_pick
# (the reading order for the validator), tests_plan and generators_draft; all of this plus
# formal_output_sketch, the examples' output and legend_sketch — to the solution stages.
statement_draft:
  legend_sketch: |                 # [required]
    The author's free text: the plot, the idea, what happens in the problem.
    The model is NOT required to derive a formal model from this text —
    the formal model comes further, as a separate field.
  formal_input_sketch: |           # [required]
    A rough but, where possible, formal description of the input data:
    what is given, in what order, which structures (array/tree/graph/string).
    Not about limits on the numbers (that's below, in constraints) — about the shape of the data.
  formal_output_sketch: |          # [required]
    Likewise — what to print and in which format. State explicitly
    whether the existence/uniqueness of the answer is guaranteed, or a
    separate "-1"/"NO" output etc. is needed.
  indexing: "1-indexed"            # [required] "0-indexed" | "1-indexed" | "irrelevant"
  sample_examples:                 # [required], at least 1
    - input: |
        ...
      output: |                    # [optional]
        ...
        # If not filled in — the author considers the exact value unimportant for
        # the problem's formulation; the field is used as a hint when
        # writing the note (note_hint) and as a sample of the output format for
        # the solution stages, but it isn't translated into Polygon tests. input
        # is mandatory: it is carried over into the real Polygon tests.
      note_hint: "What must be explained in the example's note (not the text itself)"
  known_ambiguities:               # [optional]
    - "E.g.: it's not decided whether a minute counts as a point or a time interval"
  preserve_legend_verbatim: false   # [optional] bool, default: false.
    # false (default) — the standard rule: the introductory fiction
    # paragraph is shortened to 2-3 lines, while the description of the problem model (process,
    # rules, definitions, question) stays in the legend in full; into the
    # input/output format only the description of the data is moved (see tutorials/statement.md).
    # true — for problems where the legend itself is substantive and relies on
    # real facts/history, rather than just wrapping the formal
    # setting: the model must keep the factual density of
    # `legend_sketch` practically as is. Light
    # stylistic editing is allowed (agreement of tenses, links between
    # sentences), but not shortening/retelling facts or removing
    # details. The model doesn't decide on its own "whether this is trivia" — only the author's explicit
    # flag turns this mode on.
  interaction_sketch: |               # [required for an interactive problem — there is an interactor section; otherwise null]
    The protocol of interaction between the solution and the interactor: what the solution gets
    at the start, the format of the solution's queries and of the interactor's answers, the limit on the number of
    queries and what happens if it is exceeded, how the solution reports the final
    answer and what it does after it. The model adds nothing of its own to the protocol:
    whatever is missing — `uncertain`.
    # In an interactive problem formal_input_sketch and the examples' input are the
    # TEST the interactor reads (hidden data: the hidden number
    # etc.), not what the solution sees: the validator and the
    # generators are written from them. An example in the statement is a dialogue between the solution and the interactor:
    # statement_draft builds it itself (from the input and the protocol, as proposed);
    # if the example's output is given — those are the solution's lines, they are carried over
    # as is.
  references: []                     # [optional] references only for statement_draft (see "References")

# ---------------------------------------------------------------------------
# 2. LIMITS — input for the constraints_pick step
# ---------------------------------------------------------------------------
constraints:
  intended_complexity: "O(N log N)"   # [required] — set by the author, the model doesn't derive it itself
  complexity_reasoning: |             # [required]
    The author's brief justification of why these asymptotics (which algorithm
    is meant). Needed so that the model can check the consistency of the
    chosen N/TL with this justification instead of guessing again.
  variables:                         # [required]
    - name: "N"
      min: 1
      max: null                      # if null — the model proposes options and EXPLICITLY marks them as "proposed"
      description: "number of vertices"
    - name: "K"
      min: 1
      max: "2*N"                     # an expression through other variables is allowed
      description: "number of edges"
  special_guarantees:                # [optional] everything that doesn't reduce to min/max of one variable
    - "the graph is connected by definition"
    - "all S_i consist only of lowercase Latin letters"
  test_groups_hint:                  # [optional] — if the author already has an idea about groups
    - name: "small"
      subset_of: null
      max_n: 10
    - name: "n is prime"
      subset_of: "full"
      special_property: "prime"
  test_groups_needed: false          # [optional] bool, default false — the author's explicit request for
    # a test group structure. If false and test_groups_hint isn't set — the
    # constraints_pick step doesn't propose groups at all. If
    # test_groups_hint is set (non-empty) — groups are formed from it regardless
    # of this flag's value (the hint itself is already an explicit request). If
    # test_groups_needed: true and test_groups_hint isn't set — the step proposes
    # a reasonable set itself, as proposed (the current behavior).
  time_limit_seconds: null           # [optional] if null — the model proposes, marking it as a black-box proposal
  memory_limit_mb: 256               # [optional], preferably a multiple of 256 (see tutorials/limits.md)
  uncertain_points:                  # [optional] what the author themselves isn't sure about — a focus for the step
    - "not sure 10^6 for group 3 isn't too generous for checking TL without input optimizations"
  references: []                     # [optional] references only for constraints_pick (see "References")

# ---------------------------------------------------------------------------
# 3. GENERATORS / FREEMARKER — input for the tests_plan step
# ---------------------------------------------------------------------------
# From this section (and solutions.algorithm_hint) the tests_plan step makes the
# base test plan authoring/tests_plan.yaml, and from the plan
# generators_draft writes the generators and test_script_draft — the test-script; these two
# steps don't see the spec.
# The shape of the input data for the generators is taken automatically from
# statement_draft.formal_input_sketch — it doesn't need to and can't be given
# separately in generation: (an extra field, extra="forbid").
generation:
  generator_ideas:                   # [optional] — if the author has already figured out which generators are needed
    - name: "gen_rand"
      params: ["n", "minA", "maxA"]
      purpose: "a uniformly random array"
    - name: "gen_prime"
      params: ["minN", "maxN"]
      purpose: "N is a prime from the range"
  specific_test_ideas:               # [optional] — CONCRETE test scenarios, not generators
    - "N = 1 (the minimal case)"
    - "N = N_max, maximal values"
    - "all array elements are equal"
    # This is not the same as generator_ideas (which tool is needed) and not
    # the same as solutions.known_wrong_approaches (what breaks a
    # particular wrong solution) — it's a separate list of "what must
    # be tested", which the author formulates themselves, without
    # deriving it from the legend/asymptotics.
  script_style: "groups"             # [required] "flat" | "groups" — a simple script (test_script) or by test groups (test_script_groups)
  references:                        # [optional] references only for the tests_plan, generators_draft and test_script_draft steps, added to the shared ones
    - "problems/old-task"
    - "my_examples/gen_tree.cpp"

# ---------------------------------------------------------------------------
# 4. AUTHOR SOLUTIONS (optional, drafts) — input for the solution stages solutions_*
# ---------------------------------------------------------------------------
solutions:
  wanted_verdicts: ["ok", "wa", "tl"]  # [required if the section is on] which drafts are needed: ok, wa, tl, tl_ok, ml, re, il (il — only for an interactive problem)
  languages: ["cpp", "java"]           # [required if the section is on]
  algorithm_hint: |                    # [required if the section is on]
    The author explicitly describes the idea of the main algorithm (not full code, but
    enough so that the asymptotics don't have to be derived again).
  alternative_approaches:              # [optional] — other correct ideas, one ok solution for each
    - "binary search on the answer instead of two pointers"
    # Without a languages prefix — a solution in each language from `languages` where
    # the idea applies; the prefix "(<languages separated by />): " narrows the set.
  known_wrong_approaches:              # [optional] — ideas for wa/tl/tl_ok solutions
    - "wa: off-by-one in the right boundary of the range"
    - "tl (cpp/java): O(N^2) brute force without optimization"
    - "tl_ok: the correct algorithm with unbuffered input/output"
    # Each item is a separate solution of the stage of its verdict (solutions_slow
    # for tl/tl_ok/tle/ml/mle, solutions_wrong for wa/re/il), items aren't
    # skipped or replaced by others; one item — one mistake (two
    # independent mistakes — two items). The prefix "<verdict>: " or
    # "<verdict> (<languages separated by />): " is mandatory (the spec doesn't
    # validate without it): the verdict — one of wa, tl, tl_ok, tle, ml, mle, re, il
    # (`tl_ok` — "TL or OK": a solution borderline in time or a solution
    # without input/output speedup). Without languages they are chosen by the verdict:
    # tl — C++; tl_ok — all languages from `languages` where it makes sense;
    # wa due to overflow — one of C++/Java; other wa — one language where
    # the mistake is more natural. If a verdict from wanted_verdicts has no
    # items, the model proposes one approach itself (as proposed);
    # "verdict × language" combinations aren't filled in. The exception is slowio: with
    # input or output of the order of 10^5 values or more, the model itself adds
    # a "tl_ok" solution with slow input/output in Java and Python from
    # languages, if there's no such item for the language (as proposed).
  references: []                       # [optional] references only for the solution stages (see "References")

# ---------------------------------------------------------------------------
# 5. CHECKER (only with custom_needed: true) — input for the checker_draft step
# ---------------------------------------------------------------------------
checker:
  standard: "ncmp"                   # [optional] the name of a standard checker OR null
  custom_needed: false                # [required] bool
  custom_comparison_notes: null       # [required if custom_needed=true] — how to compare answers,
                                       # precision for real numbers, etc. (see tutorials/checker.md)
  references: []                      # [optional] references only for checker_draft (see "References")

# ---------------------------------------------------------------------------
# 6. INTERACTOR (only for an interactive problem) — input for the interactor_draft step
# ---------------------------------------------------------------------------
# The section is optional: the section is present — the problem is interactive (then
# statement_draft.interaction_sketch is mandatory), absent (or null) — a normal one.
interactor:
  behavior_notes: |                   # [required if the section is on]
    A self-contained description of the interactor's behavior beyond the protocol:
    whether it is adaptive (and if so — how it chooses answers), how it checks the
    solution's final answer, what counts as a solution error (an invalid
    query, exceeding the limit), what the interactor writes to tout — the file
    that the checker from the checker section then compares (e.g.: "tout contains
    the number found by the solution", then checker.standard: wcmp).
  references: []                      # [optional] references only for interactor_draft (see "References")
```

An interactive problem on Polygon: `polygon push` sets the
interactivity flag (`set_constraints`, together with TL/ML), uploads and sets
`interactor.cpp` (`upload_interactor`), the protocol goes into the statement's Interaction
block, and the examples' dialogues — into the "input/output for statements" of the example tests
(`upload_statement`). An interactive problem still has a checker: it compares
what the interactor wrote to tout.

## Statement languages

The statement of each language lies in the directory `problems/<problem path>/statement/<code>/`
— `legend.tex`, `input_format.tex`, `output_format.tex`, `notes.tex`
(+ `interaction.tex`, `tutorial.tex`) and `name.txt`; the examples are shared by all
languages (`statement/examples/`). The code — `ru`, `en`, `kk`, … (the
`POLYGON_LANGUAGES` table in `orchestrator/statement_languages.py`: code -> language name in
Polygon, `ru` -> `russian`); a Polygon language without a code in the table — a directory with the
Polygon name itself. A directory with the Polygon name of a language that has a code
(`statement/russian/`) is an error.

`language` — the main statement language, a code: `ru` (default) or `en`. In it
`statement_draft` and `tutorial_draft` write (even if the author's sketches are in
another language), into `statement/<language>/`; the mandatory name — `title` in
this language. `language` is part of the input hash of these two steps only if it isn't
`ru` — so its introduction didn't make any old problem `stale`.

The other languages exist only at the Polygon level, the model doesn't write them: the directory
is written by the author or downloaded by `polygon pull`; `polygon push` uploads each language
separately (`upload_statement`), and `name.txt` without a file is taken from the spec's `title`
if it has a name in that language. `local check|fix` check all languages;
`<<…>>` quotes — only in `ru`. Changing `language` is just another directory for
generation: the statement in the previous language stays in its directory and on Polygon.

A problem whose statement lies directly in `statement/` (the layout from before languages)
is moved into `statement/<language>/` by the very first command on it, together with the
sync base keys (`migrate_statement_languages`): a push after the move doesn't
upload the statement again.

## Why exactly this way

- **`intended_complexity` and `complexity_reasoning` are mandatory in `constraints`.**
  This is a direct implementation of the decision "the model must not derive
  asymptotics from the text on its own". If the field is empty — the `constraints_pick` step must
  stop with an explicit error rather than substitute something plausible.
- **`max: null` for a variable isn't "forgot", but an explicit signal "propose
  options"**, and in this case the model's response must be marked as
  a proposal, not a fact (see the escalation rule in CLAUDE.md).
- **`uncertain_points` exist at two levels** (the author's — in the spec, and the model's — in
  the output). These are different things: one is what you aren't sure about, the other is what
  the agent must admit it's unsure about, even if you didn't ask.
- **`references`** — an explicit binding to samples (see "References" below),
  so that the steps don't reinvent the file structure but extend what
  the author already uses. Without references the model gets only the rules
  (`templates/`).
- Fields like `time_limit_seconds` and variables without `max` **allow null**
  deliberately — this is the only place where the model is allowed to propose
  a number, but necessarily marked "proposed, not verified".

## References

`references` — a list of paths from the repository root to files or directories
that are attached to the model call as samples. They can be shared (at the spec
root — for all steps) and per-step (in a step's section — only for it):

| Section | Step |
|--------|-----|
| `statement_draft.references` | `statement_draft` |
| `constraints.references` | `constraints_pick` (and the validator) |
| `generation.references` | `tests_plan`, `generators_draft`, `test_script_draft` |
| `solutions.references` | the solution stages `solutions_*` |
| `checker.references` | `checker_draft` |
| `interactor.references` | `interactor_draft` |

Per-step references are added to the shared ones, not replacing them.

**A directory reference** is laid out like `problems/<problem path>/` — the orchestrator's
result: a sample problem `examples/<problem path>/`, a past
problem of the author in `problems/<problem path>/` (including one downloaded by
`polygon pull`), etc. Each step takes only its own
part from the directory (`REFERENCE_DOCUMENTS` in `orchestrator/references.py`):

| Step | Files from the directory |
|-----|-------------------|
| `statement_draft` | `statement/<the sample's main language>/*.tex` (for a sample not yet moved — `statement/*.tex`), except the tutorial `tutorial.tex` (for an interactive problem — and `interaction.tex`) |
| `constraints_pick` | `authoring/constraints.yaml`, `validator.cpp` |
| `tests_plan` | `authoring/constraints.yaml`, `authoring/tests_plan.yaml`, `test_script`, `test_script_groups` |
| `generators_draft` | `authoring/tests_plan.yaml`, `generators/*.cpp` (without the spec — a "plan → generators" pair) |
| `test_script_draft` | `authoring/constraints.yaml`, `authoring/tests_plan.yaml`, `test_script`, `test_script_groups` (without the spec) |
| the solution stages `solutions_*` | `solutions/*` in the languages from `solutions.languages` |
| `checker_draft` | `checker.cpp` |
| `interactor_draft` | `interactor.cpp` |

If the directory has `authoring/spec.yaml` (the spec of that problem, as with sample
problems), it is attached together with them — the model sees a "spec → result" pair. This
also applies to the author's past problems in `problems/<problem path>/`; for a problem
downloaded by `polygon pull` without a spec, only the result is attached.

**A file reference** is attached as is — a shared one to all steps, a per-step one to
its step.

Reference errors are checked before calling the model (`llm generate` stops
the problem, `llm status` shows the step as `blocked`):
- the path doesn't exist;
- a per-step directory reference gave its step no files;
- a shared directory reference gave no files to any step.

A shared directory that has no part for some step (e.g. a problem
with a standard checker has no `checker.cpp`) simply gives that step nothing.

The contents of the attached files are part of the step's prompt hash: editing a
reference (including `problems/<another problem>/`) invalidates the cache of the steps
that get it.

The `generation.base_template_refs` field from the previous version of the format was removed —
it is replaced by `generation.references`.

## What is missing and needs to be clarified with you

1. Whether an explicit field is needed for grouping tests "how many points a group
   is worth" (partial scoring) — in the attached `statement.md` scoring is
   mentioned as an optional statement block. Not included yet, so as not to
   bloat the MVP spec.
