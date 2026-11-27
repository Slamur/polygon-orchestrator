---
paths:
  - "orchestrator/steps/constraints_pick.py"
  - "prompts/constraints_pick/**"
  - "tests/test_step_constraints_pick.py"
  - "orchestrator/steps/limits_from_constraints.py"
  - "prompts/limits_from_constraints/**"
  - "tests/test_step_limits_from_constraints.py"
  - "orchestrator/steps/validator_tests.py"
  - "orchestrator/steps/validator_tests_check.py"
  - "orchestrator/checks/validator_tests.py"
  - "prompts/validator_tests/**"
  - "tests/test_step_validator_tests.py"
  - "tests/test_validator_tests.py"
  - "orchestrator/limits.py"
  - "orchestrator/step_order.py"
  - "docs/PROMPTS.md"
  - "tests/test_prompts_doc_sync.py"
---

# Role of each generative step, item 2: `constraints_pick`, `limits_from_constraints`, `validator_tests`

Common to all steps (revision mode, conflicts, cache, escalation,
suggestions beyond the spec) — in `.claude/rules/steps.md`, models — in
`.claude/rules/models.md`, `llm` commands — in `.claude/rules/llm-commands.md`.

2. **`constraints_pick`** — input: the spec's `constraints` section, first
   of all `intended_complexity` and `complexity_reasoning`, + the input format from
   `statement_draft` (`formal_input_sketch`, `indexing`, the examples' input —
   the source of the reading order for the validator; the legend isn't passed). Output:
   fixed variable ranges, TL/ML, the split into test
   groups (in `constraints.yaml`, including the `validator` section —
   `constants`/`checks`, describing what exactly the validator must check,
   in the `templates/constraints_format.yaml` format) +
   `validator.cpp` (following the `validator.cpp` from the references, if any),
   implementing 1-to-1 the `validator.constants`/`validator.checks` that the step
   itself fixed in `constraints.yaml` — no checks beyond or
   besides those listed there. The `problem_id` in `constraints.yaml` is set by the orchestrator
   from the spec on top of the model's response. Generating `validator.cpp` isn't a separate step,
   but the last sub-step of the same reasoning about limits within one
   model call: the validator is derived entirely from the decisions that
   `constraints_pick` makes anyway, and separate steps/calls would risk
   diverging between `constraints.yaml` and `validator.cpp`. The model **has no
   right** to invent the asymptotics from the legend on its own — if
   `intended_complexity` is empty, the step must end with an
   "insufficient data" error rather than guess; in that case `validator.cpp`
   isn't written either — the whole step response goes to `uncertain`.
   Test groups (`test_groups` of `constraints.yaml`) come from the spec's
   `test_groups_hint` — names, `variables`/`special_property`, `points`,
   `dependencies`, `feedback_policy`, `points_policy` as the author gave them (missing points —
   `uncertain`, like the groups themselves; dependencies the author didn't give are only a
   `suggestion` note); without the hint there are none — the step doesn't invent groups
   (they are set by the author or `init --draft`), and `test_groups_needed: true`
   without the hint stops it as `uncertain` before calling the model
   (`_check_groups_are_set`, no cache entry). With groups the `validator` section also has `groups` —
   the narrower limits of each group's tests — and `validator.cpp` reads with the
   limits of the test's group, `group_index()` from `problem_lib.h` (0 — the
   statement examples, the full limits), as in `examples/number/roots-subtasks`.
   Before writing, the orchestrator checks the groups of the response
   (`check_test_groups_artifact`, a failure — the whole response is a conflict):
   they read as `orchestrator/test_groups.py` reads them (names `1, 2, ...`, unique,
   references to listed groups), every dependency is an earlier group, points are
   given to every group or to none and sum to 100 (or to the total of the author's
   points, when `test_groups_hint` gives points to every group), `validator.groups`
   names only listed groups, each once, and isn't there without `test_groups`; a
   group's `variables` name only the spec's variables, within their numeric bounds.
   After `constraints_pick` comes the mechanical step **`limits_from_constraints`**
   (without the model, `step_order.MECHANICAL_STEPS`): it writes `limits.yaml` —
   the problem's actual TL/ML, which `polygon push` uploads and
   `polygon pull` updates — from the TL/ML of `constraints.yaml`. Its input is only these
   TL/ML, so `limits.yaml` is overwritten (the previous one — in
   `.llm_backup/`) only when they changed: TL/ML fixed on Polygon
   or by hand survive `llm generate`. If `limits.yaml` already exists but the step
   hasn't run yet (the file was downloaded by `polygon pull`), the file is accepted as is;
   `llm generate --step limits_from_constraints --force` derives it again.

   **`validator_tests`** (`orchestrator/steps/validator_tests.py`) — the validator's tests,
   Polygon's "Validator tests" (an input, `VALID`/`INVALID` and, with test groups, optionally
   the group: the validator gets `--group <N>`, a test without a group — no `--group`, the
   full limits). In `STEP_ORDER` — right after `limits_from_constraints`, before the tests the
   validator checks. Input: `validator.cpp` (from `constraints_pick` or the author's; missing
   — an error), `constraints.yaml` without TL/ML, the statement's input format and the
   examples' input. Output: `validator_tests.yaml` (`checks/validator_tests.parse_validator_tests`:
   `tests` — `covers`, `verdict`, `group`, `input`, `expect`). What the model writes
   (`prompts/validator_tests/system.md`): boundary tests of (almost) every reading with bounds
   — `VALID` with values exactly on the bounds (several at once allowed), `INVALID` with
   exactly one value one past a bound, everything before it valid (not for tests whose values
   are bound to each other); with groups — tests without a group for the limits common to all
   groups, tests of a group for its own limits, a `VALID` test of every group; tests of the
   other checks (`ensuref`, ...) where possible. Every test is at most `MAX_LINES` (20) lines
   and `MAX_CHARS` (500) characters: a boundary test that needs more isn't written, and an
   `INVALID` one stops right after the value out of range (`n = maxN + 1` without the `n`
   numbers after it). Why: the tests are read by a person, in the repository and on Polygon.
   An `INVALID` test carries `expect` — a part of the validator's message (`[name=n]`, `a[`,
   the text of an `ensuref`), so a test that fails on something else (the missing data after
   the value) is caught. Before writing (otherwise re-requested or a conflict): the format,
   the size, known groups, no two tests with the same input and group, a `VALID` test of
   every group (`check_validator_tests`).

   **`run_validator_tests`** (`orchestrator/steps/validator_tests_check.py`, mechanical, in
   `GATE_STEPS`) — `checks/validator_tests.run_validator_tests`: the validator on every test.
   A test fails if the verdict differs, an `INVALID` test's message doesn't contain its
   `expect`, or the message is testlib's integer range violation (`equals to X, violates the
   range [a, b]`) with X not `a - 1` or `b + 1`. These failures are the step's: while nothing
   the run reads (the validator, the tests, `constraints.yaml`, the resources) changed,
   `validator_tests` reruns even with a valid cache (`forced_rerun_reason`) and revises with
   them and the author's comment from `llm_reports/run_validator_tests.md` (`--revise-attempts`
   — in the same run). If the test is right and the validator is wrong, the model keeps the
   test with a `validator_bug` note: the author fixes the validator (a failure isn't routed to
   `constraints_pick`). A validator that doesn't build, a file that doesn't parse, a group
   without a `VALID` test — problem errors, not given to the model. On Polygon the tests are
   uploaded by `polygon push` (`upload_validator_tests`, `.claude/rules/polygon.md`).
