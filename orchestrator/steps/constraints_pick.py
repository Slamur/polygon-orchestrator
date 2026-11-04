"""Step 2 — fixing the limits (N/TL/ML, test groups) + validator.cpp,
CLAUDE.md, "Role of each generative step", item 2.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from orchestrator.cache import compute_input_hash as _cache_compute_input_hash
from orchestrator.cache import compute_input_values
from orchestrator.model_contracts import ModelResponse
from orchestrator.problem_paths import authoring_path
from orchestrator.references import reference_documents
from orchestrator.spec import (
    SPEC_REFERENCES_FIELD,
    ProblemSpec,
    SpecInitError,
    set_problem_id,
)
from orchestrator.steps.base import (
    CONSTRAINTS_ARTIFACT,
    PROBLEMS_DIR,
    PROMPTS_DIR,
    TEMPLATES_DIR,
    StepResult,
    StepRevision,
    existing_artifacts,
    run_generative_step,
    statement_io_section,
    with_default_lists,
)

STEP_NAME = "constraints_pick"

# Optional[list[...]] fields of the constraints section over which user.md.j2 does
# {% for %} without a null check (see base.with_default_lists).
_LIST_FIELDS = ["special_guarantees", "test_groups_hint", "uncertain_points"]


def _build_context_sections(spec: ProblemSpec) -> dict[str, Any]:
    """The spec sections for rendering the prompt AND for the cache hash input — a single
    assembly point, so that they don't diverge. Besides the `constraints` section — the input
    format from `statement_draft` (`base.statement_io_section`, without the legend and
    the output): the validator reads the input token by token, and it has nowhere to take the order/split into
    lines from except `formal_input_sketch`.
    """
    return {
        "constraints": with_default_lists(
            spec.constraints.model_dump(mode="json", exclude=SPEC_REFERENCES_FIELD), _LIST_FIELDS
        ),
        "statement": statement_io_section(spec, include_output_and_legend=False),
    }


def set_spec_problem_id(
    problem_id: str, artifact: str | None = None
) -> Callable[[dict[str, str]], tuple[dict[str, str], list[dict[str, Any]]]]:
    """`postprocess_artifacts` for `run_generative_step`: the `problem_id` in the
    step's YAML artifact (`artifact`, `constraints.yaml` by default) is set by the
    orchestrator from the spec, not by the model — it could have rewritten it wrongly.
    There's no `problem_id:` line in the response — the file as is."""
    artifact = artifact or CONSTRAINTS_ARTIFACT

    def postprocess(artifacts: dict[str, str]) -> tuple[dict[str, str], list[dict[str, Any]]]:
        text = artifacts.get(artifact)
        if text is None:
            return artifacts, []
        try:
            fixed = set_problem_id(text, problem_id)
        except SpecInitError:
            return artifacts, []
        if fixed == text:
            return artifacts, []
        note = {
            "field": "problem_id",
            "kind": "restored",
            "explanation": (
                f"problem_id in {artifact} replaced by the orchestrator with '{problem_id}' from the spec"
            ),
        }
        return {**artifacts, artifact: fixed}, [note]

    return postprocess


def compute_input_hash(
    problem_id: str,
    spec: ProblemSpec,
    upstream_artifacts: dict[str, Any] | None = None,
) -> str:
    """The step's input hash without calling the model — see `statement_draft.compute_input_hash`
    and CLAUDE.md, "Caching by spec hash". The input — the `constraints` section +
    the input format from `statement_draft` (see `_build_context_sections`): an edit of the
    input format must invalidate `validator.cpp` too.
    `problem_id`/`upstream_artifacts` aren't used, they're present for the sake of the
    single signature of all steps.
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
    return _build_context_sections(spec), None


def primary_artifact_path(
    problem_id: str, spec: ProblemSpec, *, problems_dir: Path = PROBLEMS_DIR
) -> Path:
    """The path to the step's primary artifact, see `statement_draft.primary_artifact_path`."""
    return authoring_path(Path(problems_dir) / problem_id, CONSTRAINTS_ARTIFACT)


def current_artifacts(
    problem_id: str, spec: ProblemSpec, *, problems_dir: Path = PROBLEMS_DIR
) -> dict[str, Path]:
    """The step's current files on disk: artifact name (as the
    model would return it) -> path — for revision mode (`base.StepRevision`). `constraints.yaml` and
    `validator.cpp`."""
    problem_dir = Path(problems_dir) / problem_id
    return existing_artifacts(
        {
            CONSTRAINTS_ARTIFACT: primary_artifact_path(
                problem_id, spec, problems_dir=problems_dir
            ),
            "validator.cpp": problem_dir / "validator.cpp",
        }
    )


def extra_context_documents(
    spec: ProblemSpec, *, templates_dir: Path = TEMPLATES_DIR
) -> list[str] | None:
    """Samples for a particular call — the files the spec's references
    give this step (the shared `references` and `constraints.references`, see
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
    """Runs the `constraints_pick` step for `problem_id`.

    Input: the spec's `constraints` section — first of all
    `intended_complexity` and `complexity_reasoning`, as well as `variables`,
    `special_guarantees`, `test_groups_hint`, the limits and `uncertain_points`.
    Plus the input format from `statement_draft` (`formal_input_sketch`,
    `indexing`, the examples' input) — the source of the reading order for
    `validator.cpp`; the legend isn't passed.

    The model **has no right** to derive the solution's asymptotics from the problem's
    legend on its own — the only source of the asymptotics is
    `constraints.intended_complexity`/`complexity_reasoning`, given by the author
    explicitly (CLAUDE.md, "Role of each generative step", item 2). An empty/un-
    formulated `intended_complexity` is already rejected at the level of
    `orchestrator.spec.load_spec` (a mandatory non-empty field), but if the
    prompt still can't reconcile the limits with this justification —
    it must return `status: uncertain` rather than guess, and this call
    ends with `StepUncertainError` without writing files (see "Escalation rule
    on uncertainty"). For values not set by the author explicitly
    (e.g. `variables[].max: null` or `time_limit_seconds: null`),
    only `status: proposed` with a justification in `notes` is allowed, never
    `confirmed`.

    Besides `constraints.yaml`, the same model call writes `validator.cpp` —
    an implementation of the `validator` section (`constants`/`checks`) from `constraints.yaml`
    1-to-1, following the `validator.cpp` of the sample problem (CLAUDE.md, "Role of each
    generative step", item 2). It isn't a separate step: `status: uncertain`
    stops both files at once, `validator.cpp` isn't written without
    `constraints.yaml` and vice versa.

    On success it writes `problems/<problem_id>/authoring/constraints.yaml` and
    `problems/<problem_id>/validator.cpp`; `validator.cpp` is additionally
    run through `compile_check` (like any other `.cpp` artifact —
    see `run_generative_step`).

    `upstream_artifacts` isn't used: `constraints_pick` depends only
    on the spec (CLAUDE.md, "Caching by spec hash").
    """
    context = {"problem_id": spec.problem_id, **_build_context_sections(spec)}
    input_hash = compute_input_hash(problem_id, spec, upstream_artifacts)
    input_values = compute_input_values(*hash_inputs(problem_id, spec, upstream_artifacts))

    def resolve_artifact_path(filename: str) -> Path:
        if filename == CONSTRAINTS_ARTIFACT:
            return primary_artifact_path(problem_id, spec, problems_dir=problems_dir)
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
        postprocess_artifacts=set_spec_problem_id(spec.problem_id),
    )
