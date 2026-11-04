# Setup after cloning

## Required: enable the git hooks

Right after `git clone`, run in the repository root **once**:

```bash
git config core.hooksPath githooks
```

This is a local git setting (`.git/config`), it isn't committed and isn't
transferred on cloning — without it the hooks from `githooks/` **don't work**.

Check that the setting is applied:

```bash
git config core.hooksPath   # should print: githooks
```

### What `githooks/pre-push` does

Blocks `git push` if the current branch name or the message of any commit
not yet in `origin/main` contains the `problem_id` of one of the problems in `problems/` — the name
of a problem directory (with `authoring/spec.yaml` or `polygon_state.json`) at any nesting
depth; grouping directories aren't checked. This is a safety net for the rule from `CLAUDE.md`
("Confidentiality of problems/"): `problems/` is in `.gitignore`, but branch names
and commit messages are public.

- If `problems/` has no directories at all — the hook checks nothing.
- If `origin/main` is unavailable (e.g. the very first push) — only the
  branch name is checked.
- The comparison is by substring, case-insensitive.

When it fires, the hook writes to stderr which `problem_id` was found and where (the branch
or a commit hash). To fix: rename the branch
(`git branch -m <new-name>`) and/or rewrite the commit messages
(`git commit --amend` / `git rebase -i origin/main`).

## Optional: git blame without formatting commits

Mass formatting edits are listed in `.git-blame-ignore-revs`;
GitHub skips them on its own, local `git blame` has to be told once:

```bash
git config blame.ignoreRevsFile .git-blame-ignore-revs
```

## Optional: Java for checking the test-script and for Java solutions

The test-script check in `orchestrator <problem> local check` and before uploading in
`polygon push` expands the FreeMarker test-script with real FreeMarker, so
`java` is needed (JDK 11+, running `.java` without building):

```bash
sudo apt install default-jdk-headless   # or any other JDK 11+
```

The FreeMarker jar is downloaded from Maven Central on the first run into
`~/.cache/polygon-orchestrator/` (with a sha1 check); without a network — put it
there by hand or give its path in `ORCHESTRATOR_FREEMARKER_JAR`. Without `java`,
`polygon push` uploads the script without the check, with a warning.

The same JDK builds Java solutions in `local run` (running solutions on the
examples): `javac`, and if it isn't in `PATH` — the compiler from the
`jdk.compiler` module via `java -m`. Without a JDK, Java solutions don't run, with
an error for each.

## Optional: Tab completion of commands and problem addresses

`orchestrator` can complete by Tab the names of pipelines and commands, the values of
`--step` and the problem address (`problems/<problem path>`, `examples/<problem path>`).
Addresses aren't stored anywhere: on every Tab press they are read from the problem
directories in `problems/` and `examples/` (with a spec or with only
`polygon_state.json` — `polygon status` and `polygon pull` don't need a spec),
so a new problem is completed right away, without any regeneration.

Setup — **once** per machine, from the activated environment where
the orchestrator is installed (`pip install -e .` — pulls in `argcomplete`):

```bash
mkdir -p ~/.local/share/bash-completion/completions
register-python-argcomplete orchestrator \
    > ~/.local/share/bash-completion/completions/orchestrator
```

There's no need to edit `~/.bashrc`: the `bash-completion` package picks up the file
from this directory by the command name on the first Tab press in a **new**
terminal. The file is a static shell snippet, it doesn't depend on the repository's
contents and doesn't need updating when the commands or the problem list change.

For zsh instead — a line in `~/.zshrc` (after `compinit`):

```zsh
eval "$(register-python-argcomplete orchestrator)"
```

Check: `orchestrator <problem> polygon pu<Tab>` should complete to
`push`/`pull`.

Limitations:

- completion works only while `orchestrator` is available in `PATH` (that
  is, the environment is activated) — otherwise Tab just suggests nothing;
- `problems/` and `examples/` are looked up relative to the current directory, as in
  a normal run — complete the problem address from the repository root (or
  give `--base-dir` earlier in the same command);
- completion only suggests: the full problem name always goes into the command,
  the CLI doesn't accept abbreviated names.

## Generation: a Claude subscription (Claude Code CLI) or the Anthropic API

By default (`ORCHESTRATOR_LLM_BACKEND=claude-code` or the variable isn't
set) `orchestrator <problem> llm generate` runs the steps through the Claude Code CLI —
spending the Pro/Max subscription limits (the same as interactive
Claude Code and claude.ai: a 5-hour window + a weekly limit, the current
usage — `/usage` in Claude Code). Setup:

1. Install/update the CLI: a version with the `--system-prompt-file`
   and `--safe-mode` flags is needed (`claude update`; an old CLI fails with a hint
   to update). The VS Code plugin and the desktop app carry their own copies of the
   binary — the orchestrator calls `claude` from `PATH` or the binary from
   `CLAUDE_CODE_BIN`.
2. Make sure the CLI is logged in with the subscription: `claude`, then `/status`.
   Authorization is shared by all copies of Claude Code (`~/.claude/`).

`ANTHROPIC_API_KEY` in `.env` isn't needed by this backend and doesn't get in the way: the client
strips it from the `claude` process's environment, otherwise the CLI would go to
API billing.

The alternative — `ORCHESTRATOR_LLM_BACKEND=anthropic-api`: the same models through
the Anthropic API with `ANTHROPIC_API_KEY`, spending money from the API balance.
The models per class are the same for both backends, so switching doesn't invalidate the
step cache. If the subscription limit is exhausted, `llm generate` stops
at the current step — after the limit resets you can just run it again
(finished steps are skipped by the cache) or temporarily switch to
`anthropic-api`.
