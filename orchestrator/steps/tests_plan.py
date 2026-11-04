"""Step 3, the first part — the test plan: which generators the problem needs and which tests
the test-script assembles from them, CLAUDE.md, "Role of each generative step", item 3.

The substantive part of test preparation — implementing what the author ordered and
designing adversarial tests for `known_wrong_approaches` — is done here, in a
small response: `authoring/tests_plan.yaml` in the
`templates/tests_plan_format.yaml` format. The code from the plan is written by the next steps —
`generators_draft` (generators) and `test_script_draft` (the script); they don't see
the spec, so the plan is self-contained. Parsing the plan (`parse_tests_plan`) is
shared by all three steps.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

import yaml

from orchestrator.cache import compute_input_hash as _cache_compute_input_hash
from orchestrator.cache import compute_input_values
from orchestrator.model_contracts import ModelResponse
from orchestrator.problem_paths import TESTS_PLAN_FILENAME, authoring_path
from orchestrator.references import reference_documents
from orchestrator.spec import SPEC_REFERENCES_FIELD, ProblemSpec
from orchestrator.steps.base import (
    CONSTRAINTS_ARTIFACT,
    PROBLEMS_DIR,
    PROMPTS_DIR,
    TEMPLATES_DIR,
    StepResult,
    StepRevision,
    check_artifact_names,
    constraints_without_limits,
    existing_artifacts,
    require_constraints_yaml,
    run_generative_step,
    statement_io_section,
    with_default_lists,
)
from orchestrator.steps.constraints_pick import set_spec_problem_id

STEP_NAME = "tests_plan"
TESTS_PLAN_ARTIFACT = TESTS_PLAN_FILENAME

# Optional[list[...]] fields of the generation section over which user.md.j2 does
# {% for %} without a null check (see base.with_default_lists).
_LIST_FIELDS = ["generator_ideas", "specific_test_ideas"]
_SOLUTIONS_LIST_FIELDS = ["known_wrong_approaches"]

# The spec items the plan must cover with tests (`tests[].covers`).
COVERED_LISTS = ("specific_test_ideas", "known_wrong_approaches")
_COVERS_RE = re.compile(r"^(specific_test_ideas|known_wrong_approaches)\[(\d+)\]$")

GENERATOR_NAME_RE = re.compile(r"^(?:gen|upd)_[A-Za-z0-9_]+$")
PARAM_TYPES = ("int", "ll", "bool", "string")
PLAN_STATUSES = ("confirmed", "proposed", "suggestion")
# A parameter in a plan command: `-name=value` or a flag `--name`.
_COMMAND_PARAM_RE = re.compile(r"^--?([A-Za-z_][A-Za-z0-9_]*)(?:=\S*)?$")


class TestsPlanError(ValueError):
    """`tests_plan.yaml` doesn't parse or violates the format."""

    __test__ = False  # not a pytest test class, even though the name starts with Test


def parse_tests_plan(text: str) -> dict[str, Any]:
    """Parses and checks `tests_plan.yaml` (`templates/tests_plan_format.yaml`):
    generators with unique `gen_*`/`upd_*` names and parameters of known
    types, tests with commands containing only the plan's generators and only their
    parameters. Returns the document as is; an error — `TestsPlanError`."""
    try:
        plan = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise TestsPlanError(f"{TESTS_PLAN_ARTIFACT} is not valid YAML: {exc}") from exc
    if not isinstance(plan, dict):
        raise TestsPlanError(f"{TESTS_PLAN_ARTIFACT}: expected a mapping at the top level")

    errors: list[str] = []
    generators = plan.get("generators")
    if not isinstance(generators, list) or not generators:
        errors.append("'generators' must be a non-empty list")
        generators = []
    params_by_generator: dict[str, set[str]] = {}
    for index, generator in enumerate(generators):
        where = f"generators[{index}]"
        if not isinstance(generator, dict):
            errors.append(f"{where} must be a mapping")
            continue
        name = generator.get("name")
        if not isinstance(name, str) or not GENERATOR_NAME_RE.match(name):
            errors.append(f"{where}.name must look like gen_<...> or upd_<...>, got {name!r}")
            continue
        if name in params_by_generator:
            errors.append(f"{where}: generator '{name}' is listed twice")
        params_by_generator[name] = _check_params(
            generator.get("params"), f"{where}.params", errors
        )
        _check_status(generator.get("status"), where, errors)

    constants = plan.get("constants") or []
    if not isinstance(constants, list):
        errors.append("'constants' must be a list")

    tests = plan.get("tests")
    if not isinstance(tests, list) or not tests:
        errors.append("'tests' must be a non-empty list")
        tests = []
    for index, test in enumerate(tests):
        where = f"tests[{index}]"
        if not isinstance(test, dict):
            errors.append(f"{where} must be a mapping")
            continue
        _check_status(test.get("status"), where, errors)
        commands = test.get("commands")
        if not isinstance(commands, list) or not commands:
            errors.append(f"{where}.commands must be a non-empty list")
            continue
        for number, command in enumerate(commands):
            _check_command(command, f"{where}.commands[{number}]", params_by_generator, errors)
        covers = test.get("covers") or []
        if not isinstance(covers, list) or not all(isinstance(c, str) for c in covers):
            errors.append(f"{where}.covers must be a list of strings")

    if errors:
        raise TestsPlanError(f"{TESTS_PLAN_ARTIFACT}: " + "; ".join(errors))
    return plan


def _check_params(params: Any, where: str, errors: list[str]) -> set[str]:
    if params is None:
        return set()
    if not isinstance(params, list):
        errors.append(f"{where} must be a list")
        return set()
    names: set[str] = set()
    for index, param in enumerate(params):
        if not isinstance(param, dict) or not isinstance(param.get("name"), str):
            errors.append(f"{where}[{index}] must be a mapping with a 'name'")
            continue
        if param.get("type") not in PARAM_TYPES:
            errors.append(
                f"{where}[{index}].type must be one of {list(PARAM_TYPES)}, "
                f"got {param.get('type')!r}"
            )
        names.add(param["name"])
    return names


def _check_status(status: Any, where: str, errors: list[str]) -> None:
    if status not in PLAN_STATUSES:
        errors.append(f"{where}.status must be one of {list(PLAN_STATUSES)}, got {status!r}")


def _check_command(
    command: Any, where: str, params_by_generator: dict[str, set[str]], errors: list[str]
) -> None:
    if not isinstance(command, str) or not command.strip():
        errors.append(f"{where} must be a non-empty string")
        return
    for segment in command.split("|"):
        tokens = segment.split()
        if not tokens:
            errors.append(f"{where}: empty part of the chain in {command!r}")
            continue
        name, *params = tokens
        if name not in params_by_generator:
            errors.append(f"{where}: generator '{name}' is not in 'generators'")
            continue
        for token in params:
            match = _COMMAND_PARAM_RE.match(token)
            if not match:
                errors.append(
                    f"{where}: parameter {token!r} of '{name}' is not -name=value or --name"
                )
            elif match.group(1) not in params_by_generator[name]:
                errors.append(f"{where}: '{name}' has no parameter '{match.group(1)}'")


def plan_generators(plan: dict[str, Any]) -> list[dict[str, Any]]:
    return list(plan.get("generators") or [])


def dump_plan_part(value: Any) -> str:
    """A part of the plan as YAML text — for the prompts and the hash input of the next steps."""
    return yaml.safe_dump(value, allow_unicode=True, sort_keys=False, width=100)


def uncovered_notes(plan: dict[str, Any], spec: ProblemSpec) -> list[dict[str, Any]]:
    """`uncovered` notes: the spec's `specific_test_ideas` and
    `known_wrong_approaches` items that no test of the plan refers to
    (`tests[].covers`), and references to non-existent items — a mechanical
    reconciliation the author would otherwise have to do by eye; and also
    generators no test calls."""
    sizes = {
        "specific_test_ideas": len(spec.generation.specific_test_ideas or []),
        "known_wrong_approaches": len(
            (spec.solutions.known_wrong_approaches if spec.solutions else None) or []
        ),
    }
    covered: set[tuple[str, int]] = set()
    notes: list[dict[str, Any]] = []
    for test in plan.get("tests") or []:
        for item in test.get("covers") or []:
            match = _COVERS_RE.match(item.strip())
            if match is None:
                continue
            key = (match.group(1), int(match.group(2)))
            if key[1] >= sizes[key[0]]:
                notes.append(
                    _note(
                        item,
                        f"{TESTS_PLAN_ARTIFACT} refers to {item}, which the spec does not have",
                    )
                )
            covered.add(key)
    for field in COVERED_LISTS:
        for index in range(sizes[field]):
            if (field, index) not in covered:
                notes.append(
                    _note(
                        f"{field}[{index}]",
                        f"no test of {TESTS_PLAN_ARTIFACT} covers it (tests[].covers) — "
                        "check the plan or add the reference",
                    )
                )
    used = {
        segment.split()[0]
        for test in plan.get("tests") or []
        for command in test.get("commands") or []
        for segment in command.split("|")
        if segment.split()
    }
    for generator in plan_generators(plan):
        if generator["name"] not in used:
            notes.append(
                _note(f"generators/{generator['name']}", "no test of the plan runs this generator")
            )
    return notes


def _note(field: str, explanation: str) -> dict[str, Any]:
    return {"field": field, "kind": "uncovered", "explanation": explanation}


def load_tests_plan(
    step_name: str, problem_id: str, upstream_artifacts: dict[str, Any] | None
) -> dict[str, Any]:
    """The plan from the `upstream_artifacts` of a step that builds code from it. No
    plan or it doesn't parse — `ValueError`: first `tests_plan` (or
    editing `tests_plan.yaml` by hand)."""
    if not upstream_artifacts or TESTS_PLAN_ARTIFACT not in upstream_artifacts:
        raise ValueError(
            f"Step '{step_name}' for '{problem_id}': upstream_artifacts has no "
            f"'{TESTS_PLAN_ARTIFACT}' — the 'tests_plan' step must succeed first"
        )
    try:
        return parse_tests_plan(upstream_artifacts[TESTS_PLAN_ARTIFACT])
    except TestsPlanError as exc:
        raise ValueError(
            f"Step '{step_name}' for '{problem_id}': {exc} — fix "
            f"authoring/{TESTS_PLAN_ARTIFACT} or run the 'tests_plan' step again"
        ) from exc


def _solutions_section_data(spec: ProblemSpec) -> dict[str, Any]:
    """From the `solutions` section — what's needed for test design:
    `known_wrong_approaches` — the source of adversarial tests, `algorithm_hint`
    — the idea of the author solution, from which the model may propose its own tests
    (`kind: suggestion`). The `solutions` section is optional: without it — an empty
    list and `None`, the step runs anyway. The other fields of the section aren't
    taken: an edit of, e.g., `languages` mustn't invalidate the plan.
    """
    if spec.solutions is None:
        return {"algorithm_hint": None, "known_wrong_approaches": []}
    section_data = with_default_lists(
        spec.solutions.model_dump(mode="json", exclude=SPEC_REFERENCES_FIELD),
        _SOLUTIONS_LIST_FIELDS,
    )
    return {
        "algorithm_hint": section_data["algorithm_hint"],
        "known_wrong_approaches": section_data["known_wrong_approaches"],
    }


def _build_context_sections(spec: ProblemSpec) -> dict[str, Any]:
    """The spec sections for rendering the prompt AND for the cache hash input — a single
    assembly point (CLAUDE.md, "Caching by spec hash"): the `generation` section,
    `solutions.known_wrong_approaches` and `algorithm_hint`, the input format from
    `statement_draft` (`base.statement_io_section`) and the legend — without the problem's essence
    the model can't propose its own tests for likely mistakes.
    """
    statement = statement_io_section(spec, include_output_and_legend=False)
    statement["legend_sketch"] = spec.statement_draft.legend_sketch
    return {
        "generation": with_default_lists(
            spec.generation.model_dump(mode="json", exclude=SPEC_REFERENCES_FIELD), _LIST_FIELDS
        ),
        "solutions": _solutions_section_data(spec),
        "statement": statement,
    }


def compute_input_hash(
    problem_id: str,
    spec: ProblemSpec,
    upstream_artifacts: dict[str, Any] | None = None,
) -> str:
    """The step's input hash without calling the model (CLAUDE.md, "Caching by spec
    hash"): the spec sections (`_build_context_sections`) and `constraints.yaml`
    without TL/ML — tuning the TL doesn't change the test plan."""
    return _cache_compute_input_hash(STEP_NAME, *hash_inputs(problem_id, spec, upstream_artifacts))


def hash_inputs(
    problem_id: str,
    spec: ProblemSpec,
    upstream_artifacts: dict[str, Any] | None = None,
) -> tuple[Any, dict[str, Any] | None]:
    """What goes into the step's input hash: `(spec section, upstream artifacts)` —
    the shared source for `compute_input_hash` and the per-part hashes."""
    constraints_yaml = require_constraints_yaml(STEP_NAME, problem_id, upstream_artifacts)
    return _build_context_sections(spec), {
        CONSTRAINTS_ARTIFACT: constraints_without_limits(constraints_yaml)
    }


def extra_context_documents(
    spec: ProblemSpec, *, templates_dir: Path = TEMPLATES_DIR
) -> list[str] | None:
    """Samples — the files the spec's references give this step (the shared
    `references` and `generation.references`, `orchestrator/references.py`)."""
    return reference_documents(STEP_NAME, spec)


def primary_artifact_path(
    problem_id: str, spec: ProblemSpec, *, problems_dir: Path = PROBLEMS_DIR
) -> Path:
    return authoring_path(Path(problems_dir) / problem_id, TESTS_PLAN_ARTIFACT)


def current_artifacts(
    problem_id: str, spec: ProblemSpec, *, problems_dir: Path = PROBLEMS_DIR
) -> dict[str, Path]:
    """The step's current files for revision mode: `tests_plan.yaml`."""
    return existing_artifacts(
        {TESTS_PLAN_ARTIFACT: primary_artifact_path(problem_id, spec, problems_dir=problems_dir)}
    )


def _postprocess(
    spec: ProblemSpec,
) -> Callable[[dict[str, str]], tuple[dict[str, str], list[dict[str, Any]]]]:
    """The `problem_id` in the plan — from the spec (`set_spec_problem_id`), and notes about
    what the plan doesn't cover (`uncovered_notes`)."""
    set_problem_id = set_spec_problem_id(spec.problem_id, TESTS_PLAN_ARTIFACT)

    def postprocess(artifacts: dict[str, str]) -> tuple[dict[str, str], list[dict[str, Any]]]:
        artifacts, notes = set_problem_id(artifacts)
        text = artifacts.get(TESTS_PLAN_ARTIFACT)
        if text is not None:
            notes = [*notes, *uncovered_notes(parse_tests_plan(text), spec)]
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
    """Runs the `tests_plan` step for `problem_id`.

    Input: the `generation` section (generator ideas, concrete tests, the script
    style), `solutions.known_wrong_approaches`/`algorithm_hint`, the input
    format and the legend from `statement_draft`, `constraints.yaml` without TL/ML —
    the only source of ranges and groups. Output —
    `authoring/tests_plan.yaml`; a plan that doesn't parse
    (`parse_tests_plan`) isn't written: the response goes to conflicts and
    stays for `llm apply`.
    """
    constraints_yaml = require_constraints_yaml(STEP_NAME, problem_id, upstream_artifacts)
    context = {
        "problem_id": spec.problem_id,
        **_build_context_sections(spec),
        "constraints_yaml": constraints_without_limits(constraints_yaml),
    }
    input_hash = compute_input_hash(problem_id, spec, upstream_artifacts)
    input_values = compute_input_values(*hash_inputs(problem_id, spec, upstream_artifacts))

    def resolve_artifact_path(filename: str) -> Path:
        if filename != TESTS_PLAN_ARTIFACT:
            raise ValueError(
                f"Step '{STEP_NAME}' returns only {TESTS_PLAN_ARTIFACT}, got '{filename}'"
            )
        return primary_artifact_path(problem_id, spec, problems_dir=problems_dir)

    def validate_artifacts(artifacts: dict[str, str]) -> None:
        check_artifact_names(STEP_NAME, {TESTS_PLAN_ARTIFACT}, artifacts)
        parse_tests_plan(artifacts[TESTS_PLAN_ARTIFACT])

    return run_generative_step(
        step_name=STEP_NAME,
        problem_id=problem_id,
        context=context,
        input_hash=input_hash,
        input_values=input_values,
        revision=revision,
        saved_response=saved_response,
        resolve_artifact_path=resolve_artifact_path,
        validate_artifacts=validate_artifacts,
        prompts_dir=prompts_dir,
        problems_dir=problems_dir,
        templates_dir=templates_dir,
        extra_context_documents=extra_context_documents(spec, templates_dir=templates_dir),
        postprocess_artifacts=_postprocess(spec),
    )
