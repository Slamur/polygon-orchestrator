"""Step 5 (optional) — a custom checker draft, CLAUDE.md, "Role
of each generative step", item 5.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from orchestrator.cache import compute_input_hash as _cache_compute_input_hash
from orchestrator.cache import compute_input_values
from orchestrator.model_contracts import ModelResponse
from orchestrator.references import reference_documents
from orchestrator.spec import SPEC_REFERENCES_FIELD, ProblemSpec
from orchestrator.steps.base import (
    PROBLEMS_DIR,
    PROMPTS_DIR,
    TEMPLATES_DIR,
    StepResult,
    StepRevision,
    existing_artifacts,
    run_generative_step,
)

STEP_NAME = "checker_draft"


def _require_custom_checker(problem_id: str, spec: ProblemSpec) -> None:
    if spec.checker.custom_needed is not True:
        raise ValueError(
            f"Step '{STEP_NAME}' for '{problem_id}': 'checker.custom_needed' in the spec "
            "is not True — a standard checker is used, no draft requested, the step "
            "is not run (see CLAUDE.md, generative step roles, item 5)"
        )


def compute_input_hash(
    problem_id: str,
    spec: ProblemSpec,
    upstream_artifacts: dict[str, Any] | None = None,
) -> str:
    """The step's input hash without calling the model, see `constraints_pick.compute_input_hash`
    and CLAUDE.md, "Caching by spec hash". Like `run_step`, it requires
    `checker.custom_needed: true` in the spec. `upstream_artifacts` isn't
    used — the step depends on nothing except
    `checker.custom_comparison_notes`, it's present for the sake of the single signature
    of all steps.
    """
    return _cache_compute_input_hash(STEP_NAME, *hash_inputs(problem_id, spec, upstream_artifacts))


def hash_inputs(
    problem_id: str,
    spec: ProblemSpec,
    upstream_artifacts: dict[str, Any] | None = None,
) -> tuple[Any, dict[str, Any] | None]:
    """What goes into the step's input hash: `(spec section, upstream artifacts)` —
    the shared source for `compute_input_hash` and the per-part hashes
    (`cache.compute_input_values`), so that they don't diverge."""
    _require_custom_checker(problem_id, spec)
    return spec.checker.custom_comparison_notes, None


def primary_artifact_path(
    problem_id: str, spec: ProblemSpec, *, problems_dir: Path = PROBLEMS_DIR
) -> Path:
    """The path to the step's primary artifact — unlike the solution stages
    (a directory with an arbitrary set of files), `checker_draft` has exactly one
    file with a fixed name, see `constraints_pick.primary_artifact_path`.
    """
    return Path(problems_dir) / problem_id / "checker.cpp"


def current_artifacts(
    problem_id: str, spec: ProblemSpec, *, problems_dir: Path = PROBLEMS_DIR
) -> dict[str, Path]:
    """The step's current files on disk: artifact name (as the
    model would return it) -> path — for revision mode (`base.StepRevision`). `checker.cpp`."""
    return existing_artifacts({"checker.cpp": Path(problems_dir) / problem_id / "checker.cpp"})


def extra_context_documents(
    spec: ProblemSpec, *, templates_dir: Path = TEMPLATES_DIR
) -> list[str] | None:
    """Samples for a particular call — the files the spec's references
    give this step (the shared `references` and `checker.references`, see
    `orchestrator/references.py`). The single signature with all steps is needed by
    `pipeline.py` to compute `compute_prompt_hash` the same way.
    """
    return reference_documents(STEP_NAME, spec)


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
    """Runs the `checker_draft` step for `problem_id`.

    Input: the spec's `checker` section — the only field that actually
    determines the contents is `checker.custom_comparison_notes`
    (a self-contained text description of the answer format and the comparison rule,
    see CLAUDE.md, "Role of each generative step", item 5). The model has no
    right to derive the answer format/comparison rule from the legend or the
    statement — only from this field; if it's incomplete, the step must return
    `status: uncertain`, and this call ends with `StepUncertainError` without
    writing files.

    Requires `spec.checker.custom_needed is True` — with a standard
    checker (`custom_needed: false`) no draft was ordered, and running the step makes no
    sense (unlike the solution stages, where a whole
    optional section is absent, here the `checker` section is always mandatory, and
    "the step isn't needed" is expressed by the concrete value `custom_needed: false`).

    On success it writes `problems/<problem_id>/checker.cpp`.

    `upstream_artifacts` isn't used: `checker_draft` depends on
    nothing (CLAUDE.md, "Caching by spec hash").
    """
    _require_custom_checker(problem_id, spec)

    context = {
        "problem_id": spec.problem_id,
        "checker": spec.checker.model_dump(mode="json", exclude=SPEC_REFERENCES_FIELD),
    }
    input_hash = compute_input_hash(problem_id, spec, upstream_artifacts)
    input_values = compute_input_values(*hash_inputs(problem_id, spec, upstream_artifacts))

    def resolve_artifact_path(filename: str) -> Path:
        return Path(problems_dir) / problem_id / filename

    return run_generative_step(
        step_name=STEP_NAME,
        problem_id=problem_id,
        context=context,
        input_hash=input_hash,
        input_values=input_values,
        revision=revision,
        saved_response=saved_response,
        resolve_artifact_path=resolve_artifact_path,
        prompts_dir=prompts_dir,
        problems_dir=problems_dir,
        templates_dir=templates_dir,
        extra_context_documents=extra_context_documents(spec, templates_dir=templates_dir),
    )
