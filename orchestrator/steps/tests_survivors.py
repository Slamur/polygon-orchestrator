"""Step 3, after the solutions — tests for the wrong solutions the tests don't catch,
CLAUDE.md, "Role of each generative step", item 3.

The base test plan (`tests_plan`) doesn't design tests for particular wrong solutions:
they are added here, and only for the solutions that the local runs of
`solutions_slow` and `solutions_wrong` report as surviving — no test from the script
gets their declared verdict (the examples don't count). The response is the same
`authoring/tests_plan.yaml` with new blocks (`covers: solutions/<name>`) and, if
needed, new generators; the current plan is kept as is (`tests_plan.check_plan_extends`).
After the step the pipeline writes the code of the plan again and reruns the solutions
(`pipeline.run_pipeline`, `survivor_rounds`).

The input — the survivors (name, verdict, code), the main solution, the input format,
the legend, `algorithm_hint` and `constraints.yaml` without TL/ML. The current plan
isn't part of it — it's the context of the call: so the plan this step itself extends
doesn't make it `stale`. The survivors are still the same after the step — the next
run doesn't call the model again for them; some of them were caught — the rest are a
new input.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from orchestrator.cache import compute_input_hash as _cache_compute_input_hash
from orchestrator.cache import compute_input_values
from orchestrator.limits import LIMITS_FILENAME, LimitsError, parse_limits
from orchestrator.model_contracts import ModelResponse
from orchestrator.problem_paths import TESTS_PLAN_FILENAME, authoring_path
from orchestrator.references import reference_documents
from orchestrator.spec import ProblemSpec
from orchestrator.steps.base import (
    CONSTRAINTS_ARTIFACT,
    PROBLEMS_DIR,
    PROMPTS_DIR,
    TEMPLATES_DIR,
    StepResult,
    StepRevision,
    check_artifact_names,
    constraints_without_limits,
    require_constraints_yaml,
    run_generative_step,
    statement_io_section,
)
from orchestrator.steps.constraints_pick import set_spec_problem_id
from orchestrator.steps.solutions import GATES, SOLUTIONS_SUBDIR, main_solution, solution_files
from orchestrator.steps.tests_plan import (
    TESTS_PLAN_ARTIFACT,
    check_plan_extends,
    dump_plan_part,
    parse_tests_plan,
)

STEP_NAME = "tests_survivors"
INPUTS_KEY = f"{STEP_NAME}: inputs"

# The local runs whose survivors get tests.
SURVIVOR_RUNS = ("run_slow", "run_wrong")
# The `covers` of a block for a surviving solution: `solutions/<name>`.
COVERS_PREFIX = f"{SOLUTIONS_SUBDIR}/"


def survivors(
    problem_id: str, spec: ProblemSpec, *, problems_dir: Path = PROBLEMS_DIR
) -> list[dict[str, Any]]:
    """The solutions the current local runs of `SURVIVOR_RUNS` report as surviving."""
    found = []
    for run in SURVIVOR_RUNS:
        found += GATES[run].current_survivors(problem_id, spec, problems_dir=problems_dir)
    return sorted(found, key=lambda s: s["name"])


def skip_detail(
    spec: ProblemSpec, *, problem_id: str, problems_dir: Path = PROBLEMS_DIR
) -> str | None:
    if spec.solutions is None:
        return "spec has no 'solutions' section — no wrong solutions to catch"
    if not survivors(problem_id, spec, problems_dir=problems_dir):
        return "the tests from the script catch every wrong solution"
    return None


def load_inputs(
    problem_id: str, spec: ProblemSpec, *, problems_dir: Path = PROBLEMS_DIR
) -> dict[str, Any]:
    """The survivors with their code, the main solution and the current plan."""
    problem_dir = Path(problems_dir) / problem_id
    files = solution_files(problem_dir)
    found = [
        {**survivor, "code": files[survivor["name"]].read_text(encoding="utf-8")}
        for survivor in survivors(problem_id, spec, problems_dir=problems_dir)
        if survivor["name"] in files
    ]
    main = main_solution(files)
    plan_path = authoring_path(problem_dir, TESTS_PLAN_FILENAME)
    return {
        "survivors": found,
        "main": {main: files[main].read_text(encoding="utf-8")} if main else {},
        "plan": plan_path.read_text(encoding="utf-8") if plan_path.is_file() else None,
        "time_limit_seconds": _time_limit(problem_dir),
    }


def _time_limit(problem_dir: Path) -> float | None:
    """The problem's TL from `limits.yaml` — the context for time verdicts, not the input."""
    path = problem_dir / LIMITS_FILENAME
    try:
        time_limit_ms, _ = parse_limits(path.read_text(encoding="utf-8"))
    except (OSError, LimitsError):
        return None
    return time_limit_ms / 1000


def _inputs(problem_id: str, upstream_artifacts: dict[str, Any] | None) -> dict[str, Any]:
    if not upstream_artifacts or INPUTS_KEY not in upstream_artifacts:
        raise ValueError(f"Step '{STEP_NAME}' for '{problem_id}': the survivors aren't loaded")
    inputs = upstream_artifacts[INPUTS_KEY]
    if inputs["plan"] is None:
        raise ValueError(
            f"Step '{STEP_NAME}' for '{problem_id}': no authoring/{TESTS_PLAN_FILENAME} — "
            "the 'tests_plan' step must succeed first"
        )
    return inputs


def _build_context_sections(spec: ProblemSpec) -> dict[str, Any]:
    statement = statement_io_section(spec, include_output_and_legend=False)
    statement["legend_sketch"] = spec.statement_draft.legend_sketch
    return {
        "statement": statement,
        "solutions": {"algorithm_hint": spec.solutions.algorithm_hint if spec.solutions else None},
    }


def hash_inputs(
    problem_id: str,
    spec: ProblemSpec,
    upstream_artifacts: dict[str, Any] | None = None,
) -> tuple[Any, dict[str, Any] | None]:
    """`(spec section, upstream artifacts)`: `constraints.yaml` without TL/ML, the code of the
    main solution and of each survivor and their verdicts — not their times: those differ
    from run to run."""
    constraints_yaml = constraints_without_limits(
        require_constraints_yaml(STEP_NAME, problem_id, upstream_artifacts)
    )
    inputs = _inputs(problem_id, upstream_artifacts)
    upstream: dict[str, Any] = {CONSTRAINTS_ARTIFACT: constraints_yaml}
    upstream.update({f"{COVERS_PREFIX}{name}": code for name, code in inputs["main"].items()})
    for survivor in inputs["survivors"]:
        upstream[f"{COVERS_PREFIX}{survivor['name']}"] = survivor["code"]
    upstream["survivors"] = [
        {"name": s["name"], "verdict": s["verdict"]} for s in inputs["survivors"]
    ]
    return _build_context_sections(spec), upstream


def compute_input_hash(
    problem_id: str,
    spec: ProblemSpec,
    upstream_artifacts: dict[str, Any] | None = None,
) -> str:
    return _cache_compute_input_hash(STEP_NAME, *hash_inputs(problem_id, spec, upstream_artifacts))


def extra_context_documents(
    spec: ProblemSpec, *, templates_dir: Path = TEMPLATES_DIR
) -> list[str] | None:
    """Samples — the files the spec's references give this step, as for `tests_plan`."""
    return reference_documents(STEP_NAME, spec)


def primary_artifact_path(
    problem_id: str, spec: ProblemSpec, *, problems_dir: Path = PROBLEMS_DIR
) -> Path:
    return authoring_path(Path(problems_dir) / problem_id, TESTS_PLAN_ARTIFACT)


def current_artifacts(
    problem_id: str, spec: ProblemSpec, *, problems_dir: Path = PROBLEMS_DIR
) -> dict[str, Path]:
    """No revision mode: the current plan is always in the request (`run_step`), and the
    response extends it."""
    return {}


def keep_current_entries(current: dict[str, Any], text: str) -> str:
    """The plan `text` with every entry equal to one of the `current` plan replaced by that
    entry itself: the model may reorder the keys of an entry it kept, and the next steps
    hash entries as YAML text (`tests_plan.dump_plan_part`) — a kept generator must stay
    a cache hit of `generators_draft`."""
    plan = parse_tests_plan(text)
    for key in ("generators", "constants", "tests"):
        kept = list(current.get(key) or [])
        plan[key] = [next((e for e in kept if e == entry), entry) for entry in plan.get(key) or []]
    return dump_plan_part(plan)


def _postprocess(spec: ProblemSpec, current: dict[str, Any], names: list[str]):
    """The `problem_id` in the plan — from the spec, the current plan's entries — as they
    are (`keep_current_entries`); an `uncovered` note for each survivor that no block of the
    plan refers to."""
    set_problem_id = set_spec_problem_id(spec.problem_id, TESTS_PLAN_ARTIFACT)

    def postprocess(artifacts: dict[str, str]) -> tuple[dict[str, str], list[dict[str, Any]]]:
        text = artifacts.get(TESTS_PLAN_ARTIFACT)
        if text is None:
            return artifacts, []
        artifacts = {**artifacts, TESTS_PLAN_ARTIFACT: keep_current_entries(current, text)}
        artifacts, notes = set_problem_id(artifacts)
        text = artifacts[TESTS_PLAN_ARTIFACT]
        covered = {
            item.strip()
            for test in parse_tests_plan(text).get("tests") or []
            for item in test.get("covers") or []
        }
        notes = [*notes]
        for name in names:
            if f"{COVERS_PREFIX}{name}" not in covered:
                notes.append(
                    {
                        "field": f"{COVERS_PREFIX}{name}",
                        "kind": "uncovered",
                        "explanation": f"no test of {TESTS_PLAN_ARTIFACT} covers it "
                        "(tests[].covers) — it may survive the tests again",
                    }
                )
        return artifacts, notes

    return postprocess


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
    """Runs the step for `problem_id`: the plan with tests for the survivors. A plan
    that doesn't parse or doesn't keep the current one isn't written: the response goes
    to conflicts and stays for `llm apply`."""
    section, upstream = hash_inputs(problem_id, spec, upstream_artifacts)
    inputs = _inputs(problem_id, upstream_artifacts)
    current_plan = parse_tests_plan(inputs["plan"])
    context = {
        "problem_id": spec.problem_id,
        **section,
        "constraints_yaml": upstream[CONSTRAINTS_ARTIFACT],
        "plan": inputs["plan"],
        "main": inputs["main"],
        "survivors": inputs["survivors"],
        "time_limit_seconds": inputs["time_limit_seconds"],
    }

    def resolve_artifact_path(filename: str) -> Path:
        if filename != TESTS_PLAN_ARTIFACT:
            raise ValueError(
                f"Step '{STEP_NAME}' returns only {TESTS_PLAN_ARTIFACT}, got '{filename}'"
            )
        return primary_artifact_path(problem_id, spec, problems_dir=problems_dir)

    def validate_artifacts(artifacts: dict[str, str]) -> None:
        check_artifact_names(STEP_NAME, {TESTS_PLAN_ARTIFACT}, artifacts)
        check_plan_extends(current_plan, parse_tests_plan(artifacts[TESTS_PLAN_ARTIFACT]))

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
        extra_context_documents=extra_context_documents(spec, templates_dir=templates_dir),
        postprocess_artifacts=_postprocess(
            spec, current_plan, [s["name"] for s in inputs["survivors"]]
        ),
    )
