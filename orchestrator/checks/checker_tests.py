"""Checker tests: the files, the tests from the examples and the local run of the checker
on them. Without the model and without the network.

A checker test is what Polygon's "Checker tests" holds: an input, the participant's output,
the jury's answer and the checker's expected verdict. Two files in the problem directory,
in one format (`parse_checker_tests`):

- `checker_tests.yaml` — written by the `checker_tests` step: a test (or a pair) per branch
  of the checker that rejects an answer;
- `checker_tests.auto.yaml` — written by the local run (`run_checker_tests`), derived from
  the examples, not edited by hand: every example with the answer of Main correct as both
  the output and the answer (OK), and every other output of a correct (`ok`, `ma`)
  solution — or the example's `output` in the spec — that differs from it in tokens (OK).

The verdicts are testlib's: `OK`, `WA`, `PE` and `FAIL` (the checker failed: the jury's
answer is wrong; Polygon calls it `CRASHED`).

The local run (`run_checker_tests`): it builds the problem's custom checker and runs it on
every test of both files, the verdict must be the expected one. The input of every test of
`checker_tests.yaml` must pass the validator (with test groups — of at least one group or
of the examples), and the side of the test that isn't wrong must be accepted against the
output of Main correct on the input: the answer of an `OK`, `WA` or `PE` test, the output of
an `OK` or `FAIL` test — so a test whose "correct" side isn't correct is caught too. Without
Main correct this cross-check isn't done (a note). The failures of the tests of
`checker_tests.yaml` are the step's (it revises with them); a test of the examples the
checker doesn't accept, a checker that doesn't build or a file that doesn't parse — errors
of the problem: the author fixes the checker or the solutions.

An interactive problem isn't handled: its checker judges the interactor's `tout`.
"""

from __future__ import annotations

import tempfile
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import yaml

from orchestrator.checks.run_with_timeout import run_with_timeout
from orchestrator.checks.solutions_run import (
    VERDICT_FAIL,
    VERDICT_OK,
    VERDICT_PE,
    VERDICT_WA,
    SampleTest,
    SolutionsRunResult,
    _build_checker,
    _build_solution,
    _BuildError,
    _compile_cpp,
    _execute,
    _judge,
    _limits,
    _parse_solution_filename,
    _read_spec,
    _sample_tests,
    _solution_sources,
)
from orchestrator.checks.test_groups import VALIDATOR_GROUP_OPTION, load_test_groups
from orchestrator.polygon.steps.base import PolygonStepError
from orchestrator.polygon.steps.upload_solutions import CORRECT_VERDICTS, _choose_main
from orchestrator.polygon.sync import VALIDATOR_FILENAME
from orchestrator.resources import include_dirs as build_include_dirs
from orchestrator.test_groups import EXAMPLES_GROUP

CHECKER_TESTS_FILENAME = "checker_tests.yaml"
AUTO_CHECKER_TESTS_FILENAME = "checker_tests.auto.yaml"

VERDICTS = (VERDICT_OK, VERDICT_WA, VERDICT_PE, VERDICT_FAIL)

_AUTO_HEADER = (
    "# Written by the local run of the checker tests (run_checker_tests) from the examples\n"
    "# and the outputs of the correct solutions on them. Don't edit: it is rewritten.\n"
)
_VALIDATOR_TIMEOUT_SECONDS = 10.0

Progress = Callable[[str], None]


class CheckerTestsError(ValueError):
    """A checker tests file that doesn't parse."""


@dataclass(frozen=True)
class CheckerTest:
    """`covers` — what the test checks (a branch of the checker, an example, a solution's
    output); `output` — the participant's output, `answer` — the jury's."""

    __test__ = False  # not a pytest test class

    covers: str
    verdict: str
    input: str
    output: str
    answer: str


def parse_checker_tests(text: str, source: str = CHECKER_TESTS_FILENAME) -> list[CheckerTest]:
    """The tests of a checker tests file: `tests` — a list of mappings with `covers`,
    `verdict` (one of `VERDICTS`), `input`, `output`, `answer` (strings). `CheckerTestsError`
    — the file doesn't match the format, has no tests or has the same test twice."""
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise CheckerTestsError(f"{source} is not valid YAML: {exc}") from exc
    items = data.get("tests") if isinstance(data, dict) else None
    if not isinstance(items, list) or not items:
        raise CheckerTestsError(f"{source}: expected a non-empty list 'tests'")
    tests: list[CheckerTest] = []
    errors: list[str] = []
    seen: dict[tuple[str, str, str], int] = {}
    for number, item in enumerate(items, start=1):
        where = f"{source}: test {number}"
        if not isinstance(item, dict):
            errors.append(f"{where}: expected a mapping")
            continue
        unknown = sorted(set(item) - {"covers", "verdict", "input", "output", "answer"})
        if unknown:
            errors.append(f"{where}: unknown keys {unknown}")
        values: dict[str, str] = {}
        for key in ("covers", "verdict", "input", "output", "answer"):
            value = item.get(key)
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                value = str(value)
            if not isinstance(value, str) or (key != "output" and not value.strip()):
                errors.append(f"{where}: '{key}' must be a non-empty string")
                continue
            values[key] = value
        if len(values) < 5:
            continue
        if values["verdict"] not in VERDICTS:
            errors.append(f"{where}: verdict {values['verdict']!r} is not one of {list(VERDICTS)}")
            continue
        key = (values["input"], values["output"], values["answer"])
        if key in seen:
            errors.append(f"{where}: the same input, output and answer as test {seen[key]}")
            continue
        seen[key] = number
        tests.append(CheckerTest(**values))
    if errors:
        raise CheckerTestsError("; ".join(errors))
    return tests


def load_checker_tests(path: Path) -> list[CheckerTest]:
    """The tests of the file at `path`; no file — none."""
    if not path.is_file():
        return []
    return parse_checker_tests(path.read_text(encoding="utf-8"), path.name)


class _Dumper(yaml.SafeDumper):
    pass


def _represent_str(dumper: yaml.SafeDumper, value: str) -> yaml.Node:
    style = "|" if "\n" in value else None
    return dumper.represent_scalar("tag:yaml.org,2002:str", value, style=style)


_Dumper.add_representer(str, _represent_str)


def dump_checker_tests(tests: list[CheckerTest], header: str = "") -> str:
    """The text of a checker tests file (multi-line strings as block scalars)."""
    body = yaml.dump(
        {"tests": [asdict(test) for test in tests]},
        Dumper=_Dumper,
        allow_unicode=True,
        sort_keys=False,
        width=100,
    )
    return header + body


@dataclass
class CheckerTestsRun:
    """The result of `run_checker_tests`. `failures` — the tests of `checker_tests.yaml`
    that failed (plain data: the test's number, `covers`, what's wrong); `errors` — of the
    problem (see the module docstring)."""

    auto: list[CheckerTest] = field(default_factory=list)
    tests: int = 0
    failures: list[dict[str, Any]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    progress: Progress | None = None

    def note(self, message: str) -> None:
        self.notes.append(message)
        if self.progress is not None:
            self.progress(message)

    @property
    def failed(self) -> bool:
        return bool(self.failures or self.errors)


def run_checker_tests(problem_dir: Path, progress: Progress | None = None) -> CheckerTestsRun:
    """Writes `checker_tests.auto.yaml` from the examples and runs the checker on the tests
    of both files (see the module docstring). In the problem directory it writes only that
    file; with no example — the file isn't written (and an existing one is removed)."""
    problem_dir = Path(problem_dir)
    run = CheckerTestsRun(progress=progress)
    collector = SolutionsRunResult(progress=progress)
    spec = _read_spec(problem_dir, collector)
    try:
        model_tests = load_checker_tests(problem_dir / CHECKER_TESTS_FILENAME)
    except CheckerTestsError as exc:
        run.errors.append(str(exc))
        model_tests = []
    groups, group_errors = load_test_groups(problem_dir)
    run.errors.extend(group_errors)

    include_dirs = build_include_dirs(problem_dir)
    with tempfile.TemporaryDirectory(prefix="checker_tests_") as tmp:
        work_dir = Path(tmp)
        checker = _build_checker(problem_dir, spec, include_dirs, work_dir, collector)
        run.notes.extend(collector.notes)
        run.errors.extend(collector.errors)
        collector.notes.clear()
        collector.errors.clear()
        if checker is None:
            return run

        time_limit_ms, memory_limit_mb = _limits(problem_dir, collector)
        time_limit = time_limit_ms / 1000
        correct, main_name = _correct_solutions(problem_dir, collector)
        programs: dict[str, Any] = {}

        def program_for(name: str):
            if name not in programs:
                try:
                    programs[name] = _build_solution(
                        correct[name], include_dirs, work_dir, memory_limit_mb
                    )
                except _BuildError as exc:
                    programs[name] = str(exc)
                    run.warnings.append(f"{name} is not built: {exc}")
            return programs[name]

        def output_of(name: str, input_text: str) -> str | None:
            program = program_for(name)
            if isinstance(program, str):
                return None
            execution = _execute(
                program, SampleTest(name, input_text, None), time_limit, work_dir, None
            )
            return execution.output if execution.verdict is None else None

        run.auto = _auto_tests(problem_dir, spec, correct, main_name, output_of, run)
        auto_path = problem_dir / AUTO_CHECKER_TESTS_FILENAME
        if run.auto:
            auto_path.write_text(dump_checker_tests(run.auto, _AUTO_HEADER), encoding="utf-8")
        else:
            auto_path.unlink(missing_ok=True)

        for number, test in enumerate(run.auto, start=1):
            verdict, comment = _judge(checker, test.input, test.output, test.answer, work_dir)
            if verdict != test.verdict:
                run.errors.append(
                    f"{AUTO_CHECKER_TESTS_FILENAME}: test {number} ({test.covers}): the checker "
                    f"says {verdict}, expected {test.verdict}: {comment}"
                )

        validator = _build_validator(problem_dir, include_dirs, work_dir, run)
        validator_args = (
            [[VALIDATOR_GROUP_OPTION, g] for g in [EXAMPLES_GROUP, *(g.name for g in groups)]]
            if groups is not None
            else [[]]
        )
        if main_name is None:
            run.note("no correct solution: the tests' answers are not cross-checked")
        for number, test in enumerate(model_tests, start=1):
            problems: list[str] = []
            verdict, comment = _judge(checker, test.input, test.output, test.answer, work_dir)
            if verdict != test.verdict:
                problems.append(f"the checker says {verdict}, expected {test.verdict}: {comment}")
            if validator is not None:
                rejected = _validator_rejects(validator, validator_args, test.input)
                if rejected is not None:
                    problems.append(f"{VALIDATOR_FILENAME} rejects the input: {rejected}")
            if main_name is not None:
                problems += _cross_check(test, checker, main_name, output_of, work_dir, run, number)
            if problems:
                run.failures.append({"test": number, "covers": test.covers, "problems": problems})
        run.tests = len(run.auto) + len(model_tests)
    run.notes[:0] = collector.notes
    run.errors.extend(collector.errors)
    return run


def _correct_solutions(
    problem_dir: Path, collector: SolutionsRunResult
) -> tuple[dict[str, Path], str | None]:
    """The correct solutions (`ok`, `ma`) by name and Main correct among them."""
    parsed = {}
    for path in _solution_sources(problem_dir):
        try:
            parsed[path.name] = _parse_solution_filename(path)
        except PolygonStepError:
            continue
    correct = {name: p.path for name, p in parsed.items() if p.verdict in CORRECT_VERDICTS}
    try:
        main_name = _choose_main(list(parsed.values())) if correct else None
    except PolygonStepError:
        main_name = None
    return correct, main_name


def _auto_tests(
    problem_dir: Path,
    spec: dict | None,
    correct: dict[str, Path],
    main_name: str | None,
    output_of: Callable[[str, str], str | None],
    run: CheckerTestsRun,
) -> list[CheckerTest]:
    """The OK tests of every example (see the module docstring)."""
    tests: list[CheckerTest] = []
    for sample in _sample_tests(problem_dir, spec):
        answer = output_of(main_name, sample.input) if main_name is not None else None
        if answer is None:
            if sample.answer is None:
                run.warnings.append(f"{sample.name}: no expected output — no checker test")
                continue
            answer = sample.answer
            label = "the expected output"
        else:
            label = f"the output of {main_name}"
        candidates = [(f"{sample.name}: {label}", answer)]
        if sample.answer is not None:
            candidates.append((f"{sample.name}: its output in the spec", sample.answer))
        for name in sorted(correct):
            if name != main_name and (output := output_of(name, sample.input)) is not None:
                candidates.append((f"{sample.name}: the output of {name}", output))
        seen: set[tuple[str, ...]] = set()
        for covers, output in candidates:
            tokens = tuple(output.split())
            if tokens in seen:
                continue
            seen.add(tokens)
            tests.append(CheckerTest(covers, VERDICT_OK, sample.input, output, answer))
    return tests


def _build_validator(
    problem_dir: Path, include_dirs: list[Path], work_dir: Path, run: CheckerTestsRun
) -> Path | None:
    path = problem_dir / VALIDATOR_FILENAME
    if not path.is_file():
        run.note(f"no {VALIDATOR_FILENAME}: the tests' inputs are not validated")
        return None
    try:
        return _compile_cpp(path, include_dirs, work_dir)
    except _BuildError as exc:
        run.errors.append(f"{VALIDATOR_FILENAME}: {exc}")
        return None


def _validator_rejects(binary: Path, variants: list[list[str]], text: str) -> str | None:
    """`None` — the validator accepts the input with one of the argument `variants` (with
    test groups — of some group), otherwise — why it rejects it."""
    reason = ""
    for args in variants:
        result = run_with_timeout(binary, args, _VALIDATOR_TIMEOUT_SECONDS, text)
        if not result.timed_out and result.exit_code == 0:
            return None
        reason = "timed out" if result.timed_out else (result.stderr or result.stdout).strip()
    return reason


def _cross_check(
    test: CheckerTest,
    checker,
    main_name: str,
    output_of: Callable[[str, str], str | None],
    work_dir: Path,
    run: CheckerTestsRun,
    number: int,
) -> list[str]:
    """The side of the test that isn't wrong against the output of Main correct."""
    expected = output_of(main_name, test.input)
    if expected is None:
        run.warnings.append(
            f"{CHECKER_TESTS_FILENAME}: test {number} ({test.covers}): {main_name} gives no "
            "output on its input — not cross-checked"
        )
        return []
    sides = []
    if test.verdict in (VERDICT_OK, VERDICT_WA, VERDICT_PE):
        sides.append(("answer", test.answer))
    if test.verdict in (VERDICT_OK, VERDICT_FAIL):
        sides.append(("output", test.output))
    problems = []
    for side, text in sides:
        verdict, comment = _judge(checker, test.input, text, expected, work_dir)
        if verdict != VERDICT_OK:
            problems.append(
                f"its {side} is not a correct answer: against the output of {main_name} the "
                f"checker says {verdict}: {comment}"
            )
    return problems
