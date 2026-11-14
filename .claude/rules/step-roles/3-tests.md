---
paths:
  - "orchestrator/steps/tests_plan.py"
  - "prompts/tests_plan/**"
  - "tests/test_step_tests_plan.py"
  - "orchestrator/steps/generators_draft.py"
  - "prompts/generators_draft/**"
  - "tests/test_step_generators_draft.py"
  - "orchestrator/steps/test_script_draft.py"
  - "orchestrator/steps/tests_survivors.py"
  - "prompts/tests_survivors/**"
  - "tests/test_step_tests_survivors.py"
  - "prompts/test_script_draft/**"
  - "tests/test_step_test_script_draft.py"
  - "templates/tests_plan_format.yaml"
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
     Output: the test plan `authoring/tests_plan.yaml` in the
     `templates/tests_plan_format.yaml` format — generators (name, parameters with
     types, what they read and print, the algorithm) and test-script blocks
     (commands — generator chains, named values, the group, which
     spec items the block covers — `covers`). The plan is self-contained:
     the next two steps don't see the spec. The orchestrator checks it before writing
     (`parse_tests_plan`: commands call only the plan's generators and only
     their parameters; doesn't parse — a conflict) and reports with `uncovered` notes
     the `specific_test_ideas` items
     not covered by any block, and generators without tests. The plan is the
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
     `known_wrong_approaches[i]` or to a solution (`solutions/<name>`) are kept in
     revision mode and aren't reconciled with the spec.
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
     blocks, on the same structure if there is no sample).
   - **`tests_survivors`** — after the solution stages (`STEP_ORDER`, last): tests for the
     wrong solutions that the local runs `run_slow`/`run_wrong` report as surviving — no
     test from the script gets their declared verdict, the examples don't count
     (`solutions_check.Gate.current_survivors`: only of a passed run whose input is still
     the same). No survivors — `skipped`. Input: the survivors (name, verdict, code), the
     main solution, the input format and the legend, `algorithm_hint`, `constraints.yaml`
     without TL/ML. The current plan, the survivors' local times and the TL are the
     context of the call, not its input: the plan this step itself extends doesn't make it
     `stale`, and the same survivors after it — a cache hit, the model isn't called for
     them again; some of them caught — the rest are a new input. Output: the same
     `authoring/tests_plan.yaml` with new blocks (`covers: solutions/<name>`, `status:
     proposed`) and new generators; the current plan must stay as is
     (`tests_plan.check_plan_extends`, otherwise nothing is applied), and its entries are
     written back exactly as they were (`keep_current_entries`), so that the kept
     generators stay cache hits of `generators_draft`. A survivor no block refers to — an
     `uncovered` note; a solution the model can't catch (it may be correct) — an
     `uncertain` note, the author decides. After the step `llm generate` runs
     `SURVIVOR_RECHECK_STEPS` (`generators_draft` … `run_wrong`) again; with
     `--survivor-rounds N` (1 by default, `0` — the step doesn't run) the step runs up
     to N times in one run, again even with the same survivors
     (`pipeline._survivor_rounds`).
