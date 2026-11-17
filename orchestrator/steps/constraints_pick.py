"""Step 2 — fixing the limits (N/TL/ML, test groups) + validator.cpp,
CLAUDE.md, "Role of each generative step", item 2.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import yaml

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
from orchestrator.test_groups import TEST_GROUPS_KEY, TestGroupsError, parse_test_groups

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


# The full points of a problem with test groups.
FULL_POINTS = 100


def check_test_groups_artifact(text: str, spec: ProblemSpec) -> None:
    """The structural check of the test groups in the model's `constraints.yaml`, before it
    is written (`validate_artifacts`: a failure — a conflict, nothing is applied). The
    groups must read as `orchestrator/test_groups.py` reads them (names, dependencies and
    `subset_of` of listed groups), every dependency — an earlier group, points — given for
    every group or none, summing to 100 (or to the total of the author's points, when the
    spec's `test_groups_hint` gives points to every group), `validator.groups` — only of
    listed groups, each once, and none without `test_groups`. `ValueError` with every
    problem found."""
    try:
        groups = parse_test_groups(text)
    except TestGroupsError as exc:
        raise ValueError(str(exc)) from exc
    data = yaml.safe_load(text)
    validator = data.get("validator") if isinstance(data, dict) else None
    validator_groups = validator.get("groups") if isinstance(validator, dict) else None
    errors: list[str] = []
    if groups is None:
        if validator_groups:
            errors.append(f"validator.groups is set, but there are no {TEST_GROUPS_KEY}")
    else:
        names = [group.name for group in groups]
        for index, group in enumerate(groups):
            for dependency in group.dependencies or []:
                if dependency not in names[:index]:
                    errors.append(
                        f"group {group.name} depends on group {dependency}, "
                        "which isn't an earlier group"
                    )
        errors += _points_errors(groups, spec)
        errors += _validator_groups_errors(validator_groups, names)
    if errors:
        raise ValueError(
            f"{CONSTRAINTS_ARTIFACT}: invalid test groups:\n"
            + "\n".join(f"- {error}" for error in errors)
        )


def _points_errors(groups: list, spec: ProblemSpec) -> list[str]:
    without = [group.name for group in groups if group.points is None]
    if len(without) == len(groups):
        return []
    if without:
        return [f"groups {', '.join(without)} have no points, the other groups have"]
    total = sum(group.points for group in groups)
    hint = spec.constraints.test_groups_hint or []
    author_total = (
        sum(group.points for group in hint)
        if hint and all(group.points is not None for group in hint)
        else None
    )
    if total in (FULL_POINTS, author_total):
        return []
    return [f"the points of the groups sum to {total}, not {FULL_POINTS}"]


def _validator_groups_errors(validator_groups: Any, names: list[str]) -> list[str]:
    if validator_groups is None:
        return []
    if not isinstance(validator_groups, list) or not all(
        isinstance(item, dict) for item in validator_groups
    ):
        return ["validator.groups must be a list of groups"]
    errors = []
    seen: set[str] = set()
    for item in validator_groups:
        name = str(item.get("name", "")).strip()
        if name not in names:
            errors.append(f"validator.groups has '{name}', which isn't one of {TEST_GROUPS_KEY}")
        elif name in seen:
            errors.append(f"validator.groups has group {name} twice")
        seen.add(name)
    return errors


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

    def validate_artifacts(artifacts: dict[str, str]) -> None:
        if CONSTRAINTS_ARTIFACT in artifacts:
            check_test_groups_artifact(artifacts[CONSTRAINTS_ARTIFACT], spec)

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
        validate_artifacts=validate_artifacts,
        extra_context_documents=extra_context_documents(spec, templates_dir=templates_dir),
        postprocess_artifacts=set_spec_problem_id(spec.problem_id),
    )
