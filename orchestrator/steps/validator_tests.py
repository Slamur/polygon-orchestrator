"""Step 2, the second part — tests for the validator, CLAUDE.md, "Role of each generative
step", item 2, and `.claude/rules/step-roles/2-constraints_pick.md`.

Input: `validator.cpp` (the result of `constraints_pick` or the author's), `constraints.yaml`
without TL/ML, the statement's input format and its examples' input. Output:
`validator_tests.yaml` (`checks.validator_tests`) — boundary tests of every reading and
check of the validator: `VALID` tests with values exactly on the bounds (several at once),
`INVALID` tests with exactly one value one past a bound; with test groups — tests without a
group for the limits common to all groups, tests of a group for its own limits and a
`VALID` test of every group. Every test is small: a boundary test that needs more isn't
written, an `INVALID` one stops right after the value out of range.

The local run after the step (`run_validator_tests`) runs the validator on the tests; its
failures make this step rerun even with a valid cache and revise the tests with the report
(`load_inputs`, `forced_rerun_reason`).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from orchestrator.cache import compute_input_hash as _cache_compute_input_hash
from orchestrator.cache import compute_input_values
from orchestrator.checks.validator_tests import (
    MAX_CHARS,
    MAX_LINES,
    VALIDATOR_TESTS_FILENAME,
    ValidatorTestsError,
    coverage_errors,
    parse_validator_tests,
)
from orchestrator.model_contracts import ModelResponse
from orchestrator.polygon.sync import VALIDATOR_FILENAME
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
from orchestrator.steps.validator_tests_check import VALIDATOR_TESTS_GATE
from orchestrator.test_groups import TestGroupsError, parse_test_groups

STEP_NAME = "validator_tests"

# The step's input read from disk before it runs: the validator's code and the failures of
# the last local run (`VALIDATOR_TESTS_GATE.failed_report`).
INPUTS_KEY = f"{STEP_NAME}: inputs"
_VALIDATOR_KEY = "validator"
_RUN_KEY = "run"


def load_inputs(
    problem_id: str, spec: ProblemSpec, *, problems_dir: Path = PROBLEMS_DIR
) -> dict[str, Any]:
    path = Path(problems_dir) / problem_id / VALIDATOR_FILENAME
    return {
        _VALIDATOR_KEY: path.read_text(encoding="utf-8") if path.is_file() else None,
        _RUN_KEY: VALIDATOR_TESTS_GATE.failed_report(problem_id, spec, problems_dir=problems_dir),
    }


def _inputs(problem_id: str, upstream_artifacts: dict[str, Any] | None) -> dict[str, Any]:
    inputs = (upstream_artifacts or {}).get(INPUTS_KEY)
    if not inputs or inputs.get(_VALIDATOR_KEY) is None:
        raise ValueError(
            f"Step '{STEP_NAME}' for '{problem_id}': no {VALIDATOR_FILENAME} — the "
            "'constraints_pick' step must succeed first (or the author writes the validator)"
        )
    return inputs


def hash_inputs(
    problem_id: str,
    spec: ProblemSpec,
    upstream_artifacts: dict[str, Any] | None = None,
) -> tuple[Any, dict[str, Any] | None]:
    """`(spec section, upstream artifacts)`: the statement's input format; the validator's
    code and `constraints.yaml` without TL/ML. The local run's report isn't part of it (see
    `forced_rerun_reason`)."""
    constraints_yaml = require_constraints_yaml(STEP_NAME, problem_id, upstream_artifacts)
    inputs = _inputs(problem_id, upstream_artifacts)
    section = {"statement": statement_io_section(spec, include_output_and_legend=False)}
    upstream = {
        VALIDATOR_FILENAME: inputs[_VALIDATOR_KEY],
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
    return VALIDATOR_TESTS_GATE.revision_reason(_run_report(upstream_artifacts))


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
    return Path(problems_dir) / problem_id / VALIDATOR_TESTS_FILENAME


def current_artifacts(
    problem_id: str, spec: ProblemSpec, *, problems_dir: Path = PROBLEMS_DIR
) -> dict[str, Path]:
    return existing_artifacts(
        {
            VALIDATOR_TESTS_FILENAME: primary_artifact_path(
                problem_id, spec, problems_dir=problems_dir
            )
        }
    )


def group_names(constraints_yaml: str) -> list[str] | None:
    """The names of the test groups of `constraints.yaml`; `None` — none (or unreadable:
    `constraints_pick` checks them, not this step)."""
    try:
        groups = parse_test_groups(constraints_yaml)
    except TestGroupsError:
        return None
    return [group.name for group in groups] if groups else None


def check_validator_tests(text: str, groups: list[str] | None) -> None:
    """`ValidatorTestsError` — the response's tests don't parse (the format, the size, the
    groups, repeats) or a test group has no `VALID` test."""
    errors = coverage_errors(parse_validator_tests(text, groups=groups), groups)
    if errors:
        raise ValidatorTestsError("; ".join(errors))


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
    """Runs the `validator_tests` step for `problem_id`: writes `validator_tests.yaml`. A
    response whose tests don't pass `check_validator_tests` isn't applied."""
    section, upstream = hash_inputs(problem_id, spec, upstream_artifacts)
    constraints_yaml = upstream[CONSTRAINTS_ARTIFACT]
    groups = group_names(constraints_yaml)
    context = {
        "problem_id": spec.problem_id,
        "statement": section["statement"],
        "validator_cpp": upstream[VALIDATOR_FILENAME],
        "constraints_yaml": constraints_yaml,
        "groups": groups,
        "max_lines": MAX_LINES,
        "max_chars": MAX_CHARS,
        "validator_run": _run_report(upstream_artifacts),
    }

    def resolve_artifact_path(filename: str) -> Path:
        if filename != VALIDATOR_TESTS_FILENAME:
            raise ValueError(
                f"Step '{STEP_NAME}' returns only {VALIDATOR_TESTS_FILENAME}, got '{filename}'"
            )
        return primary_artifact_path(problem_id, spec, problems_dir=problems_dir)

    def validate_artifacts(artifacts: dict[str, str]) -> None:
        if VALIDATOR_TESTS_FILENAME not in artifacts:
            raise MissingArtifactsError(
                f"Step '{STEP_NAME}': the response has no {VALIDATOR_TESTS_FILENAME}",
                [VALIDATOR_TESTS_FILENAME],
            )
        check_validator_tests(artifacts[VALIDATOR_TESTS_FILENAME], groups)

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
