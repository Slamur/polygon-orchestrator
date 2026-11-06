---
paths:
  - "orchestrator/cli.py"
  - "orchestrator/cli_commands.py"
  - "orchestrator/pipeline.py"
  - "orchestrator/llm_report.py"
  - "README.md"
  - "tests/test_cli.py"
  - "tests/test_pipeline.py"
  - "tests/test_llm_report.py"
---

# `llm` commands

Addressing problems and groups — in `.claude/rules/cli.md`; the steps that
`llm` runs — in `.claude/rules/steps.md` (shared) and `.claude/rules/step-roles/` (roles).

## Running: `llm` commands

- `orchestrator <problem> llm generate [--step NAME] [--force | --fresh]` —
  a run of the problem (all steps or one particular step); steps whose cache
  is valid are skipped automatically (see "Caching by spec hash" in
  `.claude/rules/steps.md`), the rest revise their current files
  if they exist (see "Revision mode" there).
  `--force` ignores the cache and calls the model again — also in revision
  mode; `--fresh` — again and from scratch, without the current files (implies
  `--force`). Between the solution stages `llm generate` runs the stage's solutions
  locally (`run_*`, without the model, `.claude/rules/step-roles/4-solutions.md`): a
  failed run stops the pipeline, its report `llm_reports/<run>.md` ends with a section
  for the author's comment, and the next run lets the stage revise with the report. To the console — a line per step and, for a step that worked (called
  the model or applied a saved response), the path to its report
  `llm_reports/<step>.md` with a counter of notes by kind. The report
  (`orchestrator/llm_report.py`) is the step's current state, not a history:
  it is rewritten only by a step that worked; it contains the model's notes by kind
  (`uncertain`, `proposed`, `suggestion`, `changed`), what wasn't applied, compile-check
  errors, lines with non-ASCII characters in written source files, new/changed/renamed/marked `.removed`
  files, the `.llm_backup/` directory and diffs of files that existed and
  changed. Full notes are still written to
  `llm_generation.log` too (the run history). Likewise — `llm apply` and
  `init --draft` (`llm_reports/spec_from_draft.md`). If marks from `init --draft`
  remain in the spec (`# PROPOSED, REVIEW ME`,
  `# SUGGESTION`) — a warning with the marked lines is printed before the run
  (`spec.spec_review_marks`); it doesn't stop the run. The same
  warning — in `llm status`.
- `orchestrator <problem> llm tutorial [--ref SOLUTION ... | --no-ref] [--force | --fresh]`
  — the `tutorial_draft` step (see `.claude/rules/step-roles/7-tutorial_draft.md`):
  `statement/<language>/tutorial.tex` from `authoring/draft-tutorial.md`. `--ref` is repeated,
  the value is a file from the problem's `solutions/` (`ok.cpp` or `solutions/ok.cpp`), Tab
  completes it. Cache, report and revision mode — as in `llm generate`; a tutorial
  written by the author or downloaded from Polygon is revised, not overwritten;
  `--fresh` — from scratch. No draft — an error before calling the model; `--ref` with a problem
  group — an argument parsing error. Calls the model — the same restrictions on
  running without an explicit command as for `llm generate`.
- `orchestrator <problem> llm critic [--lens LENS ...]` — the spec critic
  (`.claude/rules/step-roles/8-spec_critic.md`, "`llm critic`") over the spec as it is:
  one round, a call per lens (`--lens` — only the named ones, all by default), the
  findings — in `llm_reports/spec_critic/<lens>.md` (the reports of lenses that
  didn't run are kept), the spec isn't changed.
  No cache, no `--force`: every run calls the model — the same restrictions on
  running without an explicit command as for `llm generate`. No spec or the spec
  isn't valid YAML — an error before calling the model; a failed call or an invalid
  critique — exit code 1.
- `orchestrator <problem> llm status` — without model calls: prints
  a "step → cache hit / stale / not run" table, using the same hashes as
  `llm generate`. Useful to see before a real run what actually
  changed after a spec edit.
  For `stale`, it shows what exactly changed (a spec field, an upstream artifact,
  a prompt or an attached document, the model) — from the hashes of the input parts
  that the cache stores next to the overall hashes.
- `orchestrator <problem> llm apply [--step NAME]` — without model
  calls: applies responses saved by `llm generate` but not
  applied. The model's response (except `uncertain`) is saved to
  `.cache/<step>.response.json` before checking and writing the files and is deleted
  once applied at least partially; if nothing from the response was applied
  (a conflict of a step applied only in full, see "Conflicts" in `.claude/rules/steps.md`) or the step
  failed with a `ValueError` (e.g. an unknown response status — the pipeline
  stops with a step error, not a traceback), the response stays — after
  fixing the orchestrator, `llm apply` applies it again without spending a new
  call.
  A response is applied only if it was received for the step's current input (the same
  hashes as the cache) and in the same mode (revision/from scratch); otherwise — an error
  suggesting `llm generate`. `llm status` shows unapplied
  responses.
- `orchestrator <problem> llm accept [--step NAME]` — without model
  calls: accepts the current artifacts of `stale` steps as up to date
  (overwrites their cache with the current hashes, `accepted: true`). For cases
  where something changed that shouldn't change the result (refactoring
  prompts/templates), or the author has already fixed the files by hand and adjusted the
  spec to them. Steps that are `not run`, `uncertain` and without an artifact on disk aren't accepted.
  A step with conflicts (`conflict`) is accepted only once the author has
  resolved them and deleted the conflicts directory — even if the input didn't change.
  An unapplied saved response of an accepted step is deleted. To the console — a
  line per step with a brief "what changed" (model, spec, prompt) and the path to
  the `llm_reports/accept.md` report, in which every accepted and not
  accepted step has the full list of changed input parts.

## Logs and reports in the problem directory

`llm_generation.log` — the log of `llm generate` (and `init --draft`, `llm critic`), appended to;
each run starts with `===== run started: orchestrator <command> =====`.
`llm_reports/<step>.md` — the report of the step's last run (`spec_from_draft.md`
from `init --draft`, `spec_critic/<lens>.md` from `llm critic` — one per lens, `accept.md`
from `llm accept`).
