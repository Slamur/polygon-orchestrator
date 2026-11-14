# polygon-orchestrator

An orchestrator for preparing competitive programming problems for [Polygon](https://polygon.codeforces.com).
From a structured problem spec (`authoring/spec.yaml`) it runs a chain of
generative steps — each step is a Claude model call plus a mechanical
check of the result — and produces drafts of the problem's artifacts:

- the statement in LaTeX (`statement/<language>/*.tex`) and examples;
- limits, TL/ML, test groups and the validator;
- generators (C++/testlib) and a FreeMarker test-script;
- solution drafts (ok / wa / tl);
- a custom checker and interactor — if the problem needs them.

Then all of this is uploaded to Polygon through the Polygon API (`polygon push`) or
downloaded from there (`polygon pull`). The result is a **draft**: the author
reviews it and finishes it by hand.

Detailed descriptions: [CLAUDE.md](CLAUDE.md) and [.claude/rules/](.claude/rules/) (architecture
and command behavior),
[docs/SPEC_FORMAT.md](docs/SPEC_FORMAT.md) (the spec format),
[docs/SETUP.md](docs/SETUP.md) (setup details).

---

## Contents

1. [Running on Windows](#running-on-windows)
2. [Installation (WSL2 + Ubuntu)](#installation-wsl2--ubuntu)
3. [First problem: from a draft to Polygon](#first-problem-from-a-draft-to-polygon)
4. [Command reference](#command-reference)
5. [What lies where](#what-lies-where)
6. [Working on problems together](#working-on-problems-together)
7. [Common problems](#common-problems)

---

## Running on Windows

The orchestrator is developed and tested on Linux. On Windows the recommended
way is **WSL2 with Ubuntu**: it's ordinary Linux inside Windows, all the instructions
below are for it.

| Option | Pros | Cons |
|---|---|---|
| **WSL2 + Ubuntu** (recommended) | Everything works as for the author, without code changes; VS Code opens the project through the WSL extension; Claude Code is installed right in WSL | WSL has to be enabled once |
| Docker | The same environment for everyone | On Windows, Docker Desktop runs on top of WSL2 anyway; the Claude Code login has to be passed into the container (`~/.claude`); Tab completion and git hooks are inconvenient; there's no image in the repository yet |
| Native Windows | No WSL | Not tested: needs `g++` (MSYS2), `bash` for the git hook and completion; running processes with timeouts and paths may behave differently |

---

## Installation (WSL2 + Ubuntu)

### 1. WSL and Ubuntu

In PowerShell **as administrator**:

```powershell
wsl --install -d Ubuntu-24.04
```

Restart the computer, open "Ubuntu" from the Start menu, set a user name
and password. From here on all commands are **in the Ubuntu terminal**.

> Work in the Linux home directory (`~/...`), not in `/mnt/c/...`:
> files on the Windows disk are read from WSL many times slower, plus there are
> problems with permissions and line endings.

If you use VS Code: install the **WSL** extension in Windows
(`ms-vscode-remote.remote-wsl`), then `code .` in the Ubuntu terminal opens the
project in VS Code, and the Claude Code extension works inside WSL.

### 2. System packages

```bash
sudo apt update
sudo apt install -y git python3 python3-venv python3-pip g++ default-jdk-headless bash-completion curl
```

What each one is for:

- `python3` (≥ 3.10) — the orchestrator itself;
- `g++` — compile-check of the generated C++ files (validator, generators,
  solutions, checker);
- `default-jdk-headless` (JDK 11+) — checking the test-script with real
  FreeMarker in `local check` and before `polygon push` (more — in item 5);
- `bash-completion` — Tab completion of commands and problem addresses.

### 3. Claude Code CLI (Pro/Max subscription)

By default the orchestrator calls models through the Claude Code CLI (`claude -p`),
so the usage comes from your subscription limits, not the API balance.

```bash
curl -fsSL https://claude.ai/install.sh | bash
# or: npm install -g @anthropic-ai/claude-code
```

Then log in once **with the subscription** (not with an API key):

```bash
claude          # a login link opens in the browser
# inside Claude Code: /status — should show your subscription, /exit — quit
```

Check that the CLI is in `PATH` and recent enough:

```bash
claude --version
claude update
```

A version with the `--system-prompt-file` and `--safe-mode` flags is needed; with an old one
the orchestrator fails with a hint to update. If `claude` isn't in
`PATH` — give its path in `CLAUDE_CODE_BIN` in `.env`.

### 4. The repository and the Python environment

```bash
cd ~
git clone https://github.com/Slamur/polygon-orchestrator.git
cd polygon-orchestrator

python3 -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
```

After that the `orchestrator` command is available in the activated environment.
The environment has to be activated in every new terminal
(`source .venv/bin/activate`), or add that line to `~/.bashrc`.

Check:

```bash
orchestrator --help
pytest               # unit tests; they don't call the model or Polygon
pytest --fast        # the same without the tests that build and run C++/Java (~5 times faster)
pytest -n auto --dist loadgroup  # in parallel (pytest-xdist), as in CI
ruff check .         # the linter (settings — in pyproject.toml); `--fix` fixes what it can
ruff format .        # code formatting
```

All three (formatting — in `--check` mode, tests — all of them, including `slow`)
run in CI on every PR (`.github/workflows/ci.yml`).

### 5. Java and FreeMarker (checking the test-script)

`local check` and `polygon push` (before uploading the test-script) expand
the script with **real FreeMarker** — the same engine Polygon uses — and look for
identical lines (with them Polygon refuses to generate tests) and lines
Polygon won't accept (quotes or an extra `>` in generator parameters). This
needs Java and the FreeMarker jar.

From the expanded script `polygon push` also waits for Polygon to generate
the tests: the test-script is uploaded at the last step, and the commit is made only
after the tests on Polygon match the script lines (up to 30 minutes).
Without Java, push relies only on Polygon's response to saving the script: if it
didn't wait for it — the step fails, and you have to commit by hand on Polygon, and then
run `polygon pull`.

**Java** (JDK 11+; the `.java` file is run without building, so it has to be
a JDK, not just a JRE) is installed together with the system packages from item 2
(`default-jdk-headless`). Check:

```bash
java -version        # must be 11 or newer
```

If you didn't install the package — `sudo apt install -y default-jdk-headless` (any
other JDK 11+ works too, as long as `java` is in `PATH`).

**FreeMarker** doesn't need to be installed separately: on the first check the orchestrator
downloads `freemarker-2.3.33.jar` from Maven Central itself (with a sha1 check) into
`~/.cache/polygon-orchestrator/`. To download it right away and make sure
everything works:

```bash
python -c "from orchestrator.checks.test_script_check import freemarker_jar; print(freemarker_jar())"
```

Without access to Maven Central (proxy, offline) — download the jar by hand
(`https://repo1.maven.org/maven2/org/freemarker/freemarker/2.3.33/freemarker-2.3.33.jar`)
and either put it in `~/.cache/polygon-orchestrator/freemarker-2.3.33.jar`,
or give its path in `.env`: `ORCHESTRATOR_FREEMARKER_JAR=/path/to/freemarker-2.3.33.jar`.

Without Java or the jar, `local check` reports that the check can't be run, and
`polygon push` uploads the script without the check, with a warning.

### 6. The git hook (if you will send code changes)

```bash
git config core.hooksPath githooks
```

The `pre-push` hook won't let you push a branch or a commit whose name/message
contains the `problem_id` of your problem from `problems/` or the name of a draft from
`drafts/` — the repository is public, the problems aren't. More — in
[docs/SETUP.md](docs/SETUP.md) and in the "Confidentiality of problems/" section of
[CLAUDE.md](CLAUDE.md).

### 7. Polygon keys and `.env`

```bash
cp .env.example .env
```

In Polygon: **Settings → API keys → Create** — get the `key` and `secret` and
put them into `.env`:

```dotenv
ORCHESTRATOR_LLM_BACKEND=claude-code
POLYGON_API_KEY=your_key
POLYGON_API_SECRET=your_secret
POLYGON_API_BASE_URL=https://polygon.codeforces.com/api
```

`.env` is in `.gitignore` and isn't committed anywhere. `ANTHROPIC_API_KEY` is needed
only with `ORCHESTRATOR_LLM_BACKEND=anthropic-api` (the same models through
the Anthropic API, money from the API balance instead of the subscription).

Problems created through `polygon push` appear in **your** Polygon
account.

### 8. Tab completion (optional)

From the activated environment, once:

```bash
mkdir -p ~/.local/share/bash-completion/completions
register-python-argcomplete orchestrator \
    > ~/.local/share/bash-completion/completions/orchestrator
```

In a new terminal `orchestrator examples/<Tab>` completes problem addresses,
`orchestrator <problem> polygon pu<Tab>` — commands. Complete from the repository
root.

---

## First problem: from a draft to Polygon

> ⚠️ The `init --draft` and `llm generate` commands call the model and spend the
> subscription limits (shared with your regular work in Claude Code and claude.ai).
> A rerun without changes does **not** call the model — the results
> are cached by the input hash.

All commands — from the repository root, with `.venv` activated.

**1. The draft.** Describe the problem in free form in a file in `drafts/`, e.g.
`drafts/my-task.md` (a subdirectory works too, `drafts/icpc/my-task.md`): the gist, input/output, limits, the solution idea, typical
mistakes — whatever you know. To understand what the draft will turn into, look at the
specs of the sample problems: `examples/*/*/authoring/spec.yaml`.

**2. The spec from the draft.**

```bash
orchestrator problems/my-task init --draft my-task
# with a sample problem as a sample of the format and files:
orchestrator problems/my-task init --draft my-task --ref examples/array/prefix-sums
```

The model writes `problems/my-task/authoring/spec.yaml`. Everything it added
itself is marked `# PROPOSED, REVIEW ME: ...` or `# SUGGESTION: ...` —
**read the spec**, fix it and remove the marks. The report — in
`problems/my-task/llm_reports/spec_from_draft.md`. Changed the draft — the same
command revises the spec rather than writing it anew: the edits in the spec
stay.

Then a second model — the critic — reads the spec without the draft and asks
"why is this here? can it be simpler?". It does that through four narrow lenses,
one model call each: `statement` (wordy sketches), `limits` (extra variables and
guarantees), `tests` (extra generators, test ideas, groups) and `solutions` (extra
solutions, wrong approaches, a custom checker; the statement lens may also note an
important case the examples don't show — that's never applied, only listed for you
under "Possibly missing"). `--critic-lens NAME` (repeatable)
runs only the named ones. Its findings on what the model added
itself are applied right away (a suggested item is removed, the rest goes back to
the model for one revision); its findings on what you wrote are only listed in the
report, under "Your values — for your review". `--no-critic` skips it,
`--critic-rounds 2` allows a second round. For a spec you wrote by hand (or
edited a lot since), `llm critic [--lens NAME]` runs the same critic as a report only.

Without `--draft` an empty spec is created from the template (or a copy of the `--ref` spec),
which is filled in by hand following [docs/SPEC_FORMAT.md](docs/SPEC_FORMAT.md).

**3. Generation.**

```bash
orchestrator problems/my-task llm status     # what will be run (free)
orchestrator problems/my-task llm generate   # all steps in order
```

After each step look at its report `problems/my-task/llm_reports/<step>.md`:
it has the model's notes (`uncertain` — what's missing in the spec, `proposed` —
assumptions to check, `suggestion` — ideas beyond the spec),
compilation errors and diffs.

If a step returned `uncertain` — the pipeline stops: add to the spec
what the model asks for, and run `llm generate` again (finished steps
are skipped by the cache). Edited the spec — only the steps the edit affects
are rerun; the current files aren't rewritten from scratch but
revised (including with your manual edits).

**4. Checking and uploading to Polygon.**

```bash
orchestrator problems/my-task local check    # statement formatting, duplicates and quotes in the script
orchestrator problems/my-task local check --full  # also generate tests: validator, identical tests
orchestrator problems/my-task local run      # validator and solutions on the examples
orchestrator problems/my-task local run --full  # also on all tests from the script
orchestrator problems/my-task local fix      # automatically fix what can be fixed
orchestrator problems/my-task polygon push   # create the problem on Polygon and upload
```

Then — tests, the package and checking solutions in Polygon by hand. If you edited
the problem in Polygon — `polygon pull` before the next `push`.

---

## Command reference

General form:

```
orchestrator [--base-dir DIR] <problem> <init|llm|local|polygon> [command] [flags]
```

`<problem>` — the path of the problem directory **together with the root**: `problems/<path>`
(your problems, at any depth: `problems/contest-2026/round-1/my-task`) or
`examples/<path>` (samples). `problem_id` (the problem's name on Polygon) is derived
from the path: for `problems/` — the name of the last directory (`my-task`), for
`examples/` — the path joined with `--` (`array--prefix-sums`).

Instead of a problem you can give a **group** — a directory above problems or the root itself
(`problems/contest-2026`, `examples`): the command runs for all problems under
it in turn. The directory is given in full, a name prefix doesn't work
(`problems/cont` isn't a group). First the list of found problems is printed
(`problem_id` and path) and a confirmation is asked; only
`llm status` and `polygon status` work without it. A failure of one problem doesn't stop the rest —
at the end it prints which problems didn't pass. Doesn't work with `init` and `polygon
pull --polygon-id`: they are about one problem.

`--base-dir` — the directory containing `problems/` and `examples/` (by
default the current one).

### `init` — create a problem

| Command | What it does | Model |
|---|---|---|
| `init` | The problem directory + `authoring/spec.yaml` from the template with comments | no |
| `init --ref <problem>` | The spec is a copy of another problem's spec; that problem is also added to `references` as a sample for the steps | no |
| `init --draft NAME` | The spec is written by the model from the draft `drafts/NAME` (`NAME` — a path in `drafts/`, with or without the extension; a name without subdirectories is also searched for in them; with `--ref` — based on it); the draft copy — in `authoring/` | **yes** |
| `init --draft NAME` (the spec already exists) | The model revises the current spec (manual edits are kept) by what changed in the draft since its copy in `authoring/`; the draft didn't change — the model isn't called (`--force` — call it anyway); previous versions — in `.llm_backup/` | **yes** |
| `init --draft ... --no-critic` / `--critic-rounds 2` / `--critic-lens NAME` | Without the critic over the written spec / with up to two rounds of critic + revision (one by default; the critic calls the model too, once per lens) / only the named lenses (`statement`, `limits`, `tests`, `solutions`; repeatable; all by default) | **yes** |

Without `--draft`, an existing `spec.yaml` isn't overwritten.

### `llm` — generative steps

The steps in order: `statement_draft` → `constraints_pick` →
`limits_from_constraints` (mechanical, without the model) → `tests_plan` (the test
plan) → `generators_draft` (generators from the plan) → `test_script_draft`
(the test-script from the plan) → `checker_draft` (only with
`checker.custom_needed: true`) → `interactor_draft` (only for interactive problems)
→ the solutions in stages, each checked by a local run of its solutions on the
examples and the tests from the script: `solutions_main` → `run_main` →
`solutions_ok` → `run_ok` → `solutions_slow` → `run_slow` → `solutions_wrong` →
`run_wrong` → `tests_survivors` (tests for the wrong solutions no test catches; after it
the generators, the script and the runs again). Separately, only via the `llm tutorial` command, —
`tutorial_draft` (the tutorial from a draft).

| Command | What it does | Model |
|---|---|---|
| `llm status` | A "step → cache hit / stale / not run / uncertain / conflict" table; for `stale` — what exactly changed | no |
| `llm generate` | A run of all steps; those valid by the cache are skipped | **yes** |
| `llm generate --step NAME` | Only one step | **yes** |
| `llm generate --force` | Ignore the cache, revise the current files again | **yes** |
| `llm generate --fresh` | From scratch, without showing the model the current files | **yes** |
| `llm generate --survivor-rounds N` | Up to N rounds of tests for uncaught wrong solutions (default 1, 0 — none) | **yes** |
| `llm tutorial [--ref SOLUTION ...]` | `statement/<language>/tutorial.tex` from `authoring/draft-tutorial.md`: the model expands your thoughts and adds only what you explicitly ask for ("prove", "spell out"). `--ref` (can be repeated) — files from `solutions/` as context; the list is remembered, new `--ref`s replace it, `--no-ref` clears it. Has `--force` and `--fresh` | **yes** |
| `llm critic [--lens NAME ...]` | The critic reviews the spec as it is: what to remove, simplify or merge (and what the statement examples may be missing) — into `llm_reports/spec_critic/<lens>.md`, a report per lens (`--lens` rewrites only the reports of the lenses it runs); the spec isn't changed, every finding is for you (values still marked `PROPOSED` / `SUGGESTION` are listed separately). One model call per lens (`statement`, `limits`, `tests`, `solutions`; `--lens` — only the named ones). No cache: every run calls the model | **yes** |
| `llm apply [--step NAME]` | Apply a saved but unapplied model response (e.g. after a conflict or an error) | no |
| `llm accept [--step NAME]` | Mark the current files of `stale` steps up to date (if the edit shouldn't change the result, or you've already fixed the files by hand); what exactly changed for each step — in `llm_reports/accept.md` | no |

What's important to know:

- **Cache.** A step doesn't call the model if its part of the spec,
  the results of the previous steps, the prompts, the samples and the model didn't change.
- **Revision.** If the step's files already exist, the model gets them (with your
  manual edits) and "what changed in the input" and revises them.
  Previous versions — in `.llm_backup/<time>/`. Files the model
  proposes to delete become `<name>.removed` — you delete them yourself.
- **Conflicts.** What can't be applied is set aside in
  `.llm_conflicts/<step>-<time>/` with `CONFLICTS.md`. Sort it out, delete the
  directory and run `llm accept` (or `llm generate --force`).
- **Subscription limit exhausted** — `llm generate` stops at the current
  step; after the limit resets just run it again.
- **A local run of the solutions failed** (`run_main`, `run_ok`, `run_slow`,
  `run_wrong` — after each solution stage, `local run --full` over its solutions).
  The pipeline stops; `llm_reports/<run>.md` says which solution got which verdict
  on which test. Either fix the files by hand, or write what the model should know at
  the end of the report and run `llm generate` again: the stage revises its solutions
  with the report. An error of the problem itself (the validator rejects a test, the
  checker fails) isn't given to the model — fix the tests, the checker or the spec.
  A wrong solution no test from the script catches is only reported.

### `local` — checks without the model and the network

| Command | What it does |
|---|---|
| `local check` | Checks the statement (Markdown lists, quotes other than `<<…>>`), duplicate lines and parameters with quotes/`>` in the expanded test-script (needs `java`) |
| `local check --full` | The same plus local generation of tests from the script: the validator on every test and a search for tests identical in content (including with the examples) |
| `local run` | Build the validator and solutions and run them on the examples (longer than `check`) |
| `local run --full` | The same plus all tests from the script, with answers from the main `ok` solution: `wa`, `re`, `il` must get their verdict at least once on a test from the script |
| `local clear` | Delete local tests and generator builds (`.tests_cache/`); doesn't touch the model's step cache, the Polygon state and backups |
| `local fix` | Fix what `check` found: lists → `itemize`/`enumerate`, quotes → `<<…>>`; a backup in `.fix_backup/` |

`local check --full`: identical tests are only shown, with the commands and script
lines, they aren't considered an error. Tests and generator builds lie in `.tests_cache/`, they
can be deleted; the number, file and command of each test — in `.tests_cache/manifest.txt`.
Only new script lines and tests of changed generators are generated again.
The tests are generated by a different compiler than on Polygon, so in rare cases
(`long`, `std::shuffle`, the order of `unordered_*`) they may differ from the Polygon tests.

`local run --full`: `wa`, `re`, `il` that never got their verdict on a test from the
script — an error: the tests don't catch the declared mistake (or the solution is
actually correct); if only an example catches it — a warning, the mistake still needs
a test of its own. `tl` without TL and `ml` without RE — only a warning, because the
time and memory are local. Such solutions stop at the first declared verdict on a test
from the script, `ok` and `tl_ok` go to the end. On a
problem with many solutions and large tests the run may take many minutes.

`local run`: each solution from `solutions/` is built and
run on `statement/examples/example_<N>.txt`, the answer is checked by the problem's
checker (its own `checker.cpp` or the standard one from `checker.standard`), the answers —
the `output` of the examples in the spec (if there isn't one — the output of the main `ok` solution).
A solution with verdict X in its name may get OK or X on an example (`tl_ok` —
OK or TL); anything else is an error. TL — from `limits.yaml`, the time is measured
locally and may not match Polygon. Memory isn't measured, only capped: C++ and Python
solutions get the ML plus 64 MB of address space, Java — `-Xmx`; a solution over it
gets RE. A solution without the `tl` verdict that
ran longer than half the TL goes into the warnings. Java solutions need a JDK
(`default-jdk-headless`). For an interactive problem the solution runs together
with `interactor.cpp`, the checker checks what the interactor wrote to `tout`, and
the answer is the `tout` of the main `ok` solution. A solution that doesn't flush
its output and hangs waiting for an answer gets IL — allowed only for `il`.
If an example has its own input/output for the statement
(`example_<N>.input_for_statement.txt` / `.output_for_statement.txt`, they are
downloaded by `polygon pull`), it is checked too: the input — by the validator, the output — by the
checker; for an interactive problem the dialogue from the statement is replayed with the
interactor. Polygon's "verify input/output for statements" flag is stored in
`example_<N>.verify_for_statement.txt` (the file exists — the flag is on).

### `polygon` — syncing with Polygon

| Command | What it does | Network |
|---|---|---|
| `polygon status` | Which polygon steps are done / outdated — by the local files | no |
| `polygon push` | Create the problem (if there isn't one) and upload only what changed since the last sync; after each step — a commit on Polygon | **yes** |
| `polygon push --step NAME` | Only one step (`create_problem`, `upload_statement`, `upload_solutions`, …) | **yes** |
| `polygon push --force` | Upload everything, even if the problem was changed on Polygon (overwrites those edits) | **yes** |
| `polygon push --no-commit` | Upload without commits (all steps or one, with `--step`; can be run several times in a row): everything stays in the working copy on Polygon. Then commit there in one batch and run `polygon pull` | **yes** |
| `polygon pull` | Bind to an existing problem on Polygon (by name = `problem_id`), compare and download changes; no spec needed | **yes**, read-only |
| `polygon pull --polygon-id N` | Pin the problem by its numeric id if there are several with that name | **yes** |
| `polygon pull --overwrite` | Also replace local files that changed on both sides; a backup in `.pull_backup/` | **yes** |

`push` stops if the problem on Polygon was changed bypassing the orchestrator
(or there are uncommitted edits there), and suggests running `pull` first.

The problem's tutorial is the optional `statement/<language>/tutorial.tex`: you write it or
the model does from your draft (`llm tutorial`). `polygon push` uploads it together with the statement,
`polygon pull` downloads the tutorial if it exists on Polygon. Deleted the file —
the next push clears the tutorial on Polygon.

A statement in several languages. The statement of each language lies in its own directory
`statement/<code>/` (`statement/ru/`, `statement/en/`, `statement/kk/`; a Polygon
language without a code in the `orchestrator/statement_languages.py` table — a directory with
its Polygon name): `legend.tex`, `input_format.tex`, `output_format.tex`,
`notes.tex` (and `interaction.tex`, `tutorial.tex`) and `name.txt` with the name;
the examples are shared and lie only in `statement/examples/`. The main language is the spec's
`language` field (`ru` by default, or `en`): the model writes in it
(`llm generate`, `llm tutorial`). The other languages are written by you: `polygon push`
uploads each language separately, `polygon pull` lays out the languages from Polygon into
the same directories. Deleted a directory — the statement in that language stays on Polygon,
delete it there by hand. A problem whose statement lies directly in
`statement/` is moved into `statement/<main language>/` by the very first command on it.

### Environment variables (`.env`)

| Variable | Value |
|---|---|
| `ORCHESTRATOR_LLM_BACKEND` | `claude-code` (default, subscription) or `anthropic-api` |
| `CLAUDE_CODE_BIN` | the path to `claude` if it isn't in `PATH` |
| `ORCHESTRATOR_LLM_TIMEOUT` | the limit of one `claude -p` call in seconds (1800 by default); didn't fit — a step error, nothing is written to disk |
| `ANTHROPIC_API_KEY` | only for `anthropic-api` |
| `POLYGON_API_KEY`, `POLYGON_API_SECRET` | Polygon API keys |
| `POLYGON_API_BASE_URL` | `https://polygon.codeforces.com/api` |
| `ORCHESTRATOR_FREEMARKER_JAR` | the path to the FreeMarker jar if there's no network (otherwise it downloads itself into `~/.cache/polygon-orchestrator/`) |
| `ORCHESTRATOR_SKIP_SCRIPT_CHECK=1` | turn off the test-script check before `push` (and waiting for test generation from it) |

---

## What lies where

```
drafts/                    problem drafts for init --draft (not in git)
problems/<problem path>/   your problems (not in git)
  authoring/spec.yaml        the spec — the input of all steps
  authoring/constraints.yaml limits, groups, what the validator checks
  authoring/tests_plan.yaml  the test plan: generators and script tests (can be edited by hand)
  authoring/draft-tutorial.md a free-form tutorial draft for llm tutorial
  statement/<code>/          the statement (.tex) and tutorial (optional tutorial.tex) in a language: ru/, en/, …
  statement/examples/        examples — shared by all languages
  limits.yaml                the actual TL/ML (can be edited by hand)
  validator.cpp, checker.cpp, interactor.cpp
  generators/*.cpp, test_script, test_script_groups
  solutions/*
  llm_reports/<step>.md      the report of the step's last run — read it first
  llm_reports/accept.md      what the last llm accept accepted
  llm_generation.log         the full log of llm runs
  polygon.log                the full log of push/pull
  polygon_state.json         the binding to the problem on Polygon
examples/<kind>/<gist>/    sample problems — samples for --ref and references
templates/                 shared templates: problem_lib.h, testlib.h, rules (tutorials/*.md)
prompts/                   step prompts
orchestrator/              code
```

`problems/` and `drafts/` are in `.gitignore` on purpose: the repository is public, and
the problems aren't, until the contest has taken place.

---

## Working on problems together

Problems aren't stored in git, so the easiest way to exchange them is through
Polygon:

1. The problem's owner gives access on Polygon (**Manage access**, the
   `WRITE` right).
2. The second participant: `orchestrator problems/<path>/<problem_id> polygon pull` —
   the problem is found by name, its files are downloaded, and from Attachments — the spec,
   `constraints.yaml`, the test plan and the draft.
3. Then the usual cycle: edits → `llm generate` / by hand → `polygon push`.
   If another person managed to push something, `push` stops and asks to
   `pull` first.

The step cache (`.cache/`) isn't transferred through Polygon: after `pull` the steps are
`not run`. If the files are already fine and don't need regenerating —
`llm accept` instead of `llm generate`.

---

## Common problems

- **`orchestrator: command not found`** — the environment isn't activated:
  `source .venv/bin/activate`.
- **`claude` not found / requires an update** — `claude --version`,
  `claude update`; or the path in `CLAUDE_CODE_BIN`. The VS Code plugin carries its own
  copy of the binary, but the orchestrator uses the one in `PATH`.
- **The model spends API money instead of the subscription** — check
  `ORCHESTRATOR_LLM_BACKEND=claude-code` and that `claude` is logged in with the subscription
  (`/status`). `ANTHROPIC_API_KEY` in `.env` doesn't get in the way — the orchestrator
  doesn't pass it to `claude`.
- **`local check` says there's no java** — `sudo apt install
  default-jdk-headless`.
- **`local check` can't download the FreeMarker jar** — no access to Maven
  Central; download the jar by hand and set `ORCHESTRATOR_FREEMARKER_JAR`
  (see installation item 5).
- **Everything is very slow in WSL** — the project lies in `/mnt/c/...`; move it to
  `~/`.
- **`push` refuses: the problem was changed on Polygon** — `polygon pull`,
  resolve the conflicts, then `push` again. `--force` — only if you really
  need to overwrite the edits on Polygon.
- **A step is constantly `uncertain`** — the spec lacks something the model has no
  right to invent itself (asymptotics, answer precision, the comparison rule
  in the checker…). What exactly — in `llm_reports/<step>.md`.

### If you work in the repository through Claude Code

[CLAUDE.md](CLAUDE.md) is picked up automatically, the topic rules from
[.claude/rules/](.claude/rules/) — when the work touches their files. Claude Code in this
project writes the orchestrator's code, not problem content, and **doesn't run**
`llm generate` / `init --draft` without an explicit command — they spend the subscription
limits.
