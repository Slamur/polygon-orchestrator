---
paths:
  - "orchestrator/steps/checker_draft.py"
  - "orchestrator/steps/checker_tests.py"
  - "orchestrator/steps/checker_tests_check.py"
  - "orchestrator/checks/checker_tests.py"
  - "prompts/checker_draft/**"
  - "prompts/checker_tests/**"
  - "tests/test_step_checker_draft.py"
  - "tests/test_step_checker_tests.py"
  - "tests/test_checker_tests.py"
  - "orchestrator/step_order.py"
  - "docs/PROMPTS.md"
  - "tests/test_prompts_doc_sync.py"
---

# Role of each generative step, item 5: `checker_draft`, `checker_tests`

Common to all steps (revision mode, conflicts, cache, escalation,
suggestions beyond the spec) — in `.claude/rules/steps.md`, models — in
`.claude/rules/models.md`, `llm` commands — in `.claude/rules/llm-commands.md`.

5. **`checker_draft`** (optional, like the solution stages) — runs
   only if `checker.custom_needed: true` in the spec; if a
   standard checker is used (`checker.standard`, e.g. `ncmp`) — the step doesn't
   run at all, there is nothing to generate. Input: `checker.custom_comparison_notes`
   (a self-contained text description of the answer format and the comparison rule —
   the author writes it themselves, the step derives nothing from the statement/legend
   on its own). Output: `checker.cpp` following the read_and_check pattern (as in the
   `checker.cpp` of the sample problem `examples/array/prefix-sums`) — a `read_answer(InStream&)` function,
   parameterized by the stream (used for `ans` and `ouf` alike),
   which reads AND validates the answer, wrapped in `read_answer_from`
   with a `seekEof()` check (extra data after the answer → `_pe`). The model
   implements exactly the comparison described in
   `custom_comparison_notes` — if it's incomplete (e.g. it isn't stated
   whether the comparison is case-sensitive, or what to do with empty input),
   the step must return `status: uncertain` rather than make up the checker's
   behavior — a mistake here is especially costly: a checker that compares answers
   too leniently or too strictly may accept a wrong solution or
   reject a correct one.

   **`checker_tests`** (`orchestrator/steps/checker_tests.py`) — the custom checker's tests,
   Polygon's "Checker tests" (an input, the participant's output, the jury's answer, the
   expected verdict). Runs only with `checker.custom_needed: true` and a problem that isn't
   interactive (its checker judges the interactor's `tout`); in `STEP_ORDER` — after
   `run_ok`: its local run needs the correct solutions. Input: `checker.cpp` (from
   `checker_draft` or the author's; missing — an error), `custom_comparison_notes`, the
   statement's input/output with the examples, `constraints.yaml` without TL/ML. Output:
   `checker_tests.yaml` (`checks/checker_tests.parse_checker_tests`: `tests` — `covers`,
   `verdict` of `OK`/`WA`/`PE`/`FAIL`, `input`, `output`, `answer`; Polygon's `CRASHED` is
   `FAIL` here). What the model writes (`prompts/checker_tests/system.md`): for every
   `quitif`/`quitf` with a condition and every implicit condition of a reading method (the
   range of `readInt(min, max)`, the pattern of `readToken`) two tests — the participant's
   output breaks it (usually `WA`) with a correct jury's answer, and the jury's answer breaks
   it (`FAIL`) with a correct participant's output; for every comparison of the answers
   ("better"/"worse") where possible two tests — a valid but worse participant's output
   (`WA`) and a valid but worse jury's answer (`FAIL`); `PE` only for an explicit `_pe`
   branch other than the extra data check; the extra data after the answer is never tested
   (a `PE` test whose output is the answer plus more tokens — the response is rejected,
   `check_checker_tests`); no tests of the examples. Why two per branch: a branch tested
   only on the participant's side doesn't show that the jury's wrong answer fails the
   checker instead of passing a wrong participant.

   **`run_checker_tests`** (`orchestrator/steps/checker_tests_check.py`, mechanical, in
   `GATE_STEPS`) — `checks/checker_tests.run_checker_tests`: writes
   `checker_tests.auto.yaml` — per example, the output of Main correct as both the output
   and the answer, and each other output (the example's `output` in the spec, every other
   `ok`/`ma` solution's) that differs from it in tokens, all `OK`; then runs the checker on
   every test of both files. A test of `checker_tests.yaml` fails if the checker gives
   another verdict, the validator rejects its input (with test groups — every group and
   group 0 reject it), or its side that isn't wrong (the answer of `OK`/`WA`/`PE`, the output
   of `OK`/`FAIL`) isn't accepted against the output of Main correct; these failures are the
   step's: while nothing the run reads changed, `checker_tests` reruns even with a valid
   cache (`forced_rerun_reason`) and revises with them and the author's comment from
   `llm_reports/run_checker_tests.md` (`--revise-attempts` — in the same run). If the test is
   right and the checker is wrong, the model keeps the test with a `checker_bug` note.
   A test of the examples the checker rejects, a checker or validator that doesn't build,
   a file that doesn't parse — problem errors, not given to the model: the author fixes
   the checker or the solutions. The run's input — the contents of the checker, the
   validator, `checker_tests.yaml`, the limits, `constraints.yaml`, the resources, the
   examples and the correct solutions, and the spec's checker section and examples' outputs.
