# CLAUDE.md — Polygon problem-prep orchestrator

## What this repository is

A Python orchestrator that takes a structured problem spec
(`problems/<problem path>/authoring/spec.yaml`, format — see `docs/SPEC_FORMAT.md`) and runs
it through a sequence of **generative steps**. Each step is a separate
LLM call over an API (not through Claude Code) plus a minimal mechanical
check of the result. The result is drafts of Polygon problem artifacts
(the `.tex` statement, limits/test groups, generators + a FreeMarker script,
optionally — solution drafts), which the author then reviews and
finishes by hand in Polygon.

**Claude Code does not generate problem content.** Claude Code's role is to
write/maintain the orchestrator itself, prompt templates, spec parsers,
the compile-check/timeout runner. Problem content is produced by a separate model
that the orchestrator calls over an API.

## Language

Everything committed to git — code, comments, docstrings, CLI messages,
docs, rules, prompts, commit messages and PRs — is written in English.
Problem content is the exception: statements and tutorials are written in
the spec's `language` (and in other Polygon languages by the author).
Generated C++ files (validator, checker, interactor, generators,
solutions) contain only ASCII — some compilers, Polygon's among them,
choke on non-ASCII characters in source files.

## Scope (current)

In scope:
- a spec draft from the author's free-form draft
  (`orchestrator <problem> init --draft`) — the model keeps
  everything the author wrote and fills in what is missing itself, marking it in
  the spec; critic models then review the spec without the draft, one narrow
  lens each (statement, limits, tests, solutions), and propose only removals and
  simplifications (`orchestrator/spec_critic.py`) — and, report only, gaps in the
  statement examples; the author
  reviews the spec before the generative steps; `orchestrator <problem> llm critic`
  runs the same critic over a spec as it is (e.g. written by hand), as a report only;
- a LaTeX statement draft — in one language, the main one (the spec's `language`,
  `ru` by default, or `en`), in `statement/<code>/`; statements in other Polygon
  languages (their own `statement/<code>/`) are written by the author, the orchestrator only syncs them
  with Polygon (`upload_statement`, `polygon pull`), see `docs/SPEC_FORMAT.md`,
  "Statement languages", and `orchestrator/statement_languages.py`;
- choosing limits (N/TL/ML, test groups) — **only based on the
  asymptotics given explicitly by the author in the spec**, without deriving
  complexity from the legend text on its own;
- a test plan (which generators and tests are needed), then generators from it
  (C++/testlib) and a FreeMarker test-script;
- optional drafts of author solutions (ok/wa/tle) for later
  manual review by the author;
- an optional custom checker draft (C++/testlib, following the
  read_and_check pattern — see `checker.cpp` of the sample problem in `examples/`), only if
  `checker.custom_needed: true` in the spec; if a standard
  checker is used (`checker.standard`) — this step does not run, there is nothing
  to generate;
- interactive problems (the `interactor` section of the spec): the interaction
  protocol in the statement (`interaction.tex`, example dialogues) and
  an interactor draft (C++/testlib), only if the problem is interactive;
- a tutorial draft `statement/<code>/tutorial.tex` from the author's draft
  `authoring/draft-tutorial.md` (`orchestrator <problem> llm tutorial`): the model
  expands the author's thoughts and adds only what they explicitly ask for;
- syncing with Polygon through the Polygon API (`orchestrator/polygon/`):
  creating the problem, TL/ML, `problem_lib.h`, validator, checker, interactor,
  statement with tutorial and examples, generators, test-script, solutions and
  `authoring/` files in Attachments; only what changed since the last
  sync is uploaded, after each step that uploaded something — `problem.commitChanges`;
  the reverse direction — `polygon pull`. Details — `.claude/rules/polygon.md`.
  The remaining integration steps (other files, packages) — later.

Out of scope (deliberately, to be added separately later):
- a full judging loop (distinguishing TLE/MLE/RE, batch stress testing,
  comparing solutions against each other on real limits).

The minimal safety net instead of a judging loop at this stage:
- **compile-check** after each generated C++ file
  (validator/checker/generator/solution) — compiles or not, without running;
- **non-ASCII check** of each generated source file (C++, Java, Python,
  test-script) — lines with non-ASCII characters are reported next to the
  compile-check errors (`orchestrator/checks/source_charset.py`): some
  compilers, Polygon's among them, fail on them; the file is still written;
- **solutions on examples** (`local run`) — running the validator and solutions
  only on the statement examples, checking that the verdict is compatible with the
  solution's name;
- **local tests** (`local check --full`, `local run --full`) — generating
  tests from the script, the validator on them, finding identical tests and running
  solutions, checking that the declared verdict is reached; local timing,
  without distinguishing MLE and without comparing solutions against each other;
- a **timeout wrapper** around any run of a generated binary
  (e.g. running a generator for a smoke test) — just kill the process on
  timeout, without analyzing TLE/MLE separately.

## Where things are described in detail

Detailed rules are split by topic in `.claude/rules/` and are loaded
automatically when the work touches files from a rule's `paths:`. If
you edit code that a rule does not formally cover, but whose behavior it
concerns, — read the rule yourself:

| Topic | File |
|---|---|
| common to the generative steps: revision mode, conflicts, hash cache, escalation on uncertainty, suggestions beyond the spec | `.claude/rules/steps.md` |
| model classes and effort per step, `LLMClient` clients | `.claude/rules/models.md` |
| the role of a particular step (input, output, checks) — one file per item | `.claude/rules/step-roles/<N>-<step>.md` |
| addressing problems and groups, the `authoring/` layout, `init`, Tab completion | `.claude/rules/cli.md` |
| `llm` commands (generate, tutorial, status, apply, accept), logs and reports | `.claude/rules/llm-commands.md` |
| `local` commands (check, run, fix, clear) | `.claude/rules/local-checks.md` |
| polygon steps, `polygon push|status|pull`, the sync base | `.claude/rules/polygon.md` |
| `templates/`, `examples/`, spec references | `.claude/rules/materials.md` |
| spec format | `docs/SPEC_FORMAT.md` |
| prompt templates | `docs/PROMPTS.md` |
| Polygon API (check parameter names against it, don't guess) | `docs/POLYGON_API.md` |
| installation, command reference for the user | `README.md`, `docs/SETUP.md` |

Comments in code and documents refer to sections as `CLAUDE.md,
"<section>"` — these sections moved to `.claude/rules/` with the same headings:
"Role of each generative step" — `step-roles/`, item N — in
`step-roles/<N>-<step>.md`; "Revision mode", "Conflicts",
"Caching by spec hash", "Escalation rule on uncertainty" —
`steps.md`; "Model routing by step criticality" — `models.md`; "Running" —
`cli.md` (`llm` commands — `llm-commands.md`, `local` — `local-checks.md`,
`polygon` — `polygon.md`);
"How to use the attached materials" — `materials.md`. "Scope (current)",
"The minimal safety net…", "Directory structure" and "Confidentiality of
problems/" stayed here.

The rules in `.claude/rules/` describe the orchestrator's behavior, just like this
file: if you change behavior — update the corresponding rule (and `README.md`, if
the user can see it) in the same PR.

## Directory structure

```
.
├── CLAUDE.md, README.md, docs/      # see "Where things are described in detail"
├── .claude/rules/                   # rules by topic, see above; step-roles/ — step roles
├── templates/                       # shared by all problems, the compile-check include dir; the author's templates —
│                                    # don't touch the structure: problem_lib.h, testlib.h, *_format.yaml,
│                                    # spec_template.yaml, test_script_functions.ftl, solutions/ (solution skeletons),
│                                    # tutorials/*.md (rules for the steps; polygon.md — reference, not attached to steps)
├── examples/<input kind>/<gist>/    # sample problems in the problems/<problem path>/ layout
├── prompts/<step>/{system.md,user.md.j2}  # + spec_from_draft/, revise.md.j2, missing_artifacts.md.j2
├── orchestrator/
│   ├── cli.py                       # parser and Tab completion; NO heavy imports (the shell runs it on every Tab)
│   ├── cli_commands.py              # command implementations, imported from cli.py lazily
│   ├── step_order.py                # step orders without importing the steps themselves
│   ├── problem_paths.py, drafts.py, statement_languages.py  # no heavy imports
│   ├── spec.py, spec_from_draft.py, spec_critic.py, references.py, limits.py
│   ├── steps/                       # generative steps (+ the mechanical limits_from_constraints), base.py — shared
│   ├── pipeline.py, cache.py, llm_report.py
│   ├── model_router.py              # model classes and effort, vendor-independent
│   ├── llm_client.py, model_contracts.py  # the LLMClient Protocol and ModelResponse
│   ├── llm_clients/                 # ClaudeCodeClient (default), AnthropicClient; __init__ imports nothing
│   ├── polygon/                     # client, signing, state, sync, pull, steps/, pipeline; __init__ imports nothing
│   └── checks/                      # compile_check, run_with_timeout, test_script_check, local_tests,
│                                    # statement_check, solutions_run, std_checkers/, FreemarkerRender.java
├── githooks/pre-push, .github/workflows/ci.yml  # ruff check, ruff format --check, pytest on every PR
├── tests/
├── drafts/                          # the author's drafts for `init --draft`; in .gitignore
└── problems/<problem path>/         # in .gitignore; at any depth
    ├── authoring/                   # spec.yaml, draft.<ext>, draft-tutorial.md, constraints.yaml, tests_plan.yaml
    │                                # (model and author only; on Polygon — in Attachments)
    ├── statement/<code>/*.tex, name.txt; statement/examples/example_<N>*.txt
    ├── limits.yaml, problem_lib.h, validator.cpp, checker.cpp, interactor.cpp
    ├── generators/*.cpp, test_script(_groups), solutions/*
    ├── polygon_state.json           # binding to Polygon + the sync base (not a cache)
    ├── .cache/                      # step cache <step>.json and unapplied responses <step>.response.json
    ├── llm_generation.log, polygon.log, llm_reports/<step>.md
    ├── .llm_backup/, .llm_conflicts/, .pull_backup/, .fix_backup/, .tests_cache/
    └── **/<name>.removed            # files proposed for deletion: the author deletes them
```

## Core principles

- Each generative step is a pure function (spec + results of previous
  steps) → artifacts + an uncertainty report. With the same input the model is
  not called (cache by the hash of the input, prompts, attached documents and the model);
  with a changed one — it revises the current files rather than writing from scratch; the response
  is applied like a `git merge`, whatever cannot be applied goes to conflicts.
- The model does not make up what the author sets: data is missing —
  `uncertain` (stops the pipeline), ambiguous — `status: proposed`
  (marked for manual review), its own ideas beyond the spec — `kind: suggestion`.
- Prompts are vendor-independent: model names and vendor specifics live
  only in `LLMClient` implementations, model classes — in `model_router.py`.
- `templates/tutorials/*.md` are mandatory rules for the model, not a reference;
  samples from `examples/` and `templates/` are extended, not reinvented.
- CLI: `orchestrator [--base-dir ...] <problem> <init|llm|local|polygon> ...`;
  the groups are split by cost: `llm` (and `init --draft`) calls the model,
  `polygon` goes to the network, `local` — neither.

## Git workflow for developing the orchestrator

`main` is the stable branch. No direct commits to `main`, including by
Claude Code — all orchestrator code (spec parser, steps, cache, CLI, prompts)
goes through a branch and a PR, even if there is only one reviewer (the author).

Rules for Claude Code:

1. **Never commit directly to `main`.** Before starting any work —
   create a branch from an up-to-date `main`: `git checkout main && git pull &&
   git checkout -b <branch>`.
2. **Branch naming**: `feature/<briefly-what-we-do>` for new
   functionality, `fix/<briefly>` for bug fixes, `chore/<briefly>` for
   routine (dependency updates, formatting). One logical piece of
   work — one branch (e.g. `feature/spec-validation`,
   `feature/cache-by-hash`, `fix/generators-compile-check`), don't mix
   unrelated changes in one branch/PR.
3. **Commits** — Conventional Commits style (`feat: ...`, `fix: ...`,
   `docs: ...`, `refactor: ...`, `chore: ...`), atomic: one commit —
   one meaningful change, not "accumulated everything and committed it in one chunk".
4. **Before push** — `ruff format .`, then `ruff check .` and `pytest
   --fast` must pass: CI (`.github/workflows/ci.yml`) checks
   all three on every PR, the tests — fully, including `slow` (building and
   running C++/Java). If the change touches building or running
   (`orchestrator/checks/`, the steps' compile-check, `.cpp` in `examples/` and
   `templates/`) — the full `pytest` before push too.
5. **When work on a branch is done** — push and open a PR via `gh`:
   `gh pr create --fill` (or with an explicit summary via `--body`). The PR title and
   description must reflect what was changed and why, not only "what" —
   in the spirit of the `docs/PROMPTS.md` contract (explicit,
   to the point, without guessing). Neither the branch name, nor the commits, nor the
   PR may contain a `problem_id` or spec details — see "Confidentiality of
   problems/" below.
6. **Claude Code does not merge PRs itself** unless explicitly asked. The default is
   the PR stays open and waits for the author's review; merge/squash — by hand on
   GitHub (or on an explicit "merge the PR" command).
7. If the task is clearly small and one-off (e.g. fixing a typo in
   `docs/`) — still through a branch+PR, there is no separate "fast path" around
   this rule, so as not to dilute the convention.

It is assumed that the `gh` CLI is already installed and authorized
(`gh auth login`) — without it step 5 won't work, and you will have to push the branch and
open the PR by hand in the web interface.

It is also assumed that `git config core.hooksPath
githooks` has been run in the clone (see `docs/SETUP.md`) — `githooks/pre-push` blocks the push if
the branch name or commit messages contain the `problem_id` of any problem in `problems/` (the name of a problem directory at any depth) or the name (without extension) of any draft in `drafts/` at any depth (except drafts of the sample problems from `examples/` — by the problem directory name or its `problem_id`).
The hook is a safety net, not a replacement for the rules in the section below.

## Confidentiality of problems/

`problems/` (together with the problem specs `problems/<problem path>/authoring/spec.yaml`) and
`drafts/` (problem drafts for `init --draft`) are in `.gitignore`
on purpose: the orchestrator repository
is public, and the problems themselves are not, until they are published at a
contest. This is the only barrier between "a problem draft" and "visible to
anyone with access to the repository".

But commits, commit messages, branch names, PR title and description,
code comments — are public just like the rest of the code, REGARDLESS
of .gitignore. If a problem's name or its details get into any of these
texts — .gitignore won't save anything, the leak has already happened.

STRICTLY FORBIDDEN in a commit message, branch name, PR title/description,
code comments and any other files committed to git:
- the `problem_id` of a particular problem being worked on, and the path of its
  directory in `problems/` (grouping directories like a contest, year,
  round — are also a problem detail; the pre-push hook doesn't catch them, only the `problem_id`);
- the contents of any spec fields (`legend_sketch`, `formal_input_sketch`,
  `algorithm_hint`, etc.) — including a retelling in your own words, not only
  verbatim quotes;
- any other details from which one could guess the problem's topic/plot/idea
  (even remote hints).

The same applies to tests and their fixtures (`tests/`): they use only
synthetic `problem_id`s (`example-problem`, `my-task`) and synthetic
specs, not copies or extracts of real files from `problems/`.

Branch names and commit messages are only about WHAT changes in the
orchestrator code, not about WHICH problem you tested it on.
Correct: `feature/validator-in-constraints-pick`. Wrong:
`feature/example-problem-validator-fix` (here `example-problem` stands in place of a real `problem_id` — don't do that).

If a PR description needs an example of behavior — use a synthetic
example (`problem_id: example-problem`, arbitrary `N`/statement), not a
real spec, even if a real one is already lying locally in `problems/<problem path>/` and
"it would be easier to show it on that".

This restriction is only on what gets into git/PRs. In a normal reply in the
terminal (not committed anywhere) mentioning the `problem_id` and spec details
is allowed and necessary — otherwise it is impossible to discuss a run of a particular
problem with you.

### Additionally: self-check before opening a PR

The rule above (no problem_id/spec details in git artifacts) has already
been violated in practice, even though it is stated explicitly.
So before `gh pr create` — a mandatory extra step:

1. Reread the final text of the PR title and description in full (don't
   trust "I remember there's nothing extra in there anyway").
2. Explicitly check: is there any mention that the change was
   tested/verified on a particular real problem — even without a
   name. Phrasings like "tested on a real problem about..."
   or "checked on an example with the limit N ≤ 10^6 and the answer modulo a
   prime" — are forbidden too, even if the problem_id is not named anywhere:
   the problem detail is recognizable on its own.
3. If it makes sense at all to mention in the PR that manual
   verification was done — write only "manually verified on a synthetic
   example (`example-problem`)", without any characteristics of the real
   problem, even remote ones.

If this rule is violated again — tell the user about it directly in the
next reply, rather than relying on them noticing it during the PR review.

## Manual runs with the real API — forbidden without an explicit command

`orchestrator <problem> llm generate` (with `--step`, `--force` or without),
`orchestrator <problem> llm tutorial`, `orchestrator <problem> llm critic` and
`orchestrator <problem> init --draft` call the real model and cost something every time: through `AnthropicClient.generate` — money
from the API balance, through `ClaudeCodeClient.generate` (`claude -p`) — the author's
subscription limits, shared with their interactive work. This is not "checking code"
in the usual sense — here any check = spent tokens.

STRICTLY FORBIDDEN for Claude Code on its own, without an explicit command from the user
in the current message, to run:
- `orchestrator <problem> llm generate ...`, `orchestrator <problem> llm tutorial ...`, `orchestrator <problem> llm critic` and `orchestrator <problem> init --draft ...` on any `problem_id` (real or synthetic);
- any other code that instantiates `AnthropicClient` and actually
  reaches `client.messages.create` — including "quickly check with one
  step", "smoke test", "let's run it and see what comes back";
- `claude -p` with a request to the model (by hand or through `ClaudeCodeClient`) —
  for the same reasons; `claude --version`/`--help` don't call the model and
  are allowed.

This applies even to situations where the motivation looks reasonable ("I want
to make sure the fix really works end-to-end") — initiative is
out of place here: the cost of a real call doesn't justify a run without asking.

Allowed without asking (doesn't call the model):
- `orchestrator <problem> llm status` — reads only the cache and files, doesn't go to the network;
- `orchestrator <problem> llm apply` — applies an already received model response, doesn't call the model
  (but writes problem files — run it at the author's request);
- `orchestrator <problem> llm accept` — writes only `problems/<problem path>/.cache/`, doesn't call the model
  (but changes the author's cache — run it at their request);
- `pytest` (integration tests are off by default with `-m 'not integration'`
  — don't touch `pyproject.toml`, so as not to enable it by accident);
- compiling and statically analyzing generated C++ files;
- unit tests with a mocked `LLMClient` (the Protocol in `orchestrator/llm_client.py`)
  or with a mocked `subprocess.run` for `ClaudeCodeClient`.

If debugging a bug requires a real end-to-end run — Claude Code must
explicitly ask and wait for a separate confirmation from the user in the chat
("run it", "run N", etc.). The wording of the question by itself does not
count as permission — an explicit answer to it in a separate message is needed.

## Not in the scope of this CLAUDE.md

- How exactly the Polygon API is called — see `docs/POLYGON_API.md` (check
  method parameter names against it, don't guess).
- The full judging loop (TLE/MLE distinction, stress tests on a pair of solutions) —
  a separate future task, not designed here.
