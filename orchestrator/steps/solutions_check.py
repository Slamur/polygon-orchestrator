"""The local runs of the solution stages (`run_main`, `run_ok`, `run_slow`,
`run_wrong`) — mechanical steps between the stages, CLAUDE.md, "Role of each generative
step", item 4, and `.claude/rules/step-roles/4-solutions.md`.

A run is `local run --full` (`checks.solutions_run.run_solutions`) limited to the
solutions its stage judges: Main correct runs anyway, its output is the expected one.
The run fails — and stops the pipeline — if a judged solution got a verdict its name
doesn't allow or didn't build (`STATUS_UNEXPECTED`, `STATUS_NOT_RUN`), or the problem
itself has errors (the validator rejects a test, the checker fails). A wrong solution
that no generated test catches (`STATUS_SURVIVED`) isn't a failure: it's reported,
the tests for it are a separate matter.

The step's input is the contents of everything the run reads (the judged solutions,
generators, the test-script, the validator, the checker, the interactor, the limits,
the examples) and the spec parts it takes (the checker, the examples' outputs), so
an unchanged input doesn't run the tests again: a run that failed fails again at once,
pointing to its report. The failures and the survivors — `.cache/<step>.report.json`
(`load_run_report`); the survivors of a passed run — the input of `tests_survivors`
(`current_survivors`). While the input is the same, the stage before reads the failures and
revises its solutions with them (and with the author's comment from the report
`llm_reports/<step>.md`, `read_author_comment`); a changed input — the report is
outdated and nobody reads it.
"""

from __future__ import annotations

import datetime
import hashlib
import json
import logging
from collections.abc import Callable
from dataclasses import dataclass
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
from orchestrator.checks.solutions_run import (
    STATUS_NOT_RUN,
    STATUS_SURVIVED,
    STATUS_UNEXPECTED,
    run_solutions,
)
from orchestrator.llm_report import COMMENT_MARKER, report_path
from orchestrator.model_contracts import ModelResponse
from orchestrator.problem_paths import CONSTRAINTS_FILENAME, authoring_path
from orchestrator.spec import SPEC_REFERENCES_FIELD, ProblemSpec
from orchestrator.steps.base import (
    PROBLEMS_DIR,
    PROMPTS_DIR,
    STATUS_CONFIRMED,
    TEMPLATES_DIR,
    StepResult,
    StepRevision,
)

logger = logging.getLogger(__name__)

# The cache entry's status of a run that failed.
STATUS_FAILED = "failed"


# What the run reads besides the solutions — relative to the problem directory.
_PROBLEM_FILES = (
    "validator.cpp",
    "checker.cpp",
    "interactor.cpp",
    "problem_lib.h",
    "limits.yaml",
    "test_script",
    "test_script_groups",
)
_PROBLEM_DIRS = ("generators", "statement/examples")
_SOLUTIONS_DIRNAME = "solutions"


class GateFailedError(Exception):
    """The local run failed: `detail` — for the console, `notes` — for the report."""

    def __init__(self, detail: str, notes: list[dict[str, Any]]):
        super().__init__(detail)
        self.detail = detail
        self.notes = notes


@dataclass(frozen=True)
class Gate:
    """A local run of one solution stage. `judged` — the names of the solutions the run
    judges, from the problem directory; `skip` — why the stage isn't needed (`None` —
    it is)."""

    step_name: str
    stage_step: str
    judged: Callable[[Path], list[str]]
    skip: Callable[[ProblemSpec], str | None]

    # Instead of the model class and the prompt hash in the cache entry.
    MODEL: ClassVar[str] = "none"
    PROMPT_HASH: ClassVar[str] = "none"

    @property
    def STEP_NAME(self) -> str:  # noqa: N802 — the same interface as the step modules
        return self.step_name

    @property
    def INPUTS_KEY(self) -> str:  # noqa: N802
        return f"{self.step_name}: inputs"

    def skip_detail(self, spec: ProblemSpec, **_: Any) -> str | None:
        return self.skip(spec)

    def load_inputs(
        self, problem_id: str, spec: ProblemSpec, *, problems_dir: Path = PROBLEMS_DIR
    ) -> dict[str, Any]:
        """The judged solutions and the hashes of the files the run reads."""
        problem_dir = Path(problems_dir) / problem_id
        judged = sorted(self.judged(problem_dir))
        paths = [problem_dir / _SOLUTIONS_DIRNAME / name for name in judged]
        paths += [problem_dir / name for name in _PROBLEM_FILES]
        paths.append(authoring_path(problem_dir, CONSTRAINTS_FILENAME))
        for dirname in _PROBLEM_DIRS:
            directory = problem_dir / dirname
            if directory.is_dir():
                paths += sorted(p for p in directory.rglob("*") if p.is_file())
        files = {
            path.relative_to(problem_dir).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in paths
            if path.is_file()
        }
        return {"judged": judged, "files": files}

    def hash_inputs(
        self,
        problem_id: str,
        spec: ProblemSpec,
        upstream_artifacts: dict[str, Any] | None = None,
    ) -> tuple[Any, dict[str, Any] | None]:
        inputs = self._inputs(problem_id, upstream_artifacts)
        section = {
            "checker": spec.checker.model_dump(mode="json", exclude=SPEC_REFERENCES_FIELD),
            "interactive": spec.interactive,
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
            self.step_name, *self.hash_inputs(problem_id, spec, upstream_artifacts)
        )

    def extra_context_documents(
        self, spec: ProblemSpec, *, templates_dir: Path = TEMPLATES_DIR
    ) -> None:
        return None

    def primary_artifact_path(
        self, problem_id: str, spec: ProblemSpec, *, problems_dir: Path = PROBLEMS_DIR
    ) -> Path:
        return report_path(Path(problems_dir) / problem_id, self.step_name)

    def current_artifacts(
        self, problem_id: str, spec: ProblemSpec, *, problems_dir: Path = PROBLEMS_DIR
    ) -> dict[str, Path]:
        return {}

    def artifact_present(
        self, problem_id: str, spec: ProblemSpec, *, problems_dir: Path = PROBLEMS_DIR
    ) -> bool:
        # The run has no artifact: an unchanged input is enough to reuse its result.
        return True

    def current_survivors(
        self, problem_id: str, spec: ProblemSpec, *, problems_dir: Path = PROBLEMS_DIR
    ) -> list[dict[str, Any]]:
        """The solutions no test from the script caught in the last run — if the run
        passed and its input is still the same; otherwise none."""
        entry = load_cache_entry(problem_id, self.step_name, problems_dir=problems_dir)
        if entry is None or entry.status == STATUS_FAILED:
            return []
        upstream = {self.INPUTS_KEY: self.load_inputs(problem_id, spec, problems_dir=problems_dir)}
        if self.compute_input_hash(problem_id, spec, upstream) != entry.input_hash:
            return []
        report = load_run_report(problem_id, self.step_name, problems_dir=problems_dir)
        return list((report or {}).get("survivors", []))

    def failed_report(
        self,
        problem_id: str,
        spec: ProblemSpec,
        names: list[str],
        *,
        problems_dir: Path = PROBLEMS_DIR,
    ) -> dict[str, Any] | None:
        """The last run's failures of the solutions `names` and the author's comment —
        if the run failed on them and its input is still the same; otherwise `None`."""
        entry = load_cache_entry(problem_id, self.step_name, problems_dir=problems_dir)
        if entry is None or entry.status != STATUS_FAILED:
            return None
        upstream = {self.INPUTS_KEY: self.load_inputs(problem_id, spec, problems_dir=problems_dir)}
        if self.compute_input_hash(problem_id, spec, upstream) != entry.input_hash:
            return None
        report = load_run_report(problem_id, self.step_name, problems_dir=problems_dir)
        failures = [f for f in (report or {}).get("failures", []) if f.get("name") in names]
        if not failures:
            return None
        problem_dir = Path(problems_dir) / problem_id
        return {
            "report": report_path(problem_dir, self.step_name).relative_to(problem_dir).as_posix(),
            "failures": failures,
            "comment": read_author_comment(problem_dir, self.step_name),
        }

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
        """Runs the judged solutions on the examples and the tests from the script and
        records the result (the cache entry and `.cache/<step>.report.json`).
        `GateFailedError` — the run failed; `revision`/`saved_response` aren't used."""
        inputs = self._inputs(problem_id, upstream_artifacts)
        judged = set(inputs["judged"])
        problem_dir = Path(problems_dir) / problem_id
        result = run_solutions(
            problem_dir,
            templates_dir,
            lambda message: logger.info("[%s] %s", self.step_name, message),
            full=True,
            only=judged,
        )
        summary = result.summary()
        judged_outcomes = [s for s in summary["solutions"] if s["name"] in judged]
        failures = [
            s for s in judged_outcomes if s["status"] in (STATUS_UNEXPECTED, STATUS_NOT_RUN)
        ]
        survivors = [s for s in judged_outcomes if s["status"] == STATUS_SURVIVED]
        failed = bool(failures or result.errors)
        _save_run_report(
            problem_id,
            self.step_name,
            {
                "failures": failures,
                "survivors": survivors,
                "errors": summary["errors"],
                "warnings": result.warnings,
            },
            problems_dir=problems_dir,
        )

        section, upstream = self.hash_inputs(problem_id, spec, upstream_artifacts)
        input_values = compute_input_values(section, upstream)
        save_cache_entry(
            problem_id,
            self.step_name,
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

        notes = _notes(failures, survivors, result.errors, result.warnings)
        if failed:
            what = []
            if failures:
                what.append(f"{len(failures)} solution(s) failed")
            if result.errors:
                what.append(f"{len(result.errors)} error(s) of the problem")
            raise GateFailedError(
                f"{', '.join(what)} — see the report; fix the files by hand, or "
                f"run 'llm generate' again to let {self.stage_step} revise its solutions "
                "with the report (problem errors: fix the tests, the checker or the spec)",
                notes,
            )
        passed = {
            "field": "run",
            "kind": "passed",
            "explanation": f"{len(judged_outcomes)} solution(s) behave as their names say",
        }
        return StepResult(
            step_name=self.step_name,
            problem_id=problem_id,
            status=STATUS_CONFIRMED,
            notes=[passed, *notes],
        )

    def _inputs(self, problem_id: str, upstream_artifacts: dict[str, Any] | None) -> dict:
        if not upstream_artifacts or self.INPUTS_KEY not in upstream_artifacts:
            raise ValueError(
                f"Step '{self.step_name}' for '{problem_id}': its input files aren't loaded"
            )
        return upstream_artifacts[self.INPUTS_KEY]


def read_author_comment(problem_dir: Path, step_name: str) -> str | None:
    """What the author wrote in the report after `COMMENT_MARKER`; `None` — nothing."""
    path = report_path(problem_dir, step_name)
    if not path.is_file():
        return None
    _, marker, comment = path.read_text(encoding="utf-8").partition(COMMENT_MARKER)
    if not marker:
        return None
    return comment.strip() or None


def _run_report_path(problem_id: str, step_name: str, *, problems_dir: Path) -> Path:
    return Path(problems_dir) / problem_id / ".cache" / f"{step_name}.report.json"


def load_run_report(
    problem_id: str, step_name: str, *, problems_dir: Path = PROBLEMS_DIR
) -> dict[str, Any] | None:
    path = _run_report_path(problem_id, step_name, problems_dir=problems_dir)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _save_run_report(
    problem_id: str, step_name: str, report: dict[str, Any], *, problems_dir: Path
) -> None:
    path = _run_report_path(problem_id, step_name, problems_dir=problems_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _notes(
    failures: list[dict], survivors: list[dict], errors: list[str], warnings: list[str]
) -> list[dict[str, Any]]:
    notes = [{"field": "problem", "kind": "error", "explanation": error} for error in errors]
    for failure in failures:
        lines = [failure["error"]] if failure.get("error") else []
        for test in failure.get("unexpected") or []:
            line = f"{test['test']}: {test['verdict']}"
            if test.get("command"):
                line += f" ({test['command']})"
            if test.get("comment"):
                line += f" — {test['comment']}"
            lines.append(line)
        notes.append(
            {
                "field": failure["name"],
                "kind": "failed",
                "explanation": "; ".join(lines) or failure["status"],
            }
        )
    for survivor in survivors:
        where = (
            "only the examples catch it"
            if survivor.get("caught_on_example")
            else "nothing catches it"
        )
        notes.append(
            {
                "field": survivor["name"],
                "kind": "survived",
                "explanation": f"no test from the script gets '{survivor['verdict']}': {where}",
            }
        )
    notes += [{"field": "run", "kind": "warning", "explanation": w} for w in warnings]
    return notes
