---
paths:
  - "orchestrator/steps/checker_draft.py"
  - "prompts/checker_draft/**"
  - "tests/test_step_checker_draft.py"
  - "orchestrator/step_order.py"
  - "docs/PROMPTS.md"
  - "tests/test_prompts_doc_sync.py"
---

# Role of each generative step, item 5: `checker_draft`

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
