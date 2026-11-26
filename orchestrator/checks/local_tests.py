"""Local generation of tests from the test-script — `orchestrator <problem> local
check --full`. Without the model and without the network; an approximation of what
Polygon generates, not a guarantee: testlib's `rnd` is deterministic, but `long`,
`std::shuffle`, the order of `unordered_*` and unstable sorting depend on the
compiler and the OS.

What is done:

1. The script is expanded and checked (`test_script_check.check_script`);
   a script with identical or unacceptable lines isn't generated.
2. A line `<generator> <parameters> [| <generator> ...] > $` is run as
   a chain; the output is a test. Test numbers come after the examples: the examples take
   `testIndex` 1..N (`upload_statement`), `$` — the next free one.
   Explicit numbers (`> 5`) aren't supported: such lines are an error.
3. The cache — in the problem directory's `.tests_cache/`. A test is a pure function of the command and
   the generators' code: the `registerGen` seed is taken from the arguments, and the test number
   isn't passed to the generator. So a test's key (`_test_key`) is the hash of the command
   and of the build hashes of its generators (source, `problem_lib.h`, `testlib.h`,
   flags); renumbering tests doesn't touch the cache, editing a generator
   invalidates only the tests with it. Files with keys no longer in the set
   are deleted. `manifest.txt` there — the number, file and command of each
   test, for manual inspection.
4. Identical tests are searched for by content (`find_equal_tests`), together with
   the examples: different commands can give the same test. With test groups — within
   one group and between a group and the groups it depends on, directly or not (the
   examples — group 0, no group depends on it): the group is judged only after those
   are passed, so the copy adds nothing; the same test in groups independent of each
   other is deliberate. Line endings
   are normalized, the rest is compared byte by byte. Each group of equal tests is an
   error (`equal_test_issues`) with the script lines they correspond to; examples
   equal only to each other are the statement's matter, not the tests'.
5. The validator (`validator.cpp`, if any) is run on each test. A problem with test
   groups (`test_groups` in `constraints.yaml`, `test_groups.py`): a test's group is the
   `-group=<N>` marker of its script line, the validator gets `--group <N>`, as on
   Polygon; a test without the marker or of an unknown group, a group without tests —
   errors (`check_test_groups`).
"""

from __future__ import annotations

import hashlib
import re
import shutil
import subprocess
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from orchestrator.checks.compile_check import compile_check
from orchestrator.checks.run_with_timeout import _raise_stack_limit
from orchestrator.checks.statement_check import STATEMENT_DIRNAME
from orchestrator.checks.test_groups import VALIDATOR_GROUP_OPTION, TestGroup, upstream_groups
from orchestrator.checks.test_script_check import (
    ScriptCheckUnavailable,
    ScriptLine,
    ScriptRenderError,
    check_script,
)
from orchestrator.polygon.sync import VALIDATOR_FILENAME
from orchestrator.problem_paths import TEST_SCRIPT_FILENAME, is_removed_mark
from orchestrator.resources import TESTLIB_FILENAME, include_dirs, resource_path
from orchestrator.test_groups import EXAMPLES_GROUP, group_marker

TESTS_CACHE_DIRNAME = ".tests_cache"
MANIFEST_FILENAME = "manifest.txt"
GENERATORS_DIRNAME = "generators"
EXAMPLES_SUBDIR = "examples"
_EXAMPLE_NAME = re.compile(r"^example_(\d+)\.txt$")
_PROBLEM_LIB = "problem_lib.h"
# They change together with the `compile_check` flags: other flags — another build.
_BUILD_VERSION = "g++ -std=c++17 -O2"
_COMPILE_TIMEOUT_SECONDS = 60.0
_GENERATOR_TIMEOUT_SECONDS = 60.0
_VALIDATOR_TIMEOUT_SECONDS = 30.0
_NEXT_TEST_TARGET = "$"
# What `local clear` deletes: only what the local checks recreate
# themselves. The model's step cache (`.cache/`), `polygon_state.json` and backups aren't
# included: they can't be restored without cost, or at all.
LOCAL_CACHE_DIRNAMES = (TESTS_CACHE_DIRNAME,)

Progress = Callable[[str], None]


@dataclass(frozen=True)
class LocalTest:
    number: int
    command: str
    path: Path
    # The script's file name and the line that printed the command (`None` — not recognized).
    script: str = ""
    script_line: int | None = None
    # The `-group=<N>` marker of the line (`None` — no marker).
    group: str | None = None

    @property
    def name(self) -> str:
        return f"test {self.number}"


@dataclass
class EqualTests:
    """A group of tests with identical contents: the names in the order of their numbers."""

    names: list[str]


# The kinds of `TestIssue` — what is wrong, so that the `run_tests` report reaches the step
# that writes the file at fault (`steps.tests_check`).
ISSUE_SCRIPT = "script"  # no script, it doesn't expand, a bad line, the lines' groups
ISSUE_GENERATOR_MISSING = "generator_missing"  # a line calls a generator with no source
ISSUE_GENERATOR_BUILD = "generator_build"  # a generator doesn't compile
ISSUE_GENERATOR_RUN = "generator_run"  # a generator of a line fails or prints nothing
ISSUE_VALIDATOR_BUILD = "validator_build"
ISSUE_VALIDATOR_REJECT = "validator_reject"  # the validator rejects a test or times out
ISSUE_UNAVAILABLE = "unavailable"  # the script can't be checked here (no `java`)
# Script tests with identical contents in one group or in groups one of which depends on
# the other (`find_equal_tests`); only examples equal to each other — `ISSUE_EQUAL_EXAMPLES`.
ISSUE_EQUAL_TESTS = "equal_tests"
ISSUE_EQUAL_EXAMPLES = "equal_examples"


@dataclass(frozen=True)
class TestIssue:
    """An error of the local tests: `message` — as `local check --full` prints it; the
    script line's command and number and the generator — if the error is about them."""

    kind: str
    message: str
    command: str | None = None
    script_line: int | None = None
    generator: str | None = None


@dataclass
class LocalTestsResult:
    tests: list[LocalTest] = field(default_factory=list)
    generated: int = 0
    reused: int = 0
    removed: int = 0
    errors: list[str] = field(default_factory=list)
    # The same errors with their kinds, in the same order.
    issues: list[TestIssue] = field(default_factory=list)

    def add_issue(self, issue: TestIssue) -> None:
        self.issues.append(issue)
        self.errors.append(issue.message)


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _test_key(command: str, generator_hashes: list[str]) -> str:
    return _sha256("\n".join([command, *generator_hashes]).encode("utf-8"))


def parse_chain(text: str) -> tuple[str, list[list[str]]]:
    """A line of the expanded script -> (the command without `> $`, the parts of the chain: the
    generator's name and its arguments). `ValueError` — the line can't be run."""
    command, separator, target = text.rpartition(">")
    if not separator:
        raise ValueError("no '> $' redirect")
    if target.strip() != _NEXT_TEST_TARGET:
        raise ValueError(f"only '> $' is supported, got '> {target.strip()}'")
    stages = [stage.split() for stage in command.split("|")]
    if any(not stage for stage in stages):
        raise ValueError("empty command in the chain")
    return " ".join(command.split()), stages


def _generator_source(problem_dir: Path, name: str) -> Path | None:
    # On Polygon a generator is named after its file without the extension.
    path = problem_dir / GENERATORS_DIRNAME / f"{name}.cpp"
    return path if path.is_file() and not is_removed_mark(path) else None


def _build_hash(source: Path, problem_dir: Path) -> str:
    """The hash of everything the build depends on: the source and the resources — of the
    same version the compiler finds (the problem's, then the default, see
    `orchestrator/resources.py`)."""
    digest = hashlib.sha256(_BUILD_VERSION.encode("utf-8"))
    digest.update(source.read_bytes())
    for header in (_PROBLEM_LIB, TESTLIB_FILENAME):
        path = resource_path(problem_dir, header)
        if path is not None:
            digest.update(path.read_bytes())
    return digest.hexdigest()


def _build_generator(
    source: Path, build_hash: str, bin_dir: Path, problem_dir: Path
) -> tuple[Path | None, str]:
    """(binary, error). The build is cached by `build_hash`."""
    target = bin_dir / f"{source.stem}-{build_hash[:16]}.bin"
    if target.is_file():
        return target, ""
    compiled = compile_check(
        source, include_dirs(problem_dir), timeout_seconds=_COMPILE_TIMEOUT_SECONDS
    )
    if not compiled.success or compiled.binary_path is None:
        return None, compiled.stderr.strip()
    bin_dir.mkdir(parents=True, exist_ok=True)
    shutil.move(str(compiled.binary_path), target)
    return target, ""


def _run_chain(stages: list[tuple[Path, list[str]]], cwd: Path) -> tuple[bytes | None, str, int]:
    """(the output of the last generator, error, the index of the failed part of the chain):
    bytes as is, the output of each is the input of the next."""
    data = b""
    for index, (binary, args) in enumerate(stages):
        try:
            run = subprocess.run(
                [str(binary), *args],
                input=data,
                capture_output=True,
                timeout=_GENERATOR_TIMEOUT_SECONDS,
                cwd=cwd,
                preexec_fn=_raise_stack_limit,
            )
        except subprocess.TimeoutExpired:
            return None, f"{binary.name} exceeded {_GENERATOR_TIMEOUT_SECONDS:.0f}s", index
        if run.returncode != 0:
            stderr = run.stderr.decode("utf-8", "replace").strip()
            return None, f"{binary.name} exit code {run.returncode}: {stderr}", index
        data = run.stdout
    return data, "", -1


def _line_issue(
    line: ScriptLine, kind: str, message: str, generator: str | None = None
) -> TestIssue:
    return TestIssue(kind, message, line.text, line.source.line if line.source else None, generator)


def _describe_line(line: ScriptLine) -> str:
    where = f" (script line {line.source.line})" if line.source else ""
    return f'"{line.text}"{where}'


def generate_tests(problem_dir: Path, progress: Progress | None = None) -> LocalTestsResult:
    """Expands the problem's script and brings the test cache to the current set (see the
    module docstring). An error in a line doesn't prevent generating the rest."""
    # Generators are run from the cache directory — the paths must be absolute.
    problem_dir = Path(problem_dir).resolve()
    result = LocalTestsResult()
    script = problem_dir / TEST_SCRIPT_FILENAME
    if not script.is_file():
        result.add_issue(TestIssue(ISSUE_SCRIPT, f"no test script ({TEST_SCRIPT_FILENAME})"))
        return result
    try:
        checked = check_script(script.read_text(encoding="utf-8"), script.name)
    except ScriptCheckUnavailable as exc:
        result.add_issue(TestIssue(ISSUE_UNAVAILABLE, f"{script.name}: check unavailable — {exc}"))
        return result
    except ScriptRenderError as exc:
        result.add_issue(
            TestIssue(
                ISSUE_SCRIPT, f"{script.name}: FreeMarker failed to expand the script:\n{exc}"
            )
        )
        return result
    if checked.failed:
        result.add_issue(
            TestIssue(
                ISSUE_SCRIPT,
                f"{script.name}: tests are not generated — fix the script first:\n{checked.report}",
            )
        )
        return result

    cache_dir = problem_dir / TESTS_CACHE_DIRNAME
    objects_dir = cache_dir / "tests"
    bin_dir = cache_dir / "bin"
    objects_dir.mkdir(parents=True, exist_ok=True)

    builds: dict[str, tuple[str, Path | None, str]] = {}
    first_number = len(sample_paths(problem_dir)) + 1
    keep_keys: set[str] = set()
    keep_bins: set[str] = set()
    manifest: list[str] = []
    for index, line in enumerate(checked.lines):
        number = first_number + index
        try:
            command, stages = parse_chain(line.text)
        except ValueError as exc:
            result.add_issue(
                _line_issue(line, ISSUE_SCRIPT, f"test {number}: {exc}: {_describe_line(line)}")
            )
            continue

        chain: list[tuple[Path, list[str]]] = []
        hashes: list[str] = []
        failure = ""
        failed_generator = ""
        failure_kind = ISSUE_GENERATOR_BUILD
        for name, *args in stages:
            if name not in builds:
                source = _generator_source(problem_dir, name)
                if source is None:
                    builds[name] = ("", None, f"no {GENERATORS_DIRNAME}/{name}.cpp")
                else:
                    build_hash = _build_hash(source, problem_dir)
                    if (
                        progress is not None
                        and not (bin_dir / f"{name}-{build_hash[:16]}.bin").is_file()
                    ):
                        progress(f"building {name}")
                    binary, error = _build_generator(source, build_hash, bin_dir, problem_dir)
                    builds[name] = (
                        build_hash,
                        binary,
                        f"{GENERATORS_DIRNAME}/{name}.cpp failed to compile:\n{error}"
                        if error
                        else "",
                    )
            build_hash, binary, error = builds[name]
            if binary is None:
                failure, failed_generator = error, name
                if not build_hash:
                    failure_kind = ISSUE_GENERATOR_MISSING
                break
            keep_bins.add(binary.name)
            chain.append((binary, args))
            hashes.append(build_hash)
        if failure:
            result.add_issue(
                _line_issue(line, failure_kind, f"test {number}: {failure}", failed_generator)
            )
            continue

        key = _test_key(command, hashes)
        path = objects_dir / key
        if path.is_file():
            result.reused += 1
        else:
            data, error, failed = _run_chain(chain, cache_dir)
            if data is None:
                result.add_issue(
                    _line_issue(
                        line,
                        ISSUE_GENERATOR_RUN,
                        f"test {number}: {error}: {_describe_line(line)}",
                        stages[failed][0],
                    )
                )
                continue
            if not data.strip():
                result.add_issue(
                    _line_issue(
                        line,
                        ISSUE_GENERATOR_RUN,
                        f"test {number}: empty output: {_describe_line(line)}",
                        stages[-1][0],
                    )
                )
                continue
            part = path.with_name(key + ".part")
            part.write_bytes(data)
            part.replace(path)
            result.generated += 1
        keep_keys.add(key)
        result.tests.append(
            LocalTest(
                number,
                command,
                path,
                script.name,
                line.source.line if line.source else None,
                group_marker(command),
            )
        )
        manifest.append(f"{number}\t{path.relative_to(cache_dir).as_posix()}\t{command}")

    for path in objects_dir.iterdir():
        if path.name not in keep_keys:
            path.unlink()
            result.removed += 1
    if bin_dir.is_dir():
        for path in bin_dir.iterdir():
            if path.name not in keep_bins:
                path.unlink()
    (cache_dir / MANIFEST_FILENAME).write_text(
        "".join(line + "\n" for line in manifest), encoding="utf-8"
    )
    return result


def example_files(examples_dir: Path) -> list[tuple[int, Path]]:
    """The examples' input files in the order of their numbers: (N, `example_<N>.txt`)."""
    if not examples_dir.is_dir():
        return []
    return sorted(
        (int(match.group(1)), path)
        for path in examples_dir.iterdir()
        if path.is_file() and (match := _EXAMPLE_NAME.match(path.name))
    )


def sample_paths(problem_dir: Path) -> list[tuple[str, Path]]:
    """The examples (tests 1..N) in order: the name and the input file."""
    return [
        (path.stem, path)
        for _, path in example_files(problem_dir / STATEMENT_DIRNAME / EXAMPLES_SUBDIR)
    ]


def _normalized_hash(path: Path) -> str:
    return _sha256(path.read_bytes().replace(b"\r\n", b"\n"))


def find_equal_tests(
    problem_dir: Path, tests: list[LocalTest], groups: list[TestGroup] | None = None
) -> list[EqualTests]:
    """Groups of tests (and examples) with identical contents — in the order of first
    appearance. With test groups (some test has a `-group` marker) a test is compared
    with the tests of its own group and of the groups it depends on, directly or not
    (`groups`' `dependencies`, `upstream_groups`; the examples are group 0, which no group
    depends on): such a copy adds nothing — the group is judged only after those are
    passed. The same test in groups independent of each other is deliberate (e.g. an edge
    case that fits several groups' limits), so it isn't reported."""
    grouped = any(test.group is not None for test in tests)
    examples_group = EXAMPLES_GROUP if grouped else None
    upstream = upstream_groups(groups or [])
    entries = [(examples_group, name, path) for name, path in sample_paths(problem_dir)]
    entries += [(test.group, test.name, test.path) for test in tests]
    by_content: dict[str, list[int]] = {}
    for index, (_, _, path) in enumerate(entries):
        by_content.setdefault(_normalized_hash(path), []).append(index)

    def related(first: str | None, second: str | None) -> bool:
        return (
            first == second
            or first in upstream.get(second or "", ())
            or second in upstream.get(first or "", ())
        )

    # Connected components of "the same group or one depends on the other" among the
    # entries of one content.
    parent = list(range(len(entries)))

    def root(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    for same in by_content.values():
        for position, i in enumerate(same):
            for j in same[:position]:
                if related(entries[i][0], entries[j][0]):
                    parent[root(i)] = root(j)
    components: dict[int, list[int]] = {}
    for index in range(len(entries)):
        components.setdefault(root(index), []).append(index)
    found = sorted(indices for indices in components.values() if len(indices) > 1)
    return [EqualTests([entries[i][1] for i in indices]) for indices in found]


def clear_local_caches(problem_dir: Path) -> list[tuple[str, int]]:
    """Deletes the problem's local check caches (`LOCAL_CACHE_DIRNAMES`).
    Returns (the directory name, how many bytes it took) for each deleted one."""
    removed = []
    for name in LOCAL_CACHE_DIRNAMES:
        path = Path(problem_dir) / name
        if path.is_symlink():
            path.unlink()
            removed.append((name, 0))
        elif path.is_dir():
            size = sum(p.stat().st_size for p in path.rglob("*") if p.is_file())
            shutil.rmtree(path)
            removed.append((name, size))
    return removed


def _equal_lines(group: EqualTests, tests: list[LocalTest]) -> list[str]:
    by_name = {test.name: test for test in tests}
    lines = [" = ".join(group.names)]
    for name in group.names:
        test = by_name.get(name)
        if test is None:
            lines.append(f"  {name}: example of the statement")
            continue
        where = f" ({test.script} line {test.script_line})" if test.script_line else ""
        lines.append(f"  {name}: {test.command}{where}")
    return lines


def equal_test_issues(groups: list[EqualTests], tests: list[LocalTest]) -> list[TestIssue]:
    """An error per group of `find_equal_tests`: a copy of a test in the same test group or
    in a group that depends on the other's adds nothing — the generated contents, not the
    commands, are compared. Only examples equal to each other — not the tests' error."""
    by_name = {test.name: test for test in tests}
    issues = []
    for group in groups:
        script_tests = [by_name[name] for name in group.names if name in by_name]
        first = script_tests[0] if script_tests else None
        message = "\n".join(
            [
                "equal tests: these come out identical when generated, in one test group "
                "or in groups one of which depends on the other (the copy adds nothing — "
                "keep one, or make the others differ):",
                *("  " + line for line in _equal_lines(group, tests)),
            ]
        )
        issues.append(
            TestIssue(
                ISSUE_EQUAL_TESTS if first else ISSUE_EQUAL_EXAMPLES,
                message,
                command=first.command if first else None,
                script_line=first.script_line if first else None,
            )
        )
    return issues


def validate_tests(
    problem_dir: Path,
    tests: list[LocalTest],
    groups: list[TestGroup] | None = None,
) -> tuple[list[str], bool]:
    """(errors, whether the validator was run) — `validate_test_issues` as messages."""
    issues, validated = validate_test_issues(problem_dir, tests, groups)
    return [issue.message for issue in issues], validated


def validate_test_issues(
    problem_dir: Path,
    tests: list[LocalTest],
    groups: list[TestGroup] | None = None,
) -> tuple[list[TestIssue], bool]:
    """(errors, whether the validator was run). No `validator.cpp` — it wasn't run.
    The build is cached, as for generators. With `groups` (the problem has test groups)
    a test with a group marker is validated with `--group <N>`."""
    problem_dir = Path(problem_dir).resolve()
    source = problem_dir / VALIDATOR_FILENAME
    if not source.is_file():
        return [], False
    bin_dir = problem_dir / TESTS_CACHE_DIRNAME / "validator_bin"
    binary, error = _build_generator(source, _build_hash(source, problem_dir), bin_dir, problem_dir)
    if binary is None:
        return [
            TestIssue(ISSUE_VALIDATOR_BUILD, f"{VALIDATOR_FILENAME}: compilation failed:\n{error}")
        ], True
    for path in bin_dir.iterdir():
        if path != binary:
            path.unlink()
    errors: list[TestIssue] = []
    for test in tests:
        with_group = groups is not None and test.group is not None
        args = [VALIDATOR_GROUP_OPTION, test.group] if with_group else []
        name = f"{test.name} (group {test.group})" if with_group else test.name
        try:
            with test.path.open("rb") as stdin:
                run = subprocess.run(
                    [str(binary), *args],
                    stdin=stdin,
                    capture_output=True,
                    timeout=_VALIDATOR_TIMEOUT_SECONDS,
                )
        except subprocess.TimeoutExpired:
            message = f"{VALIDATOR_FILENAME} timed out on {name}: {test.command}"
            errors.append(
                TestIssue(ISSUE_VALIDATOR_REJECT, message, test.command, test.script_line)
            )
            continue
        if run.returncode != 0:
            reason = (run.stderr or run.stdout).decode("utf-8", "replace").strip()
            message = f"{VALIDATOR_FILENAME} rejects {name}: {reason}\n    {test.command}"
            errors.append(
                TestIssue(ISSUE_VALIDATOR_REJECT, message, test.command, test.script_line)
            )
    return errors, True
