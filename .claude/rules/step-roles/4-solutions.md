---
paths:
  - "orchestrator/steps/solutions.py"
  - "orchestrator/steps/solutions_check.py"
  - "prompts/solutions/**"
  - "tests/test_step_solutions.py"
  - "tests/test_solutions_check.py"
  - "orchestrator/step_order.py"
  - "docs/PROMPTS.md"
  - "tests/test_prompts_doc_sync.py"
---

# Role of each generative step, item 4: the solutions in stages

Common to all steps (revision mode, conflicts, cache, escalation,
suggestions beyond the spec) — in `.claude/rules/steps.md`, models — in
`.claude/rules/models.md`, `llm` commands — in `.claude/rules/llm-commands.md`.

4. **The solutions** — drafts with declared verdicts (ok/wa/tl/tl_ok/ml/re/il),
   which the author must double-check and probably rewrite themselves — these aren't
   final solutions. They are written in stages, one model call per stage over the
   shared prompts `prompts/solutions/` (`step_order.PROMPT_DIRS`), and each stage is
   checked by a local run of its solutions before the next one builds on them
   (`orchestrator/steps/solutions.py`, `orchestrator/steps/solutions_check.py`):

   | Stage | Writes | Builds on | Local run |
   |---|---|---|---|
   | `solutions_main` | the main `ok` solution `ok_<language>_main_<author>` in the first of cpp/java/py among `solutions.languages` | — | `run_main`: the main solution |
   | `solutions_ok` | the other `ok` solutions: the main idea in the other languages, `alternative_approaches` | the main solution | `run_ok`: all `ok` ones against the output of the main one |
   | `solutions_slow` | `tl`, `tl_ok`, `tle`, `ml`, `mle` | all `ok` solutions | `run_slow` |
   | `solutions_wrong` | `wa`, `re`, `il` | all `ok` solutions | `run_wrong` |

   The input of every stage: its part of the `solutions` section
   (`algorithm_hint`; `main` — the main language, `ok` — `languages` and
   `alternative_approaches`, `slow`/`wrong` — `languages` and the `wanted_verdicts` and
   `known_wrong_approaches` items of the stage's verdicts — the item's verdict prefix
   is mandatory, `spec.parse_wrong_approach`) + `constraints.intended_complexity` + the
   text of `constraints.yaml` from step 2 without TL/ML (the model derives time
   verdicts from the asymptotics and the variables' maximum values, and tuning the TL
   doesn't invalidate the solutions) + the statement from `statement_draft` in its
   input/output part (`formal_input_sketch`, `formal_output_sketch`, `indexing`,
   examples with answers, `legend_sketch` — to understand the terms of
   `algorithm_hint`, not as a source of the author solution's algorithm) + the code of
   the solutions it builds on: editing the main solution makes the later stages `stale`.
   A stage isn't needed — `skipped`: `ok` — one language and no `alternative_approaches`;
   `slow`/`wrong` — none of their verdicts in `wanted_verdicts` and the items (`slow`
   also runs for Java/Python in `languages`: a `tl_ok` with slow input/output).
   Beyond the solutions described in the spec, a stage may propose its own (another or
   a faster `ok` in `solutions_ok`, likely contestant mistakes in `slow`/`wrong`) — see
   "Model suggestions beyond the spec" in `.claude/rules/steps.md`.

   A stage owns solutions by their names (`Stage.owned`): `main` — Main correct as
   Polygon chooses it (`upload_solutions._choose_main`: a file with the `main`
   description first), `ok` — the other `ok` ones, `slow`/`wrong` — by the verdict.
   Revision mode shows the model only the stage's files; a response with a file of
   another stage isn't applied (a conflict). Solutions that exist while the stage has
   never run (written by the author or before the stages) are kept as is, like
   `limits.yaml` (`step_order.ADOPTED_STEPS`); `--force` writes them again.

   **The local runs** (`run_*`, mechanical, `step_order.GATE_STEPS`) — `local run
   --full` limited to the solutions the stage owns (`run_ok` — with the main one;
   Main correct always runs: its output is the expected one). A run fails and stops
   the pipeline if a judged solution got a verdict its name doesn't allow or didn't
   build, or the problem itself has errors (the validator rejects a test, the checker
   fails). A wrong solution that no test from the script catches (`survived`, the
   examples don't count) isn't a failure — it's in the report, and `tests_survivors`
   adds tests for it (`.claude/rules/step-roles/3-tests.md`). The run's input is the
   contents of everything it reads, so an unchanged input doesn't run the tests again:
   a run that failed fails again at once. Its report — `llm_reports/<run>.md`, with a
   section for the author's comment for the model at the end; the failures —
   `.cache/<run>.report.json`. While nothing the run read has changed, the next `llm
   generate` reruns the stage (`forced_rerun_reason`, `llm status` shows it `stale`)
   in revision mode with the failures and the comment: the author looks at the report
   first, then either fixes the files by hand (the run's input changed — the report is
   outdated, the model isn't called) or lets the model revise. The report isn't part
   of the stage's input, so after the revision the stage is a cache hit again. Problem
   errors aren't given to the model: the author fixes the tests, the checker or the
   spec. `llm accept` doesn't accept local runs (`llm generate` runs them) and stages
   whose run failed.

For an interactive problem the stages also get the interaction protocol — see
`.claude/rules/step-roles/6-interactor_draft.md`.
