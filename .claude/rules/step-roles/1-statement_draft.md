---
paths:
  - "orchestrator/steps/statement_draft.py"
  - "prompts/statement_draft/**"
  - "tests/test_step_statement_draft.py"
  - "orchestrator/step_order.py"
  - "docs/PROMPTS.md"
  - "tests/test_prompts_doc_sync.py"
---

# Role of each generative step, item 1: `statement_draft`

Common to all steps (revision mode, conflicts, cache, escalation,
suggestions beyond the spec) — in `.claude/rules/steps.md`, models — in
`.claude/rules/models.md`, `llm` commands — in `.claude/rules/llm-commands.md`.

1. **`statement_draft`** — input: the spec's `statement_draft` section and the statement
   language (`language`; in the hash — only if it isn't `ru`). Output:
   a statement draft in this language in `problems/<problem path>/statement/<language>/` — separate `.tex` files for
   the legend/input/output/notes (the set of names is checked before writing to
   disk), following the structure and style rules from
   `templates/tutorials/statement.md`. The input of each example in
   `examples/example_<N>.txt` is a copy from `sample_examples`: it is written by
   the orchestrator, not the model (`GeneratedArtifacts` in
   `orchestrator/steps/base.py`); example files that are no longer in the
   spec are marked `.removed`.

For an interactive problem the step also gets the interaction protocol — see
`.claude/rules/step-roles/6-interactor_draft.md`.
