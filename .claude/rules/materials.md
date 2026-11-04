---
paths:
  - "templates/**"
  - "examples/**"
  - "orchestrator/references.py"
  - "docs/SPEC_FORMAT.md"
  - "tests/test_example_templates.py"
  - "tests/test_references.py"
---

# Attached materials: templates/, examples/, references

## How to use the attached materials


- `templates/tutorials/*.md` (except `polygon.md`) are **rules**, not reference
  material "for general knowledge". Prompts must explicitly instruct
  the model to follow them as mandatory constraints, not as advice.
  The rules are split by topic, and a step gets only the ones it needs
  (`STEP_CONTEXT_DOCUMENTS`), so editing one topic invalidates the cache
  only of the steps that read it. The files are written for the model: each is
  self-contained, without references to other rule files. `polygon.md` is a reference
  on the Polygon interface, it isn't attached to any step.
- `templates/problem_lib.h` and the files of the sample problems
  `examples/<problem path>/` (`validator.cpp`, `checker.cpp`,
  `generators/*.cpp`, `test_script`, `solutions/*`)
  are the base to extend, not to reinvent. If a
  generation step creates a new generator — it must `#include "problem_lib.h"` and
  follow the same naming conventions (`register...`, `opt<T>(...)`,
  the `gen_test` style from `generators/*.cpp`). Each problem has its own copy of
  `problem_lib.h` (`problems/<problem path>/problem_lib.h`, see
  `PROBLEM_LIB_FILENAME` in `orchestrator/steps/base.py`): the template is only its
  initial content, an edit of the template doesn't reach already existing copies.
  Compile-check looks for `problem_lib.h` first in the problem directory,
  then in `templates/`; the model is given the template.
- `templates/` is shared by all problems and is always attached to the steps
  (`STEP_CONTEXT_DOCUMENTS` in `orchestrator/steps/base.py`); also there is
  `constraints_format.yaml` — the format of the `constraints_pick` result, which
  the following steps and `polygon push` read, and `tests_plan_format.yaml` —
  the test plan format. `examples/` are sample
  problems, a `<input data kind>/<problem gist>` directory for each
  (`array/prefix-sums`), in the `problems/<problem path>/` layout plus their own
  spec `authoring/spec.yaml` (`problem_id` — the path joined with `--`, `array--prefix-sums`;
  the orchestrator's service files in them — cache, logs, `polygon_state.json`,
  `.pull_backup/`, `.llm_backup/`, `.llm_conflicts/`, `.fix_backup/`, `.tests_cache/`, `llm_reports/`, `*.removed` — are in `.gitignore`;
  `limits.yaml` is committed — the problem's actual TL/ML, after tuning on
  Polygon they diverge from `constraints.yaml`, and that's fine; the expected
  `constraints_pick` result for it — `authoring/constraints.yaml` next to it, `tests_plan`
  — `authoring/tests_plan.yaml` (its generators are exactly `generators/*.cpp` with
  the same parameters, and it covers all items of the spec); when editing
  the samples keep them consistent with these and with `constraints_format.yaml`
  — this is checked by `tests/test_example_templates.py`, which also compile-checks
  all `.cpp` samples).
- `templates/solutions/<verdict>_<description>.<language>` — solution
  skeletons (`ok.cpp`, `tl_ok_slowio.java`): the author's input/output method,
  `solve()`, separate answer computation and an entry point, without an algorithm;
  the skeleton's language is its extension. `solutions_draft` attaches all
  skeletons of each language from `solutions.languages` for which the references
  gave no `ok` solution (`extra_context_documents` in
  `orchestrator/steps/solutions_draft.py`) — so input/output is set even without
  references. A new language or verdict — a new file with the extension the language
  is named with in solution names (`ok.kt`), no code changes needed.
- Samples (files of other problems) are attached to the model call only as
  **spec references** — the `references` field at the spec root (for all steps) and
  in step sections (only for their own step), see `docs/SPEC_FORMAT.md`,
  "References", and `orchestrator/references.py`. A reference is a file or a directory
  in the `problems/<problem path>/` layout (a sample problem from
  `examples/`, a past problem of the author in `problems/`); each step
  takes only its own part from the directory (`REFERENCE_DOCUMENTS`), solutions —
  only in the languages from `solutions.languages`. Without references a step gets
  only `templates/`. The references' content is part of the step's prompt hash
  (see "Caching by spec hash" in
  `.claude/rules/steps.md`), the `references` paths themselves aren't part of
  `input_hash`. The model sees a reference's spec and `constraints.yaml`
  under a path without `authoring/` (`examples/<problem path>/constraints.yaml`,
  `_context_document_key` in `orchestrator/steps/base.py`) — under the same
  name under which the step returns its own `constraints.yaml`. Prompts must explicitly
  separate what to carry over from a sample (the file skeleton, input/output, testlib
  functions, composing generators through a pipeline) and what not (the data format,
  variable names and the logic of the sample problem), and work without samples — see
  `docs/PROMPTS.md`, "Attached templates as samples".


## Naming conventions in the samples

- Generators: `gen_*.cpp` produce input, `upd_*.cpp` transform a ready test.
- Solutions of sample problems: `<verdict>_<language>_<description>_draft.<ext>`;
  skeletons in `templates/solutions/` — `<verdict>_<description>.<language>`.
- A sample problem's spec — `authoring/spec.yaml` (`problem_id` — the path joined with `--`),
  next to it the expected `authoring/constraints.yaml` and `authoring/tests_plan.yaml`.
