"""The local run of the checker tests (`run_checker_tests`) — a mechanical step after the
`checker_tests` step, `.claude/rules/step-roles/5-checker_draft.md`.

It is `checks.checker_tests.run_checker_tests`: it writes `checker_tests.auto.yaml` (the
tests of the examples and of the correct solutions' outputs on them) and runs the checker
on it and on `checker_tests.yaml`. A test of `checker_tests.yaml` with another verdict, an
input the validator rejects or a "correct" side that isn't correct — a failure of the
`checker_tests` step: while nothing the run reads has changed, the step reruns even with a
valid cache (`forced_rerun_reason`) and revises its tests with the failures and the
author's comment from `llm_reports/run_checker_tests.md`. A test of the examples the
checker doesn't accept, a checker that doesn't build — errors of the problem: the author
fixes the checker (or the solutions); they fail the run too.

The run's input is the contents of everything it reads — the checker, the validator, both
the step's tests and the examples, the correct solutions, the limits, the resources — and
the spec parts it takes (the checker, the examples' outputs), so an unchanged input doesn't
run it again: a run that failed fails again at once.
"""

from __future__ import annotations

import datetime
import hashlib
import logging
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
from orchestrator.checks.checker_tests import (
    AUTO_CHECKER_TESTS_FILENAME,
    CHECKER_TESTS_FILENAME,
    run_checker_tests,
)
from orchestrator.llm_report import report_path
from orchestrator.model_contracts import ModelResponse
from orchestrator.polygon.steps.upload_solutions import CORRECT_VERDICTS
from orchestrator.problem_paths import CONSTRAINTS_FILENAME, authoring_path, is_removed_mark
from orchestrator.resources import problem_resources_dir
from orchestrator.spec import SPEC_REFERENCES_FIELD, ProblemSpec, split_solution_stem
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

STEP_NAME = "run_checker_tests"
CHECKER_TESTS_STEP = "checker_tests"

_PROBLEM_FILES = ("checker.cpp", "validator.cpp", "limits.yaml", CHECKER_TESTS_FILENAME)
_EXAMPLES_DIR = "statement/examples"
_SOLUTIONS_DIR = "solutions"


def skip_detail(spec: ProblemSpec) -> str | None:
    """Why the checker tests aren't needed for this problem; `None` — they are. Shared by
    the `checker_tests` step and its local run."""
    if spec.checker.custom_needed is not True:
        return "checker.custom_needed is not true — a standard checker, no checker tests"
    if spec.interactive:
        return "an interactive problem — its checker judges the interactor's output"
    return None


def _input_files(problem_dir: Path) -> list[Path]:
    paths = [problem_dir / name for name in _PROBLEM_FILES]
    paths.append(authoring_path(problem_dir, CONSTRAINTS_FILENAME))
    for directory in (problem_resources_dir(problem_dir), problem_dir / _EXAMPLES_DIR):
        if directory.is_dir():
            paths += sorted(p for p in directory.rglob("*") if p.is_file())
    solutions_dir = problem_dir / _SOLUTIONS_DIR
    if solutions_dir.is_dir():
        paths += sorted(
            p
            for p in solutions_dir.iterdir()
            if p.is_file()
            and not is_removed_mark(p)
            and split_solution_stem(p.stem)[0].lower() in CORRECT_VERDICTS
        )
    return paths


class CheckerTestsGate:
    """`run_checker_tests` with the interface of the step modules (`pipeline._STEP_MODULES`)."""

    STEP_NAME: ClassVar[str] = STEP_NAME
    INPUTS_KEY: ClassVar[str] = f"{STEP_NAME}: inputs"
    # Instead of the model class and the prompt hash in the cache entry.
    MODEL: ClassVar[str] = "none"
    PROMPT_HASH: ClassVar[str] = "none"

    def skip_detail(self, spec: ProblemSpec, **_: Any) -> str | None:
        return skip_detail(spec)

    def load_inputs(
        self, problem_id: str, spec: ProblemSpec, *, problems_dir: Path = PROBLEMS_DIR
    ) -> dict[str, Any]:
        """The hashes of the files the run reads."""
        problem_dir = Path(problems_dir) / problem_id
        files = {
            path.relative_to(problem_dir).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in _input_files(problem_dir)
            if path.is_file()
        }
        return {"files": files}

    def hash_inputs(
        self,
        problem_id: str,
        spec: ProblemSpec,
        upstream_artifacts: dict[str, Any] | None = None,
    ) -> tuple[Any, dict[str, Any] | None]:
        inputs = self._inputs(problem_id, upstream_artifacts)
        section = {
            "checker": spec.checker.model_dump(mode="json", exclude=SPEC_REFERENCES_FIELD),
            "sample_outputs": [ex.output for ex in spec.statement_draft.sample_examples],
        }
        return section, {f"file: {name}": digest for name, digest in inputs["files"].items()}

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
        # The run's only file: the tests of the examples (absent — none were derived).
        report = load_run_report(problem_id, self.STEP_NAME, problems_dir=problems_dir) or {}
        if not report.get("auto_tests"):
            return True
        return (Path(problems_dir) / problem_id / AUTO_CHECKER_TESTS_FILENAME).is_file()

    def failed_report(
        self, problem_id: str, spec: ProblemSpec, *, problems_dir: Path = PROBLEMS_DIR
    ) -> dict[str, Any] | None:
        """The last run's failures of the tests of `checker_tests.yaml` and the author's
        comment — if the run failed on them and its input is still the same; otherwise
        `None`."""
        entry = load_cache_entry(problem_id, self.STEP_NAME, problems_dir=problems_dir)
        if entry is None or entry.status != STATUS_FAILED:
            return None
        upstream = {self.INPUTS_KEY: self.load_inputs(problem_id, spec, problems_dir=problems_dir)}
        if self.compute_input_hash(problem_id, spec, upstream) != entry.input_hash:
            return None
        report = load_run_report(problem_id, self.STEP_NAME, problems_dir=problems_dir) or {}
        failures = report.get("failures") or []
        if not failures:
            return None
        problem_dir = Path(problems_dir) / problem_id
        return {
            "report": report_path(problem_dir, self.STEP_NAME).relative_to(problem_dir).as_posix(),
            "failures": failures,
            "comment": read_author_comment(problem_dir, self.STEP_NAME),
        }

    def revision_reason(self, report: dict[str, Any] | None) -> str | None:
        """Why `checker_tests` reruns even with a valid cache: `report` (`failed_report`)."""
        if report is None:
            return None
        return (
            f"the local run '{self.STEP_NAME}' failed on {len(report['failures'])} test(s) — "
            f"revising with its report ({report['report']})"
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
        """Runs the checker on its tests and records the result (the cache entry and
        `.cache/run_checker_tests.report.json`). `GateFailedError` — the run failed;
        `revision`/`saved_response` aren't used."""
        problem_dir = Path(problems_dir) / problem_id
        run = run_checker_tests(
            problem_dir, lambda message: logger.info("[%s] %s", self.STEP_NAME, message)
        )
        save_run_report(
            problem_id,
            self.STEP_NAME,
            {
                "failures": run.failures,
                "errors": run.errors,
                "warnings": run.warnings,
                "auto_tests": len(run.auto),
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
                status=STATUS_FAILED if run.failed else STATUS_CONFIRMED,
                timestamp=datetime.datetime.now(datetime.timezone.utc).isoformat(),
                input_parts=input_parts_from_values(input_values),
                prompt_parts={},
                input_values=input_values,
            ),
            problems_dir=problems_dir,
        )

        notes = [{"field": "problem", "kind": "error", "explanation": e} for e in run.errors]
        for failure in run.failures:
            notes.append(
                {
                    "field": f"{CHECKER_TESTS_FILENAME}: test {failure['test']}",
                    "kind": "failed",
                    "explanation": f"{failure['covers']}: {'; '.join(failure['problems'])}",
                }
            )
        notes += [{"field": "run", "kind": "warning", "explanation": w} for w in run.warnings]
        if run.failed:
            what = []
            if run.failures:
                what.append(f"{len(run.failures)} checker test(s) failed")
            if run.errors:
                what.append(f"{len(run.errors)} error(s) of the problem")
            raise GateFailedError(
                f"{', '.join(what)} — see the report"
                + (" (problem errors: fix the checker or the solutions)" if run.errors else ""),
                notes,
                (
                    "; fix the files by hand, or run 'llm generate' again to let "
                    f"{CHECKER_TESTS_STEP} revise its tests with the report"
                )
                if run.failures
                else "",
            )
        passed = {
            "field": "run",
            "kind": "passed",
            "explanation": (
                f"{run.tests} checker test(s) get the expected verdict "
                f"({len(run.auto)} of them from the examples, {AUTO_CHECKER_TESTS_FILENAME})"
            ),
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


CHECKER_TESTS_GATE = CheckerTestsGate()
