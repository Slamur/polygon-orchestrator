"""The local generation of the tests (`run_tests`) — a mechanical step after the test
preparation (`tests_plan`, `generators_draft`, `test_script_draft`) and before the
solutions, CLAUDE.md, "Role of each generative step", item 3, and
`.claude/rules/step-roles/3-tests.md`.

It is `local check --full` without the statement checks: the tests from the script (with
the cache of `.tests_cache/`), the lines' groups, the validator on every test; identical
tests are only reported. Any error fails the run and stops the pipeline: the solution
stages after it would only meet the same errors in their own runs.

Each error has a kind (`local_tests.TestIssue`), and the kind says whose files may be at
fault (`ROUTES`): a script that doesn't expand or a line without its group — the script;
a generator that doesn't compile — the generators; a line that calls a generator with no
source — the generators or the script; a generator that fails on a line or a test the
validator rejects — the design (the commands' arguments), the generators (their code) and
the script (how it wrote the plan's commands). The validator that doesn't compile, invalid
groups in `constraints.yaml`, no `java` — no step's, the author fixes them.

The report (`.cache/run_tests.report.json`) keeps the errors and, for each step it is
routed to, the hash of that step's files at the time of the run. A step reads the errors
routed to it (`failed_report`) — and reruns even with a valid cache to revise its files
with them — while the run's result is a failure, its own files are the same as at the run
and nothing else the run reads (the validator, the limits, the examples) has changed.
So each step revises with one report once: its revised files make the report outdated
for it, and the next run of `run_tests` writes a new one.
"""

from __future__ import annotations

import datetime
import hashlib
import logging
from dataclasses import asdict
from pathlib import Path
from typing import Any, ClassVar

from orchestrator.cache import (
    CacheEntry,
    compute_input_values,
    input_parts_from_values,
    load_cache_entry,
    save_cache_entry,
)
from orchestrator.cache import compute_input_hash as _cache_compute_input_hash
from orchestrator.checks.local_tests import (
    GENERATORS_DIRNAME,
    ISSUE_GENERATOR_BUILD,
    ISSUE_GENERATOR_MISSING,
    ISSUE_GENERATOR_RUN,
    ISSUE_SCRIPT,
    ISSUE_VALIDATOR_REJECT,
    TestIssue,
    find_equal_tests,
    format_equal_tests,
    generate_tests,
    validate_test_issues,
)
from orchestrator.checks.test_groups import check_test_groups, load_test_groups
from orchestrator.llm_report import report_path
from orchestrator.model_contracts import ModelResponse
from orchestrator.problem_paths import (
    CONSTRAINTS_FILENAME,
    TEST_SCRIPT_FILENAME,
    TESTS_DESIGN_FILENAME,
    TESTS_PLAN_FILENAME,
    authoring_path,
)
from orchestrator.spec import ProblemSpec
from orchestrator.steps.base import (
    PROBLEMS_DIR,
    PROMPTS_DIR,
    STATUS_CONFIRMED,
    TEMPLATES_DIR,
    StepResult,
    StepRevision,
)
from orchestrator.steps.solutions_check import (
    STATUS_FAILED,
    GateFailedError,
    load_run_report,
    read_author_comment,
    save_run_report,
)

logger = logging.getLogger(__name__)

STEP_NAME = "run_tests"
# The report's key: the number of tests of each group (read by `scoring_table`).
GROUP_COUNTS_KEY = "group_counts"

TESTS_PLAN_STEP = "tests_plan"
GENERATORS_STEP = "generators_draft"
TEST_SCRIPT_STEP = "test_script_draft"
# The steps that may revise with the run's report, in the order of `STEP_ORDER`.
REVISING_STEPS = (TESTS_PLAN_STEP, GENERATORS_STEP, TEST_SCRIPT_STEP)

# An error's kind -> the steps whose files may cause it; a kind not listed — no step's.
ROUTES: dict[str, tuple[str, ...]] = {
    ISSUE_SCRIPT: (TEST_SCRIPT_STEP,),
    ISSUE_GENERATOR_BUILD: (GENERATORS_STEP,),
    ISSUE_GENERATOR_MISSING: (GENERATORS_STEP, TEST_SCRIPT_STEP),
    ISSUE_GENERATOR_RUN: REVISING_STEPS,
    ISSUE_VALIDATOR_REJECT: REVISING_STEPS,
}
# Invalid groups in `constraints.yaml`: no test step's.
_ISSUE_CONSTRAINTS = "constraints"

_VALIDATOR = "validator.cpp"
_PROBLEM_LIB = "problem_lib.h"
_EXAMPLES_DIR = "statement/examples"


def _step_files(problem_dir: Path, step_name: str) -> list[Path]:
    """The files `step_name` writes — whose change makes the report outdated for it."""
    if step_name == TESTS_PLAN_STEP:
        return [
            authoring_path(problem_dir, TESTS_DESIGN_FILENAME),
            authoring_path(problem_dir, TESTS_PLAN_FILENAME),
        ]
    if step_name == GENERATORS_STEP:
        directory = problem_dir / GENERATORS_DIRNAME
        return sorted(p for p in directory.rglob("*") if p.is_file()) if directory.is_dir() else []
    return [problem_dir / TEST_SCRIPT_FILENAME]


def _other_files(problem_dir: Path) -> list[Path]:
    """What the run reads besides the test steps' files."""
    paths = [
        problem_dir / _VALIDATOR,
        problem_dir / _PROBLEM_LIB,
        authoring_path(problem_dir, CONSTRAINTS_FILENAME),
    ]
    examples = problem_dir / _EXAMPLES_DIR
    if examples.is_dir():
        paths += sorted(p for p in examples.rglob("*") if p.is_file())
    return paths


def _file_hashes(problem_dir: Path, paths: list[Path]) -> dict[str, str]:
    return {
        path.relative_to(problem_dir).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in paths
        if path.is_file()
    }


def _digest(hashes: dict[str, str]) -> str:
    return hashlib.sha256(repr(sorted(hashes.items())).encode("utf-8")).hexdigest()


class TestsGate:
    """`run_tests` with the interface of the step modules (`pipeline._STEP_MODULES`)."""

    STEP_NAME: ClassVar[str] = STEP_NAME
    INPUTS_KEY: ClassVar[str] = f"{STEP_NAME}: inputs"
    # Instead of the model class and the prompt hash in the cache entry.
    MODEL: ClassVar[str] = "none"
    PROMPT_HASH: ClassVar[str] = "none"

    def skip_detail(self, spec: ProblemSpec, **_: Any) -> str | None:
        return None

    def load_inputs(
        self, problem_id: str, spec: ProblemSpec, *, problems_dir: Path = PROBLEMS_DIR
    ) -> dict[str, Any]:
        """The hashes of the files the run reads: the generators, the script and the rest."""
        problem_dir = Path(problems_dir) / problem_id
        paths = _step_files(problem_dir, GENERATORS_STEP) + _step_files(
            problem_dir, TEST_SCRIPT_STEP
        )
        return {"files": _file_hashes(problem_dir, paths + _other_files(problem_dir))}

    def hash_inputs(
        self,
        problem_id: str,
        spec: ProblemSpec,
        upstream_artifacts: dict[str, Any] | None = None,
    ) -> tuple[Any, dict[str, Any] | None]:
        inputs = self._inputs(problem_id, upstream_artifacts)
        return {}, {f"file: {name}": digest for name, digest in inputs["files"].items()}

    def compute_input_hash(
        self,
        problem_id: str,
        spec: ProblemSpec,
        upstream_artifacts: dict[str, Any] | None = None,
    ) -> str:
        return _cache_compute_input_hash(
            self.STEP_NAME, *self.hash_inputs(problem_id, spec, upstream_artifacts)
        )

    def extra_context_documents(
        self, spec: ProblemSpec, *, templates_dir: Path = TEMPLATES_DIR
    ) -> None:
        return None

    def primary_artifact_path(
        self, problem_id: str, spec: ProblemSpec, *, problems_dir: Path = PROBLEMS_DIR
    ) -> Path:
        return report_path(Path(problems_dir) / problem_id, self.STEP_NAME)

    def current_artifacts(
        self, problem_id: str, spec: ProblemSpec, *, problems_dir: Path = PROBLEMS_DIR
    ) -> dict[str, Path]:
        return {}

    def artifact_present(
        self, problem_id: str, spec: ProblemSpec, *, problems_dir: Path = PROBLEMS_DIR
    ) -> bool:
        # The run has no artifact: an unchanged input is enough to reuse its result.
        return True

    def failed_report(
        self, problem_id: str, step_name: str, *, problems_dir: Path = PROBLEMS_DIR
    ) -> dict[str, Any] | None:
        """The last run's errors routed to `step_name` and the author's comment — if the run
        failed, the step's files are the same as at the run and nothing else the run read
        has changed (see the module docstring); otherwise `None`."""
        entry = load_cache_entry(problem_id, self.STEP_NAME, problems_dir=problems_dir)
        if entry is None or entry.status != STATUS_FAILED:
            return None
        report = load_run_report(problem_id, self.STEP_NAME, problems_dir=problems_dir) or {}
        problem_dir = Path(problems_dir) / problem_id
        recorded = report.get("files") or {}
        if recorded.get("other") != _digest(_file_hashes(problem_dir, _other_files(problem_dir))):
            return None
        if recorded.get(step_name) != _digest(
            _file_hashes(problem_dir, _step_files(problem_dir, step_name))
        ):
            return None
        issues = [
            issue
            for issue in report.get("issues", [])
            if step_name in ROUTES.get(issue.get("kind", ""), ())
        ]
        if not issues:
            return None
        return {
            "report": report_path(problem_dir, self.STEP_NAME).relative_to(problem_dir).as_posix(),
            "issues": issues,
            "comment": read_author_comment(problem_dir, self.STEP_NAME),
        }

    def revision_reason(self, report: dict[str, Any] | None) -> str | None:
        """Why a test step reruns even with a valid cache: `report` (`failed_report`).
        Not `forced_rerun_reason`: the run itself has no such reason."""
        if report is None:
            return None
        return (
            f"the local tests '{self.STEP_NAME}' failed with {len(report['issues'])} error(s) "
            f"that may come from this step's files — revising with its report "
            f"({report['report']})"
        )

    def run_step(
        self,
        problem_id: str,
        spec: ProblemSpec,
        upstream_artifacts: dict[str, Any] | None = None,
        *,
        revision: StepRevision | None = None,
        saved_response: ModelResponse | None = None,
        prompts_dir: Path = PROMPTS_DIR,
        problems_dir: Path = PROBLEMS_DIR,
        templates_dir: Path = TEMPLATES_DIR,
    ) -> StepResult:
        """Generates the tests and validates them, records the result (the cache entry and
        `.cache/run_tests.report.json`). `GateFailedError` — the run failed;
        `revision`/`saved_response` aren't used."""
        problem_dir = Path(problems_dir) / problem_id
        issues, warnings, counts = _run(problem_dir, Path(templates_dir))
        count = sum(counts.values())
        failed = bool(issues)
        files = {"other": _digest(_file_hashes(problem_dir, _other_files(problem_dir)))}
        for step in REVISING_STEPS:
            files[step] = _digest(_file_hashes(problem_dir, _step_files(problem_dir, step)))
        save_run_report(
            problem_id,
            self.STEP_NAME,
            {
                "issues": [asdict(issue) for issue in issues],
                "warnings": warnings,
                "files": files,
                # The tests of each group (`scoring_table`: the points of one test of an
                # `each_test` group) — only of a passed run.
                **({} if failed else {GROUP_COUNTS_KEY: _group_counts(counts)}),
            },
            problems_dir=problems_dir,
        )

        section, upstream = self.hash_inputs(problem_id, spec, upstream_artifacts)
        input_values = compute_input_values(section, upstream)
        save_cache_entry(
            problem_id,
            self.STEP_NAME,
            CacheEntry(
                input_hash=self.compute_input_hash(problem_id, spec, upstream_artifacts),
                prompt_hash=self.PROMPT_HASH,
                model=self.MODEL,
                status=STATUS_FAILED if failed else STATUS_CONFIRMED,
                timestamp=datetime.datetime.now(datetime.timezone.utc).isoformat(),
                input_parts=input_parts_from_values(input_values),
                prompt_parts={},
                input_values=input_values,
            ),
            problems_dir=problems_dir,
        )

        notes = [_issue_note(issue) for issue in issues]
        notes += [{"field": "tests", "kind": "warning", "explanation": w} for w in warnings]
        if failed:
            raise GateFailedError(_failure_detail(issues), notes)
        passed = {
            "field": "tests",
            "kind": "passed",
            "explanation": f"{count} test(s) generated, the validator accepts them",
        }
        return StepResult(
            step_name=self.STEP_NAME,
            problem_id=problem_id,
            status=STATUS_CONFIRMED,
            notes=[passed, *notes],
        )

    def _inputs(self, problem_id: str, upstream_artifacts: dict[str, Any] | None) -> dict:
        if not upstream_artifacts or self.INPUTS_KEY not in upstream_artifacts:
            raise ValueError(
                f"Step '{self.STEP_NAME}' for '{problem_id}': its input files aren't loaded"
            )
        return upstream_artifacts[self.INPUTS_KEY]


TESTS_GATE = TestsGate()


def _group_counts(counts: dict[str | None, int]) -> dict[str, int]:
    return {group: count for group, count in counts.items() if group is not None}


def _run(
    problem_dir: Path, templates_dir: Path
) -> tuple[list[TestIssue], list[str], dict[str | None, int]]:
    """(errors, warnings, the number of tests of each group — `None` for the tests without
    a group) — as `local check --full` finds them."""
    generated = generate_tests(
        problem_dir, templates_dir, lambda line: logger.info("[%s] %s", STEP_NAME, line)
    )
    issues = list(generated.issues)
    groups, group_errors = load_test_groups(problem_dir)
    issues += [TestIssue(_ISSUE_CONSTRAINTS, error) for error in group_errors]
    # With lines that didn't generate a group may lack tests only because of them.
    if groups is not None and not generated.issues:
        marked = [(test.name, test.group, test.command) for test in generated.tests]
        issues += [TestIssue(ISSUE_SCRIPT, error) for error in check_test_groups(groups, marked)]
    validator_issues, _ = validate_test_issues(problem_dir, templates_dir, generated.tests, groups)
    issues += validator_issues
    equal = find_equal_tests(problem_dir, generated.tests, groups)
    warnings = [format_equal_tests(equal, generated.tests)] if equal else []
    counts: dict[str | None, int] = {}
    for test in generated.tests:
        counts[test.group] = counts.get(test.group, 0) + 1
    return issues, warnings, counts


def _issue_note(issue: TestIssue) -> dict[str, Any]:
    steps = ROUTES.get(issue.kind, ())
    field = ", ".join(steps) if steps else "problem"
    return {"field": field, "kind": "error", "explanation": issue.message}


def _failure_detail(issues: list[TestIssue]) -> str:
    routed = [
        step for step in REVISING_STEPS if any(step in ROUTES.get(i.kind, ()) for i in issues)
    ]
    unrouted = sum(1 for issue in issues if not ROUTES.get(issue.kind))
    detail = f"{len(issues)} error(s) of the tests — see the report; fix the files by hand"
    if routed:
        detail += f", or run 'llm generate' again to let {', '.join(routed)} revise with the report"
    if unrouted:
        detail += (
            f" ({unrouted} error(s) come from no test step's files — the validator, "
            "the limits or the environment: fix them by hand)"
        )
    return detail
