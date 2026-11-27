"""The local run of the validator tests (`run_validator_tests`) — a mechanical step after the
`validator_tests` step, `.claude/rules/step-roles/2-constraints_pick.md`.

It is `checks.validator_tests.run_validator_tests`: the validator on every test of
`validator_tests.yaml`. A test with another verdict, or an `INVALID` test that fails on
another check than the one it covers (its `expect` isn't in the message) or with an
integer that isn't one past the bound — a failure of the `validator_tests` step: while
nothing the run reads has changed, the step reruns even with a valid cache
(`forced_rerun_reason`) and revises its tests with the failures and the author's comment
from `llm_reports/run_validator_tests.md`. A validator that doesn't build, a file that
doesn't parse, a test group without a `VALID` test — errors of the problem; they fail the
run too.

The run's input is the contents of everything it reads — the validator, the tests,
`constraints.yaml` (the groups), the resources — so an unchanged input doesn't run it
again: a run that failed fails again at once.
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
from orchestrator.checks.validator_tests import VALIDATOR_TESTS_FILENAME, run_validator_tests
from orchestrator.llm_report import report_path
from orchestrator.model_contracts import ModelResponse
from orchestrator.polygon.sync import VALIDATOR_FILENAME
from orchestrator.problem_paths import CONSTRAINTS_FILENAME, authoring_path
from orchestrator.resources import problem_resources_dir
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

STEP_NAME = "run_validator_tests"
VALIDATOR_TESTS_STEP = "validator_tests"


def _input_files(problem_dir: Path) -> list[Path]:
    paths = [
        problem_dir / VALIDATOR_FILENAME,
        problem_dir / VALIDATOR_TESTS_FILENAME,
        authoring_path(problem_dir, CONSTRAINTS_FILENAME),
    ]
    resources = problem_resources_dir(problem_dir)
    if resources.is_dir():
        paths += sorted(p for p in resources.rglob("*") if p.is_file())
    return paths


class ValidatorTestsGate:
    """`run_validator_tests` with the interface of the step modules
    (`pipeline._STEP_MODULES`)."""

    STEP_NAME: ClassVar[str] = STEP_NAME
    INPUTS_KEY: ClassVar[str] = f"{STEP_NAME}: inputs"
    # Instead of the model class and the prompt hash in the cache entry.
    MODEL: ClassVar[str] = "none"
    PROMPT_HASH: ClassVar[str] = "none"

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
        # The run writes no file of the problem.
        return True

    def failed_report(
        self, problem_id: str, spec: ProblemSpec, *, problems_dir: Path = PROBLEMS_DIR
    ) -> dict[str, Any] | None:
        """The last run's failures of the tests and the author's comment — if the run
        failed on them and its input is still the same; otherwise `None`."""
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
        """Why `validator_tests` reruns even with a valid cache: `report` (`failed_report`)."""
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
        """Runs the validator on its tests and records the result (the cache entry and
        `.cache/run_validator_tests.report.json`). `GateFailedError` — the run failed;
        `revision`/`saved_response` aren't used."""
        problem_dir = Path(problems_dir) / problem_id
        run = run_validator_tests(
            problem_dir, lambda message: logger.info("[%s] %s", self.STEP_NAME, message)
        )
        save_run_report(
            problem_id,
            self.STEP_NAME,
            {"failures": run.failures, "errors": run.errors},
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
                    "field": f"{VALIDATOR_TESTS_FILENAME}: test {failure['test']}",
                    "kind": "failed",
                    "explanation": f"{failure['covers']}: {'; '.join(failure['problems'])}",
                }
            )
        if run.failed:
            what = []
            if run.failures:
                what.append(f"{len(run.failures)} validator test(s) failed")
            if run.errors:
                what.append(f"{len(run.errors)} error(s) of the problem")
            raise GateFailedError(
                f"{', '.join(what)} — see the report"
                + (" (problem errors: fix the validator or the tests)" if run.errors else ""),
                notes,
                (
                    "; fix the files by hand, or run 'llm generate' again to let "
                    f"{VALIDATOR_TESTS_STEP} revise its tests with the report"
                )
                if run.failures
                else "",
            )
        passed = {
            "field": "run",
            "kind": "passed",
            "explanation": f"{run.tests} validator test(s) get the expected verdict",
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


VALIDATOR_TESTS_GATE = ValidatorTestsGate()
