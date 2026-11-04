"""Step 6 (optional) — an interactor draft for an interactive problem, CLAUDE.md,
"Role of each generative step", item 6.
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

STEP_NAME = "interactor_draft"

INTERACTOR_FILENAME = "interactor.cpp"


def _require_interactive(problem_id: str, spec: ProblemSpec) -> None:
    if not spec.interactive:
        raise ValueError(
            f"Step '{STEP_NAME}' for '{problem_id}': the spec has no 'interactor' "
            "section — the problem is not interactive, the step is not run "
            "(see CLAUDE.md, generative step roles, item 6)"
        )


def _build_context_sections(spec: ProblemSpec) -> dict[str, Any]:
    """The spec sections for rendering the prompt AND for the cache hash input — a single
    assembly point. Besides the `interactor` section — what the interactor reads and how it
    communicates with the solution: the test format (`formal_input_sketch`, `indexing`,
    the examples' input — the interactor reads the test the same way the validator does) and
    the protocol (`interaction_sketch`). The legend and the output format aren't passed:
    the interactor's behavior is set by the author, not the plot."""
    statement = spec.statement_draft
    return {
        "interactor": spec.interactor.model_dump(mode="json", exclude=SPEC_REFERENCES_FIELD),
        "statement": {
            "formal_input_sketch": statement.formal_input_sketch,
            "indexing": statement.indexing,
            "interaction_sketch": statement.interaction_sketch,
            "sample_examples": [{"input": ex.input} for ex in statement.sample_examples],
        },
    }


def compute_input_hash(
    problem_id: str,
    spec: ProblemSpec,
    upstream_artifacts: dict[str, Any] | None = None,
) -> str:
    """The step's input hash without calling the model, see `constraints_pick.compute_input_hash`
    and CLAUDE.md, "Caching by spec hash". Like `run_step`, it requires
    an interactive problem (the `interactor` section in the spec). `upstream_artifacts`
    isn't used — the step depends only on the spec, it's present for the sake of the single
    signature of all steps.
    """
    return _cache_compute_input_hash(STEP_NAME, *hash_inputs(problem_id, spec, upstream_artifacts))


def hash_inputs(
    problem_id: str,
    spec: ProblemSpec,
    upstream_artifacts: dict[str, Any] | None = None,
) -> tuple[Any, dict[str, Any] | None]:
    """What goes into the step's input hash: `(spec sections, upstream artifacts)` —
    the shared source for `compute_input_hash` and the per-part hashes
    (`cache.compute_input_values`), so that they don't diverge."""
    _require_interactive(problem_id, spec)
    return _build_context_sections(spec), None


def primary_artifact_path(
    problem_id: str, spec: ProblemSpec, *, problems_dir: Path = PROBLEMS_DIR
) -> Path:
    """The path to the step's primary artifact — one file with a fixed name,
    as for `checker_draft`."""
    return Path(problems_dir) / problem_id / INTERACTOR_FILENAME


def current_artifacts(
    problem_id: str, spec: ProblemSpec, *, problems_dir: Path = PROBLEMS_DIR
) -> dict[str, Path]:
    """The step's current files on disk: artifact name (as the
    model would return it) -> path — for revision mode (`base.StepRevision`). `interactor.cpp`."""
    return existing_artifacts(
        {INTERACTOR_FILENAME: Path(problems_dir) / problem_id / INTERACTOR_FILENAME}
    )


def extra_context_documents(
    spec: ProblemSpec, *, templates_dir: Path = TEMPLATES_DIR
) -> list[str] | None:
    """Samples for a particular call — the files the spec's references
    give this step (the shared `references` and `interactor.references`, see
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
    """Runs the `interactor_draft` step for `problem_id`.

    Input: `interactor.behavior_notes` (how the interactor chooses answers,
    what counts as a solution error, what it writes for the checker) + the test format and
    the protocol from `statement_draft` (`_build_context_sections`). The model
    implements exactly this behavior; if it isn't enough for an unambiguous
    interactor (e.g. it isn't said whether it's adaptive, or what to do when
    the query limit is exceeded), the step must return `status: uncertain`, and the
    call ends with `StepUncertainError` without writing files.

    Requires an interactive problem (the `interactor` section in the spec) — a normal
    problem has no interactor, the step doesn't run (`pipeline._optional_skip_detail`).

    On success it writes `problems/<problem_id>/interactor.cpp` and checks that it
    compiles. `upstream_artifacts` isn't used.
    """
    _require_interactive(problem_id, spec)

    context = {"problem_id": spec.problem_id, **_build_context_sections(spec)}
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
