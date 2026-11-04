"""Step 3, the second part — generators (C++/testlib) from the test plan, CLAUDE.md, "Role
of each generative step", item 3.

The mechanical part of test preparation: each generator of the plan
(`authoring/tests_plan.yaml`, the `tests_plan` step) is a file
`generators/<name>.cpp` with the parameters from the plan. The step doesn't see the spec, except
the input format: what and how to generate is decided in the plan.

The input by parts — each generator of the plan separately (`upstream: tests_plan.yaml:
<name>`): editing one plan entry is shown to the model in revision mode
as editing one generator, and editing the plan's tests (`tests`, `constants`)
doesn't touch this step at all.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from orchestrator.cache import compute_input_hash as _cache_compute_input_hash
from orchestrator.cache import compute_input_values
from orchestrator.model_contracts import ModelResponse
from orchestrator.references import reference_documents
from orchestrator.spec import ProblemSpec
from orchestrator.steps.base import (
    CONSTRAINTS_ARTIFACT,
    PROBLEMS_DIR,
    PROMPTS_DIR,
    TEMPLATES_DIR,
    MissingArtifactsError,
    StepResult,
    StepRevision,
    artifact_filename,
    constraints_without_limits,
    directory_artifacts,
    require_constraints_yaml,
    run_generative_step,
    statement_io_section,
)
from orchestrator.steps.tests_plan import (
    TESTS_PLAN_ARTIFACT,
    dump_plan_part,
    load_tests_plan,
    plan_generators,
)

STEP_NAME = "generators_draft"

# The generators directory inside the problem directory.
GENERATORS_SUBDIR = "generators"

# `opt<T>("name")` in a generator is a testlib command-line parameter.
_OPT_RE = re.compile(r"\bopt\s*<[^>]*>\s*\(\s*\"([A-Za-z_][A-Za-z0-9_]*)\"")


def _plan_key(name: str) -> str:
    return f"{TESTS_PLAN_ARTIFACT}: {name}"


def hash_inputs(
    problem_id: str,
    spec: ProblemSpec,
    upstream_artifacts: dict[str, Any] | None = None,
) -> tuple[Any, dict[str, Any] | None]:
    """`(spec section, upstream artifacts)`: the input format from `statement_draft`;
    `constraints.yaml` without TL/ML (the input guarantees the
    validator checks — the generator must respect them) and each generator of the plan
    as a separate part."""
    constraints_yaml = require_constraints_yaml(STEP_NAME, problem_id, upstream_artifacts)
    plan = load_tests_plan(STEP_NAME, problem_id, upstream_artifacts)
    upstream = {CONSTRAINTS_ARTIFACT: constraints_without_limits(constraints_yaml)}
    for generator in plan_generators(plan):
        upstream[_plan_key(generator["name"])] = dump_plan_part(generator)
    return {"statement": statement_io_section(spec, include_output_and_legend=False)}, upstream


def compute_input_hash(
    problem_id: str,
    spec: ProblemSpec,
    upstream_artifacts: dict[str, Any] | None = None,
) -> str:
    return _cache_compute_input_hash(STEP_NAME, *hash_inputs(problem_id, spec, upstream_artifacts))


def extra_context_documents(
    spec: ProblemSpec, *, templates_dir: Path = TEMPLATES_DIR
) -> list[str] | None:
    """Samples — generators from the spec's references (`orchestrator/references.py`)."""
    return reference_documents(STEP_NAME, spec)


def primary_artifact_path(
    problem_id: str, spec: ProblemSpec, *, problems_dir: Path = PROBLEMS_DIR
) -> Path:
    """The generators directory: their names are set by the plan, so `pipeline.py`
    considers the step's files in place if the directory exists and isn't empty."""
    return Path(problems_dir) / problem_id / GENERATORS_SUBDIR


def current_artifacts(
    problem_id: str, spec: ProblemSpec, *, problems_dir: Path = PROBLEMS_DIR
) -> dict[str, Path]:
    """The step's current files for revision mode: `generators/*.cpp` under
    their file names."""
    return directory_artifacts(
        primary_artifact_path(problem_id, spec, problems_dir=problems_dir), "*.cpp"
    )


def generator_params(source: str) -> list[str]:
    """The names of the `opt<T>("name")` parameters in the generator's code, without repeats."""
    return list(dict.fromkeys(_OPT_RE.findall(source)))


def plan_mismatch_notes(plan: dict[str, Any], generators_dir: Path) -> list[dict[str, Any]]:
    """`plan_mismatch` notes after writing the files: a generator on disk
    that isn't in the plan, and a generator whose `opt<T>(...)` don't match the
    plan's parameters — the test-script is built from the plan and will call it with
    other parameters."""
    expected = {
        g["name"]: [p["name"] for p in g.get("params") or []] for g in plan_generators(plan)
    }
    notes: list[dict[str, Any]] = []
    for path in sorted(Path(generators_dir).glob("*.cpp")):
        name = path.stem
        if name not in expected:
            notes.append(
                _note(
                    f"generators/{path.name}",
                    f"not in {TESTS_PLAN_ARTIFACT}: test_script_draft will not use it — "
                    "add it to the plan or delete the file",
                )
            )
            continue
        actual = generator_params(path.read_text(encoding="utf-8"))
        if set(actual) != set(expected[name]):
            notes.append(
                _note(
                    f"generators/{path.name}",
                    f"opt parameters {actual} differ from the plan {expected[name]}",
                )
            )
    return notes


def _note(field: str, explanation: str) -> dict[str, Any]:
    return {"field": field, "kind": "plan_mismatch", "explanation": explanation}


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
    """Runs the `generators_draft` step for `problem_id`.

    Input: the plan's generators (`upstream_artifacts["tests_plan.yaml"]`), the input
    format from `statement_draft`, `constraints.yaml` without TL/ML. Output — one
    file `generators/<name>.cpp` per generator of the plan: the model completes missing ones
    once with a separate call (`MissingArtifactsError`), about
    generators that aren't in the plan — a note. The files are independent and
    are applied one by one (`partial_apply`); after writing — reconciling the
    parameters with the plan (`plan_mismatch_notes`).
    """
    constraints_yaml = require_constraints_yaml(STEP_NAME, problem_id, upstream_artifacts)
    plan = load_tests_plan(STEP_NAME, problem_id, upstream_artifacts)
    generators = plan_generators(plan)
    context = {
        "problem_id": spec.problem_id,
        "statement": statement_io_section(spec, include_output_and_legend=False),
        "constraints_yaml": constraints_without_limits(constraints_yaml),
        "generators": [
            {"name": generator["name"], "plan": dump_plan_part(generator)}
            for generator in generators
        ],
    }
    input_hash = compute_input_hash(problem_id, spec, upstream_artifacts)
    input_values = compute_input_values(*hash_inputs(problem_id, spec, upstream_artifacts))
    expected = {f"{generator['name']}.cpp" for generator in generators}
    generators_dir = primary_artifact_path(problem_id, spec, problems_dir=problems_dir)

    def resolve_artifact_path(filename: str) -> Path:
        if not filename.endswith(".cpp"):
            raise ValueError(f"Step '{STEP_NAME}' returns only generators/*.cpp, got '{filename}'")
        return generators_dir / artifact_filename(filename, GENERATORS_SUBDIR)

    def validate_artifacts(artifacts: dict[str, str]) -> None:
        # Extra generators aren't a response error (e.g. a previous generator
        # the model kept silent about in revision mode): there's a
        # `plan_mismatch_notes` note about them.
        missing = sorted(expected - {resolve_artifact_path(name).name for name in artifacts})
        if missing:
            raise MissingArtifactsError(
                f"Step '{STEP_NAME}': incomplete response — missing generators: {missing}", missing
            )

    result = run_generative_step(
        step_name=STEP_NAME,
        problem_id=problem_id,
        context=context,
        input_hash=input_hash,
        input_values=input_values,
        revision=revision,
        saved_response=saved_response,
        resolve_artifact_path=resolve_artifact_path,
        validate_artifacts=validate_artifacts,
        # The generators are independent: they're applied one by one.
        partial_apply=True,
        prompts_dir=prompts_dir,
        problems_dir=problems_dir,
        templates_dir=templates_dir,
        extra_context_documents=extra_context_documents(spec, templates_dir=templates_dir),
    )
    if result.not_applied_reason is None:
        result.notes.extend(plan_mismatch_notes(plan, generators_dir))
    return result
