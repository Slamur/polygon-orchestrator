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

For a problem with test groups (a non-empty `constraints.test_groups_hint` in the spec)
the step also gets the hint (as the author wrote it) and the variables' names and
descriptions — in the hash only then — and the rules `templates/tutorials/scoring.md`
(attached only then), and also writes `statement/<language>/scoring.tex`: the Scoring
block, a subtasks table (points, extra constraints, necessary subtasks, feedback) as
in `examples/number/roots-subtasks`. Its source is the spec's hint, not
`constraints.yaml`: the step runs before `constraints_pick`. A group without points or
an unclear `max_n` variable — `uncertain`. Groups that only `constraints_pick` proposes
(`test_groups_needed` without a hint) get no table from the step: `local check`
reports the missing `scoring.tex` (`.claude/rules/local-checks.md`). A `scoring.tex` of a
problem without groups in the spec isn't the step's file (not revised, not expected).

For an interactive problem the step also gets the interaction protocol — see
`.claude/rules/step-roles/6-interactor_draft.md`.
