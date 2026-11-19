"""The test design (`authoring/tests_design.yaml`, `templates/tests_design_format.yaml`) and
the test plan built from it, CLAUDE.md, "Role of each generative step", item 3.

The `tests_plan` step asks the model for the design, not for the plan: the generators and,
for each test idea, the commands as templates whose values are formulas over a test
group's limits (`{n.max}`, `{n.lo}`, `{n.max / 2}`) and the groups it goes into. Picking
the numbers of every group and checking them against its limits is what made one plan
call heavy; here the orchestrator does it mechanically (`build_tests_plan`): a block per
idea entry and group, every formula — a named constant of the plan (`n_max_g3`), so the
script keeps referring to values by name. The plan's format and the steps after it
(`generators_draft`, `test_script_draft`, `tests_survivors`) don't change.

Blocks of the current plan for wrong solutions (`tests_survivors`: `covers` refers to a
solution or to `known_wrong_approaches`) aren't part of the design: rebuilding the plan
carries them over together with the generators and the named values they use.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass
from typing import Any

import yaml

from orchestrator.problem_paths import TESTS_DESIGN_FILENAME, TESTS_PLAN_FILENAME
from orchestrator.spec import TestGroupHint
from orchestrator.steps.tests_plan import (
    GENERATOR_NAME_RE,
    TestsPlanError,
    _check_command,
    _check_params,
    _check_status,
    dump_plan_part,
    parse_tests_plan,
)
from orchestrator.test_groups import EXAMPLES_GROUP, TestGroupsError, parse_test_groups

ALL_GROUPS = "all"
_IDEA_ID_RE = re.compile(r"^(?:specific_test_ideas|own)\[\d+\]$")
_COVERED_ID_RE = re.compile(r"^specific_test_ideas\[\d+\]$")
# A block of the current plan made for a wrong solution (`tests_survivors`).
_WRONG_SOLUTION_COVERS_RE = re.compile(r"^(?:known_wrong_approaches\[\d+\]|solutions/.+)$")
_FORMULA_RE = re.compile(r"\{([^{}]*)\}")
_REF_RE = re.compile(r"([A-Za-z_][A-Za-z0-9_]*)\.(min|max|lo)\b")
_NAME_TOKEN_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_OPERATOR_NAMES = {"+": "plus", "-": "minus", "*": "times", "/": "div"}

PLAN_HEADER = (
    f"# Built by the orchestrator from {TESTS_DESIGN_FILENAME} (the tests_plan step): edit\n"
    f"# {TESTS_DESIGN_FILENAME}, this file is rebuilt from it on the next run of the step.\n"
    "# Blocks for wrong solutions (tests_survivors) are kept as they are.\n"
)


class TestsDesignError(ValueError):
    """`tests_design.yaml` doesn't parse, violates the format or doesn't fit the limits."""

    __test__ = False  # not a pytest test class


@dataclass(frozen=True)
class _Limits:
    """What the formulas are computed from: the variables of `constraints.yaml` (name ->
    its `min`/`max` as written there) and its test groups (`None` — the problem has none)."""

    variables: dict[str, dict[str, Any]]
    groups: list[TestGroupHint] | None


def parse_limits(constraints_yaml: str) -> _Limits:
    """The variables and test groups of `constraints.yaml` for the formulas."""
    try:
        data = yaml.safe_load(constraints_yaml) or {}
        groups = parse_test_groups(constraints_yaml)
    except (yaml.YAMLError, TestGroupsError) as exc:
        raise TestsDesignError(f"constraints.yaml can't be read: {exc}") from exc
    variables: dict[str, dict[str, Any]] = {}
    raw_variables = data.get("variables") if isinstance(data, dict) else None
    for variable in raw_variables or []:
        if isinstance(variable, dict) and isinstance(variable.get("name"), str):
            variables[variable["name"]] = variable
    return _Limits(variables=variables, groups=groups)


def parse_tests_design(text: str, limits: _Limits) -> dict[str, Any]:
    """Parses and checks `tests_design.yaml`: the generators (as in the plan), the
    named values, the ideas — known ids, groups of `constraints.yaml` (or none without
    groups), entries of one idea on disjoint groups, commands of the design's generators
    with their parameters only, formulas over known variables. Returns the document as is;
    an error — `TestsDesignError`."""
    try:
        design = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise TestsDesignError(f"{TESTS_DESIGN_FILENAME} is not valid YAML: {exc}") from exc
    if not isinstance(design, dict):
        raise TestsDesignError(f"{TESTS_DESIGN_FILENAME}: expected a mapping at the top level")

    errors: list[str] = []
    params_by_generator = _check_generators(design.get("generators"), errors)
    constants = design.get("constants") or []
    if not isinstance(constants, list) or not all(
        isinstance(c, dict) and isinstance(c.get("name"), str) for c in constants
    ):
        errors.append("'constants' must be a list of mappings with a 'name'")

    ideas = design.get("ideas")
    if not isinstance(ideas, list) or not ideas:
        errors.append("'ideas' must be a non-empty list")
        ideas = []
    groups_by_id: dict[str, list[tuple[int, set[str]]]] = {}
    for index, idea in enumerate(ideas):
        where = f"ideas[{index}]"
        if not isinstance(idea, dict):
            errors.append(f"{where} must be a mapping")
            continue
        idea_id = idea.get("id")
        if not isinstance(idea_id, str) or not _IDEA_ID_RE.match(idea_id.strip()):
            errors.append(
                f"{where}.id must be specific_test_ideas[<number>] or own[<number>], "
                f"got {idea_id!r}"
            )
            idea_id = None
        _check_status(idea.get("status"), where, errors)
        if not isinstance(idea.get("what"), str) or not idea["what"].strip():
            errors.append(f"{where}.what must be a non-empty string")
        groups = _idea_groups(idea, limits, where, errors)
        if idea_id is not None and groups is not None:
            groups_by_id.setdefault(idea_id.strip(), []).append((index, set(groups)))
        tests = idea.get("tests")
        if not isinstance(tests, list) or not tests:
            errors.append(f"{where}.tests must be a non-empty list")
            continue
        for number, template in enumerate(tests):
            _check_template(
                template, f"{where}.tests[{number}]", limits, params_by_generator, errors
            )
        repeat = idea.get("repeat")
        if repeat is not None:
            if not isinstance(repeat, str):
                errors.append(f"{where}.repeat must be a string or null")
            else:
                _check_formulas(repeat, f"{where}.repeat", limits, errors)

    for idea_id, entries in groups_by_id.items():
        for (first, first_groups), (second, second_groups) in _pairs(entries):
            common = first_groups & second_groups
            if limits.groups is None:
                errors.append(f"ideas[{first}] and ideas[{second}]: '{idea_id}' is listed twice")
            elif common:
                errors.append(
                    f"ideas[{first}] and ideas[{second}]: '{idea_id}' is in group(s) "
                    f"{sorted(common)} twice — entries of one idea take disjoint groups"
                )

    if errors:
        raise TestsDesignError(f"{TESTS_DESIGN_FILENAME}: " + "; ".join(errors))
    return design


def _pairs(items: list[Any]) -> list[tuple[Any, Any]]:
    return [(a, b) for i, a in enumerate(items) for b in items[i + 1 :]]


def _check_generators(generators: Any, errors: list[str]) -> dict[str, set[str]]:
    if not isinstance(generators, list) or not generators:
        errors.append("'generators' must be a non-empty list")
        return {}
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
    return params_by_generator


def _group_names(limits: _Limits) -> list[str]:
    return [group.name for group in limits.groups or []]


def _idea_groups(
    idea: dict[str, Any], limits: _Limits, where: str, errors: list[str]
) -> list[str] | None:
    """The groups of an idea entry in the order of `constraints.yaml`; `[]` — the problem
    has no groups; `None` — the field is invalid (reported to `errors`)."""
    value = idea.get("groups")
    if limits.groups is None:
        if value is not None:
            errors.append(f"{where}.groups: constraints.yaml has no test_groups — leave it out")
            return None
        return []
    names = _group_names(limits)
    if value == ALL_GROUPS:
        return names
    if not isinstance(value, list) or not value:
        errors.append(f"{where}.groups must be '{ALL_GROUPS}' or a non-empty list of group names")
        return None
    wanted = [str(name) for name in value]
    unknown = [name for name in wanted if name not in names]
    if EXAMPLES_GROUP in unknown:
        errors.append(f"{where}.groups: group {EXAMPLES_GROUP} is the statement examples")
    elif unknown:
        errors.append(f"{where}.groups: no such group(s) in constraints.yaml: {unknown}")
    if len(set(wanted)) != len(wanted):
        errors.append(f"{where}.groups lists a group twice")
    if unknown:
        return None
    return [name for name in names if name in wanted]


def _check_template(
    template: Any,
    where: str,
    limits: _Limits,
    params_by_generator: dict[str, set[str]],
    errors: list[str],
) -> None:
    if not isinstance(template, str) or not template.strip():
        errors.append(f"{where} must be a non-empty string")
        return
    if not _check_formulas(template, where, limits, errors):
        return
    # Formulas are single words in the command: the generator and parameter names are
    # checked on the command with each formula replaced by a placeholder.
    _check_command(_FORMULA_RE.sub("0", template), where, params_by_generator, errors)


def _check_formulas(text: str, where: str, limits: _Limits, errors: list[str]) -> bool:
    ok = True
    if text.count("{") != text.count("}"):
        errors.append(f"{where}: unbalanced braces in {text!r}")
        return False
    for formula in _FORMULA_RE.findall(text):
        try:
            _parse_formula(formula, limits)
        except TestsDesignError as exc:
            errors.append(f"{where}: {exc}")
            ok = False
    return ok


def _parse_formula(formula: str, limits: _Limits) -> list[tuple[str, str]]:
    """The references `(variable, min|max|lo)` of a formula; a formula that isn't integer
    arithmetic (`+ - * /`, brackets) over references and numbers — `TestsDesignError`."""
    refs: list[tuple[str, str]] = []

    def placeholder(match: re.Match[str]) -> str:
        refs.append((match.group(1), match.group(2)))
        return f"_r{len(refs) - 1}"

    expression = _REF_RE.sub(placeholder, formula)
    try:
        tree = ast.parse(expression.strip(), mode="eval")
    except SyntaxError:
        tree = None
    allowed = (ast.Expression, ast.BinOp, ast.UnaryOp, ast.Add, ast.Sub, ast.Mult, ast.Div)
    allowed += (ast.USub, ast.Name, ast.Load, ast.Constant)
    if tree is None or not all(
        isinstance(node, allowed)
        and (not isinstance(node, ast.Constant) or type(node.value) is int)
        and (not isinstance(node, ast.Name) or re.fullmatch(r"_r\d+", node.id))
        for node in ast.walk(tree)
    ):
        raise TestsDesignError(
            f"formula {{{formula}}} must be integer arithmetic (+ - * /, brackets) over "
            "<variable>.min, <variable>.max, <variable>.lo and numbers"
        )
    for variable, _ in refs:
        if variable not in limits.variables:
            raise TestsDesignError(
                f"formula {{{formula}}}: no variable '{variable}' in constraints.yaml "
                f"(known: {sorted(limits.variables)})"
            )
    return refs


# --- building the plan ---------------------------------------------------------------


class _Constants:
    """The named values the formulas turn into, in the order of first use: one per name and
    value — a name already taken by another value gets a suffix."""

    def __init__(self, taken: set[str]) -> None:
        self.entries: list[dict[str, Any]] = []
        self._names: dict[tuple[str, str], str] = {}
        self._taken = set(taken)

    def name_for(self, name: str, value: str, description: str) -> str:
        if name[:1].isdigit():
            name = f"c_{name}"
        if (name, value) in self._names:
            return self._names[(name, value)]
        unique, number = name, 2
        while unique in self._taken:
            unique, number = f"{name}_{number}", number + 1
        self._taken.add(unique)
        self._names[(name, value)] = unique
        self.entries.append({"name": unique, "value": value, "description": description})
        return unique


def _sanitize(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9_]", "_", name)


def _closure(group: TestGroupHint, by_name: dict[str, TestGroupHint]) -> list[str]:
    """The groups `group` depends on, directly or through other groups."""
    seen: list[str] = []
    stack = list(group.dependencies or [])
    while stack:
        name = stack.pop()
        if name in seen or name not in by_name:
            continue
        seen.append(name)
        stack.extend(by_name[name].dependencies or [])
    return seen


class _Resolver:
    """Turns the references of a formula into the names of the plan's constants for one group
    (`None` — the problem has no groups)."""

    def __init__(self, limits: _Limits, constants: _Constants) -> None:
        self.limits = limits
        self.constants = constants
        self.by_name = {group.name: group for group in limits.groups or []}

    def _bound(self, variable: str, kind: str, group: str | None) -> Any:
        """The variable's `min`/`max` in the group: its own, or the full one."""
        if group is not None:
            own = (self.by_name[group].variables or {}).get(variable)
            value = getattr(own, kind) if own is not None else None
            if value is not None:
                return value
        return self.limits.variables[variable].get(kind)

    def _bound_name(self, variable: str, kind: str, group: str | None) -> str:
        value = self._bound(variable, kind, group)
        if value is None:
            raise TestsDesignError(f"{variable}.{kind} isn't set in constraints.yaml")
        full = self.limits.variables[variable].get(kind)
        if group is None or value == full:
            return self.constants.name_for(
                f"{_sanitize(variable)}_{kind}",
                str(value),
                f"{variable}.{kind} from the variables of constraints.yaml",
            )
        return self.constants.name_for(
            f"{_sanitize(variable)}_{kind}_g{_sanitize(group)}",
            str(value),
            f"{variable}.{kind} of test group {group} in constraints.yaml",
        )

    def _lo_name(self, variable: str, group: str | None) -> str:
        """The first value of the group's own range of the variable: one more than the
        largest value of the groups it depends on; without such groups — its `min`."""
        dependencies = _closure(self.by_name[group], self.by_name) if group else []
        if not dependencies:
            return self._bound_name(variable, "min", group)
        maxima = {name: self._bound(variable, "max", name) for name in dependencies}
        if not all(isinstance(value, int) for value in maxima.values()):
            raise TestsDesignError(
                f"{variable}.lo of group {group}: the maxima of {variable} in the groups it "
                f"depends on aren't all numbers ({maxima}) — write the value explicitly"
            )
        lo = max(maxima.values()) + 1
        own_max = self._bound(variable, "max", group)
        if isinstance(own_max, int) and lo > own_max:
            raise TestsDesignError(
                f"{variable}.lo of group {group}: the groups it depends on already take "
                f"{variable} up to its max {own_max} — the group's own range is in another "
                "variable or its special_property; use another formula for this group"
            )
        own_min = self._bound(variable, "min", group)
        if isinstance(own_min, int) and lo <= own_min:
            return self._bound_name(variable, "min", group)
        source = max(maxima, key=lambda name: maxima[name])
        return self.constants.name_for(
            f"{_sanitize(variable)}_lo_g{_sanitize(group)}",
            str(lo),
            f"{variable}.max of test group {source} (one of the groups group {group} "
            "depends on) + 1: the first value of the group's own range",
        )

    def resolve(self, formula: str, group: str | None) -> str:
        """The formula's value in a command: a constant's name or a number."""
        refs = _parse_formula(formula, self.limits)
        names = [
            self._lo_name(variable, group)
            if kind == "lo"
            else self._bound_name(variable, kind, group)
            for variable, kind in refs
        ]
        expression = formula.strip()
        if not refs and re.fullmatch(r"\d+", expression):
            return expression
        if len(refs) == 1 and _REF_RE.fullmatch(expression):
            return names[0]
        iterator = iter(names)
        value = re.sub(r"\s+", " ", _REF_RE.sub(lambda _: next(iterator), expression))
        value = re.sub(r"\s*([-+*/()])\s*", r" \1 ", value).replace("( ", "(").replace(" )", ")")
        value = re.sub(r"\s+", " ", value).strip()
        name_parts = [
            _OPERATOR_NAMES.get(token, token)
            for token in re.findall(r"[A-Za-z_][A-Za-z0-9_]*|\d+|[-+*/]", value)
        ]
        description = f"{formula.strip()}"
        if group is not None:
            description += f" for test group {group}"
        if "/" in value:
            description += " (integer division)"
        return self.constants.name_for("_".join(name_parts), value, description)


def _substitute(text: str, resolver: _Resolver, group: str | None) -> str:
    return _FORMULA_RE.sub(lambda match: resolver.resolve(match.group(1), group), text)


def wrong_solution_blocks(
    current_plan: dict[str, Any] | None,
) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    """The current plan's blocks for wrong solutions, with its generators and named values
    (by name) — what rebuilding the plan keeps."""
    if not current_plan:
        return [], {}, {}
    blocks = [
        block
        for block in current_plan.get("tests") or []
        if isinstance(block, dict)
        and any(
            isinstance(item, str) and _WRONG_SOLUTION_COVERS_RE.match(item.strip())
            for item in block.get("covers") or []
        )
    ]
    generators = {
        entry["name"]: entry
        for entry in current_plan.get("generators") or []
        if isinstance(entry, dict) and isinstance(entry.get("name"), str)
    }
    constants = {
        entry["name"]: entry
        for entry in current_plan.get("constants") or []
        if isinstance(entry, dict) and isinstance(entry.get("name"), str)
    }
    return blocks, generators, constants


def _block_names(block: dict[str, Any]) -> set[str]:
    texts = [*(block.get("commands") or []), str(block.get("repeat") or "")]
    return {token for text in texts for token in _NAME_TOKEN_RE.findall(str(text))}


def _block_generators(block: dict[str, Any]) -> list[str]:
    return [
        segment.split()[0]
        for command in block.get("commands") or []
        for segment in str(command).split("|")
        if segment.split()
    ]


def build_tests_plan(
    design: dict[str, Any],
    limits: _Limits,
    problem_id: str,
    current_plan: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """The plan (`templates/tests_plan_format.yaml`) from a parsed design: the design's
    generators as they are, its named values and the constants of the formulas, a block per
    idea entry and group — in the order of the groups, within a group — of the ideas; then
    the current plan's blocks for wrong solutions, at the end of their group. A formula
    that can't be computed for a group, or a group left without tests — `TestsDesignError`."""
    own_constants = [dict(entry) for entry in design.get("constants") or []]
    constants = _Constants({entry["name"] for entry in own_constants})
    resolver = _Resolver(limits, constants)
    group_order = _group_names(limits) if limits.groups is not None else [None]

    errors: list[str] = []
    by_group: dict[str | None, list[dict[str, Any]]] = {group: [] for group in group_order}
    for index, idea in enumerate(design["ideas"]):
        groups = _idea_groups(idea, limits, f"ideas[{index}]", errors) or [None]
        for group in groups:
            try:
                by_group[group].append(_block(idea, group, resolver))
            except TestsDesignError as exc:
                where = f"ideas[{index}]" + (f", group {group}" if group else "")
                errors.append(f"{where}: {exc}")

    kept, current_generators, current_constants = wrong_solution_blocks(current_plan)
    generators = [dict(entry) for entry in design["generators"]]
    generator_names = {entry["name"] for entry in generators}
    for block in kept:
        group = block.get("group")
        first = str(group[0] if isinstance(group, list) and group else group or "")
        by_group[first if first in by_group else group_order[-1]].append(block)
        for name in _block_generators(block):
            if name not in generator_names and name in current_generators:
                generators.append(current_generators[name])
                generator_names.add(name)

    empty = [group for group, blocks in by_group.items() if group is not None and not blocks]
    if empty:
        errors.append(f"test group(s) {empty} get no tests — every group needs some")
    if errors:
        raise TestsDesignError(f"{TESTS_DESIGN_FILENAME}: " + "; ".join(errors))

    all_constants = [*own_constants, *constants.entries]
    known = {entry["name"] for entry in all_constants}
    wanted = set().union(*(_block_names(block) for block in kept)) if kept else set()
    while True:
        added = [
            current_constants[name] for name in sorted(wanted - known) if name in current_constants
        ]
        if not added:
            break
        for entry in added:
            all_constants.append(entry)
            known.add(entry["name"])
            wanted |= set(_NAME_TOKEN_RE.findall(str(entry.get("value", ""))))

    return {
        "problem_id": problem_id,
        "generators": generators,
        "constants": all_constants,
        "tests": [block for group in group_order for block in by_group[group]],
    }


def _block(idea: dict[str, Any], group: str | None, resolver: _Resolver) -> dict[str, Any]:
    idea_id = idea["id"].strip()
    block: dict[str, Any] = {"description": idea["what"].strip()}
    if group is not None:
        block["group"] = group
    block["status"] = idea["status"]
    block["covers"] = [idea_id] if _COVERED_ID_RE.match(idea_id) else []
    block["commands"] = [_substitute(command, resolver, group) for command in idea["tests"]]
    repeat = idea.get("repeat")
    block["repeat"] = _substitute(repeat, resolver, group) if repeat else None
    if idea.get("rationale"):
        block["rationale"] = idea["rationale"]
    return block


def render_tests_plan(plan: dict[str, Any]) -> str:
    return PLAN_HEADER + "\n" + dump_plan_part(plan)


def plan_from_design(
    design_text: str,
    constraints_yaml: str,
    problem_id: str,
    current_plan_text: str | None = None,
) -> str:
    """`tests_plan.yaml` from the text of the design: parsed, checked, built
    (`build_tests_plan`) and checked as a plan (`parse_tests_plan`). The current plan —
    for its blocks for wrong solutions; one that doesn't parse as YAML is ignored."""
    limits = parse_limits(constraints_yaml)
    design = parse_tests_design(design_text, limits)
    current_plan = None
    if current_plan_text:
        try:
            loaded = yaml.safe_load(current_plan_text)
        except yaml.YAMLError:
            loaded = None
        current_plan = loaded if isinstance(loaded, dict) else None
    text = render_tests_plan(build_tests_plan(design, limits, problem_id, current_plan))
    try:
        parse_tests_plan(text)
    except TestsPlanError as exc:
        raise TestsDesignError(
            f"the {TESTS_PLAN_FILENAME} built from {TESTS_DESIGN_FILENAME} is invalid: {exc}"
        ) from exc
    return text
