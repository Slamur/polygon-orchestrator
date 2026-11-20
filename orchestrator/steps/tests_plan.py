"""Step 3, the first part — the test plan: which generators the problem needs and which tests
the test-script assembles from them, CLAUDE.md, "Role of each generative step", item 3.

The substantive part of test preparation — implementing what the author ordered
(`generator_ideas`, `specific_test_ideas`) and edge cases of the input — is done here, in a
small response: the model writes the test design `authoring/tests_design.yaml`
(`templates/tests_design_format.yaml`) — generators and command templates whose values are
formulas over a test group's limits — and the orchestrator builds from it
`authoring/tests_plan.yaml` in the `templates/tests_plan_format.yaml` format
(`orchestrator/steps/tests_design.py`). The code from the plan is written by the next steps —
`generators_draft` (generators) and `test_script_draft` (the script); they don't see
the spec, so the plan is self-contained. Parsing the plan (`parse_tests_plan`) is
shared by all three steps.

The plan is the base one: tests aimed at particular wrong solutions
(`solutions.known_wrong_approaches`) aren't designed here — they are added only for
a wrong solution that the base tests don't catch, once the solutions are run on them.
Blocks of the current plan that cover wrong approaches are carried over when the plan is
rebuilt from the design.
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
from orchestrator.problem_paths import (
    TESTS_DESIGN_FILENAME,
    TESTS_PLAN_FILENAME,
    authoring_path,
)
from orchestrator.references import reference_documents
from orchestrator.spec import EACH_TEST, SPEC_REFERENCES_FIELD, ProblemSpec, TestGroupHint
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
TESTS_DESIGN_ARTIFACT = TESTS_DESIGN_FILENAME

# Optional[list[...]] fields of the generation section over which user.md.j2 does
# {% for %} without a null check (see base.with_default_lists).
_LIST_FIELDS = ["generator_ideas", "specific_test_ideas"]

# The spec items the plan must cover with tests (`tests[].covers`). `covers` may also
# refer to `known_wrong_approaches[i]` (blocks for wrong solutions), these aren't
# reconciled: not every wrong approach needs a test of its own.
COVERED_LISTS = ("specific_test_ideas",)
_COVERS_RE = re.compile(r"^(specific_test_ideas)\[(\d+)\]$")

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
    types, tests with commands containing only the plan's generators, only with their
    parameters and with all of them except `bool` flags. Returns the document as is; an error — `TestsPlanError`."""
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
    params_by_generator: dict[str, dict[str, str]] = {}
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


def _check_params(params: Any, where: str, errors: list[str]) -> dict[str, str]:
    """The parameters' names -> their types (`PARAM_TYPES`)."""
    if params is None:
        return {}
    if not isinstance(params, list):
        errors.append(f"{where} must be a list")
        return {}
    names: dict[str, str] = {}
    for index, param in enumerate(params):
        if not isinstance(param, dict) or not isinstance(param.get("name"), str):
            errors.append(f"{where}[{index}] must be a mapping with a 'name'")
            continue
        if param.get("type") not in PARAM_TYPES:
            errors.append(
                f"{where}[{index}].type must be one of {list(PARAM_TYPES)}, "
                f"got {param.get('type')!r}"
            )
        names[param["name"]] = param.get("type")
    return names


def _check_status(status: Any, where: str, errors: list[str]) -> None:
    if status not in PLAN_STATUSES:
        errors.append(f"{where}.status must be one of {list(PLAN_STATUSES)}, got {status!r}")


def _check_command(
    command: Any, where: str, params_by_generator: dict[str, dict[str, str]], errors: list[str]
) -> None:
    """Every part of the chain calls a known generator with only its parameters and with
    all of them except `bool` flags: the generators read every parameter with `opt<T>`,
    and testlib fails on a missing one (a missing flag `opt<bool>` reads as `false`)."""
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
        given: set[str] = set()
        for token in params:
            match = _COMMAND_PARAM_RE.match(token)
            if not match:
                errors.append(
                    f"{where}: parameter {token!r} of '{name}' is not -name=value or --name"
                )
            elif match.group(1) not in params_by_generator[name]:
                errors.append(f"{where}: '{name}' has no parameter '{match.group(1)}'")
            else:
                given.add(match.group(1))
        missing = [
            param
            for param, param_type in params_by_generator[name].items()
            if param_type != "bool" and param not in given
        ]
        if missing:
            errors.append(
                f"{where}: '{name}' is called without its parameter(s) "
                + ", ".join(f"'{param}'" for param in missing)
            )


def check_plan_extends(old: dict[str, Any], new: dict[str, Any]) -> None:
    """`new` only adds to `old`: every generator, named value and test block of `old` is
    in `new` unchanged, the test blocks in the same order. `TestsPlanError` — not so."""
    errors: list[str] = []
    for key, what in (("generators", "generator"), ("constants", "named value")):
        new_entries = {e.get("name"): e for e in new.get(key) or [] if isinstance(e, dict)}
        for entry in old.get(key) or []:
            name = entry.get("name") if isinstance(entry, dict) else None
            if new_entries.get(name) != entry:
                errors.append(f"{what} '{name}' is removed or changed")
    new_tests = list(new.get("tests") or [])
    position = 0
    for index, block in enumerate(old.get("tests") or []):
        try:
            position = new_tests.index(block, position) + 1
        except ValueError:
            errors.append(f"tests[{index}] ({block.get('description', '?')}) is removed or changed")
    if errors:
        raise TestsPlanError(
            f"{TESTS_PLAN_ARTIFACT} must keep the current plan and only add to it: "
            + "; ".join(errors)
        )


def block_groups(block: dict[str, Any]) -> list[str]:
    """The groups of a plan's test block: `group` — a name or a list of names."""
    group = block.get("group")
    if isinstance(group, list):
        return [str(name) for name in group]
    return [str(group)] if group is not None else []


def each_test_counts(
    blocks: list[dict[str, Any]], groups: list[TestGroupHint] | None
) -> dict[str, int | None]:
    """Group name -> the number of tests `blocks` put into it, for the `each_test` groups
    (`points_policy`); `None` — a block of the group is a loop (`repeat`): its number of
    tests isn't known before the script is expanded."""
    counts: dict[str, int | None] = {
        group.name: 0 for group in groups or [] if group.points_policy == EACH_TEST
    }
    for block in blocks:
        for name in block_groups(block):
            if name not in counts or counts[name] is None:
                continue
            if block.get("repeat"):
                counts[name] = None
            else:
                counts[name] += len(block.get("commands") or [])
    return counts


def each_test_errors(plan: dict[str, Any], groups: list[TestGroupHint] | None) -> list[str]:
    """Why the plan's tests don't fit its `each_test` groups: a number of tests that doesn't
    divide the group's points (`test_groups.each_test_count_error`). A group with a loop
    (`repeat`) isn't checked here: its tests are counted once the script is expanded
    (`test_script_draft`, `run_tests`)."""
    from orchestrator.test_groups import each_test_count_error

    points = {group.name: group.points or 0 for group in groups or []}
    errors = []
    for name, count in each_test_counts(list(plan.get("tests") or []), groups).items():
        if count is None:
            continue
        error = each_test_count_error(name, points[name], count)
        if error:
            errors.append(error)
    return errors


def plan_generators(plan: dict[str, Any]) -> list[dict[str, Any]]:
    return list(plan.get("generators") or [])


def dump_plan_part(value: Any) -> str:
    """A part of the plan as YAML text — for the prompts and the hash input of the next steps."""
    return yaml.safe_dump(value, allow_unicode=True, sort_keys=False, width=100)


def uncovered_notes(plan: dict[str, Any], spec: ProblemSpec) -> list[dict[str, Any]]:
    """`uncovered` notes: the spec's `specific_test_ideas` items that no test
    of the plan refers to (`tests[].covers`), and references to non-existent items — a mechanical
    reconciliation the author would otherwise have to do by eye; and also
    generators no test calls."""
    sizes = {
        "specific_test_ideas": len(spec.generation.specific_test_ideas or []),
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
    """From the `solutions` section — only `algorithm_hint`: the idea of the author
    solution, from which the model may propose its own edge cases (`kind: suggestion`).
    The section is optional: without it — `None`, the step runs anyway. The other
    fields aren't taken: an edit of `known_wrong_approaches` or `languages` mustn't
    invalidate the base plan.
    """
    return {"algorithm_hint": spec.solutions.algorithm_hint if spec.solutions else None}


def _build_context_sections(spec: ProblemSpec) -> dict[str, Any]:
    """The spec sections for rendering the prompt AND for the cache hash input — a single
    assembly point (CLAUDE.md, "Caching by spec hash"): the `generation` section,
    `solutions.algorithm_hint`, the input format from
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


# The errors of the last local tests (`run_tests`) that may come from this step's files:
# while there are some, the step reruns to revise its files with them (`tests_check`).
# `tests_check` is imported in the functions: through `local_tests` it imports the test steps.
INPUTS_KEY = f"{STEP_NAME}: run_tests report"


def load_inputs(
    problem_id: str, spec: ProblemSpec, *, problems_dir: Path = PROBLEMS_DIR
) -> dict[str, Any] | None:
    from orchestrator.steps.tests_check import TESTS_GATE

    return TESTS_GATE.failed_report(problem_id, STEP_NAME, problems_dir=problems_dir)


def forced_rerun_reason(
    problem_id: str, spec: ProblemSpec, upstream_artifacts: dict[str, Any] | None = None
) -> str | None:
    from orchestrator.steps.tests_check import TESTS_GATE

    return TESTS_GATE.revision_reason(_tests_run(upstream_artifacts))


def _tests_run(upstream_artifacts: dict[str, Any] | None) -> dict[str, Any] | None:
    return (upstream_artifacts or {}).get(INPUTS_KEY)


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


def design_artifact_path(
    problem_id: str, spec: ProblemSpec, *, problems_dir: Path = PROBLEMS_DIR
) -> Path:
    return authoring_path(Path(problems_dir) / problem_id, TESTS_DESIGN_ARTIFACT)


def current_artifacts(
    problem_id: str, spec: ProblemSpec, *, problems_dir: Path = PROBLEMS_DIR
) -> dict[str, Path]:
    """The step's current files for revision mode: `tests_design.yaml` — what the model
    writes; the plan is built from it."""
    return existing_artifacts(
        {TESTS_DESIGN_ARTIFACT: design_artifact_path(problem_id, spec, problems_dir=problems_dir)}
    )


def _current_plan_text(plan_path: Path) -> str | None:
    return plan_path.read_text(encoding="utf-8") if plan_path.is_file() else None


def _each_test_targets(constraints_yaml: str, plan_text: str | None) -> list[dict[str, Any]]:
    """The `each_test` groups for the prompt (`tests_design.each_test_targets`); limits
    that can't be read — none (the design's check reports them)."""
    from orchestrator.steps.tests_design import TestsDesignError, each_test_targets, parse_limits

    try:
        limits = parse_limits(constraints_yaml)
        plan = yaml.safe_load(plan_text) if plan_text else None
    except (TestsDesignError, yaml.YAMLError):
        return []
    return each_test_targets(limits, plan if isinstance(plan, dict) else None)


def _derive(
    spec: ProblemSpec, constraints_yaml: str, plan_path: Path
) -> Callable[[dict[str, str]], tuple[dict[str, str], list[dict[str, Any]]]]:
    """`tests_plan.yaml` from the design (`tests_design.plan_from_design`, the current
    plan — for its blocks for wrong solutions) and notes about what the plan doesn't cover
    (`uncovered_notes`)."""
    # Imported here: `tests_design` takes the plan's checks from this module.
    from orchestrator.steps.tests_design import plan_from_design

    def derive(artifacts: dict[str, str]) -> tuple[dict[str, str], list[dict[str, Any]]]:
        text = plan_from_design(
            artifacts[TESTS_DESIGN_ARTIFACT],
            constraints_yaml,
            spec.problem_id,
            _current_plan_text(plan_path),
        )
        return {TESTS_PLAN_ARTIFACT: text}, uncovered_notes(parse_tests_plan(text), spec)

    return derive


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
    style), `solutions.algorithm_hint`, the input
    format and the legend from `statement_draft`, `constraints.yaml` without TL/ML —
    the only source of ranges and groups. Output — `authoring/tests_design.yaml` from
    the model and `authoring/tests_plan.yaml` built from it; a design that doesn't parse
    or doesn't give a valid plan (`tests_design.plan_from_design`) isn't written: the
    response goes to conflicts and stays for `llm apply`.
    """
    # Imported here: `tests_design` takes the plan's checks from this module.
    from orchestrator.steps.tests_design import plan_from_design

    constraints_yaml = require_constraints_yaml(STEP_NAME, problem_id, upstream_artifacts)
    plan_path = primary_artifact_path(problem_id, spec, problems_dir=problems_dir)
    plan_constraints = constraints_without_limits(constraints_yaml)
    context = {
        "problem_id": spec.problem_id,
        **_build_context_sections(spec),
        "constraints_yaml": plan_constraints,
        "tests_run": _tests_run(upstream_artifacts),
        "each_test_groups": _each_test_targets(plan_constraints, _current_plan_text(plan_path)),
    }
    input_hash = compute_input_hash(problem_id, spec, upstream_artifacts)
    input_values = compute_input_values(*hash_inputs(problem_id, spec, upstream_artifacts))

    def resolve_artifact_path(filename: str) -> Path:
        if filename == TESTS_PLAN_ARTIFACT:
            return plan_path
        if filename != TESTS_DESIGN_ARTIFACT:
            raise ValueError(
                f"Step '{STEP_NAME}' returns only {TESTS_DESIGN_ARTIFACT}, got '{filename}'"
            )
        return design_artifact_path(problem_id, spec, problems_dir=problems_dir)

    def validate_artifacts(artifacts: dict[str, str]) -> None:
        check_artifact_names(STEP_NAME, {TESTS_DESIGN_ARTIFACT}, artifacts)
        plan_from_design(
            artifacts[TESTS_DESIGN_ARTIFACT],
            plan_constraints,
            spec.problem_id,
            _current_plan_text(plan_path),
        )

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
        postprocess_artifacts=set_spec_problem_id(spec.problem_id, TESTS_DESIGN_ARTIFACT),
        derive_artifacts=_derive(spec, plan_constraints, plan_path),
    )
