"""Running a problem's solutions on the examples — `orchestrator <problem> local run`.
Without the model and without the network; not a judging loop (CLAUDE.md, "Scope (current)"), but
a check that the solutions behave, at least on the examples, as the verdict
in their name claims. Separate from `local check`: building and running all solutions
takes long and isn't needed on every check.

What is done:

1. The tests — `statement/examples/example_<N>.txt` (what goes to Polygon as
   examples). The answer of the N-th one is the `output` of the spec's `sample_examples[N-1]`; for
   examples without it the answer is the output of Main correct (chosen as in
   `upload_solutions`), and a note about it is printed.
2. The validator (`validator.cpp`, if any) is run on the input of each
   example: an example it rejects is an error.
3. Each solution from `solutions/` is built once: C++ — g++ (as in
   compile-check, the process's stack raised to the hard limit), Java — `javac`,
   and without it in PATH — the same compiler from the `jdk.compiler` module via
   `java -m` (it exists wherever `java` runs `.java` without building), and
   run as `java -Xmx<ML>m -Xss64m`; Python — `python3` without building. The verdict
   on an example: TL — longer than the problem's TL (`limits.yaml`, without it —
   `authoring/constraints.yaml`; a process killed by a signal — by its CPU time,
   see `_over_time`), RE — a non-zero exit code, otherwise —
   the checker's verdict (`checker.cpp` with `checker.custom_needed`, otherwise
   the standard testlib checker from `checker.standard` — the sources are in
   `std_checkers/`, the same `testlib.h` from `templates/`).
4. What's allowed on the examples by the verdict in the solution's name —
   `ALLOWED_VERDICTS`: a solution with verdict X gets OK or X. Anything else is
   an error. A solution without the `tl` verdict that ran on an example longer than
   `SLOW_FRACTION` of the TL — a warning: on large tests it may not
   fit (for `tl_ok` — it shows how borderline it is).

The time is wall-clock on the local machine, including starting the JVM/interpreter:
compilers, flags and speed aren't those of Polygon, so the TL here is
a signal, not a Polygon verdict. Memory isn't measured (`run_with_timeout` says
why), only capped: the address space of C++ and Python solutions — at the ML plus
`_MEMORY_CAP_SLACK_MB` for the runtime, Java — `-Xmx<ML>m`; a solution over it gets RE
(a failed allocation, `OutOfMemoryError`). The address space isn't Polygon's
memory accounting either: this is a signal too.

An interactive problem (the `interactor` section in the spec, without a spec — `interactor.cpp`
exists): the solution and the interactor (`interactor.cpp`, testlib) are connected by
two pipes, the interactor reads the test and writes `tout` — which, as on Polygon,
is judged by the checker, and the answer is the `tout` of Main correct (the examples' `output` in the spec of
an interactive problem are the solution's lines in the dialogue, not the answer). The verdict:
the solution didn't finish within TL with a margin — IL, if it spent less CPU time
than half of the elapsed time (it was waiting for the interactor's answer, usually — it didn't
flush its output), otherwise TL; then — the interactor's verdict (WA/PE/FAIL), the solution's RE,
the checker's verdict.

An example's input/output for statements (`example_<N>.input_for_statement.txt`,
`example_<N>.output_for_statement.txt` — what is shown in the statement
instead of the test, see `upload_statement`) are checked too, regardless of the
verify flag on Polygon:

- a normal problem: the input for statements — by the validator; the shown output
  (the output for statements, without it — the example's answer) — by the checker against
  the answer of Main correct on the shown input (the input for statements, without it —
  the example's answer on the test's input);
- an interactive problem: the dialogue is replayed — the interactor gets the test and
  the solution's lines (the output for statements) at once, its answers to the solution must
  match the input for statements token by token, it must finish with OK, and
  its `tout` must pass the checker against the `tout` of Main correct.

A mismatch is an error (the `errors` group).

With `--full` (`full=True`), tests from the test-script are added to the examples
(`local_tests.generate_tests`, with a cache), on which the validator is
run too. The answer on them is the output of Main correct (for an interactive problem — its
`tout`): Main correct goes first, the other solutions aren't run on a test without an answer.
On the full set the declared verdict is required, not only
allowed (`REQUIRED_VERDICTS`), and only on the generated tests: the examples
don't count — a mistake the examples catch still needs a test of its own.
`wa`, `re`, `il` without it — an error (the tests don't catch the declared mistake),
unless an example catches it — then a warning; `tl`, `ml` — only a warning (local
time and memory aren't Polygon's). Such solutions stop at the first declared
verdict on a generated test: running to the end is needed only for `ok` and `tl_ok`,
for which every test matters. A stray verdict after the stop therefore won't be
noticed.

`SolutionsRunResult.summary()` — the same outcome in a machine-readable form: the
status of each solution (`STATUS_*`; `survived` — the declared verdict on no
generated test) and the test that caught it.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable, Collection
from dataclasses import dataclass, field, replace
from pathlib import Path

import yaml

from orchestrator.checks.compile_check import compile_check
from orchestrator.checks.local_tests import (
    EXAMPLES_SUBDIR,
    MANIFEST_FILENAME,
    TESTS_CACHE_DIRNAME,
    LocalTest,
    example_files,
    generate_tests,
    validate_tests,
)
from orchestrator.checks.run_with_timeout import (
    RunResult,
    resource_limits,
    run_with_timeout,
)
from orchestrator.checks.statement_check import STATEMENT_DIRNAME
from orchestrator.limits import LIMITS_FILENAME, LimitsError, parse_limits
from orchestrator.polygon.steps.base import PolygonStepError
from orchestrator.polygon.steps.upload_solutions import (
    _choose_main,
    _parse_solution_filename,
)
from orchestrator.polygon.sync import (
    CHECKER_FILENAME,
    INTERACTOR_FILENAME,
    VALIDATOR_FILENAME,
)
from orchestrator.problem_paths import (
    CONSTRAINTS_FILENAME,
    SPEC_FILENAME,
    find_authoring_file,
    is_removed_mark,
)
from orchestrator.steps.statement_draft import dialogue_filenames

VERDICT_OK = "OK"
VERDICT_WA = "WA"
VERDICT_PE = "PE"
VERDICT_TL = "TL"
VERDICT_RE = "RE"
# Idleness limit: an interactive problem's solution waits for an answer that won't come.
VERDICT_IL = "IL"
# The checker crashed or returned an unknown code — a checking error, not the solution's.
VERDICT_FAIL = "FAIL"

# The verdict from the solution's name -> what's allowed on the examples. `ml`: memory isn't
# measured here, a shortage shows up as RE (OutOfMemoryError in Java, a failed
# allocation over the address space cap in C++ and Python). `il` (Idleness limit)
# happens only in an interactive problem.
ALLOWED_VERDICTS: dict[str, frozenset[str]] = {
    "ok": frozenset({VERDICT_OK}),
    "wa": frozenset({VERDICT_OK, VERDICT_WA}),
    "tl": frozenset({VERDICT_OK, VERDICT_TL}),
    "tle": frozenset({VERDICT_OK, VERDICT_TL}),
    "tl_ok": frozenset({VERDICT_OK, VERDICT_TL}),
    "re": frozenset({VERDICT_OK, VERDICT_RE}),
    "ml": frozenset({VERDICT_OK, VERDICT_RE}),
    "mle": frozenset({VERDICT_OK, VERDICT_RE}),
    "il": frozenset({VERDICT_OK, VERDICT_IL}),
}

# The verdict from the solution's name -> what it must get at least once on the
# generated tests of the full set.
REQUIRED_VERDICTS: dict[str, frozenset[str]] = {
    "wa": frozenset({VERDICT_WA}),
    "re": frozenset({VERDICT_RE}),
    "il": frozenset({VERDICT_IL}),
    "tl": frozenset({VERDICT_TL}),
    "tle": frozenset({VERDICT_TL}),
    "ml": frozenset({VERDICT_RE}),
    "mle": frozenset({VERDICT_RE}),
}
# Solutions for which a missed required verdict is only a warning: local time and
# memory aren't Polygon's.
_SOFT_REQUIRED = frozenset({"tl", "tle", "ml", "mle"})

# `SolutionOutcome.status`.
STATUS_EXPECTED = "expected"
STATUS_UNEXPECTED = "unexpected"
# The declared verdict on no generated test (only the full set).
STATUS_SURVIVED = "survived"
STATUS_NOT_RUN = "not_run"
# How many of a solution's tests are listed one by one, then — a summary.
_MAX_LISTED_RUNS = 8
_MAX_LISTED_UNEXPECTED = 10

# The fraction of the TL above which a solution without the `tl` verdict gets a warning.
SLOW_FRACTION = 0.5
_TL_VERDICTS = frozenset({"tl", "tle"})

STD_CHECKERS_DIR = Path(__file__).with_name("std_checkers")
SOLUTIONS_DIRNAME = "solutions"

_STD_PREFIX = "std::"
# Without `limits.yaml` and `constraints.yaml`.
_DEFAULT_TIME_LIMIT_MS = 2000
_DEFAULT_MEMORY_LIMIT_MB = 256
# The address space cap of C++ and Python solutions — the ML plus this, for the
# runtime's own mappings (the interpreter, shared libraries).
_MEMORY_CAP_SLACK_MB = 64
_CHECKER_TIMEOUT_SECONDS = 10.0
_COMPILE_TIMEOUT_SECONDS = 60.0
_JAVA_STACK = "-Xss64m"
_NO_JDK_HINT = "no Java compiler: install a JDK (sudo apt install default-jdk-headless)"

_PUBLIC_CLASS = re.compile(r"^\s*public\s+(?:final\s+|abstract\s+)*class\s+(\w+)", re.MULTILINE)
_ANY_CLASS = re.compile(r"^\s*(?:public\s+)?(?:final\s+|abstract\s+)*class\s+(\w+)", re.MULTILINE)


@dataclass(frozen=True)
class SampleTest:
    name: str
    input: str
    answer: str | None
    # Input/output for statements (for an interactive problem — the dialogue).
    statement_input: str | None = None
    statement_output: str | None = None

    @property
    def has_statement_io(self) -> bool:
        return self.statement_input is not None or self.statement_output is not None


@dataclass
class TestOutcome:
    test: str
    verdict: str
    seconds: float | None = None
    comment: str = ""
    # The command from the test-script; empty for the examples.
    command: str = ""
    # A test from the test-script, not an example.
    generated: bool = False


@dataclass
class SolutionOutcome:
    name: str
    verdict: str
    allowed: frozenset[str]
    tests: list[TestOutcome] = field(default_factory=list)
    # The name didn't parse, it didn't build, it didn't start.
    error: str | None = None
    # The verdicts one of which the solution must get on a generated test (only `--full`).
    required: frozenset[str] | None = None
    stopped_early: bool = False

    @property
    def required_label(self) -> str:
        return "/".join(sorted(self.required or ()))

    @property
    def caught_by(self) -> TestOutcome | None:
        """The first generated test with the required verdict: the examples don't count."""
        if self.required is None:
            return None
        return next((t for t in self.tests if t.generated and t.verdict in self.required), None)

    @property
    def caught_on_example(self) -> bool:
        return self.required is not None and any(
            not t.generated and t.verdict in self.required for t in self.tests
        )

    @property
    def missed_required(self) -> bool:
        return self.required is not None and self.caught_by is None

    @property
    def status(self) -> str:
        if self.error is not None:
            return STATUS_NOT_RUN
        if any(t.verdict not in self.allowed for t in self.tests):
            return STATUS_UNEXPECTED
        if self.missed_required:
            return STATUS_SURVIVED
        return STATUS_EXPECTED

    @property
    def failed(self) -> bool:
        """For `local run`: a survivor is an error only if no example catches it either
        and the verdict isn't a local time/memory one."""
        return (
            self.error is not None
            or any(t.verdict not in self.allowed for t in self.tests)
            or (
                self.missed_required
                and not self.caught_on_example
                and self.verdict not in _SOFT_REQUIRED
            )
        )

    @property
    def runs(self) -> str:
        if len(self.tests) <= _MAX_LISTED_RUNS:
            text = ", ".join(
                t.verdict + (f" {t.seconds:.2f}s" if t.seconds is not None else "")
                for t in self.tests
            )
        else:
            counts: dict[str, int] = {}
            for t in self.tests:
                counts[t.verdict] = counts.get(t.verdict, 0) + 1
            text = ", ".join(f"{verdict} x{count}" for verdict, count in counts.items())
            seconds = [t.seconds for t in self.tests if t.seconds is not None]
            if seconds:
                text += f", max {max(seconds):.2f}s"
        if self.stopped_early:
            text += f", stopped at the first {self.required_label}"
        return text

    def summary(self) -> dict:
        """See `SolutionsRunResult.summary`."""
        seconds = [t.seconds for t in self.tests if t.seconds is not None]
        caught = self.caught_by
        return {
            "name": self.name,
            "verdict": self.verdict,
            "status": self.status,
            "error": self.error,
            "caught_by": _test_summary(caught) if caught is not None else None,
            "caught_on_example": self.caught_on_example,
            "unexpected": [_test_summary(t) for t in self.tests if t.verdict not in self.allowed][
                :_MAX_LISTED_UNEXPECTED
            ],
            "max_seconds": round(max(seconds), 2) if seconds else None,
        }


def _test_summary(test: TestOutcome) -> dict:
    return {
        "test": test.test,
        "verdict": test.verdict,
        "command": test.command or None,
        "comment": "\n".join(test.comment.strip().splitlines()[-5:]) or None,
    }


Progress = Callable[[str], None]


@dataclass
class SolutionsRunResult:
    # What was checked and where it came from (TL, checker, answers) — printed along the way.
    notes: list[str] = field(default_factory=list)
    # Problems that aren't of a single solution: validator, checker, answers.
    errors: list[str] = field(default_factory=list)
    solutions: list[SolutionOutcome] = field(default_factory=list)
    time_limit: float | None = None
    memory_limit_mb: int | None = None
    # Whether the tests from the test-script were run (`full`).
    full: bool = False
    progress: Progress | None = None

    def note(self, message: str) -> None:
        self.notes.append(message)
        if self.progress is not None:
            self.progress(message)

    @property
    def failed(self) -> bool:
        return bool(self.errors) or any(s.failed for s in self.solutions)

    def summary(self) -> dict:
        """The outcome in a machine-readable form, for the steps that act on it: the
        problem-wide errors and, per solution, its status (`STATUS_*`), the test that
        caught it, its unexpected verdicts. Plain data, ready for YAML/JSON."""
        return {
            "full": self.full,
            "time_limit_seconds": self.time_limit,
            "memory_limit_mb": self.memory_limit_mb,
            "errors": list(self.errors),
            "solutions": [s.summary() for s in self.solutions],
        }

    @property
    def warnings(self) -> list[str]:
        """Survivors that aren't an error (caught only on the examples; `tl`/`ml`) and
        solutions without the `tl` verdict that ran on an example longer than
        `SLOW_FRACTION` of the TL (but fit into it)."""
        warnings = []
        for s in self.solutions:
            if s.status != STATUS_SURVIVED:
                continue
            if s.caught_on_example:
                warnings.append(
                    f"{s.name}: got {s.required_label} only on examples — "
                    "no generated test catches it"
                )
            elif s.verdict in _SOFT_REQUIRED:
                warnings.append(
                    f"{s.name}: never got {s.required_label} (local time and memory, not Polygon's)"
                )
        if self.time_limit is None:
            return warnings
        threshold = SLOW_FRACTION * self.time_limit
        for s in self.solutions:
            if s.verdict in _TL_VERDICTS:
                continue
            slow = [
                t
                for t in s.tests
                if t.seconds is not None and threshold < t.seconds <= self.time_limit
            ]
            if slow:
                slowest = max(slow, key=lambda t: t.seconds)
                more = f" (and {len(slow) - 1} more)" if len(slow) > 1 else ""
                warnings.append(
                    f"{s.name}: {slowest.test} {slowest.seconds:.2f}s — more than "
                    f"{SLOW_FRACTION:g} of TL {self.time_limit:g}s{more}"
                )
        return warnings

    def groups(self) -> list[tuple[str, list[str], str]]:
        """The summary by groups `(title, lines, hint)` — as in `polygon
        push`; the printer skips empty groups."""
        expected = [f"{s.name}: {s.runs}" for s in self.solutions if not s.failed]
        unexpected = []
        not_run = []
        for s in self.solutions:
            if s.error is not None:
                first, *rest = s.error.strip().splitlines() or [""]
                not_run.append(f"{s.name}: {first}")
                not_run.extend(f"    {line}" for line in rest[:10])
            elif s.failed:
                unexpected.append(f"{s.name}: {s.runs}")
                if (
                    s.missed_required
                    and not s.caught_on_example
                    and s.verdict not in _SOFT_REQUIRED
                ):
                    unexpected.append(f"  never got {s.required_label}: no test catches it")
                bad = [t for t in s.tests if t.verdict not in s.allowed]
                for t in bad[:_MAX_LISTED_UNEXPECTED]:
                    allowed = ", ".join(sorted(s.allowed))
                    unexpected.append(
                        f"  {t.test}: {t.verdict}, expected {allowed} for '{s.verdict}'"
                    )
                    if t.command:
                        unexpected.append(f"    {t.command}")
                    unexpected.extend(f"    {line}" for line in t.comment.strip().splitlines()[-5:])
                if len(bad) > _MAX_LISTED_UNEXPECTED:
                    unexpected.append(f"  ... and {len(bad) - _MAX_LISTED_UNEXPECTED} more test(s)")
        return [
            ("expected verdicts", expected, ""),
            ("unexpected verdicts", unexpected, ""),
            ("not run", not_run, ""),
            ("warnings", self.warnings, ""),
            ("errors", self.errors, ""),
        ]


@dataclass
class _Program:
    command: list[str]
    unlimited_stack: bool = False
    # Cap the address space at this ML (plus the slack); `None` — don't.
    memory_limit_mb: int | None = None

    @property
    def memory_cap_mb(self) -> int | None:
        if self.memory_limit_mb is None:
            return None
        return self.memory_limit_mb + _MEMORY_CAP_SLACK_MB

    def run(self, stdin: str, timeout_seconds: float) -> RunResult:
        return run_with_timeout(
            Path(self.command[0]),
            self.command[1:],
            timeout_seconds,
            stdin,
            unlimited_stack=self.unlimited_stack,
            memory_cap_mb=self.memory_cap_mb,
        )


class _BuildError(Exception):
    pass


def run_solutions(
    problem_dir: Path,
    templates_dir: Path,
    progress: Progress | None = None,
    *,
    full: bool = False,
    only: Collection[str] | None = None,
) -> SolutionsRunResult:
    """Runs the problem's validator and solutions on its examples, with `full` — also
    on the tests from the test-script (see the module docstring). In the problem directory it
    writes only the test cache (`full`). `progress` gets lines along the way:
    notes and the start of each solution. `only` — the names of the solutions to run
    (`None` — all); Main correct is run anyway: its output is the expected one."""
    problem_dir = Path(problem_dir)
    result = SolutionsRunResult(progress=progress, full=full)
    spec = _read_spec(problem_dir, result)
    tests = _sample_tests(problem_dir, spec)
    generated = _generated_tests(problem_dir, templates_dir, result) if full else []
    if not tests and not generated:
        result.note(f"no {STATEMENT_DIRNAME}/{EXAMPLES_SUBDIR}/example_<N>.txt — skipped")
        return result

    include_dirs = [problem_dir, Path(templates_dir)]
    interactive = _is_interactive(problem_dir, spec)
    with tempfile.TemporaryDirectory(prefix="solutions_run_") as tmp:
        work_dir = Path(tmp)
        if tests:
            _check_validator(
                problem_dir, include_dirs, tests, work_dir, result, interactive=interactive
            )

        sources = _solution_sources(problem_dir)
        if not sources:
            result.note(f"no {SOLUTIONS_DIRNAME}/ files — skipped")
            if any(t.has_statement_io for t in tests):
                result.note("input/output for statements are not checked: no solutions")
            return result

        interactor = None
        if interactive:
            interactor = _build_interactor(problem_dir, include_dirs, work_dir, result)
            if interactor is None:
                return result
        checker = _build_checker(problem_dir, spec, include_dirs, work_dir, result)
        if checker is None:
            return result
        time_limit_ms, memory_limit_mb = _limits(problem_dir, result)
        time_limit = time_limit_ms / 1000
        result.time_limit = time_limit
        result.memory_limit_mb = memory_limit_mb

        parsed = {}
        for path in sources:
            try:
                parsed[path.name] = _parse_solution_filename(path)
            except PolygonStepError as exc:
                if only is None or path.name in only:
                    result.solutions.append(
                        SolutionOutcome(path.name, "?", frozenset(), error=str(exc))
                    )
        if only is not None:
            parsed = _only_with_main(parsed, only)

        programs: dict[str, _Program | str] = {}

        def program_for(path: Path) -> _Program | str:
            if path.name not in programs:
                try:
                    programs[path.name] = _build_solution(
                        path, include_dirs, work_dir, memory_limit_mb
                    )
                except _BuildError as exc:
                    programs[path.name] = str(exc)
            return programs[path.name]

        if tests:
            tests = _fill_answers(
                tests, parsed, program_for, time_limit, work_dir, interactor, result
            )
        _check_statement_io(
            tests, parsed, program_for, checker, time_limit, work_dir, interactor, result
        )

        for name, solution in parsed.items():
            outcome = SolutionOutcome(
                name,
                solution.verdict,
                ALLOWED_VERDICTS.get(solution.verdict, frozenset()),
                required=REQUIRED_VERDICTS.get(solution.verdict) if generated else None,
            )
            result.solutions.append(outcome)
            if progress is not None:
                progress(f"running {name}")
            program = program_for(solution.path)
            if isinstance(program, str):
                outcome.error = program
                continue
            for test in tests:
                outcome.tests.append(
                    _run_test(program, checker, test, time_limit, work_dir, interactor)
                )
        if interactor is None:
            _note_suspicious_answers(tests, result)
        if generated:
            _run_generated(
                generated, parsed, program_for, checker, time_limit, work_dir, interactor, result
            )
    result.solutions.sort(key=lambda s: s.name)
    return result


def _only_with_main(parsed: dict, only: Collection[str]) -> dict:
    try:
        main_name = _choose_main(list(parsed.values()))
    except PolygonStepError:
        main_name = None
    return {name: p for name, p in parsed.items() if name in only or name == main_name}


def _generated_tests(
    problem_dir: Path, templates_dir: Path, result: SolutionsRunResult
) -> list[LocalTest]:
    """Tests from the test-script, checked by the validator."""
    generated = generate_tests(problem_dir, templates_dir, progress=result.progress)
    result.errors.extend(generated.errors)
    result.note(
        f"tests: {len(generated.tests)} ({generated.generated} generated, "
        f"{generated.reused} from cache, {generated.removed} removed), "
        f"see {TESTS_CACHE_DIRNAME}/{MANIFEST_FILENAME}"
    )
    errors, validated = validate_tests(problem_dir, templates_dir, generated.tests)
    result.errors.extend(errors)
    if validated and not errors:
        result.note(f"{VALIDATOR_FILENAME} accepts all {len(generated.tests)} test(s)")
    return generated.tests


def _run_generated(
    generated: list[LocalTest],
    parsed: dict,
    program_for,
    checker: _Program,
    time_limit: float,
    work_dir: Path,
    interactor: _Program | None,
    result: SolutionsRunResult,
) -> None:
    """Solutions on the tests from the test-script (see the module docstring). Tests and
    answers are read one at a time: the whole set isn't kept in memory."""
    try:
        main_name = _choose_main(list(parsed.values()))
    except PolygonStepError:
        result.errors.append(
            "no ok solution to produce expected outputs: generated tests are not run"
        )
        return
    main_program = program_for(parsed[main_name].path)
    if isinstance(main_program, str):
        result.errors.append(f"main solution {main_name} is not built: generated tests are not run")
        return
    outcomes = {outcome.name: outcome for outcome in result.solutions}
    answers_dir = work_dir / "answers"
    answers_dir.mkdir()

    def report(outcome: SolutionOutcome) -> None:
        if result.progress is not None:
            result.progress(f"  {outcome.name}: {outcome.runs}")

    def load(test: LocalTest, answer: str | None = None) -> SampleTest:
        return SampleTest(
            test.name, test.path.read_text(encoding="utf-8", errors="replace"), answer
        )

    if result.progress is not None:
        result.progress(
            f"running {main_name} on {len(generated)} generated test(s): expected outputs"
        )
    main_outcome = outcomes[main_name]
    answered = []
    for test in generated:
        sample = load(test)
        execution = _execute(main_program, sample, time_limit, work_dir, interactor)
        if execution.verdict is not None:
            main_outcome.tests.append(
                TestOutcome(
                    test.name,
                    execution.verdict,
                    execution.seconds,
                    execution.comment,
                    test.command,
                    generated=True,
                )
            )
            continue
        (answers_dir / str(test.number)).write_text(execution.output, encoding="utf-8")
        answered.append(test)
        # The answer is its own output: the checker must accept it, otherwise the checker is wrong.
        verdict, comment = _judge(
            checker, sample.input, execution.output, execution.output, work_dir
        )
        main_outcome.tests.append(
            TestOutcome(
                test.name,
                verdict,
                execution.seconds,
                comment,
                test.command,
                generated=True,
            )
        )
    report(main_outcome)
    if len(answered) < len(generated):
        result.errors.append(
            f"{len(generated) - len(answered)} generated test(s) have no expected output — "
            f"{main_name} did not finish them: other solutions skip these tests"
        )

    for name, solution in parsed.items():
        outcome = outcomes[name]
        if name == main_name or outcome.error is not None:
            continue
        if result.progress is not None:
            result.progress(f"running {name} on generated tests")
        program = program_for(solution.path)
        for test in answered:
            answer = (answers_dir / str(test.number)).read_text(encoding="utf-8")
            tested = _run_test(
                program, checker, load(test, answer), time_limit, work_dir, interactor
            )
            outcome.tests.append(replace(tested, command=test.command, generated=True))
            if outcome.required is not None and tested.verdict in outcome.required:
                outcome.stopped_early = True
                break
        report(outcome)


def _read_spec(problem_dir: Path, result: SolutionsRunResult) -> dict | None:
    """The spec as is (YAML), without validation: the check is needed for a problem whose spec
    doesn't pass validation yet too. No spec — `None`."""
    path = find_authoring_file(problem_dir, SPEC_FILENAME)
    if not path.is_file():
        result.note(f"no {SPEC_FILENAME}: no expected outputs and checker settings")
        return None
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        result.errors.append(f"{SPEC_FILENAME} is not valid YAML: {exc}")
        return None
    return data if isinstance(data, dict) else None


def _section(spec: dict | None, name: str) -> dict:
    value = (spec or {}).get(name)
    return value if isinstance(value, dict) else {}


def _sample_tests(problem_dir: Path, spec: dict | None) -> list[SampleTest]:
    examples_dir = problem_dir / STATEMENT_DIRNAME / EXAMPLES_SUBDIR
    spec_examples = _section(spec, "statement_draft").get("sample_examples") or []
    tests = []
    for number, path in example_files(examples_dir):
        answer = None
        if 0 < number <= len(spec_examples) and isinstance(spec_examples[number - 1], dict):
            output = spec_examples[number - 1].get("output")
            if isinstance(output, (str, int, float)) and str(output).strip():
                answer = str(output)
        statement_io = [
            (examples_dir / name).read_text(encoding="utf-8")
            if (examples_dir / name).is_file()
            else None
            for name in dialogue_filenames(number)
        ]
        tests.append(SampleTest(path.stem, path.read_text(encoding="utf-8"), answer, *statement_io))
    return tests


def _is_interactive(problem_dir: Path, spec: dict | None) -> bool:
    if spec is not None:
        return spec.get("interactor") is not None
    return (problem_dir / INTERACTOR_FILENAME).is_file()


def _solution_sources(problem_dir: Path) -> list[Path]:
    solutions_dir = problem_dir / SOLUTIONS_DIRNAME
    if not solutions_dir.is_dir():
        return []
    return sorted(
        (p for p in solutions_dir.iterdir() if p.is_file() and not is_removed_mark(p)),
        key=lambda p: p.name,
    )


def _limits(problem_dir: Path, result: SolutionsRunResult) -> tuple[int, int]:
    for path in (
        problem_dir / LIMITS_FILENAME,
        find_authoring_file(problem_dir, CONSTRAINTS_FILENAME),
    ):
        if not path.is_file():
            continue
        try:
            time_limit_ms, memory_limit_mb = parse_limits(path.read_text(encoding="utf-8"))
        except LimitsError as exc:
            result.errors.append(f"{path.name}: {exc}")
            continue
        result.note(
            f"TL {time_limit_ms / 1000:g}s, ML {memory_limit_mb} MB from {path.name} "
            "(local wall-clock time, not Polygon's)"
        )
        return time_limit_ms, memory_limit_mb
    result.note(
        f"no TL/ML in {LIMITS_FILENAME} or {CONSTRAINTS_FILENAME}: assuming "
        f"TL {_DEFAULT_TIME_LIMIT_MS / 1000:g}s, ML {_DEFAULT_MEMORY_LIMIT_MB} MB"
    )
    return _DEFAULT_TIME_LIMIT_MS, _DEFAULT_MEMORY_LIMIT_MB


def _compile_cpp(path: Path, include_dirs: list[Path], work_dir: Path) -> Path:
    compiled = compile_check(path, include_dirs, timeout_seconds=_COMPILE_TIMEOUT_SECONDS)
    if not compiled.success or compiled.binary_path is None:
        raise _BuildError(f"compilation failed:\n{compiled.stderr.strip()}")
    target = work_dir / f"{path.stem}.bin"
    shutil.move(str(compiled.binary_path), target)
    return target


def _check_validator(
    problem_dir: Path,
    include_dirs: list[Path],
    tests: list[SampleTest],
    work_dir: Path,
    result: SolutionsRunResult,
    *,
    interactive: bool = False,
) -> None:
    """The examples' input and, for a normal problem, their input for statements (for
    an interactive one these are the interactor's lines, not a test)."""
    path = problem_dir / VALIDATOR_FILENAME
    inputs = [(test.name, test.input) for test in tests]
    if not interactive:
        inputs += [
            (f"{test.name} input for statements", test.statement_input)
            for test in tests
            if test.statement_input is not None
        ]
    if not path.is_file():
        result.note(f"no {VALIDATOR_FILENAME}: examples are not validated")
        return
    if result.progress is not None:
        result.progress(f"running {VALIDATOR_FILENAME}")
    try:
        binary = _compile_cpp(path, include_dirs, work_dir)
    except _BuildError as exc:
        result.errors.append(f"{VALIDATOR_FILENAME}: {exc}")
        return
    rejected = 0
    for name, text in inputs:
        run = run_with_timeout(binary, [], _CHECKER_TIMEOUT_SECONDS, text)
        if run.timed_out or run.exit_code != 0:
            rejected += 1
            reason = "timed out" if run.timed_out else (run.stderr or run.stdout).strip()
            result.errors.append(f"{VALIDATOR_FILENAME} rejects {name}: {reason}")
    if not rejected:
        extra = len(inputs) - len(tests)
        suffix = f" and {extra} input(s) for statements" if extra else ""
        result.note(f"{VALIDATOR_FILENAME} accepts all {len(tests)} example(s){suffix}")


def _build_checker(
    problem_dir: Path,
    spec: dict | None,
    include_dirs: list[Path],
    work_dir: Path,
    result: SolutionsRunResult,
) -> _Program | None:
    section = _section(spec, "checker")
    custom = (
        bool(section.get("custom_needed"))
        if spec is not None
        else (problem_dir / CHECKER_FILENAME).is_file()
    )
    if custom:
        source = problem_dir / CHECKER_FILENAME
        label = CHECKER_FILENAME
        if not source.is_file():
            result.errors.append(
                f"checker.custom_needed is true, but there is no {CHECKER_FILENAME} — "
                "solutions are not run"
            )
            return None
    else:
        standard = str(section.get("standard") or "").strip()
        if not standard:
            result.errors.append(
                "checker.standard is not set in the spec — unknown checker, solutions are not run"
            )
            return None
        name = standard.removeprefix(_STD_PREFIX).removesuffix(".cpp")
        source = STD_CHECKERS_DIR / f"{name}.cpp"
        label = f"{_STD_PREFIX}{name}.cpp"
        if not source.is_file():
            known = ", ".join(sorted(p.stem for p in STD_CHECKERS_DIR.glob("*.cpp")))
            result.errors.append(
                f"unknown standard checker {standard!r} (known: {known}) — solutions are not run"
            )
            return None
    try:
        binary = _compile_cpp(source, include_dirs, work_dir)
    except _BuildError as exc:
        result.errors.append(f"checker {label}: {exc}")
        return None
    result.note(f"checker {label}")
    return _Program([str(binary)])


def _build_interactor(
    problem_dir: Path, include_dirs: list[Path], work_dir: Path, result: SolutionsRunResult
) -> _Program | None:
    source = problem_dir / INTERACTOR_FILENAME
    if not source.is_file():
        result.errors.append(
            f"interactive problem without {INTERACTOR_FILENAME} — solutions are not run"
        )
        return None
    try:
        binary = _compile_cpp(source, include_dirs, work_dir)
    except _BuildError as exc:
        result.errors.append(f"{INTERACTOR_FILENAME}: {exc}")
        return None
    result.note(f"interactive problem: interactor {INTERACTOR_FILENAME}")
    return _Program([str(binary)])


def _build_solution(
    path: Path, include_dirs: list[Path], work_dir: Path, memory_limit_mb: int
) -> _Program:
    suffix = path.suffix.lower()
    if suffix == ".cpp":
        return _Program(
            [str(_compile_cpp(path, include_dirs, work_dir))],
            unlimited_stack=True,
            memory_limit_mb=memory_limit_mb,
        )
    if suffix == ".java":
        return _build_java(path, work_dir, memory_limit_mb)
    if suffix == ".py":
        python = shutil.which("python3") or sys.executable
        return _Program([python, str(path)], memory_limit_mb=memory_limit_mb)
    raise _BuildError(f"unsupported language '{path.suffix}' — not run")


def _java_class_name(source: str) -> str | None:
    """The class name under which Polygon saves the file: the public class, otherwise
    the file's first class."""
    match = _PUBLIC_CLASS.search(source) or _ANY_CLASS.search(source)
    return match.group(1) if match else None


def _javac_command() -> list[str] | None:
    javac = shutil.which("javac")
    if javac:
        return [javac]
    java = shutil.which("java")
    if java:
        return [java, "-m", "jdk.compiler/com.sun.tools.javac.Main"]
    return None


def _build_java(path: Path, work_dir: Path, memory_limit_mb: int) -> _Program:
    java = shutil.which("java")
    javac = _javac_command()
    if java is None or javac is None:
        raise _BuildError(_NO_JDK_HINT)
    source = path.read_text(encoding="utf-8")
    class_name = _java_class_name(source)
    if class_name is None:
        raise _BuildError("no class found in the Java source")
    # javac requires the public class to be in a file with its name.
    src_dir = work_dir / f"java_{path.stem}"
    out_dir = src_dir / "out"
    out_dir.mkdir(parents=True)
    (src_dir / f"{class_name}.java").write_text(source, encoding="utf-8")
    compiled = run_with_timeout(
        Path(javac[0]),
        [*javac[1:], "-encoding", "UTF-8", "-d", str(out_dir), str(src_dir / f"{class_name}.java")],
        _COMPILE_TIMEOUT_SECONDS,
    )
    if compiled.timed_out:
        raise _BuildError(f"compilation exceeded timeout {_COMPILE_TIMEOUT_SECONDS}s")
    if compiled.exit_code != 0:
        if "jdk.compiler" in compiled.stderr and "not found" in compiled.stderr:
            raise _BuildError(_NO_JDK_HINT)
        raise _BuildError(f"compilation failed:\n{compiled.stderr.strip()}")
    return _Program([java, f"-Xmx{memory_limit_mb}m", _JAVA_STACK, "-cp", str(out_dir), class_name])


def _hard_timeout(time_limit: float) -> float:
    """The process is killed later than the TL, so that it's visible by how much it didn't
    fit."""
    return 2 * time_limit + 1


@dataclass
class _Execution:
    seconds: float
    # `None` — the solution finished as it should, its output is judged by the checker.
    verdict: str | None = None
    comment: str = ""
    # The solution's output, for an interactive problem — the interactor's `tout`.
    output: str = ""


def _execute(
    program: _Program,
    test: SampleTest,
    time_limit: float,
    work_dir: Path,
    interactor: _Program | None,
) -> _Execution:
    if interactor is not None:
        return _execute_interactive(program, interactor, test, time_limit, work_dir)
    run = program.run(test.input, _hard_timeout(time_limit))
    seconds = run.elapsed_seconds
    if run.timed_out or _over_time(seconds, run.cpu_seconds, run.exit_code, time_limit):
        return _Execution(seconds, VERDICT_TL)
    if run.exit_code != 0:
        return _Execution(seconds, VERDICT_RE, _runtime_error(program, run.exit_code, run.stderr))
    return _Execution(seconds, output=run.stdout)


def _over_time(seconds: float, cpu: float, exit_code: int | None, time_limit: float) -> bool:
    """Longer than the TL. A process killed by a signal (`abort` after an exception,
    a failed allocation over the memory cap) — by its CPU time: the elapsed time then
    includes the system crash handler (apport on Ubuntu takes over a second)."""
    if exit_code is not None and exit_code < 0:
        return cpu > time_limit
    return seconds > time_limit


def _runtime_error(program: _Program, exit_code: int | None, stderr: str) -> str:
    cap = program.memory_cap_mb
    hint = f" (the address space is capped at {cap} MB: maybe memory)" if cap is not None else ""
    return f"exit code {exit_code}{hint}\n{stderr}"


def _wait(proc: subprocess.Popen, deadline: float | None) -> tuple[int | None, float]:
    """(exit code, CPU time in seconds) of the process; `None` if
    it didn't finish by `deadline` (`None` — wait as long as needed).
    `os.wait4`, not `Popen.wait`: the CPU time of the process itself is needed."""
    while True:
        pid, status, usage = os.wait4(proc.pid, 0 if deadline is None else os.WNOHANG)
        if pid:
            proc.returncode = os.waitstatus_to_exitcode(status)
            return proc.returncode, usage.ru_utime + usage.ru_stime
        if time.monotonic() >= deadline:
            return None, 0.0
        time.sleep(0.002)


def _execute_interactive(
    program: _Program,
    interactor: _Program,
    test: SampleTest,
    time_limit: float,
    work_dir: Path,
) -> _Execution:
    """The solution and the interactor connected with pipes: the solution's output is the
    interactor's input, and vice versa. The interactor is `interactor <input> <tout>`."""
    run_dir = work_dir / "interaction"
    run_dir.mkdir(exist_ok=True)
    input_path = run_dir / "input.txt"
    input_path.write_text(test.input, encoding="utf-8")
    tout_path = run_dir / "tout.txt"
    tout_path.unlink(missing_ok=True)
    to_interactor_read, to_interactor_write = os.pipe()
    to_solution_read, to_solution_write = os.pipe()
    with (
        open(run_dir / "solution.err", "w+", encoding="utf-8", errors="replace") as solution_err,
        open(
            run_dir / "interactor.err", "w+", encoding="utf-8", errors="replace"
        ) as interactor_err,
    ):
        started = time.monotonic()
        interactor_proc = subprocess.Popen(
            [*interactor.command, str(input_path), str(tout_path)],
            stdin=to_interactor_read,
            stdout=to_solution_write,
            stderr=interactor_err,
        )
        solution_proc = subprocess.Popen(
            program.command,
            stdin=to_solution_read,
            stdout=to_interactor_write,
            stderr=solution_err,
            preexec_fn=resource_limits(
                unlimited_stack=program.unlimited_stack, memory_cap_mb=program.memory_cap_mb
            ),
        )
        # Otherwise the end of input won't reach either of the processes.
        for fd in (to_interactor_read, to_interactor_write, to_solution_read, to_solution_write):
            os.close(fd)
        exit_code, cpu = _wait(solution_proc, started + _hard_timeout(time_limit))
        seconds = time.monotonic() - started
        killed = exit_code is None
        if killed:
            solution_proc.kill()
            exit_code, cpu = _wait(solution_proc, None)
        interactor_code, _ = _wait(interactor_proc, time.monotonic() + _CHECKER_TIMEOUT_SECONDS)
        if interactor_code is None:
            interactor_proc.kill()
            _wait(interactor_proc, None)
        solution_err.seek(0)
        interactor_err.seek(0)
        solution_stderr = solution_err.read()
        interactor_stderr = interactor_err.read().strip()

    if killed:
        verdict = VERDICT_IL if cpu < seconds / 2 else VERDICT_TL
        return _Execution(seconds, verdict, f"CPU time {cpu:.2f}s of {seconds:.2f}s")
    if _over_time(seconds, cpu, exit_code, time_limit):
        return _Execution(seconds, VERDICT_TL)
    if interactor_code is None:
        return _Execution(seconds, VERDICT_FAIL, "interactor did not finish after the solution")
    if interactor_code != 0:
        verdict = {1: VERDICT_WA, 2: VERDICT_PE}.get(interactor_code, VERDICT_FAIL)
        return _Execution(seconds, verdict, f"interactor: {interactor_stderr}")
    if exit_code != 0:
        return _Execution(seconds, VERDICT_RE, _runtime_error(program, exit_code, solution_stderr))
    output = tout_path.read_text(encoding="utf-8") if tout_path.is_file() else ""
    return _Execution(seconds, output=output, comment=f"interactor: {interactor_stderr}")


def _fill_answers(
    tests: list[SampleTest],
    parsed: dict,
    program_for,
    time_limit: float,
    work_dir: Path,
    interactor: _Program | None,
    result: SolutionsRunResult,
) -> list[SampleTest]:
    """The answers of examples without `output` in the spec — the output of Main correct. For
    an interactive problem — of all examples: the `tout` of Main correct."""
    if interactor is not None:
        tests = [replace(t, answer=None) for t in tests]
    missing = [t for t in tests if t.answer is None]
    if not missing:
        result.note("expected outputs: from the spec")
        return tests
    names = ", ".join(t.name for t in missing)
    try:
        main_name = _choose_main(list(parsed.values()))
    except PolygonStepError:
        result.errors.append(f"no expected output for {names} and no ok solution to produce it")
        return tests
    program = program_for(parsed[main_name].path)
    if interactor is not None:
        result.note(f"expected outputs: tout of the main solution {main_name}")
    else:
        result.note(f"expected outputs for {names}: from the main solution {main_name}")
    if isinstance(program, str):
        return tests
    filled = []
    for test in tests:
        if test.answer is None:
            execution = _execute(program, test, time_limit, work_dir, interactor)
            if execution.verdict is None:
                test = replace(test, answer=execution.output)
            else:
                result.errors.append(
                    f"{test.name}: no expected output — {main_name} got {execution.verdict} on it"
                )
        filled.append(test)
    return filled


def _run_test(
    program: _Program,
    checker: _Program,
    test: SampleTest,
    time_limit: float,
    work_dir: Path,
    interactor: _Program | None = None,
) -> TestOutcome:
    execution = _execute(program, test, time_limit, work_dir, interactor)
    seconds = execution.seconds
    if execution.verdict is not None:
        return TestOutcome(test.name, execution.verdict, seconds, execution.comment)
    if test.answer is None:
        return TestOutcome(test.name, VERDICT_OK, seconds, "not judged: no expected output")
    verdict, comment = _judge(checker, test.input, execution.output, test.answer, work_dir)
    return TestOutcome(test.name, verdict, seconds, comment)


def _judge(
    checker: _Program, input_text: str, output: str, answer: str, work_dir: Path
) -> tuple[str, str]:
    """The checker's verdict and its comment."""
    judge_dir = work_dir / "judge"
    judge_dir.mkdir(exist_ok=True)
    files = {"input": input_text, "output": output, "answer": answer}
    for name, text in files.items():
        (judge_dir / f"{name}.txt").write_text(text, encoding="utf-8")
    # A testlib checker: `checker <input> <output> <answer>`, the comment — in stderr.
    judged = run_with_timeout(
        Path(checker.command[0]),
        [str(judge_dir / f"{name}.txt") for name in files],
        _CHECKER_TIMEOUT_SECONDS,
    )
    verdict = {0: VERDICT_OK, 1: VERDICT_WA, 2: VERDICT_PE}.get(judged.exit_code, VERDICT_FAIL)
    return verdict, judged.stderr.strip()


def _check_statement_io(
    tests: list[SampleTest],
    parsed: dict,
    program_for,
    checker: _Program,
    time_limit: float,
    work_dir: Path,
    interactor: _Program | None,
    result: SolutionsRunResult,
) -> None:
    """The examples' input/output for statements (see the module docstring)."""
    for test in tests:
        if not test.has_statement_io:
            continue
        label = f"{test.name} input/output for statements"
        if interactor is not None:
            error = _check_dialogue(test, interactor, checker, work_dir)
        else:
            error = _check_shown_output(test, parsed, program_for, checker, time_limit, work_dir)
        if error is None:
            result.note(f"{label}: OK")
        else:
            result.errors.append(f"{label}: {error}")


def _check_shown_output(
    test: SampleTest,
    parsed: dict,
    program_for,
    checker: _Program,
    time_limit: float,
    work_dir: Path,
) -> str | None:
    """A normal problem: the output shown in the statement is a correct answer to
    the shown input. `None` — yes, otherwise — what's wrong."""
    shown_output = test.statement_output if test.statement_output is not None else test.answer
    if test.statement_input is None:
        shown_input, answer = test.input, test.answer
    else:
        shown_input = test.statement_input
        try:
            main_name = _choose_main(list(parsed.values()))
        except PolygonStepError:
            return "not checked — no ok solution to produce the expected output"
        program = program_for(parsed[main_name].path)
        if isinstance(program, str):
            return f"not checked — {main_name} is not built"
        execution = _execute(
            program, SampleTest(test.name, shown_input, None), time_limit, work_dir, None
        )
        if execution.verdict is not None:
            return f"{main_name} got {execution.verdict} on the input for statements"
        answer = execution.output
    if shown_output is None or answer is None:
        return "not checked — no expected output of the example"
    verdict, comment = _judge(checker, shown_input, shown_output, answer, work_dir)
    if verdict == VERDICT_OK:
        return None
    return f"checker says {verdict} on the output shown in the statement: {comment}"


def _check_dialogue(
    test: SampleTest, interactor: _Program, checker: _Program, work_dir: Path
) -> str | None:
    """An interactive problem: the dialogue in the statement is replayed with the interactor.
    `None` — the dialogue agrees with it, otherwise — what's wrong."""
    if test.statement_output is None:
        return "not checked — no output for statements (what the solution prints) to replay"
    run_dir = work_dir / "dialogue"
    run_dir.mkdir(exist_ok=True)
    input_path = run_dir / "input.txt"
    input_path.write_text(test.input, encoding="utf-8")
    tout_path = run_dir / "tout.txt"
    tout_path.unlink(missing_ok=True)
    run = run_with_timeout(
        Path(interactor.command[0]),
        [*interactor.command[1:], str(input_path), str(tout_path)],
        _CHECKER_TIMEOUT_SECONDS,
        test.statement_output,
    )
    if run.timed_out:
        return "the interactor did not finish on the replayed dialogue"
    if run.exit_code != 0:
        verdict = {1: VERDICT_WA, 2: VERDICT_PE}.get(run.exit_code, VERDICT_FAIL)
        return f"the interactor says {verdict} on the replayed dialogue: {run.stderr.strip()}"
    if test.statement_input is not None and run.stdout.split() != test.statement_input.split():
        return (
            "the interactor answers differ from the input for statements:\n"
            f"interactor: {' '.join(run.stdout.split())}\n"
            f"statement:  {' '.join(test.statement_input.split())}"
        )
    if test.answer is None:
        return None
    tout = tout_path.read_text(encoding="utf-8") if tout_path.is_file() else ""
    verdict, comment = _judge(checker, test.input, tout, test.answer, work_dir)
    if verdict != VERDICT_OK:
        return f"checker says {verdict} on the tout of the replayed dialogue: {comment}"
    return None


def _note_suspicious_answers(tests: list[SampleTest], result: SolutionsRunResult) -> None:
    """If on an example with an answer from the spec all `ok` solutions get WA — more likely
    the answer in the spec is wrong than all the solutions at once."""
    ok_solutions = [s for s in result.solutions if s.verdict == "ok" and s.error is None]
    if len(ok_solutions) < 2:
        return
    for index, test in enumerate(tests):
        if all(index < len(s.tests) and s.tests[index].verdict == VERDICT_WA for s in ok_solutions):
            result.errors.append(
                f"{test.name}: all ok solutions get WA — check the expected output in the spec"
            )
