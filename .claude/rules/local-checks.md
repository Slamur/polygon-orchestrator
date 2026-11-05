---
paths:
  - "orchestrator/checks/**"
  - "tests/test_compile_check.py"
  - "tests/test_run_with_timeout.py"
  - "tests/test_local_tests.py"
  - "tests/test_solutions_run.py"
  - "tests/test_statement_check.py"
---

# `local` commands: checks without the model and without the network

Addressing problems and groups — in `.claude/rules/cli.md`.

- `orchestrator <problem> local check` — without the model and without the network:
  checks the statement's formatting (`statement/<code>/*.tex`, quotes — only in
  `ru`): Markdown-style
  lists (lines with `- `, `* `, `1. ` instead of `itemize`/`enumerate`) and
  quotes other than `<<…>>` (`"`, `“”`, `«»`, ```` ``…'' ````; outside
  formulas and comments) — with the file and line of each finding. It also
  expands the problem's test-script(s) with real FreeMarker (needs `java`;
  the FreeMarker jar is downloaded from Maven Central into
  `~/.cache/polygon-orchestrator/` on the first run) and prints identical lines — with
  them Polygon doesn't generate tests — indicating which script line
  printed each of them and with which values of the `<#list>` variables, and
  lines Polygon won't accept: quotes in generator parameters and
  `>` not exactly once (only the final `> $`); `local fix` doesn't
  fix them — that's a matter of the generator's parameters. The same
  check — before uploading in `upload_test_script`: a finding
  stops the push before the network; if the check can't be run or
  FreeMarker didn't expand the script — only a warning
  (`ORCHESTRATOR_SKIP_SCRIPT_CHECK=1` turns it off). Details — in the
  docstring of `orchestrator/checks/test_script_check.py`.
  With `--full` it also generates tests from the script locally, runs the
  validator on them and shows the ones identical in content, together with the examples, and
  the script lines corresponding to them (the exit code doesn't depend on them). The cache —
  `.tests_cache/`: a test's key is the hash of the command and of the build of its generators, so
  renumbering regenerates nothing, editing a generator — only the tests
  with it, tests with vanished keys are deleted (`manifest.txt` — the number, file
  and command of each). It doesn't run before push. This is an approximation: Polygon has
  a different compiler, and the tests may differ. Details — in the docstring of
  `orchestrator/checks/local_tests.py`.
- `orchestrator <problem> local run` — without the model and without the network: runs
  the validator and all solutions from `solutions/` on the examples
  (`statement/examples/example_<N>.txt`). Separate from `local check` and not run
  before push: building and running all solutions takes long and isn't always needed.
  Building: C++ — g++, Java — `javac` (without
  it — the same compiler from the `jdk.compiler` module via `java -m`), Python
  — `python3`; TL — from `limits.yaml` (without it — from `constraints.yaml`),
  the time — local wall-clock (a process killed by a signal — its CPU time: the
  elapsed one includes the system crash handler, apport takes over a second); ML —
  only a cap: the address space of C++ and Python — the ML plus 64 MB, Java —
  `-Xmx`, a solution over it gets RE (memory isn't measured: `ru_maxrss` of a
  child includes the orchestrator's own memory); the answer — the example's `output` in the spec, without it —
  the output of Main correct; the verdict is given by the problem's checker (`checker.cpp` or
  the standard one from `checker.standard`, sources — in
  `orchestrator/checks/std_checkers/`). A solution with verdict X in its name may
  get OK or X on an example (`tl_ok` — OK or TL), anything else is an error
  (`ALLOWED_VERDICTS`); a solution without the `tl` verdict that ran on an example
  longer than half the TL (and fit into it) — a warning. For an
  interactive problem the solution and `interactor.cpp` are connected with pipes,
  the checker judges the interactor's `tout`, the answer — the `tout` of Main correct (the examples' `output`
  in the spec are dialogue lines, not the answer); a solution that didn't finish
  within TL with a margin and spent less than half of the time on the CPU —
  IL (waiting for an answer: it didn't flush its output), otherwise TL; `il` allows OK or IL.
  The examples' input/output for statements are checked too: for a normal problem
  the input — by the validator, the shown output — by the checker against the answer of Main
  correct on the shown input; for an interactive one the dialogue is replayed with
  the interactor (its answers — token-wise as the input for statements, the verdict
  OK, `tout` passes the checker). A mismatch — in `errors`.
  With `--full`, tests from the script are added to the examples (as in `local check
  --full`) with answers from Main correct. The declared verdict is required here,
  and on the generated tests — the examples don't count: `wa`, `re`, `il` that
  never got it — an error (the tests don't catch them), got it only on the examples —
  a warning; `tl`, `ml` (RE) — a warning (the time and memory are local). These solutions
  stop at the first declared verdict on a generated test, `ok` and `tl_ok` go to the end.
  `SolutionsRunResult.summary()` — the same outcome as plain data (per solution: a status
  `expected` / `unexpected` / `survived` / `not_run` and the test that caught it) for
  the steps that act on the run.
  The output — as in `polygon push`: notes along the way and a line per
  each solution being run, at the end the groups `expected verdicts`,
  `unexpected verdicts`, `not run`, `warnings`, `errors`. This is a check, not
  a judging loop: without distinguishing MLE from RE. Details — in the
  docstring of `orchestrator/checks/solutions_run.py`.
- `orchestrator <problem> local fix` — without the model and without the network:
  fixes in the statement what `local check` found that can be fixed
  mechanically — Markdown lists into `itemize`/`enumerate` (nesting —
  by indentation), quotes into `<<…>>`; a paragraph with unpaired quotes isn't touched
  and they are printed for manual editing (exit code 1). Previous versions of
  changed files — in the problem directory's `.fix_backup/<time>/`; the next
  `polygon push` uploads the fixed version as an ordinary edit. Details — in the
  docstring of `orchestrator/checks/statement_check.py`.
- `orchestrator <problem> local clear` — without the model and without the network: deletes
  the local caches of the checks that they recreate themselves (`.tests_cache/`,
  `LOCAL_CACHE_DIRNAMES` in `orchestrator/checks/local_tests.py`). The model's step cache
  (`.cache/`), `polygon_state.json` and backups aren't touched.
