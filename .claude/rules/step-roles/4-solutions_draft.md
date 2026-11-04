---
paths:
  - "orchestrator/steps/solutions_draft.py"
  - "prompts/solutions_draft/**"
  - "tests/test_step_solutions_draft.py"
  - "orchestrator/step_order.py"
  - "docs/PROMPTS.md"
  - "tests/test_prompts_doc_sync.py"
---

# Role of each generative step, item 4: `solutions_draft`

Common to all steps (revision mode, conflicts, cache, escalation,
suggestions beyond the spec) — in `.claude/rules/steps.md`, models — in
`.claude/rules/models.md`, `llm` commands — in `.claude/rules/llm-commands.md`.

4. **`solutions_draft`** — input: the spec's `solutions` section +
   `constraints.intended_complexity` + the text of `constraints.yaml` from
   step 2 without TL/ML (the model derives time verdicts from the asymptotics and
   the variables' maximum values, and tuning the TL doesn't invalidate the solutions)
   + the statement from `statement_draft` in its input/output part
   (`formal_input_sketch`, `formal_output_sketch`, `indexing`, examples with
   answers, `legend_sketch` — to understand the terms of `algorithm_hint`, not
   as a source of the author solution's algorithm). Output: solution drafts with
   declared verdicts (ok/wa/tl/tl_ok), which the author must
   double-check and probably rewrite themselves — these aren't final solutions.
   Beyond the solutions described in the spec, the step may propose its own (another or
   a faster `ok`, likely contestant mistakes) — see "Model
   suggestions beyond the spec" in `.claude/rules/steps.md`.

For an interactive problem the step also gets the interaction protocol — see
`.claude/rules/step-roles/6-interactor_draft.md`.
