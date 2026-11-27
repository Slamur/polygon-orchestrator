"""Step 5, the second part (optional) — tests for the custom checker, CLAUDE.md, "Role of
each generative step", item 5, and `.claude/rules/step-roles/5-checker_draft.md`.

Runs only with `checker.custom_needed: true` and a problem that isn't interactive
(`checker_tests_check.skip_detail`). Input: `checker.cpp` (the result of `checker_draft`
or the author's), `checker.custom_comparison_notes`, the statement's input/output and its
examples, `constraints.yaml` without TL/ML. Output: `checker_tests.yaml` — for every
condition of the checker that rejects an answer (a `quitif`/`quitf` with a condition and
the bound/format checks of the reading methods) two tests, the participant's output wrong
(`WA`) and the jury's answer wrong (`FAIL`), and for every comparison of the answers
("better"/"worse") two tests where one side is valid but not optimal. The extra data after
the answer isn't tested. The tests of the examples are not the model's: the local run
derives them (`checks.checker_tests`).

The local run after the step (`run_checker_tests`) runs the checker on the tests; its
failures make this step rerun even with a valid cache and revise the tests with the
report (`load_inputs`, `forced_rerun_reason`).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from orchestrator.cache import compute_input_hash as _cache_compute_input_hash
from orchestrator.cache import compute_input_values
from orchestrator.checks.checker_tests import (
    CHECKER_TESTS_FILENAME,
    VERDICT_PE,
    CheckerTestsError,
    parse_checker_tests,
)
from orchestrator.model_contracts import ModelResponse
from orchestrator.polygon.sync import CHECKER_FILENAME
from orchestrator.spec import ProblemSpec
from orchestrator.steps.base import (
    CONSTRAINTS_ARTIFACT,
    PROBLEMS_DIR,
    PROMPTS_DIR,
    TEMPLATES_DIR,
    MissingArtifactsError,
    StepResult,
    StepRevision,
    constraints_without_limits,
    existing_artifacts,
    require_constraints_yaml,
    run_generative_step,
    statement_io_section,
)
from orchestrator.steps.checker_tests_check import CHECKER_TESTS_GATE
from orchestrator.steps.checker_tests_check import skip_detail as _skip_detail

STEP_NAME = "checker_tests"

# The step's input read from disk before it runs: the checker's code and the failures of
# the last local run (`CHECKER_TESTS_GATE.failed_report`).
INPUTS_KEY = f"{STEP_NAME}: inputs"
_CHECKER_KEY = "checker"
_RUN_KEY = "run"


def load_inputs(
    problem_id: str, spec: ProblemSpec, *, problems_dir: Path = PROBLEMS_DIR
) -> dict[str, Any]:
    path = Path(problems_dir) / problem_id / CHECKER_FILENAME
    return {
        _CHECKER_KEY: path.read_text(encoding="utf-8") if path.is_file() else None,
        _RUN_KEY: CHECKER_TESTS_GATE.failed_report(problem_id, spec, problems_dir=problems_dir),
    }


def _inputs(problem_id: str, upstream_artifacts: dict[str, Any] | None) -> dict[str, Any]:
    inputs = (upstream_artifacts or {}).get(INPUTS_KEY)
    if not inputs or inputs.get(_CHECKER_KEY) is None:
        raise ValueError(
            f"Step '{STEP_NAME}' for '{problem_id}': no {CHECKER_FILENAME} — the "
            "'checker_draft' step must succeed first (or the author writes the checker)"
        )
    return inputs


def skip_detail(spec: ProblemSpec, **_: Any) -> str | None:
    """Why the step isn't needed for this problem; `None` — it is."""
    return _skip_detail(spec)


def _require_needed(problem_id: str, spec: ProblemSpec) -> None:
    detail = _skip_detail(spec)
    if detail is not None:
        raise ValueError(f"Step '{STEP_NAME}' for '{problem_id}' is not needed: {detail}")


def hash_inputs(
    problem_id: str,
    spec: ProblemSpec,
    upstream_artifacts: dict[str, Any] | None = None,
) -> tuple[Any, dict[str, Any] | None]:
    """`(spec section, upstream artifacts)`: the comparison rule and the statement's
    input/output; the checker's code and `constraints.yaml` without TL/ML. The local run's
    report isn't part of it (see `forced_rerun_reason`)."""
    _require_needed(problem_id, spec)
    constraints_yaml = require_constraints_yaml(STEP_NAME, problem_id, upstream_artifacts)
    inputs = _inputs(problem_id, upstream_artifacts)
    section = {
        "custom_comparison_notes": spec.checker.custom_comparison_notes,
        "statement": statement_io_section(spec, include_output_and_legend=True),
    }
    upstream = {
        CHECKER_FILENAME: inputs[_CHECKER_KEY],
        CONSTRAINTS_ARTIFACT: constraints_without_limits(constraints_yaml),
    }
    return section, upstream


def compute_input_hash(
    problem_id: str,
    spec: ProblemSpec,
    upstream_artifacts: dict[str, Any] | None = None,
) -> str:
    return _cache_compute_input_hash(STEP_NAME, *hash_inputs(problem_id, spec, upstream_artifacts))


def forced_rerun_reason(
    problem_id: str, spec: ProblemSpec, upstream_artifacts: dict[str, Any] | None = None
) -> str | None:
    return CHECKER_TESTS_GATE.revision_reason(_run_report(upstream_artifacts))


def _run_report(upstream_artifacts: dict[str, Any] | None) -> dict[str, Any] | None:
    return ((upstream_artifacts or {}).get(INPUTS_KEY) or {}).get(_RUN_KEY)


def extra_context_documents(
    spec: ProblemSpec, *, templates_dir: Path = TEMPLATES_DIR
) -> list[str] | None:
    """No samples: the step's single signature with the other steps (`pipeline.py`)."""
    return None


def primary_artifact_path(
    problem_id: str, spec: ProblemSpec, *, problems_dir: Path = PROBLEMS_DIR
) -> Path:
    return Path(problems_dir) / problem_id / CHECKER_TESTS_FILENAME


def current_artifacts(
    problem_id: str, spec: ProblemSpec, *, problems_dir: Path = PROBLEMS_DIR
) -> dict[str, Path]:
    return existing_artifacts(
        {CHECKER_TESTS_FILENAME: primary_artifact_path(problem_id, spec, problems_dir=problems_dir)}
    )


def check_checker_tests(text: str) -> None:
    """`ValueError` — the response's tests don't parse or a `PE` test only adds data
    after a correct answer (the extra data after the answer isn't tested)."""
    tests = parse_checker_tests(text)
    extra = [
        str(number)
        for number, test in enumerate(tests, start=1)
        if test.verdict == VERDICT_PE
        and len(test.output.split()) > len(test.answer.split())
        and test.output.split()[: len(test.answer.split())] == test.answer.split()
    ]
    if extra:
        raise CheckerTestsError(
            f"{CHECKER_TESTS_FILENAME}: test(s) {', '.join(extra)} only add data after the "
            "answer — the extra data after the answer is not tested, remove them"
        )


def run_step(
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
    """Runs the `checker_tests` step for `problem_id`: writes `checker_tests.yaml`. A
    response whose tests don't parse (`check_checker_tests`) isn't applied."""
    section, upstream = hash_inputs(problem_id, spec, upstream_artifacts)
    context = {
        "problem_id": spec.problem_id,
        "custom_comparison_notes": section["custom_comparison_notes"],
        "statement": section["statement"],
        "checker_cpp": upstream[CHECKER_FILENAME],
        "constraints_yaml": upstream[CONSTRAINTS_ARTIFACT],
        "checker_run": _run_report(upstream_artifacts),
    }

    def resolve_artifact_path(filename: str) -> Path:
        if filename != CHECKER_TESTS_FILENAME:
            raise ValueError(
                f"Step '{STEP_NAME}' returns only {CHECKER_TESTS_FILENAME}, got '{filename}'"
            )
        return primary_artifact_path(problem_id, spec, problems_dir=problems_dir)

    def validate_artifacts(artifacts: dict[str, str]) -> None:
        if CHECKER_TESTS_FILENAME not in artifacts:
            raise MissingArtifactsError(
                f"Step '{STEP_NAME}': the response has no {CHECKER_TESTS_FILENAME}",
                [CHECKER_TESTS_FILENAME],
            )
        check_checker_tests(artifacts[CHECKER_TESTS_FILENAME])

    return run_generative_step(
        step_name=STEP_NAME,
        problem_id=problem_id,
        context=context,
        input_hash=compute_input_hash(problem_id, spec, upstream_artifacts),
        input_values=compute_input_values(section, upstream),
        revision=revision,
        saved_response=saved_response,
        resolve_artifact_path=resolve_artifact_path,
        validate_artifacts=validate_artifacts,
        prompts_dir=prompts_dir,
        problems_dir=problems_dir,
        templates_dir=templates_dir,
        extra_context_documents=None,
    )
