---
paths:
  - "orchestrator/polygon/**"
  - "docs/POLYGON_API.md"
  - "tests/test_polygon_*.py"
  - "tests/polygon_sync_helpers.py"
---

# Polygon integration

Polygon API method parameter names — only from `docs/POLYGON_API.md`, don't
guess. Addressing problems and groups — in `.claude/rules/cli.md`.

## What the polygon steps do

- creating the problem on Polygon by the spec's `problem_id` through the Polygon API
  (`orchestrator/polygon/`; the `problem_id` -> Polygon problemId binding
  is stored in `problems/<problem path>/polygon_state.json`) and transferring the
  actual TL/ML from `problems/<problem path>/limits.yaml` via
  `problem.updateInfo` (`set_constraints`), uploading `problems/<problem path>/problem_lib.h` as
  a resource file (`upload_problem_lib`), uploading
  `problems/<problem path>/validator.cpp` and setting it as the validator
  (`upload_validator`), setting the checker (`upload_checker`: for
  `checker.custom_needed: true` — uploading `problems/<problem path>/checker.cpp`,
  otherwise — the standard checker from `checker.standard` without uploading a file),
  uploading `problems/<problem path>/interactor.cpp` and setting it as the
  interactor (`upload_interactor`, only for an interactive problem; the
  interactivity flag is set by `set_constraints`),
  the tutorial `problems/<problem path>/statement/<code>/tutorial.tex` (optional;
  written by the author, `polygon pull` or the `tutorial_draft` step) —
  together with the statement in `upload_statement`,
  uploading `problems/<problem path>/authoring/` — `spec.yaml`,
  `constraints.yaml` (the whole `constraints_pick` result), `tests_plan.yaml`
  (the test plan), the copies of the draft `draft.<extension>` and of the tutorial draft
  `draft-tutorial.md` — as is into the
  Attachments section (by file name, without `authoring/`) (aux files,
  `upload_attachments`) — so that `polygon pull` restores the problem together
  with the spec, limits, test plan and draft.
  The steps upload only what changed since the last sync with
  Polygon (the base — `revision`/`synced` in `polygon_state.json`, see
  `orchestrator/polygon/sync.py`). After each polygon step that
  uploaded something (except `create_problem`), the problem's working copy
  is committed (`problem.commitChanges`, `minorChanges=true`) — on a failure
  at a later step, everything done by the previous ones is already in a revision (`polygon push
  --no-commit` doesn't commit, see `polygon push` below).
  The remaining integration steps (other files, packages) — later, as
  they are worked out.


## `polygon` commands

- `orchestrator <problem> polygon push [--step NAME] [--force]` — a run of the
  polygon steps in the `POLYGON_STEP_ORDER` order; each step uploads
  only the problem elements changed since the last sync (like
  `git push` with a fast-forward check). Before the first upload one request
  compares the problem's revision on Polygon with the recorded one: if Polygon was changed
  bypassing the orchestrator (a different revision or uncommitted edits in the working
  copy) — the push stops and suggests `polygon pull` or
  `--force`. The revision is unknown (the problem is bound to one already existing on
  Polygon or from before this check appeared) — the push also stops until
  `polygon pull`; for a problem just created by `create_problem`, the revision
  is recorded right away. `--force` uploads everything without the revision check and
  overwrites committed edits made on Polygon (they stay in the
  revision history); the sync base after it — only what it
  uploaded. At the end the push records the version stamps of the uploaded files on
  Polygon (modification time and length from the listing), so the next pull doesn't
  download them. Uncommitted edits in the working copy on Polygon
  always stop the push, even with `--force`: the commit after a step would carry
  them into the orchestrator's revision. The exception — `--no-commit` (with `--step` and without,
  can be run several times in a row): the steps only upload to the working copy, without
  commits and without writing to the sync base — the author sorts things out on Polygon
  and commits there in one batch; uncommitted edits are then only a
  warning (the upload will overwrite the same files), and `polygon status`
  shows what was uploaded as not yet synced. After the commit on
  Polygon the revision is different — the next push will require `polygon pull`, and that,
  seeing the same files, will update the base. Output: along the way — a line per step, at the end —
  the groups `pushed` (uploaded files and other problem elements), `notes`,
  `errors`, `not pushed` (steps that didn't upload theirs because of an error) and
  `warnings`; what didn't change isn't printed. It actually calls the Polygon
  API — the same restrictions on running without an explicit command as for
  `llm generate`.
- `orchestrator <problem> polygon status` — which polygon steps are already
  done, without going to the network: `done / stale / not run` by
  `PolygonStep.compute_status` (comparing the local files with the sync
  base — what the last push uploaded or pull saw on
  Polygon). Edits on Polygon after the last sync aren't visible
  from here — only `polygon push`/`pull` notice them.
- `orchestrator <problem> polygon pull [--polygon-id N] [--overwrite]` —
  the reverse direction: binds the problem to one already existing on
  Polygon (from `polygon_state.json`, otherwise by searching for a non-deleted problem
  named `problem_id` — with the same location checks as `init`;
  `--polygon-id` additionally narrows the search if
  there are several problems with that name), compares with it the problem parts
  the orchestrator uses (statement, tutorial and examples, TL/ML, validator,
  non-standard checker, generators, test-script, solutions, spec,
  `constraints.yaml`, test plan, draft and tutorial draft from Attachments), downloads into
  the problem directory those missing locally and rewrites the sync
  base by the current Polygon revision. Files with a modification time in
  the Polygon listing (validator, checker, generators, solutions, `problem_lib.h`,
  Attachments) are downloaded only if their version on Polygon could have changed
  since the last sync (the stamp in the base didn't match the
  listing and the revision is different) or they need to be written to disk. Files differing
  from Polygon are compared three-way, like in `git merge`, with the sync
  base: only Polygon changed — the local file is replaced
  (`updated`); only the local one changed — it stays (`kept`, the next push
  will upload it), even with `--overwrite`; both sides changed (`conflict`)
  or there is no base (`differs`) — the local one stays, with `--overwrite` — it is replaced by the version
  from Polygon. Replaced files are moved to `.pull_backup/`. Files that
  aren't on Polygon are listed explicitly and never deleted;
  files from Attachments (spec, `constraints.yaml`, `tests_plan.yaml`, draft, tutorial draft) are compared
  only with their versions in Attachments, and the problem's TL/ML — only with
  `limits.yaml` (the same comparison, by the TL/ML values; they don't touch
  `constraints.yaml`). It refuses
  to work if there are uncommitted edits on Polygon. The problem directory
  is created if it doesn't exist; a local spec isn't needed (`spec.yaml` itself
  is compared and downloaded as an ordinary file). It changes nothing on Polygon
  (read-only), but it goes to the network — the same restrictions on running without an explicit
  command. Output — likewise: along the way a line per problem part, at the end the groups
  `pulled`, `pulled over local changes` (`--overwrite`), `will be pushed`
  (for a file that push uploads only with a certain spec — that
  condition), `conflicts` (`conflict` and `differs`), `errors`, `warnings`;
  what matches Polygon isn't printed. Details — in the docstring of
  `orchestrator/polygon/pull.py`.


## Problem files related to Polygon

- `polygon_state.json` — `problem_id` -> Polygon problemId and the sync
  base: `revision`, `synced` (element -> sha256), `stamps` (file ->
  the stamp of its version on Polygon). Not in `.cache/`: it's a fact about the state on
  Polygon, not a step cache.
- `statement/<code>/name.txt` — the problem's name in this language: push creates it
  from the spec's `title` (`title.ru` for `ru`) if the file doesn't exist; pull writes the
  name from Polygon here.
- `statement/examples/example_<N>.{input,output}_for_statement.txt` —
  input/output for statements, each on its own: for an interactive problem —
  the dialogue from `statement_draft`, for a normal one — only from the author;
  `example_<N>.verify_for_statement.txt` — the verify flag, written by the author or pull.
- `polygon.log` — the push log (appended to): step starts, the full text of errors,
  tracebacks, HTTP requests; `.pull_backup/<time>/` — files replaced by pull.
- `PolygonClient` takes the keys from `POLYGON_API_KEY`/`POLYGON_API_SECRET`/
  `POLYGON_API_BASE_URL` (see `.env.example`), pauses between requests and
  does an exponential back-off on HTTP 429.
