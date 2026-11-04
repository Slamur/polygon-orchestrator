---
paths:
  - "orchestrator/steps/tests_plan.py"
  - "prompts/tests_plan/**"
  - "tests/test_step_tests_plan.py"
  - "orchestrator/steps/generators_draft.py"
  - "prompts/generators_draft/**"
  - "tests/test_step_generators_draft.py"
  - "orchestrator/steps/test_script_draft.py"
  - "prompts/test_script_draft/**"
  - "tests/test_step_test_script_draft.py"
  - "templates/tests_plan_format.yaml"
  - "orchestrator/step_order.py"
  - "docs/PROMPTS.md"
  - "tests/test_prompts_doc_sync.py"
---

# Role of each generative step, item 3: `tests_plan`, `generators_draft`, `test_script_draft`

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
     `solutions.known_wrong_approaches` and `solutions.algorithm_hint`.
     Output: the test plan `authoring/tests_plan.yaml` in the
     `templates/tests_plan_format.yaml` format — generators (name, parameters with
     types, what they read and print, the algorithm) and test-script blocks
     (commands — generator chains, named values, the group, which
     spec items the block covers — `covers`). The plan is self-contained:
     the next two steps don't see the spec. The orchestrator checks it before writing
     (`parse_tests_plan`: commands call only the plan's generators and only
     their parameters; doesn't parse — a conflict) and reports with `uncovered` notes
     the `specific_test_ideas`/`known_wrong_approaches` items
     not covered by any block, and generators without tests. Inside,
     the step does two things of different nature, and it's important not to mix them:
     if `generation.generator_ideas` already describes the needed generator — that's
     the mechanical part; for each `known_wrong_approaches` item the step
     must come up with a test itself (and, if needed, a generator) that purposefully
     checks exactly this scenario, and mark it `status: proposed` with
     a justification in `rationale` (why this test catches exactly this
     mistake) — as with any other model assumption, this isn't an
     independent decision but a proposal for the author to review. If neither
     `generator_ideas` nor `known_wrong_approaches` give any hints for
     stress tests — the step must not stay silent about it: explicitly state in `notes`
     that adversarial cases weren't invented and manual review is needed. Beyond
     what the author ordered, the step may propose its own tests for likely
     mistakes that aren't in the spec (from the legend and `algorithm_hint`) — see
     "Model suggestions beyond the spec" in `.claude/rules/steps.md`.
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
