"""Validator tests: the file and the local run of the validator on it. Without the model and
without the network.

A validator test is what Polygon's "Validator tests" holds: an input, the expected verdict
(`VALID` or `INVALID`) and, with test groups, the group whose limits the validator checks
(`--group <N>`; no group — the validator gets no `--group`, `group_index()` is 0: the full
limits). The file `validator_tests.yaml` is written by the `validator_tests` step
(`parse_validator_tests`):

~~~yaml
tests:
  - covers: 'inf.readInt(minN, maxN, "n")'
    verdict: INVALID
    group: 2
    input: |
      11
    expect: "[name=n]"
~~~

`expect` — only for an `INVALID` test, a part of the validator's message: the test must
fail on the check it covers, not on another one (e.g. not on the missing data after a
value out of range — a test with `n` out of range doesn't need the `n` numbers after it).
Every test is small (`MAX_LINES` lines, `MAX_CHARS` characters): a boundary test that
needs more is not written. No two tests have the same input, even in different groups:
Polygon refuses a validator test whose input another one already has (line endings
aside — `same_input_key`).

The local run (`run_validator_tests`): it builds `validator.cpp` and runs it on every test
— the verdict must be the expected one; the message of an `INVALID` test must contain its
`expect`, and if it is testlib's message about an integer out of range (`... equals to X,
violates the range [a, b]`), X must be `a - 1` or `b + 1` — one past the bound. The failures
are the step's (it revises with them); a validator that doesn't build or a file that
doesn't parse — errors of the problem.
"""

from __future__ import annotations

import re
import tempfile
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from orchestrator.checks.run_with_timeout import run_with_timeout
from orchestrator.checks.solutions_run import _BuildError, _compile_cpp
from orchestrator.checks.test_groups import VALIDATOR_GROUP_OPTION, load_test_groups
from orchestrator.polygon.sync import VALIDATOR_FILENAME
from orchestrator.resources import include_dirs as build_include_dirs
from orchestrator.test_groups import EXAMPLES_GROUP

VALIDATOR_TESTS_FILENAME = "validator_tests.yaml"

VERDICT_VALID = "VALID"
VERDICT_INVALID = "INVALID"
VERDICTS = (VERDICT_VALID, VERDICT_INVALID)

# A test a person reads at a glance — in the repository and on Polygon.
MAX_LINES = 20
MAX_CHARS = 500

_KEYS = ("covers", "verdict", "group", "input", "expect")
_VALIDATOR_TIMEOUT_SECONDS = 10.0

Progress = Callable[[str], None]


class ValidatorTestsError(ValueError):
    """A validator tests file that doesn't parse."""


@dataclass(frozen=True)
class ValidatorTest:
    """`covers` — the check of the validator the test covers; `group` — the test group
    whose limits it is validated with, `None` — none (the full limits); `expect` — a part
    of the validator's message on an `INVALID` test."""

    __test__ = False  # not a pytest test class

    covers: str
    verdict: str
    input: str
    group: str | None = None
    expect: str | None = None


def _as_text(value: Any) -> Any:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return str(value)
    return value


def parse_validator_tests(
    text: str,
    source: str = VALIDATOR_TESTS_FILENAME,
    groups: list[str] | None = None,
) -> list[ValidatorTest]:
    """The tests of a validator tests file (see the module docstring). `groups` — the
    names of the problem's test groups (`None` — it has none: a test with a group is an
    error). `ValidatorTestsError` — the file doesn't match the format, has no tests, has a
    test over the size limit or two tests with the same input."""
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ValidatorTestsError(f"{source} is not valid YAML: {exc}") from exc
    items = data.get("tests") if isinstance(data, dict) else None
    if not isinstance(items, list) or not items:
        raise ValidatorTestsError(f"{source}: expected a non-empty list 'tests'")
    tests: list[ValidatorTest] = []
    errors: list[str] = []
    seen: dict[tuple, int] = {}
    for number, item in enumerate(items, start=1):
        where = f"{source}: test {number}"
        if not isinstance(item, dict):
            errors.append(f"{where}: expected a mapping")
            continue
        unknown = sorted(set(item) - set(_KEYS))
        if unknown:
            errors.append(f"{where}: unknown keys {unknown}")
        values = {key: _as_text(item.get(key)) for key in _KEYS}
        problems = [
            f"'{key}' must be a non-empty string"
            for key in ("covers", "verdict")
            if not isinstance(values[key], str) or not values[key].strip()
        ]
        if not isinstance(values["input"], str):
            problems.append("'input' must be a string")
        if problems:
            errors += [f"{where}: {problem}" for problem in problems]
            continue
        verdict = values["verdict"]
        if verdict not in VERDICTS:
            errors.append(f"{where}: verdict {verdict!r} is not one of {list(VERDICTS)}")
            continue
        expect = values["expect"]
        if verdict == VERDICT_INVALID and (not isinstance(expect, str) or not expect.strip()):
            errors.append(
                f"{where}: an INVALID test needs 'expect' — a part of the validator's "
                "message on it (the check it fails on)"
            )
            continue
        if verdict == VERDICT_VALID and expect is not None:
            errors.append(f"{where}: 'expect' is only for an INVALID test")
            continue
        group = values["group"]
        if group is not None:
            if groups is None:
                errors.append(f"{where}: group {group!r}, but the problem has no test groups")
                continue
            if group not in groups:
                errors.append(f"{where}: unknown test group {group!r} (groups: {groups})")
                continue
        size_error = _size_error(values["input"])
        if size_error:
            errors.append(f"{where}: {size_error}")
            continue
        test = ValidatorTest(values["covers"], verdict, values["input"], group, expect)
        key = same_input_key(test.input)
        if key in seen:
            errors.append(
                f"{where}: the same input as test {seen[key]} — Polygon refuses two validator "
                "tests with the same input, even in different groups: change a value the test "
                "doesn't cover (keeping it valid)"
            )
            continue
        seen[key] = number
        tests.append(test)
    if errors:
        raise ValidatorTestsError("; ".join(errors))
    return tests


def same_input_key(text: str) -> str:
    """The input as two tests are compared: only the line endings aside (Polygon stores
    `\\r\\n`); a missing end of line is another input — a test of its own."""
    return text.replace("\r\n", "\n")


def _size_error(text: str) -> str | None:
    lines = len(text.splitlines())
    if lines > MAX_LINES or len(text) > MAX_CHARS:
        return (
            f"the input has {lines} line(s) and {len(text)} character(s) — at most "
            f"{MAX_LINES} lines and {MAX_CHARS} characters: a boundary test that needs more "
            "isn't written (an INVALID one stops right after the value out of range)"
        )
    return None


def coverage_errors(tests: list[ValidatorTest], groups: list[str] | None) -> list[str]:
    """What the set of tests lacks: a `VALID` test of every test group."""
    if not groups:
        return []
    valid = {test.group for test in tests if test.verdict == VERDICT_VALID}
    missing = [name for name in groups if name not in valid]
    if not missing:
        return []
    return [f"{VALIDATOR_TESTS_FILENAME}: no VALID test of test group(s) {', '.join(missing)}"]


def load_validator_tests(path: Path, groups: list[str] | None) -> list[ValidatorTest]:
    """The tests of the file at `path`; no file — none."""
    if not path.is_file():
        return []
    return parse_validator_tests(path.read_text(encoding="utf-8"), path.name, groups)


_RANGE_RE = re.compile(
    r"(?:Integer|Unsigned integer)\b.*?(?:equals to )?(-?\d+),? violates the range "
    r"\[(-?[\d*^]+), (-?[\d*^]+)\]"
)
_HUMAN_NUMBER_RE = re.compile(r"(-?)(?:(\d+)\*)?10\^(\d+)")


def _number(text: str) -> int | None:
    """A number as testlib prints a bound (`toHumanReadableString`: `10^9`, `3*10^7`)."""
    if re.fullmatch(r"-?\d+", text):
        return int(text)
    match = _HUMAN_NUMBER_RE.fullmatch(text)
    if match is None:
        return None
    sign, mantissa, power = match.groups()
    value = int(mantissa or 1) * 10 ** int(power)
    return -value if sign else value


def range_violation(message: str) -> tuple[int, int, int] | None:
    """`(value, min, max)` of testlib's message about an integer out of range; `None` — the
    message isn't one."""
    match = _RANGE_RE.search(message)
    if match is None:
        return None
    value, low, high = (_number(part) for part in match.groups())
    if value is None or low is None or high is None:
        return None
    return value, low, high


def judge_message(test: ValidatorTest, message: str) -> list[str]:
    """What's wrong with the validator's message on an `INVALID` test it rejected."""
    problems = []
    if test.expect is not None and test.expect not in message:
        problems.append(
            f"the validator rejects it, but its message doesn't contain {test.expect!r} — the "
            f"test fails on another check: {message}"
        )
    violation = range_violation(message)
    if violation is not None:
        value, low, high = violation
        if value not in (low - 1, high + 1):
            problems.append(
                f"the value {value} is out of the range [{low}, {high}], but not one past its "
                f"bound ({low - 1} or {high + 1}): {message}"
            )
    return problems


@dataclass
class ValidatorTestsRun:
    """The result of `run_validator_tests`. `failures` — the tests that failed (plain
    data: the test's number, `covers`, what's wrong); `errors` — of the problem."""

    tests: int = 0
    failures: list[dict[str, Any]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    progress: Progress | None = None

    def note(self, message: str) -> None:
        self.notes.append(message)
        if self.progress is not None:
            self.progress(message)

    @property
    def failed(self) -> bool:
        return bool(self.failures or self.errors)


def run_validator_tests(problem_dir: Path, progress: Progress | None = None) -> ValidatorTestsRun:
    """Runs the validator on the tests of `validator_tests.yaml` (see the module
    docstring). Writes nothing into the problem directory."""
    problem_dir = Path(problem_dir)
    run = ValidatorTestsRun(progress=progress)
    groups, group_errors = load_test_groups(problem_dir)
    run.errors.extend(group_errors)
    names = [group.name for group in groups] if groups is not None else None
    path = problem_dir / VALIDATOR_TESTS_FILENAME
    if not path.is_file():
        run.errors.append(f"no {VALIDATOR_TESTS_FILENAME}")
        return run
    try:
        tests = load_validator_tests(path, names)
    except ValidatorTestsError as exc:
        run.errors.append(str(exc))
        return run
    run.errors.extend(coverage_errors(tests, names))
    validator_path = problem_dir / VALIDATOR_FILENAME
    if not validator_path.is_file():
        run.errors.append(f"no {VALIDATOR_FILENAME}")
        return run
    with tempfile.TemporaryDirectory(prefix="validator_tests_") as tmp:
        try:
            validator = _compile_cpp(validator_path, build_include_dirs(problem_dir), Path(tmp))
        except _BuildError as exc:
            run.errors.append(f"{VALIDATOR_FILENAME}: {exc}")
            return run
        run.note(f"{VALIDATOR_FILENAME} on {len(tests)} test(s)")
        for number, test in enumerate(tests, start=1):
            problems = _judge(validator, test)
            if problems:
                run.failures.append({"test": number, "covers": test.covers, "problems": problems})
    run.tests = len(tests)
    return run


def validator_args(group: str | None) -> list[str]:
    """The validator's arguments for a test of `group` (`None` — no `--group`)."""
    if group is None or group == EXAMPLES_GROUP:
        return []
    return [VALIDATOR_GROUP_OPTION, group]


def _judge(validator: Path, test: ValidatorTest) -> list[str]:
    result = run_with_timeout(
        validator, validator_args(test.group), _VALIDATOR_TIMEOUT_SECONDS, test.input
    )
    if result.timed_out:
        return ["the validator timed out"]
    message = " ".join((result.stderr or result.stdout or "").split())
    if test.verdict == VERDICT_VALID:
        if result.exit_code != 0:
            return [f"expected VALID, the validator rejects it: {message}"]
        return []
    if result.exit_code == 0:
        return ["expected INVALID, the validator accepts it"]
    return judge_message(test, message)
