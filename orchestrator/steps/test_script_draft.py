"""Step 3, the third part — the FreeMarker test-script from the test plan, CLAUDE.md, "Role of each
generative step", item 3.

The mechanical part: the plan's `tests` blocks (`authoring/tests_plan.yaml`, the
`tests_plan` step) with its `constants` — into `test_script` (`generation.script_style:
flat`) or `test_script_groups` (`groups`). The step sees the generators only by the
plan — the name and parameters, without code: the script calls them the way the plan described them,
and editing a generator's code doesn't invalidate the script.
"""

from __future__ import annotations

import re
from collections.abc import Callable
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
    existing_artifacts,
    require_constraints_yaml,
    run_generative_step,
)
from orchestrator.steps.tests_plan import (
    TESTS_PLAN_ARTIFACT,
    dump_plan_part,
    load_tests_plan,
    plan_generators,
)

STEP_NAME = "test_script_draft"

# The test-script's standard functions (div, pow, fact, ...) — the author's template in
# templates/. The model must copy the block in full (prompts/
# test_script_draft/system.md), but in practice it drops unused
# functions, and the author needs them at hand when editing the script by hand —
# so the dropped ones are appended mechanically (`restore_script_functions`).
SCRIPT_FUNCTIONS_FILENAME = "test_script_functions.ftl"
SCRIPT_NAMES = ("test_script", "test_script_groups")
_SCRIPT_SECTION_MARKER = '<#assign desc = "{}"/>'
_FUNCTION_RE = re.compile(r"<#function\s+(\w+)\b.*?</#function>", re.S)


def _script_functions(script: str) -> dict[str, str]:
    """Function name -> its definition `<#function ...>...</#function>`."""
    return {m.group(1): m.group(0) for m in _FUNCTION_RE.finditer(script)}


def restore_script_functions(script: str, functions_block: str) -> tuple[str, list[str]]:
    """Appends to `script` the functions from `functions_block` that it lacks
    (by name; a function the model rewrote isn't touched). Where:
    - after the section marker `<#assign desc = "Functions"/>`, if there is one;
    - otherwise before the script's first function, if there is one;
    - otherwise the whole block with the marker — before the "Constraints" section, and without it —
      at the start of the script (function definitions in FreeMarker are visible in the whole
      template, so the order doesn't affect how the script works).
    Returns the new script and the names of the appended functions (in the block's order).
    """
    present = _script_functions(script)
    missing = [
        (name, body)
        for name, body in _script_functions(functions_block).items()
        if name not in present
    ]
    if not missing:
        return script, []
    names = [name for name, _ in missing]
    bodies = "\n".join(body for _, body in missing) + "\n"

    functions_marker = _SCRIPT_SECTION_MARKER.format("Functions")
    marker_at = script.find(functions_marker)
    if marker_at != -1:
        insert_at = marker_at + len(functions_marker)
        return script[:insert_at] + "\n" + bodies.rstrip("\n") + script[insert_at:], names
    first_function = _FUNCTION_RE.search(script)
    if first_function:
        insert_at = first_function.start()
        return script[:insert_at] + bodies + script[insert_at:], names
    block = functions_marker + "\n" + bodies + "\n"
    constraints_at = script.find(_SCRIPT_SECTION_MARKER.format("Constraints"))
    insert_at = 0 if constraints_at == -1 else constraints_at
    return script[:insert_at] + block + script[insert_at:], names


# A FreeMarker directive taking the tail of a line: `<#if value >= n>`. Inside
# a tag `>` closes the tag (`<#if value>` + the text `= n>`), so comparisons
# are written as words (`gt`/`gte`/`lt`/`lte`, templates/tutorials/freemarker.md),
# but the model writes signs from time to time. The bounds of the expression are taken by the end of
# the line, not by the first `>` — otherwise it can't be restored.
_DIRECTIVE_LINE_RE = re.compile(
    r"(<#(?:if|elseif|assign|local|global|return)\s)((?:(?!<[#/@]).)*?)(\s*/?>[ \t]*)$", re.M
)
_STRING_RE = re.compile(r"\"(?:[^\"\\]|\\.)*\"|'(?:[^'\\]|\\.)*'")
# `->` (a lambda) and `..<` (a range) aren't comparisons.
_COMPARISON_RE = re.compile(r"\s*(?<![-.])(>=|<=|>|<)\s*")
_COMPARISON_WORDS = {">=": "gte", "<=": "lte", ">": "gt", "<": "lt"}


def _replace_comparisons(expression: str) -> tuple[str, int]:
    parts: list[str] = []
    count = 0
    position = 0
    for string in [*_STRING_RE.finditer(expression), None]:
        end = string.start() if string else len(expression)
        code, replaced = _COMPARISON_RE.subn(
            lambda m: f" {_COMPARISON_WORDS[m.group(1)]} ", expression[position:end]
        )
        parts.append(code)
        count += replaced
        if string:
            parts.append(string.group(0))
            position = string.end()
    return "".join(parts), count


def fix_script_comparisons(script: str) -> tuple[str, int]:
    """Replaces comparison signs in the script's directives with `gt`/`gte`/`lt`/`lte`
    (see `_DIRECTIVE_LINE_RE`). Only directives that end at the
    end of their line; string literals aren't touched. Returns the new
    script and the number of replacements."""
    total = 0

    def replace(match: re.Match[str]) -> str:
        nonlocal total
        expression, count = _replace_comparisons(match.group(2))
        total += count
        return match.group(1) + expression + match.group(3)

    return _DIRECTIVE_LINE_RE.sub(replace, script), total


def _restore_functions_in_artifacts(
    functions_block: str,
) -> Callable[[dict[str, str]], tuple[dict[str, str], list[dict[str, Any]]]]:
    """`postprocess_artifacts` for `run_generative_step`: runs each
    test-script of the response through `fix_script_comparisons` and
    `restore_script_functions` and leaves a note on each where something was
    fixed or appended."""

    def postprocess(artifacts: dict[str, str]) -> tuple[dict[str, str], list[dict[str, Any]]]:
        result = dict(artifacts)
        notes: list[dict[str, Any]] = []
        for name in SCRIPT_NAMES:
            if name not in result:
                continue
            result[name], fixed = fix_script_comparisons(result[name])
            if fixed:
                notes.append(
                    {
                        "field": name,
                        "kind": "restored",
                        "explanation": (
                            f"comparison signs inside FreeMarker directives ({fixed}) replaced "
                            "by the orchestrator with gt/gte/lt/lte: `>` inside a tag closes the tag"
                        ),
                    }
                )
            result[name], restored = restore_script_functions(result[name], functions_block)
            if restored:
                notes.append(
                    {
                        "field": name,
                        "kind": "restored",
                        "explanation": (
                            f"the model didn't copy the standard functions {', '.join(restored)} "
                            f"from {SCRIPT_FUNCTIONS_FILENAME} — appended by the orchestrator"
                        ),
                    }
                )
        return result, notes

    return postprocess


def _generator_signatures(plan: dict[str, Any]) -> list[dict[str, Any]]:
    """The plan's generators without `algorithm`/`rationale`: the script only needs
    to know how to call them."""
    keys = ("name", "params", "input", "output")
    return [{key: generator.get(key) for key in keys} for generator in plan_generators(plan)]


def script_name(spec: ProblemSpec) -> str:
    return "test_script" if spec.generation.script_style == "flat" else "test_script_groups"


def hash_inputs(
    problem_id: str,
    spec: ProblemSpec,
    upstream_artifacts: dict[str, Any] | None = None,
) -> tuple[Any, dict[str, Any] | None]:
    """`(spec section, upstream artifacts)`: `generation.script_style`;
    `constraints.yaml` without TL/ML (the script's limits and groups), from the plan —
    `constants`, `tests` and the generator signatures (`_generator_signatures`)."""
    constraints_yaml = require_constraints_yaml(STEP_NAME, problem_id, upstream_artifacts)
    plan = load_tests_plan(STEP_NAME, problem_id, upstream_artifacts)
    return {"generation": {"script_style": spec.generation.script_style}}, {
        CONSTRAINTS_ARTIFACT: constraints_without_limits(constraints_yaml),
        f"{TESTS_PLAN_ARTIFACT}: generators": dump_plan_part(_generator_signatures(plan)),
        f"{TESTS_PLAN_ARTIFACT}: constants": dump_plan_part(plan.get("constants") or []),
        f"{TESTS_PLAN_ARTIFACT}: tests": dump_plan_part(plan.get("tests")),
    }


def compute_input_hash(
    problem_id: str,
    spec: ProblemSpec,
    upstream_artifacts: dict[str, Any] | None = None,
) -> str:
    return _cache_compute_input_hash(STEP_NAME, *hash_inputs(problem_id, spec, upstream_artifacts))


def extra_context_documents(
    spec: ProblemSpec, *, templates_dir: Path = TEMPLATES_DIR
) -> list[str] | None:
    """Samples — test-scripts from the spec's references (`orchestrator/references.py`)."""
    return reference_documents(STEP_NAME, spec)


def primary_artifact_path(
    problem_id: str, spec: ProblemSpec, *, problems_dir: Path = PROBLEMS_DIR
) -> Path:
    """The path to the test-script: the name depends on `generation.script_style`
    (CLAUDE.md, "Directory structure")."""
    return Path(problems_dir) / problem_id / script_name(spec)


def current_artifacts(
    problem_id: str, spec: ProblemSpec, *, problems_dir: Path = PROBLEMS_DIR
) -> dict[str, Path]:
    """The step's current files for revision mode: both test-scripts, whichever exist."""
    problem_dir = Path(problems_dir) / problem_id
    return existing_artifacts({name: problem_dir / name for name in SCRIPT_NAMES})


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
    """Runs the `test_script_draft` step for `problem_id`.

    Input: `constants`, `tests` and the signatures of the plan's generators,
    `constraints.yaml` without TL/ML — the only source of ranges and groups,
    `generation.script_style`. Output — `test_script` or `test_script_groups`
    in the problem directory; comparison signs in directives and standard functions
    dropped by the model are fixed by the orchestrator (`_restore_functions_in_artifacts`).
    """
    constraints_yaml = require_constraints_yaml(STEP_NAME, problem_id, upstream_artifacts)
    plan = load_tests_plan(STEP_NAME, problem_id, upstream_artifacts)
    name = script_name(spec)
    context = {
        "problem_id": spec.problem_id,
        "script_name": name,
        "script_style": spec.generation.script_style,
        "constraints_yaml": constraints_without_limits(constraints_yaml),
        "generators": dump_plan_part(_generator_signatures(plan)),
        "constants": dump_plan_part(plan.get("constants") or []),
        "tests": dump_plan_part(plan.get("tests")),
    }
    input_hash = compute_input_hash(problem_id, spec, upstream_artifacts)
    input_values = compute_input_values(*hash_inputs(problem_id, spec, upstream_artifacts))

    def resolve_artifact_path(filename: str) -> Path:
        filename = artifact_filename(filename)
        if filename not in SCRIPT_NAMES:
            raise ValueError(
                f"Step '{STEP_NAME}' returns only {' or '.join(SCRIPT_NAMES)}, got '{filename}'"
            )
        return Path(problems_dir) / problem_id / filename

    def validate_artifacts(artifacts: dict[str, str]) -> None:
        if name not in artifacts:
            raise MissingArtifactsError(f"Step '{STEP_NAME}': the response has no {name}", [name])

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
        # The files are independent: an extraneous file in the response doesn't interfere with the script.
        partial_apply=True,
        prompts_dir=prompts_dir,
        problems_dir=problems_dir,
        templates_dir=templates_dir,
        extra_context_documents=extra_context_documents(spec, templates_dir=templates_dir),
        postprocess_artifacts=_restore_functions_in_artifacts(
            (Path(templates_dir) / SCRIPT_FUNCTIONS_FILENAME).read_text(encoding="utf-8")
        ),
    )
