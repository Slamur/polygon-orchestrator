---
paths:
  - "orchestrator/steps/base.py"
  - "orchestrator/steps/__init__.py"
  - "orchestrator/pipeline.py"
  - "orchestrator/cache.py"
  - "orchestrator/step_order.py"
  - "orchestrator/llm_report.py"
  - "prompts/revise.md.j2"
  - "prompts/missing_artifacts.md.j2"
  - "docs/PROMPTS.md"
  - "tests/test_steps_base.py"
  - "tests/test_step_revision.py"
  - "tests/test_pipeline.py"
  - "tests/test_cache.py"
  - "tests/test_llm_report.py"
  - "tests/test_prompts_doc_sync.py"
---

# Generative steps: revision, conflicts, cache, escalation

The `llm generate|tutorial|critic|status|apply|accept` commands — in `.claude/rules/llm-commands.md`.
The role of each generative step — in `.claude/rules/step-roles/`, one file per
item (`<N>-<step>.md`). Model routing by step — in
`.claude/rules/models.md`.

## Common to all steps

Each step is a pure function from (spec + results of previous steps) to
artifact(s) + an uncertainty report. Steps are idempotent: a rerun
with the same input must be safe (it overwrites `problems/<problem path>/...`, doesn't
accumulate state) — but by default a rerun with **the same**
input must not even reach the model call, see "Caching by spec
hash" below.

**Revision mode.** If the step's files already exist on disk, rerunning the step
(the input changed or `--force`) by default doesn't write them from scratch: the model
gets the current files (possibly with the author's manual edits) and what
changed in the step's input since the last run ("before → after" by parts of the
input — their values are stored in the step cache, `input_values`), and revises
them — the shared block `prompts/revise.md.j2` (`StepRevision` in
`orchestrator/steps/base.py`, the current files — the step module's `current_artifacts`).
Each current file in the response is in exactly one of `artifacts`
(changed), `unchanged`, `removed`, `renamed` (checked before writing to
disk, see "Conflicts" below). Files the model proposes to delete aren't deleted but
renamed to `<name>.removed` — the author looks and deletes them themselves; such
files are seen by neither the steps, nor the Polygon steps, nor references (`is_removed_mark` in
`orchestrator/problem_paths.py`). On Polygon, `.removed` files and the old
names of renamed files aren't deleted — the author does that by hand.
Renames (e.g. a change of a solution's verdict) are applied immediately.
Previous versions of overwritten and renamed files — in the problem directory's
`.llm_backup/<time>/` (in any mode: `problems/` is outside
git). `llm generate --fresh` — generation from scratch, without the current files.

**Conflicts.** The model's response is applied like a `git merge`
(`_plan_response` in `orchestrator/steps/base.py`): what can be applied
is applied, the rest is a conflict. A current file the response is silent about
stays as is; an unknown name in `unchanged`/`removed`/`renamed` is
ignored (both situations — a note in the step's output). A conflict is
an invalid file name, a current file in several places of the response at once,
a rename to an occupied or duplicate name: the problem file stays as it
was, the model's version is set aside in `.llm_conflicts/<step>-<time>/` (at
its own path in the problem directory) with a `CONFLICTS.md` report — what wasn't
applied and why. The files of `generators_draft`, `test_script_draft` and
`solutions_draft` are independent and are applied one by one (`partial_apply`);
`statement_draft`, `constraints_pick` (`constraints.yaml` and `validator.cpp`
— 1-to-1), `tests_plan` and `checker_draft` on any conflict aren't applied at all — just like a response
that didn't pass the step's file-set check (`validate_artifacts`). A step with
a conflict stops the pipeline; until the author resolves the conflicts
(deletes their directory) and runs `llm accept`, `llm generate` doesn't call the model for
this step (`--force` — calls it again), `llm status`
shows `conflict`. An incomplete response — missing some of the files the step
must return (`MissingArtifactsError`) — the model completes once with a
separate call (`prompts/missing_artifacts.md.j2`: it is shown the
files already returned, only the missing ones are taken; this block isn't part of the
step's prompt hash); if files are still missing after it — a conflict, as
above. A response not applied in full also remains available for
`llm apply`.

## Caching by spec hash

Each call to the generative model costs money and time, and specs will
be edited iteratively (draft → edits → rerun). So
a step must not call the model again if nothing that
affects it has changed since the last successful run.

What goes into the hash for a step (computed in `orchestrator/cache.py`,
used in `orchestrator/pipeline.py` before calling
`model_router`):

- the canonical JSON representation of the spec section the step depends on
  (`statement_draft` → the `statement_draft` section + `language` if it isn't
  `ru`; `constraints_pick` →
  the `constraints` section + the input format from `statement_draft`
  (`formal_input_sketch`, `indexing`, the examples' input — an edit of the input format
  must invalidate `validator.cpp` too); `tests_plan` → the
  `generation` section + the same input format from `statement_draft` (an edit of the
  input format in the statement must invalidate the plan, even if
  the `generation` section didn't change) + `legend_sketch` +
  `problems/<problem path>/authoring/constraints.yaml` from step 2 without TL/ML +
  `solutions.algorithm_hint` (if the `solutions` section is set — editing it must
  invalidate the plan too, not only an edit of `generation`; `known_wrong_approaches`
  isn't part of it: the base plan doesn't design tests for wrong approaches); `generators_draft` → the input format +
  `constraints.yaml` without TL/ML + each generator of `tests_plan.yaml`
  as a separate part; `test_script_draft` → `generation.script_style` +
  `constraints.yaml` without TL/ML + `constants`, `tests` and the plan's generator
  signatures; `solutions_draft` → the
  `solutions` section + `constraints.intended_complexity` + input/output from
  `statement_draft` (input and output format, `indexing`, examples with answers,
  legend, for an interactive problem — the protocol) + `constraints.yaml` without TL/ML;
  `interactor_draft` → the `interactor` section + the input format and protocol from
  `statement_draft`; the mechanical `limits_from_constraints` → only TL/ML
  from `constraints.yaml`, without prompts and model). Assembling this part of `statement_draft` —
  `statement_io_section` in `orchestrator/steps/base.py`, shared by all
  three steps;
- the hash of the contents of this step's prompt templates (`prompts/<step>/system.md` +
  `user.md.j2`) and of the documents attached to it (`templates/` and
  the spec's reference files) — if a prompt or a sample was edited, the cache must
  be invalidated, even if the spec didn't change;
- the model name/version from `model_router.py` for this step (a model change also
  invalidates the cache).

The logic in `pipeline.py` before each step:

1. Compute `input_hash` by the rules above.
2. Read `problems/<problem path>/.cache/<step>.json`, if it exists.
3. If `input_hash` matches the stored one **and** the expected artifact files
   are in place **and** the stored `status` wasn't `uncertain`
   (caching a "failure due to missing data" is pointless — it must
   surface on every run until the spec is fixed) — the step is skipped,
   "cache hit" goes to the log, `problems/` isn't touched.
4. Otherwise — a normal model call, and on a successful result the cache file
   is overwritten with the new `input_hash`/`prompt_hash`/`model`/`status`/
   `timestamp`.

Besides the overall `input_hash`/`prompt_hash`, the cache stores hashes by parts
(`input_parts`: fields of the spec section and upstream artifacts; `prompt_parts`:
prompt templates and each attached document) — they don't change the cache-hit
decision, they only explain in `llm status`/`llm generate` what changed.
`orchestrator <problem> llm accept` records the current hashes without calling the model (see
`.claude/rules/llm-commands.md`).

The cache also stores the values of the input parts themselves (`input_values`) — from them
the next run in revision mode shows the model "before → after".
The step's current files aren't part of the hash: they are revision context, not input —
a manual edit of a file doesn't make the step `stale`.

`--force`/`--fresh` in the CLI bypass this mechanism completely for an explicit
forced regeneration (`--force` — by revising the current files,
`--fresh` — from scratch).

## Escalation rule on uncertainty

This rule applies to the prompts of all steps (see `prompts/*/system.md`):

- If the answer lacks data explicitly set by the author in the spec (first
  of all — the asymptotics, the precision of a real-valued answer, behavior in
  edge cases not described in `special_guarantees`) — the model
  must **not make it up**, but return a structured response with the field
  `uncertain: true` and a concrete indication of which spec field exactly is
  missing.
- If the data is sufficient but the decision is ambiguous (e.g. the author didn't
  give a variable's upper bound, `max: null`) — the model may propose
  an option, but must explicitly mark it as `status: proposed` (not
  `status: confirmed`), and state what the assumption is based on.
- References to sources beyond the attached `tutorials/*.md` (e.g.
  to testlib documentation) are allowed, but must be explicit URLs, not
  a retelling "from memory".
- The orchestrator (Python code) must distinguish these two cases mechanically:
  a response with `uncertain: true` stops the pipeline for this problem_id and
  requires a spec edit; a response with `status: proposed` fields — doesn't stop the
  pipeline, but marks the corresponding files/fields in `problems/` as
  requiring manual review by the author (e.g. with a `# PROPOSED,
  REVIEW ME` suffix in the generated file or a separate field in
  `constraints.yaml`).

### Model suggestions beyond the spec

"Don't make it up" refers to what the author sets, not to the model's own
ideas. The author's decisions — the asymptotics by which the limits are chosen,
the reference solution's algorithm, the ordered solutions and tests —
the model implements as given and doesn't substitute its own. But if it sees something
that isn't in the spec, the author wants to know about it, so beyond what was ordered
the model may propose its own — with `kind: suggestion` in `notes` (and no lower than
`status: proposed` for the response, if files were added):

- `solutions_draft` — another correct solution (better asymptotics than
  `intended_complexity` — with a remark on whether to reconsider the limits;
  noticeably simpler) and likely contestant mistakes that aren't in
  `known_wrong_approaches` — as separate files;
- `tests_plan` — edge cases of the input not covered by
  those ordered by the author — as separate generators and plan blocks with
  `status: suggestion` (`generators_draft` and `test_script_draft` carry
  them into code with a `SUGGESTION: ...` comment, they don't add their own);
- `constraints_pick` — only as notes, without changing `constraints.yaml`:
  inconsistencies in `complexity_reasoning`, limits under which a slow solution
  passes, useful groups.

Files with the model's ideas start with a `SUGGESTION: ...` comment — the author
sees them both in the problem directory and on Polygon. `statement_draft`,
`checker_draft` and `interactor_draft` don't propose their own: the statement,
the comparison rule and the interactor's behavior are set only by the author. `tutorial_draft`
proposes its own only as notes, it doesn't write it into the tutorial: its content is set by the draft.

The spec from a draft (`init --draft`) is where the author only formulates
their decisions, so there the model fills them in too (the asymptotics, the solution
idea, the comparison rule in the checker), but marks each such value
right in the spec (`# PROPOSED, REVIEW ME: ...`, `# SUGGESTION: ...`): after
the author's review they become their decisions for all steps. The essence of the problem
(objects, rules, question) and the legend's plot — only from the draft.
