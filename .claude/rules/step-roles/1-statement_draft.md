---
paths:
  - "orchestrator/steps/statement_draft.py"
  - "prompts/statement_draft/**"
  - "tests/test_step_statement_draft.py"
  - "orchestrator/scoring_table.py"
  - "orchestrator/steps/scoring_table.py"
  - "tests/test_scoring_table.py"
  - "templates/tutorials/scoring.md"
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
(attached only then), and also writes `authoring/scoring.yaml`: the constraints cell of
each subtask of the Scoring block's table (LaTeX in the statement language: the bounds of
the group's `variables` and its `special_property` written out; `null` — only for a group
with neither), for exactly the hint's groups (`parse_scoring_texts`, and a `null` cell of a
group with `variables` or `special_property` is an error — `_check_constrained_cells`;
checked before writing: otherwise the response is requested again or is a conflict). Why
the null check: the table puts the cell as is, and the model once left `null` for groups
with only variable bounds, assuming the orchestrator would add them. As in `examples/number/roots-subtasks`. The rest of the
table the model doesn't write: it follows from the groups and the tests, see
`scoring_table` below. Why: the names, points, necessary subtasks and the last column are
a fixed enumeration of the groups' fields, and the model got their wording wrong (e.g.
"first error; points per test" for a subtask scored per test). A group without points —
`uncertain` from the model. `test_groups_needed: true` without a hint — `uncertain` before the model is called (`_check_groups_are_set`, a
`StepUncertainError` with the field `constraints.test_groups_hint`): the published table
needs the author's groups and points, and no step invents them: groups are set only in
the spec (by the author or by `init --draft`). A spec without groups: `scoring.yaml` and
the generation language's `scoring.tex` (from an earlier run with groups) are marked
`.removed` (`GeneratedArtifacts.stale`); they aren't shown to the model as current files.
Other languages' tables are the author's — `local check` reports them
(`.claude/rules/local-checks.md`).

**`scoring_table`** (`orchestrator/steps/scoring_table.py`, the renderer —
`orchestrator/scoring_table.py`) — a mechanical step right after `run_tests` (in
`SURVIVOR_RECHECK_STEPS` too: new tests change the counts). It assembles
`statement/<language>/scoring.tex` from the groups of `constraints.yaml` (name, points,
dependencies, `feedback_policy`, `points_policy`), `authoring/scoring.yaml` and the number of
tests of each group, which a passed `run_tests` records in its report (`group_counts`):
the total (with the Russian plural), the paragraph on how the subtasks are scored (when a
subtask is tested, then an item for every value of the last column the table uses, in the
order of the rows: a `complete_group` value — its points only if all its tests are passed,
"points per test" — each test scored independently), the header
and a row per group — the name; the points, for an `each_test` group with the points of
one test in parentheses (`30 (2)`); the constraints cell or "No additional constraints";
the necessary subtasks; the last column by `feedback_policy` (`icpc` — "first error", ...)
or, for an `each_test` group, only "points per test". The words — in `ru` and `en` (the
generation languages). Input: the language, those group fields, the text of
`scoring.yaml`, the counts; an unchanged input — a cache hit. A different table on disk
(by hand, `polygon pull`) is replaced with a backup in `.llm_backup/` and a note, as for
`limits_from_constraints`. No groups in the spec or in `constraints.yaml` — skipped; no
`scoring.yaml`, groups it doesn't match, an `each_test` group without counts or with a
count that doesn't divide its points — an error that stops the pipeline.

For an interactive problem the step also gets the interaction protocol — see
`.claude/rules/step-roles/6-interactor_draft.md`.
