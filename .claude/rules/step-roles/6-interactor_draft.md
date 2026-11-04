---
paths:
  - "orchestrator/steps/interactor_draft.py"
  - "prompts/interactor_draft/**"
  - "tests/test_step_interactor_draft.py"
  - "orchestrator/step_order.py"
  - "docs/PROMPTS.md"
  - "tests/test_prompts_doc_sync.py"
---

# Role of each generative step, item 6: `interactor_draft`

Common to all steps (revision mode, conflicts, cache, escalation,
suggestions beyond the spec) — in `.claude/rules/steps.md`, models — in
`.claude/rules/models.md`, `llm` commands — in `.claude/rules/llm-commands.md`.

6. **`interactor_draft`** (optional) — runs only if the problem
   is interactive (the spec has an `interactor` section). Input:
   `interactor.behavior_notes` (adaptivity, checking the final answer,
   solution errors, what to write to `tout` for the checker) + from `statement_draft`
   the test format (`formal_input_sketch`, `indexing`, the examples' input — in
   an interactive problem this is the interactor's input, not the solution's) and the protocol
   `interaction_sketch`. Output: `interactor.cpp` (testlib,
   `registerInteraction`). As with the checker, the model implements exactly the
   described behavior; whatever is missing — `uncertain`. The protocol of an
   interactive problem is also given to `statement_draft` (it writes
   `interaction.tex` and the dialogue of each example — what the solution read and
   what it printed, for the "input/output for statements" on Polygon) and
   `solutions_draft`; the rules — `templates/tutorials/interactive.md`,
   which these two steps get only for an interactive problem.
