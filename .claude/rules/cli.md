---
paths:
  - "orchestrator/cli.py"
  - "orchestrator/cli_commands.py"
  - "orchestrator/problem_paths.py"
  - "orchestrator/drafts.py"
  - "orchestrator/spec.py"
  - "orchestrator/spec_from_draft.py"
  - "prompts/spec_from_draft/**"
  - "orchestrator/statement_languages.py"
  - "orchestrator/limits.py"
  - "README.md"
  - "tests/test_cli*.py"
  - "tests/test_problem_paths.py"
  - "tests/test_spec*.py"
  - "tests/test_statement_languages.py"
---

# CLI: addressing problems, groups, `init`

`llm` commands — in `.claude/rules/llm-commands.md`, `local` — in
`.claude/rules/local-checks.md`, `polygon` — in `.claude/rules/polygon.md`.

## Running

Problems in this project are independent of each other: everything that relates to
a problem — the spec and the results of the steps — lies in its directory (the spec —
`authoring/spec.yaml`). Problem directories lie in two roots — `problems/` (the author's
problems) and `examples/` (sample problems) — at any depth: intermediate
directories only group problems
(`problems/contest/2026/round-1/my-task`). A problem directory is the first directory
down from the root that contains `authoring/spec.yaml` or
`polygon_state.json`; problems aren't nested in each other, and a directory
named `authoring` can't be a problem or a problem group.

The spec, `constraints.yaml` and the draft copy lie in the problem directory's
`authoring/` subdirectory — these are the files only the model and the author work with;
everything else, including `limits.yaml`, — in the directory itself, as on
Polygon. A problem whose files still lie directly in its directory (the layout
from before `authoring/`) is first moved into `authoring/` by any command on it
(`migrate_to_authoring` in `orchestrator/problem_paths.py`, prints what
was moved; a file that exists in both places is an error before any
move). Until the problem is moved, completion and the `problem_id` checks
find it, and references and `--ref` read its spec and `constraints.yaml` from
the old place.

Likewise, a statement lying directly in `statement/` (the layout from before language directories)
is first moved into `statement/<language>/` by any command on the problem — together with
the sync base keys in `polygon_state.json`, so that push doesn't upload it
again (`migrate_statement_languages` in `orchestrator/statement_languages.py`,
prints one line; a file that exists in both places is an error before any
move). References read the statement of a problem not yet moved from the old place.

In the CLI a problem is addressed by the path of its directory together with the root — below
`<problem>`: `problems/<problem path>` or `examples/<problem path>`, the root is
mandatory. `problem_id` (the spec field and the problem's name on Polygon) is derived from
the path: in `problems/` — the name of the innermost directory (`my-task`), in
`examples/` — all path components joined with `--` (`examples/array/prefix-sums`
-> `array--prefix-sums`; so `--` is forbidden in `examples/` directory
names). `problem_id` is unique among the problems of both roots. All of this is in
`orchestrator/problem_paths.py`.

Usually the work is on one problem, so the problem comes
first in the CLI, and the command to it — after: `orchestrator [--base-dir ...]
<problem> <init|llm|local|polygon> [command] [flags]` (`--base-dir` — the directory
containing `problems/` and `examples/`, the current one by default). The command
groups are split by what they cost: `llm` (and `init --draft`) calls the
model, `polygon` goes to the network, `local` — neither; local
checking lives in `local`, even if it is needed before a push.

Instead of `<problem>` you can give a group — a directory above problems or the root itself
(`problems/contest/2026`, `examples`): the command runs for all problems under
it in turn (`_run_group` in `orchestrator/cli.py`, `problems_under` in
`orchestrator/problem_paths.py`). A group is a whole directory, not a name prefix.
First the list of problems (`problem_id` and address) is printed and a confirmation is asked
(`[y/N]`, closed input — a refusal); only
`llm status` and `polygon status` work without it. A failure of a problem, even an unhandled exception,
doesn't stop the rest; at the end — how many passed and which didn't, exit code
1 if at least one didn't pass. `init` doesn't accept groups, `polygon
pull --polygon-id` with a group is an error: they are about one problem.

What the CLI must provide (`orchestrator/cli.py` —
the parser, `orchestrator/cli_commands.py` — the commands themselves):

- `orchestrator <problem> init [--ref <base problem>] [--draft NAME [--force] [--no-critic] [--critic-rounds N]]` —
  creates the problem directory (if it doesn't exist) with `authoring/spec.yaml`. Without flags — from
  `templates/spec_template.yaml`: `problem_id` (derived from the path)
  is filled in, the other fields are empty, with hint comments.
  `--ref` — the spec's base is not the template but another problem's spec (an address with the root,
  like `<problem>`; from `problems/` or `examples/`): copied as is,
  with comments, with the new `problem_id`, and the base problem is added to the
  shared `references` — its files become samples for the steps.
  `--draft NAME` — the author's free-form draft from `drafts/` (a path
  relative to `drafts/` with or without the extension, `icpc/my-idea`; a name without
  subdirectories is also searched for in subdirectories; `orchestrator/drafts.py`): the spec from it
  is written by the model (`orchestrator/spec_from_draft.py`, the prompt
  `prompts/spec_from_draft/`) based on the template or the `--ref` spec. Everything from
  the draft the model keeps, and what isn't in it — it fills in itself and may
  propose its own (solutions, contestant mistakes, tests); each such
  value is marked in the spec with a `# PROPOSED, REVIEW ME: ...` or
  `# SUGGESTION: ...` comment and printed as a note. The `problem_id` and the reference to
  the base problem are set by the orchestrator on top of the response. The spec is written even if
  it doesn't pass validation yet (the errors are printed), the draft copy — to the problem
  directory's `authoring/draft.<extension>`, the log — to `llm_generation.log`.
  `init --draft` has no cache. If `spec.yaml` already exists, `init --draft`
  revises it, like the steps' "Revision mode": the model gets the current
  spec (with the author's manual edits) and the "before → after" of the draft — the previous
  copy `authoring/draft.*` against the new one (the shared block
  `prompts/revise.md.j2`); the draft didn't change — the model isn't called
  (`--force` — it is called); `--ref` during a revision is an error. Previous versions of
  the spec and the draft copy — in `.llm_backup/<time>/`, the spec diff — in the
  report. After the model writes or changes the spec, the critic reviews it
  (`orchestrator/spec_critic.py`, `.claude/rules/step-roles/8-spec_critic.md`):
  findings on the author's values go to the report, on the model's — removed
  mechanically or back to the model for `--critic-rounds` rounds (1 by default, at
  most 2); `--no-critic` turns it off. Without `--draft`, an existing `spec.yaml` is never
  overwritten. It refuses to create a problem inside
  another problem, around already existing problems and with a `problem_id` that
  is already taken by a problem in another directory of either root — all checks
  (both finding the draft and the `--ref` spec) before calling the model. It doesn't call
  Polygon; the model — only with `--draft` (the same restrictions on running without an
  explicit command as for `llm generate`).

`<problem>` is completed by Tab (`argcomplete`; the one-time shell setup — in
`docs/SETUP.md`): the candidates are the addresses of all problems in `problems/` and
`examples/`, with a spec or with only `polygon_state.json` (`polygon
status|pull` don't need a spec, and the command isn't typed yet at the moment of completing the address);
they are read on every press, nothing is cached. On every Tab the shell runs `cli.py`
in full, so the top level of `cli.py` must not import the pipelines
(pydantic, requests) — they are loaded lazily through `cli_commands.py`.
Completion only suggests: the full address of a problem or
group goes into the command (the candidates are only problems, but a group directory is completed as their common
prefix), the CLI doesn't accept abbreviated addresses.

Grouping problems into contests/rounds with shared defaults (TL/ML conventions,
a shared test-group style for the whole round) is deliberately not put into the spec
yet — there is no decision on this question yet. If it becomes necessary, the
cheapest extension is an optional `extends: <contest_id>.yaml` field in the
spec, whose values are merged into the spec as defaults before validation;
`SPEC_FORMAT.md` itself isn't rewritten for this, so as not to fix the format
prematurely.
