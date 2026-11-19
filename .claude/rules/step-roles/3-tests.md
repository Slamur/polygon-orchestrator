---
paths:
  - "orchestrator/steps/tests_plan.py"
  - "prompts/tests_plan/**"
  - "tests/test_step_tests_plan.py"
  - "orchestrator/steps/generators_draft.py"
  - "prompts/generators_draft/**"
  - "tests/test_step_generators_draft.py"
  - "orchestrator/steps/test_script_draft.py"
  - "orchestrator/steps/tests_check.py"
  - "tests/test_tests_check.py"
  - "orchestrator/steps/tests_survivors.py"
  - "prompts/tests_survivors/**"
  - "tests/test_step_tests_survivors.py"
  - "prompts/test_script_draft/**"
  - "tests/test_step_test_script_draft.py"
  - "templates/tests_plan_format.yaml"
  - "templates/tests_design_format.yaml"
  - "orchestrator/steps/tests_design.py"
  - "tests/test_tests_design.py"
  - "orchestrator/step_order.py"
  - "docs/PROMPTS.md"
  - "tests/test_prompts_doc_sync.py"
---

# Role of each generative step, item 3: `tests_plan`, `generators_draft`, `test_script_draft`, `tests_survivors`

Common to all steps (revision mode, conflicts, cache, escalation,
suggestions beyond the spec) — in `.claude/rules/steps.md`, models — in
`.claude/rules/models.md`, `llm` commands — in `.claude/rules/llm-commands.md`.

3. **Test preparation** — three steps: the substantive part separate from
   the voluminous one, so that no model call does both at once.
   - **`tests_plan`** — input: the spec's `generation` section + the input format from
     `statement_draft` (`formal_input_sketch`, `indexing`, the examples' input —
     there is no separate `generation.input_shape` field in the spec, so as not to
     duplicate the input format description) + `legend_sketch` + the fixed
     limits from step 2 without TL/ML + (if the `solutions` section is set in the spec)
     `solutions.algorithm_hint`.
     The model's output: the test design `authoring/tests_design.yaml` in the
     `templates/tests_design_format.yaml` format — generators (name, parameters with
     types, what they read and print, the algorithm) and ideas: for each spec item
     (`id: specific_test_ideas[i]`) or the model's own idea (`own[k]`) — command
     templates and the groups they go into (`all` or a list; one idea may take several
     entries on disjoint groups, e.g. when a group's `special_property` needs another
     generator). A value in a template is a number, a named value or a formula over the
     limits of `constraints.yaml` — `{n.max}`, `{n.min}`, `{n.lo}` (one more than the
     largest `n` of the groups the group depends on, transitively) and integer
     arithmetic over them. Why: picking every group's numbers and checking them against
     its limits made one plan call heavy; this part is mechanical.
     From the design the orchestrator builds the test plan `authoring/tests_plan.yaml`
     (`tests_design.plan_from_design`, the `derive_artifacts` of
     `run_generative_step`) in the `templates/tests_plan_format.yaml` format: the
     design's generators as they are; a block per idea entry and group, in the order of
     the groups, within a group — of the ideas (`covers` — the idea's id for the spec's
     items); every formula — a named value of the plan (`n_max_g3`, `n_lo_g2`,
     `n_max_div_2`; a group's bound equal to the problem's one — the problem's name
     `n_max`), so the script keeps referring to values by name. The blocks of the current
     plan for wrong solutions (`covers` — `solutions/<name>` or
     `known_wrong_approaches[i]`) are carried over with the generators and named values
     they use, at the end of their group. The plan is rebuilt on every run of the step
     (also when the model returns the design unchanged — e.g. the group limits changed),
     so the author edits the design, not the plan. The plan is self-contained:
     the next two steps don't see the spec. The commands don't carry `-group`, the
     script adds it. Before writing, the design and the plan are checked
     (`parse_tests_design`, `parse_tests_plan`): commands call only the generators,
     only with their parameters and with all of them except `bool` flags (the generators
     read every parameter with `opt<T>`, testlib fails on a missing one), formulas refer to known variables and compute for every
     group (`{n.lo}` of a group whose dependencies already take `n` up to its max — an
     error), groups exist (not `0`), entries of one idea take disjoint groups, every group
     gets tests; anything wrong — the response is a conflict. The orchestrator reports
     with `uncovered` notes the `specific_test_ideas` items not covered by any block, and
     generators without tests. The design is the
     base one: the author's generators (`generator_ideas`, `status: confirmed`),
     the author's concrete tests (`specific_test_ideas`; a generator the author didn't
     describe — `status: proposed`) and, beyond what the author ordered, the model's own
     edge cases of the input (from the legend and `algorithm_hint`, `status:
     suggestion`) — see "Model suggestions beyond the spec" in `.claude/rules/steps.md`.
     Tests aimed at particular wrong solutions (`known_wrong_approaches`) aren't
     designed here: the solutions are run on the base tests first, and a test of its own
     is added only for a wrong solution the base tests don't catch (`tests_survivors`
     below). So `known_wrong_approaches` isn't part of the step's input — editing it
     doesn't invalidate the plan; blocks whose `covers` refer to
     `known_wrong_approaches[i]` or to a solution (`solutions/<name>`) are carried over
     when the plan is rebuilt (see above) and aren't reconciled with the spec.
   - **`generators_draft`** — input: the plan's generators (each a separate
     input part: editing one plan entry — editing one generator in
     revision mode, editing the plan's tests doesn't touch the step) + the input format from
     `statement_draft` + `constraints.yaml` without TL/ML (the input guarantees
     the generator must respect). Output: `generators/<name>.cpp` for
     each generator of the plan (based on `generators/*.cpp` from the spec's
     references); missing ones are requested again, about generators outside the plan and
     `opt<T>(...)` parameters differing from the plan — `plan_mismatch` notes.
     If a plan entry can't be implemented unambiguously — `uncertain`: the author fixes
     the plan.
   - **`test_script_draft`** — input: `constants`, `tests` and the signatures of the
     plan's generators (without algorithms — editing a generator's code doesn't invalidate
     the script) + `constraints.yaml` without TL/ML + `generation.script_style`.
     Output: a FreeMarker test-script (`flat` — `test_script` following the sample from the
     references; `groups` — `test_script_groups` by the `group`s of the plan's
     blocks, on the same structure if there is no sample: a list of groups, a macro per
     group, one `<#list>` over them). With test groups every script line carries
     `-group=<N>` (the test's group for the local checks and Polygon); generators don't
     read it, so `generators_draft` avoids `has_opt`/`opt` with a default (testlib then
     fails on an unread option). Before writing, the step expands the script with
     FreeMarker (as `local check`, with the dropped functions restored) and checks the
     markers (`check_script_groups`): a line without `-group=<N>` or of an unknown
     group, a group without lines — the response is a conflict. No `java` or the script
     doesn't expand — not checked here (`local check` reports it); invalid groups in
     `constraints.yaml` — not this step's error, not checked either.
   - **`run_tests`** (`orchestrator/steps/tests_check.py`) — a mechanical step after
     `test_script_draft`: `local check --full` without the statement checks — the tests
     from the script, the lines' groups, the validator on every test (identical tests —
     a warning only). Any error fails it and stops the pipeline before the solutions.
     Each error has a kind (`local_tests.TestIssue`) routed to the steps whose files may
     cause it (`tests_check.ROUTES`): the script doesn't expand, a bad line, the lines'
     groups — `test_script_draft`; a generator doesn't compile — `generators_draft`; a
     line calls a generator with no source — `generators_draft`, `test_script_draft`; a
     generator fails on a line or the validator rejects a test — all three (the design's
     arguments, the generator's code, the script's command). The validator doesn't compile,
     invalid groups, no `java` — no step's, the author fixes them. The report
     (`.cache/run_tests.report.json`) keeps the errors and the hashes of each step's files
     at the run; while the run's result is a failure, a step's files are the same and
     nothing else the run reads (the validator, `problem_lib.h`, `constraints.yaml`, the
     examples) changed, the step reruns even with a valid cache (`forced_rerun_reason`) and
     revises its files with the errors routed to it and the author's comment from
     `llm_reports/run_tests.md` (`tests_run` in its prompt). Its revised files make the
     report outdated for it.
   - **`tests_survivors`** — after the solution stages (`STEP_ORDER`, last): tests for the
     wrong solutions that the local runs `run_slow`/`run_wrong` report as surviving — no
     test from the script gets their declared verdict, the examples don't count — and the
     wrong solutions that pass a test group their spec item says they fail
     (`uncaught_groups`, `.claude/rules/local-checks.md`): survivors with those groups,
     even if another group catches them, whose tests go into those groups
     (`solutions_check.Gate.current_survivors`: only of a passed run whose input is still
     the same). No survivors — `skipped`. Input: the survivors (name, verdict, the
     uncaught groups, code), the
     main solution, the input format and the legend, `algorithm_hint`, `constraints.yaml`
     without TL/ML. The current plan, the survivors' local times and the TL are the
     context of the call, not its input: the plan this step itself extends doesn't make it
     `stale`, and the same survivors after it — a cache hit, the model isn't called for
     them again; some of them caught — the rest are a new input. Output: the same
     `authoring/tests_plan.yaml` with new blocks (`covers: solutions/<name>`, `status:
     proposed`) and new generators; the current plan must stay as is
     (`tests_plan.check_plan_extends`, otherwise nothing is applied), and its entries are
     written back exactly as they were (`keep_current_entries`), so that the kept
     generators stay cache hits of `generators_draft`. A survivor no block refers to, or
     an uncaught group of it that no block referring to it is in — an `uncovered` note; a solution the model can't catch (it may be correct) — an
     `uncertain` note, the author decides. After the step `llm generate` runs
     `SURVIVOR_RECHECK_STEPS` (`generators_draft` … `run_wrong`) again; with
     `--survivor-rounds N` (1 by default, `0` — the step doesn't run) the step runs up
     to N times in one run, again even with the same survivors
     (`pipeline._survivor_rounds`).
