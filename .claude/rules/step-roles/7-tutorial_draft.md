---
paths:
  - "orchestrator/steps/tutorial_draft.py"
  - "prompts/tutorial_draft/**"
  - "tests/test_step_tutorial_draft.py"
  - "orchestrator/step_order.py"
  - "docs/PROMPTS.md"
  - "tests/test_prompts_doc_sync.py"
---

# Role of each generative step, item 7: `tutorial_draft`

Common to all steps (revision mode, conflicts, cache, escalation,
suggestions beyond the spec) — in `.claude/rules/steps.md`, models — in
`.claude/rules/models.md`, `llm` commands — in `.claude/rules/llm-commands.md`.

7. **`tutorial_draft`** (optional, only via the `llm tutorial` command, outside
   `STEP_ORDER`: `llm generate` doesn't run it, `llm status|apply|accept`
   see it; without a draft — `skipped`) — input: the author's draft
   `authoring/draft-tutorial.md` + `statement/<language>/*.tex` from disk (the notation) +
   the spec's `sample_examples` + the statement language (`language`, as in `statement_draft`) +
   the solutions from `--ref`; the model doesn't see the rest of the spec,
   so that it has nothing to add its own from. Output: `statement/<language>/tutorial.tex` in
   the statement language.
   The draft is the only source of content: the model expands the author's thoughts
   and writes new pieces only where they explicitly ask ("prove", "finish",
   "spell out"); the form of address ("we"/"you") — as in the draft's text. Solutions — only
   to understand the draft: implementation details aren't carried over from them, code — only on
   request. Anything notable beyond the draft — a `suggestion` note, a discrepancy between the draft and
   a solution or an error in the reasoning — an `uncertain` note with `status:
   proposed`: the file is written with the author's version (the step doesn't use `status: uncertain`,
   unlike the escalation rule: the draft is a draft, not a spec).
   The solutions from `--ref` are part of the input: editing them makes the step `stale`. The list
   is remembered: without `--ref` the previous one is taken (from the `input_values` of the cache entry or
   of the saved response), new `--ref`s replace it entirely, `--no-ref` clears it.
