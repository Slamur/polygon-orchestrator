---
paths:
  - "orchestrator/steps/solutions.py"
  - "orchestrator/steps/solutions_check.py"
  - "prompts/solutions/**"
  - "prompts/solutions_groups/**"
  - "tests/test_step_solutions.py"
  - "tests/test_step_solutions_groups.py"
  - "tests/test_solutions_check.py"
  - "orchestrator/step_order.py"
  - "docs/PROMPTS.md"
  - "tests/test_prompts_doc_sync.py"
---

# Role of each generative step, item 4: the solutions in stages

Common to all steps (revision mode, conflicts, cache, escalation,
suggestions beyond the spec) — in `.claude/rules/steps.md`, models — in
`.claude/rules/models.md`, `llm` commands — in `.claude/rules/llm-commands.md`.

4. **The solutions** — drafts with declared verdicts (ok/wa/tl/tl_ok/tl_ml/ml/re/il),
   which the author must double-check and probably rewrite themselves — these aren't
   final solutions. They are written in stages, one model call per stage over the
   shared prompts `prompts/solutions/` (`step_order.PROMPT_DIRS`; `solutions_groups` — its
   own `prompts/solutions_groups/`), and each stage is
   checked by a local run of its solutions before the next one builds on them
   (`orchestrator/steps/solutions.py`, `orchestrator/steps/solutions_check.py`):

   | Stage | Writes | Builds on | Local run |
   |---|---|---|---|
   | `solutions_main` | the main correct solution `ma_<language>_<idea>_<author>` (Polygon's `MA` tag) in the first of cpp/java/py among `solutions.languages` | — | `run_main`: the main solution |
   | `solutions_ok` | the other `ok` solutions: the main idea in the other languages (`ok_<language>_<idea>_<author>` with the main one's `<idea>`), `alternative_approaches` | the main solution | `run_ok`: all `ok` ones against the output of the main one |
   | `solutions_slow` | `tl`, `tl_ok`, `tl_ml`, `tle`, `ml`, `mle` | all `ok` solutions | `run_slow` |
   | `solutions_wrong` | `wa`, `re`, `il` | all `ok` solutions | `run_wrong` |
| `solutions_groups` | with test groups: solutions specific to particular groups (below) | the main solution and every solution's result per group | `run_groups` |

   The input of every stage: its part of the `solutions` section
   (`algorithm_hint`; `main` — the main language, `ok` — `languages` and
   `alternative_approaches`, `slow`/`wrong` — `languages` and the `wanted_verdicts` and
   `known_wrong_approaches` items of the stage's verdicts — the item's verdict prefix
   is mandatory, `spec.parse_wrong_approach`; with test groups it may name the groups
   the solution passes, `[1-3]` — the verdict stays the one on the whole test set,
   the groups aren't in the file name, and the solution must pass those groups; each
   item goes with its index in the whole list, `known_wrong_approaches[<i>]`, and every
   file written from it carries the comment `spec: solutions.known_wrong_approaches[<i>]`
   — by it the local run compares the passed groups with the item's,
   `.claude/rules/local-checks.md`; inserting an item before others renumbers them and
   makes the stage `stale`) + `constraints.intended_complexity` + the
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
   The model isn't given the ML either: it estimates each slow solution's memory from
   its code at the maximal values against 256 MB, and a solution both too slow and far
   over that is named `tl_ml` ("TL or ML", Polygon's `TM` tag), even from a `tl`/`ml`
   item — locally it's allowed OK, TL or RE (the address space cap turns a memory
   shortage into RE), and missing both is only a warning, as for `tl` and `ml`.
   The input/output of every solution is taken from the language's skeleton in
   `templates/solutions/`, attached even when the references have solutions in the
   language, and over a reference's where they differ (`.claude/rules/materials.md`);
   `ok.<ext>` for the main idea and `alternative_approaches`, a described `ok` skeleton
   (`ok_fastio.py`) only for an additional `proposed` copy of the main idea in stage
   `ok` when the input/output volume is of the order of 10^5 or more.
   Beyond the solutions described in the spec, a stage may propose its own (another or
   a faster `ok` in `solutions_ok`, likely contestant mistakes in `slow`/`wrong`) — see "Model suggestions beyond the spec" in `.claude/rules/steps.md`.

   **`solutions_groups`** — runs only if `constraints.yaml` has a test group with limits of
   its own (`variables` or `special_property`; `solutions.special_groups`), otherwise
   `skipped`. One call for all such groups, the strong model. Its input: the spec part of
   the other stages (`algorithm_hint`, `languages`, the statement's input/output,
   `intended_complexity`, `constraints.yaml` without TL/ML), the main solution, the groups
   to consider, and how every solution of the earlier stages did on each group — `OK`, the
   verdict of the group's first non-OK test or `not run`, without times
   (`solutions_check.GROUP_RESULTS_KEY` in each local run's report, taken only from a run
   whose input is still current). For each group the model says in `notes`
   (`test_groups[<name>]`, `kind: suggestion`) whether it sees a solution specific to the
   group — an algorithm correct under its special property or a slower one fitting its
   bounds, not one a solution of the earlier stages already is — and writes it if so: at
   most one file per group, named by its verdict on the whole test set (`wa`, `re`, `tl`,
   `tle`, `tl_ml`, `ml`, `mle` — `solutions.GROUP_VERDICTS`), with the comments
   `SUGGESTION: ...` and `passes groups: [...]` (every group whose limits it handles). A
   response file without the comment or with another verdict isn't applied. `run_groups`
   judges these solutions like the other runs: a named group not passed — the run fails
   and the stage revises; a group passed but not named — a survivor for `tests_survivors`.

   A stage owns solutions by their names (`Stage.owned`): `main` — Main correct as
   Polygon chooses it (`upload_solutions._choose_main`: the file with the `ma`
   verdict first), `ok` — the other correct ones, `groups` — the solutions with a `passes
   groups` comment not written from a spec item (`solutions.claims_groups`), `slow`/`wrong`
   — the rest by the verdict; `slow`/`wrong` may not write a file with the comment and
   without a spec item marker.
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
   adds tests for it (`.claude/rules/step-roles/3-tests.md`); so does a wrong solution that
   passes a test group its spec item says it fails (the tests go into that group). A
   solution not written from a spec item may name its groups in the comment `passes
   groups: [...]`: it is judged the same way. The
   run's input is the contents of everything it reads and the `known_wrong_approaches`
   items (their groups are compared with the results), so an unchanged input doesn't run the tests again:
   a run that failed fails again at once. Its report — `llm_reports/<run>.md`, with a
   section for the author's comment for the model at the end; the failures —
   `.cache/<run>.report.json`. While nothing the run read has changed, the next `llm
   generate` reruns the stage (`forced_rerun_reason`, `llm status` shows it `stale`)
   in revision mode with the failures and the comment: the author looks at the report
   first, then either fixes the files by hand (the run's input changed — the report is
   outdated, the model isn't called) or lets the model revise. With `llm generate
   --revise-attempts N` (1 by default) the stage revises and the run repeats in the same
   `llm generate`, up to N times, before the pipeline stops. The report isn't part
   of the stage's input, so after the revision the stage is a cache hit again. Problem
   errors aren't given to the model: the author fixes the tests, the checker or the
   spec. `llm accept` doesn't accept local runs (`llm generate` runs them) and stages
   whose run failed.

For an interactive problem the stages also get the interaction protocol — see
`.claude/rules/step-roles/6-interactor_draft.md`.
