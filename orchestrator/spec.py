"""Loading and validating a problem's spec (`problems/<problem path>/authoring/spec.yaml`) by the format from docs/SPEC_FORMAT.md.

The module only parses and checks the spec — no model calls and no
artifact generation here (see CLAUDE.md).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import yaml
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    ValidationInfo,
    field_validator,
    model_validator,
)

from orchestrator.problem_paths import (
    PROBLEM_ID_PATTERN,
    SPEC_FILENAME,
    ProblemPathError,
    authoring_path,
    check_new_problem_location,
    find_authoring_file,
    parse_problem_address,
    problem_address,
    problem_id_for,
)
from orchestrator.statement_languages import DEFAULT_LANGUAGE, GENERATION_LANGUAGES

# The sections' `references` — which samples to attach to the call (their contents are part
# of `compute_prompt_hash`, see orchestrator/references.py), not problem
# content: the field is excluded from the sections' `model_dump` for the prompt and `input_hash`.
SPEC_REFERENCES_FIELD = {"references"}

# Hints for particular fields — added to the validation error message,
# to state explicitly why the field is needed and where it's described
# (see "Escalation rule on uncertainty" in CLAUDE.md).
_FIELD_HINTS: dict[str, str] = {
    "constraints.intended_complexity": (
        "required for the constraints_pick step — the model must not infer "
        "the complexity from the legend on its own, see CLAUDE.md and SPEC_FORMAT.md"
    ),
    "constraints.complexity_reasoning": (
        "required for the constraints_pick step — the model checks N/TL "
        "for consistency with the author's reasoning instead of "
        "guessing anew, see CLAUDE.md and SPEC_FORMAT.md"
    ),
    "generation.base_template_refs": (
        "the field was renamed to generation.references (a reference may now "
        "also be a directory laid out like problems/<problem_id>/), see SPEC_FORMAT.md"
    ),
}


def _non_blank(value: str, field_name: str) -> str:
    if not value.strip():
        raise ValueError(f"field '{field_name}' is required and must not be empty")
    return value


class SpecValidationError(Exception):
    """A spec validation error with a human-readable list of problems."""

    def __init__(self, path: Path, errors: list[str]):
        self.path = path
        self.errors = errors
        message = f"Spec {path} failed validation:\n" + "\n".join(
            f"  - {error}" for error in errors
        )
        super().__init__(message)


class Title(BaseModel):
    """The problem's title by language; mandatory — in the main statement language
    (`ProblemSpec._check_title`)."""

    model_config = ConfigDict(extra="forbid")

    ru: str | None = None
    en: str | None = None

    def for_language(self, code: str) -> str | None:
        """The title in the language with code `code` (`ru`); `None` — not set."""
        value = getattr(self, code, None) if code in type(self).model_fields else None
        return value if value and value.strip() else None


class SampleExample(BaseModel):
    model_config = ConfigDict(extra="forbid")

    input: str
    output: str | None = None
    note_hint: str | None = None

    @model_validator(mode="after")
    def _check_non_blank(self) -> SampleExample:
        _non_blank(self.input, "sample_examples[].input")
        return self


class StatementDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    legend_sketch: str
    formal_input_sketch: str
    formal_output_sketch: str
    indexing: Literal["0-indexed", "1-indexed", "irrelevant"]
    sample_examples: list[SampleExample] = Field(min_length=1)
    known_ambiguities: list[str] | None = None
    preserve_legend_verbatim: bool = False
    # The interaction protocol — only for an interactive problem (there's an
    # `interactor` section, see `ProblemSpec._check_interactive`).
    interaction_sketch: str | None = None
    references: list[str] | None = None

    @property
    def has_interaction(self) -> bool:
        return bool(self.interaction_sketch and self.interaction_sketch.strip())

    @model_validator(mode="after")
    def _check_non_blank(self) -> StatementDraft:
        _non_blank(self.legend_sketch, "statement_draft.legend_sketch")
        _non_blank(self.formal_input_sketch, "statement_draft.formal_input_sketch")
        _non_blank(self.formal_output_sketch, "statement_draft.formal_output_sketch")
        return self


class Variable(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    min: int | str
    max: int | str | None = None
    description: str

    @model_validator(mode="after")
    def _check_non_blank(self) -> Variable:
        _non_blank(self.name, "constraints.variables[].name")
        _non_blank(self.description, "constraints.variables[].description")
        return self


# How Polygon shows a contestant the result on a test group (`feedbackPolicy` of
# `problem.saveTestGroup`): `points` — only the points, `icpc` — the verdict of the first
# failed test (the default: "the first error" in the scoring table), `complete` — the
# verdicts of all tests, `none` — nothing.
FEEDBACK_POLICIES = ("points", "icpc", "complete", "none")
DEFAULT_FEEDBACK_POLICY = "icpc"

# How a test group's points are given (`pointsPolicy` of `problem.saveTestGroup`):
# `complete_group` — only for passing every test of the group (the default), `each_test` —
# for each passed test, every test of the group worth the same: the group's points divided
# by its number of tests, so that number must divide the points (`allowed_test_counts`).
POINTS_POLICIES = ("complete_group", "each_test")
DEFAULT_POINTS_POLICY = "complete_group"
EACH_TEST = "each_test"


class GroupVariableLimit(BaseModel):
    """A test group's own bounds of one of `constraints.variables`: a number or an
    expression through other variables, as in `Variable`; an absent bound — the
    variable's full one."""

    model_config = ConfigDict(extra="forbid")

    min: int | str | None = None
    max: int | str | None = None

    @model_validator(mode="after")
    def _check_bounds(self) -> GroupVariableLimit:
        if self.min is None and self.max is None:
            raise ValueError("a group's variable limit sets min, max or both")
        for value in (self.min, self.max):
            if isinstance(value, str):
                _non_blank(value, "a group's variable limit")
        if isinstance(self.min, int) and isinstance(self.max, int) and self.min > self.max:
            raise ValueError(f"a group's variable limit has min {self.min} > max {self.max}")
        return self


class TestGroupHint(BaseModel):
    """A test group (subtask) the author already has in mind. The names are numbers
    `1`, `2`, ...: the validator gets the group as a number (`group_index()` in
    `problem_lib.h`); group `0` — the statement examples with the full limits and
    0 points — is implicit and isn't listed."""

    model_config = ConfigDict(extra="forbid")

    name: str
    # The group's own bounds of the problem's variables, by the variable's name in
    # `constraints.variables`; everything else about the group's tests — `special_property`.
    variables: dict[str, GroupVariableLimit] | None = None
    special_property: str | None = None
    points: int | None = None
    dependencies: list[str] | None = None
    feedback_policy: Literal[FEEDBACK_POLICIES] = DEFAULT_FEEDBACK_POLICY  # type: ignore[valid-type]
    points_policy: Literal[POINTS_POLICIES] = DEFAULT_POINTS_POLICY  # type: ignore[valid-type]


class Constraints(BaseModel):
    model_config = ConfigDict(extra="forbid")

    intended_complexity: str
    complexity_reasoning: str
    variables: list[Variable] = Field(min_length=1)
    special_guarantees: list[str] | None = None
    test_groups_hint: list[TestGroupHint] | None = None
    test_groups_needed: bool = False
    time_limit_seconds: float | None = None
    memory_limit_mb: int | None = None
    uncertain_points: list[str] | None = None
    references: list[str] | None = None

    @model_validator(mode="after")
    def _check_non_blank(self) -> Constraints:
        # This is the hard check from CLAUDE.md: without the asymptotics and the
        # justification explicitly given by the author, the constraints_pick step has no
        # right to run and must fail with a clear error.
        _non_blank(self.intended_complexity, "constraints.intended_complexity")
        _non_blank(self.complexity_reasoning, "constraints.complexity_reasoning")
        groups = self.test_groups_hint or []
        check_test_groups(groups, "constraints.test_groups_hint")
        check_group_variables(groups, self.variables, "constraints.test_groups_hint")
        # In the spec a group depends only on earlier groups: the statement's subtasks
        # table and constraints_pick take them in order (`constraints.yaml` read back from
        # Polygon isn't held to this).
        names = [group.name for group in groups]
        for index, group in enumerate(groups):
            for dependency in group.dependencies or []:
                if dependency not in names[:index]:
                    raise ValueError(
                        f"constraints.test_groups_hint[{index}].dependencies: group "
                        f"{group.name} depends on group {dependency}, which isn't an earlier "
                        "group — a group depends only on groups listed before it"
                    )
        return self


def allowed_test_counts(points: int) -> list[int]:
    """The numbers of tests an `each_test` group with `points` may have: every test worth
    the same whole number of points — the divisors of `points` (`0` points — any number,
    `[]` here)."""
    return [count for count in range(1, points + 1) if points % count == 0]


def check_test_groups(groups: list[TestGroupHint], where: str) -> None:
    """Group names — distinct positive numbers (`0` — the statement examples, implicit),
    `dependencies` — names of the listed groups, other than the group itself.
    `where` — the list's location for the messages: `constraints.test_groups_hint` of the
    spec or `test_groups` of `constraints.yaml` (`orchestrator/test_groups.py`)."""
    names = [group.name for group in groups]
    for index, name in enumerate(names):
        if not name.isdigit() or int(name) == 0 or name != str(int(name)):
            raise ValueError(
                f"{where}[{index}].name must be a positive number "
                f"(1, 2, ...; group 0 — the statement examples — is implicit), got {name!r}"
            )
    if len(set(names)) != len(names):
        raise ValueError(f"{where}: group names repeat: {names}")
    for index, group in enumerate(groups):
        if group.points is not None and group.points < 0:
            raise ValueError(f"{where}[{index}].points must be >= 0")
        if group.points_policy == EACH_TEST and not group.points:
            raise ValueError(
                f"{where}[{index}]: points_policy each_test needs positive points — "
                "each test of the group gets an equal share of them"
            )
        for ref in group.dependencies or []:
            if ref == group.name or ref not in names:
                raise ValueError(
                    f"{where}[{index}].dependencies: {ref!r} must name "
                    f"another listed group (one of {names})"
                )


def check_group_variables(
    groups: list[TestGroupHint], variables: list[Variable], where: str
) -> None:
    """Each group's `variables` — names from `constraints.variables`, and its numeric
    bounds are within the variable's numeric ones (an expression on either side isn't
    compared)."""
    by_name = {variable.name: variable for variable in variables}
    for index, group in enumerate(groups):
        for name, limit in (group.variables or {}).items():
            variable = by_name.get(name)
            if variable is None:
                raise ValueError(
                    f"{where}[{index}].variables: {name!r} isn't one of "
                    f"constraints.variables ({', '.join(by_name)})"
                )
            if _int(limit.min) is not None and _int(variable.min) is not None:
                if limit.min < variable.min:
                    raise ValueError(
                        f"{where}[{index}].variables.{name}.min {limit.min} is below the "
                        f"variable's min {variable.min}"
                    )
            if _int(limit.max) is not None and _int(variable.max) is not None:
                if limit.max > variable.max:
                    raise ValueError(
                        f"{where}[{index}].variables.{name}.max {limit.max} is above the "
                        f"variable's max {variable.max}"
                    )


def _int(value: int | str | None) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


class GeneratorIdea(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    params: list[str] = Field(default_factory=list)
    purpose: str

    @model_validator(mode="after")
    def _check_non_blank(self) -> GeneratorIdea:
        _non_blank(self.name, "generation.generator_ideas[].name")
        _non_blank(self.purpose, "generation.generator_ideas[].purpose")
        return self


class Generation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    generator_ideas: list[GeneratorIdea] | None = None
    specific_test_ideas: list[str] | None = None
    script_style: Literal["flat", "groups"]
    references: list[str] | None = None


# `tl_ok` — "TL or OK": a solution borderline in time or a solution without
# input/output speedup (`templates/tutorials/solutions.md`); `tl_ml` — "TL or ML": a
# solution too slow and too memory-hungry at once (which limit it hits first isn't
# known in advance; locally a memory shortage shows up as RE); `il` —
# Idleness limit exceeded in an interactive problem (a solution without flushing the buffer,
# `templates/tutorials/interactive.md`).
_ALLOWED_VERDICTS = {"ok", "wa", "tl", "tl_ok", "tl_ml", "tle", "ml", "mle", "re", "il"}


def split_solution_stem(stem: str) -> list[str]:
    """The tokens of a solution name `<verdict>_<language>_<description>_<author>`
    (`stem` — without the extension). The verdict itself may contain `_` (`tl_ok`, `tl_ml`) —
    such a one is glued back into one first token."""
    parts = stem.split("_")
    if len(parts) >= 2 and f"{parts[0]}_{parts[1]}".lower() in _ALLOWED_VERDICTS:
        return [f"{parts[0]}_{parts[1]}", *parts[2:]]
    return parts


# The verdicts of a `known_wrong_approaches` item; its prefix is mandatory: by the verdict
# the solution stages decide which of them writes the item.
WRONG_APPROACH_VERDICTS = frozenset(_ALLOWED_VERDICTS - {"ok"})
_WRONG_APPROACH_RE = re.compile(
    r"^\s*([A-Za-z_]+)\s*(?:\(([^)]*)\))?\s*(?:\[([^\]]*)\])?\s*:\s*(\S.*)$", re.S
)
_GROUP_RANGE_RE = re.compile(r"^(\d+)\s*-\s*(\d+)$")


@dataclass(frozen=True)
class WrongApproach:
    """A `known_wrong_approaches` item
    `<verdict> (<languages separated by />) [<test groups>]: <mistake>`;
    `languages` — empty if the item doesn't give them; `passed_groups` — the test groups
    (subtasks) the solution passes completely, `None` if the item doesn't give them
    (`[]` — an explicit "passes none"). The verdict is the one on the whole test set."""

    verdict: str
    languages: tuple[str, ...]
    mistake: str
    passed_groups: tuple[str, ...] | None = None


def _parse_groups(text: str) -> tuple[str, ...] | None:
    """`1-3, 5` -> ("1", "2", "3", "5"); an empty text — (); a malformed one — None."""
    groups: list[str] = []
    for part in (p.strip() for p in text.split(",")):
        if not part:
            continue
        if (match := _GROUP_RANGE_RE.match(part)) is not None:
            first, last = int(match.group(1)), int(match.group(2))
            if first > last:
                return None
            groups += [str(number) for number in range(first, last + 1)]
        elif part.isdigit():
            groups.append(str(int(part)))
        else:
            return None
    return tuple(dict.fromkeys(groups))


def parse_wrong_approach(item: str) -> WrongApproach | None:
    """The item's prefix; `None` — there's no prefix with a known verdict or the
    groups in `[...]` aren't a list of numbers and ranges."""
    match = _WRONG_APPROACH_RE.match(item)
    if match is None or match.group(1).lower() not in WRONG_APPROACH_VERDICTS:
        return None
    languages = tuple(lang.strip() for lang in (match.group(2) or "").split("/") if lang.strip())
    passed_groups = None
    if match.group(3) is not None:
        passed_groups = _parse_groups(match.group(3))
        if passed_groups is None:
            return None
    return WrongApproach(match.group(1).lower(), languages, match.group(4).strip(), passed_groups)


# The comment a solution written from a `known_wrong_approaches` item carries in its
# first lines: `// spec: solutions.known_wrong_approaches[<i>]` (`#` in Python), `<i>` —
# the item's 0-based index in the whole list. By it the local run finds the item of a
# solution file and compares the test groups it passes with the item's `[...]`.
APPROACH_MARKER_RE = re.compile(r"\bsolutions\.known_wrong_approaches\[(\d+)\]")


def find_approach_marker(source: str) -> int | None:
    """The item index of the first `APPROACH_MARKER_RE` in `source`; `None` — no marker."""
    match = APPROACH_MARKER_RE.search(source)
    return int(match.group(1)) if match is not None else None


# The comment a model's own solution for particular test groups (a `SUGGESTION` file of
# a solution stage, not written from a spec item) carries: `// passes groups: [2, 3]` —
# the groups it is meant to pass completely, in the syntax of an item's `[...]`.
SUGGESTED_GROUPS_RE = re.compile(r"\bpasses groups:\s*\[([^\]\n]*)\]")


def find_suggested_groups(source: str) -> tuple[str, ...] | None | bool:
    """The groups of the first `SUGGESTED_GROUPS_RE` in `source`; `None` — no marker,
    `False` — a marker whose groups aren't a list of numbers and ranges."""
    match = SUGGESTED_GROUPS_RE.search(source)
    if match is None:
        return None
    groups = _parse_groups(match.group(1))
    return False if groups is None else groups


class Solutions(BaseModel):
    model_config = ConfigDict(extra="forbid")

    wanted_verdicts: list[str] = Field(min_length=1)
    languages: list[str] = Field(min_length=1)
    algorithm_hint: str
    alternative_approaches: list[str] | None = None
    known_wrong_approaches: list[str] | None = None
    references: list[str] | None = None

    @model_validator(mode="after")
    def _check_fields(self) -> Solutions:
        _non_blank(self.algorithm_hint, "solutions.algorithm_hint")
        unknown = sorted(set(self.wanted_verdicts) - _ALLOWED_VERDICTS)
        if unknown:
            raise ValueError(
                "solutions.wanted_verdicts contains unknown verdicts: "
                f"{unknown} (allowed: {sorted(_ALLOWED_VERDICTS)})"
            )
        for index, item in enumerate(self.known_wrong_approaches or []):
            if parse_wrong_approach(item) is None:
                raise ValueError(
                    f"solutions.known_wrong_approaches[{index}] must start with the verdict — "
                    "'<verdict>: <mistake>' or '<verdict> (<languages separated by />) "
                    "[<passed test groups, e.g. 1-3, 5>]: <mistake>' (languages and groups "
                    f"are optional), verdict one of {sorted(WRONG_APPROACH_VERDICTS)}; "
                    f"got {item!r}"
                )
        return self


class Checker(BaseModel):
    model_config = ConfigDict(extra="forbid")

    standard: str | None = None
    custom_needed: bool
    custom_comparison_notes: str | None = None
    references: list[str] | None = None

    @model_validator(mode="after")
    def _check_custom_notes(self) -> Checker:
        if self.custom_needed and not (
            self.custom_comparison_notes and self.custom_comparison_notes.strip()
        ):
            raise ValueError(
                "checker.custom_comparison_notes is required when "
                "checker.custom_needed=true, see SPEC_FORMAT.md"
            )
        return self


class Interactor(BaseModel):
    """The interactive problem section: the section is present — the problem is interactive."""

    model_config = ConfigDict(extra="forbid")

    behavior_notes: str
    references: list[str] | None = None

    @model_validator(mode="after")
    def _check_non_blank(self) -> Interactor:
        _non_blank(self.behavior_notes, "interactor.behavior_notes")
        return self


class ProblemSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    problem_id: str
    # The main statement language (code): the model's steps write in it, its statement is in
    # `statement/<code>/` (see `orchestrator/statement_languages.py`). Declared
    # before `title`: its check (`_check_title`) reads the language.
    language: Literal[tuple(GENERATION_LANGUAGES)] = DEFAULT_LANGUAGE  # type: ignore[valid-type]
    title: Title
    references: list[str] | None = None
    statement_draft: StatementDraft
    constraints: Constraints
    generation: Generation
    solutions: Solutions | None = None
    checker: Checker
    interactor: Interactor | None = None

    @property
    def interactive(self) -> bool:
        return self.interactor is not None

    @model_validator(mode="after")
    def _check_problem_id(self) -> ProblemSpec:
        if not PROBLEM_ID_PATTERN.match(self.problem_id):
            raise ValueError(
                f"problem_id '{self.problem_id}' must be ASCII "
                "(letters/digits/'-'/'_', starting with a letter), see SPEC_FORMAT.md"
            )
        return self

    @field_validator("title")
    @classmethod
    def _check_title(cls, title: Title, info: ValidationInfo) -> Title:
        # A field validator, not one for the whole model: the error is shown together with the rest. A language
        # with an error (it isn't in `info.data`) is checked on its own.
        language = info.data.get("language")
        if language is not None:
            _non_blank(getattr(title, language) or "", f"title.{language}")
        return title

    @model_validator(mode="after")
    def _check_interactive(self) -> ProblemSpec:
        # The interaction protocol is part of the statement, the interactor's behavior is the
        # input of the interactor_draft step: one makes no sense without the other.
        has_protocol = self.statement_draft.has_interaction
        if self.interactive and not has_protocol:
            raise ValueError(
                "statement_draft.interaction_sketch is required when the spec has an "
                "'interactor' section (interactive problem), see SPEC_FORMAT.md"
            )
        if not self.interactive and has_protocol:
            raise ValueError(
                "statement_draft.interaction_sketch is set, but the spec has no "
                "'interactor' section — add it for an interactive problem or remove "
                "the field, see SPEC_FORMAT.md"
            )
        return self

    @model_validator(mode="after")
    def _check_script_style(self) -> ProblemSpec:
        # A problem with test groups marks each script line with its group, which the
        # flat script (`test_script`) doesn't do.
        has_groups = bool(self.constraints.test_groups_hint) or self.constraints.test_groups_needed
        if has_groups and self.generation.script_style == "flat":
            raise ValueError(
                'generation.script_style is "flat", but the problem has test groups '
                '(constraints.test_groups_hint or test_groups_needed) — use "groups", '
                "see SPEC_FORMAT.md"
            )
        return self


def _format_pydantic_errors(exc: ValidationError) -> list[str]:
    lines: list[str] = []
    for error in exc.errors():
        loc = ".".join(str(part) for part in error["loc"])
        msg = error["msg"]
        hint = _FIELD_HINTS.get(loc)
        line = f"{loc}: {msg}" if loc else msg
        if hint:
            line += f" — {hint}"
        lines.append(line)
    return lines


def load_spec(path: Path, *, expected_id: str | None = None) -> ProblemSpec:
    """Loads and validates a problem's spec.

    Raises SpecValidationError with a human-readable list of problems instead of
    a hard-to-read pydantic traceback — the calling code (the pipeline, the CLI)
    must show these errors to the spec's author and stop, not guess.

    The spec's `problem_id` must match `expected_id` — by default it's
    the file name without `.yaml`; a problem's spec lies in its directory
    (`<problem directory>/spec.yaml`, see `load_problem_spec`), and `problem_id`
    is compared with the directory name.
    """
    path = Path(path)
    if not path.exists():
        raise SpecValidationError(path, [f"file not found: {path}"])

    try:
        raw_text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise SpecValidationError(path, [f"failed to read file: {exc}"]) from exc

    try:
        raw = yaml.safe_load(raw_text)
    except yaml.YAMLError as exc:
        raise SpecValidationError(path, [f"invalid YAML: {exc}"]) from exc

    if not isinstance(raw, dict):
        raise SpecValidationError(path, ["spec root must be a YAML mapping"])

    try:
        spec = ProblemSpec.model_validate(raw)
    except ValidationError as exc:
        raise SpecValidationError(path, _format_pydantic_errors(exc)) from exc

    if expected_id is None:
        expected_id = path.stem
        source = f"the file name ('{expected_id}.yaml')"
    else:
        source = f"the expected id ('{expected_id}')"
    if spec.problem_id != expected_id:
        raise SpecValidationError(
            path,
            [f"problem_id ('{spec.problem_id}') does not match {source}, see SPEC_FORMAT.md"],
        )

    return spec


# A problem's spec — `SPEC_FILENAME` in its directory's `authoring/`
# (`problems/<problem path>/authoring/spec.yaml`, as for the sample problems
# `examples/<problem path>/authoring/spec.yaml`), see
# `orchestrator/problem_paths.py`.

# The spec draft template for `orchestrator <problem> init` (a path
# relative to templates_dir). Not attached to the steps.
SPEC_TEMPLATE = "spec_template.yaml"

# The top-level `problem_id` line: the value (quoted or not) with
# spaces around it — up to a comment or the end of the line.
_PROBLEM_ID_LINE = re.compile(
    r"""^problem_id:(?P<value>[ \t]*(?:"[^"\n]*"|'[^'\n]*'|[^#\n]*?)[ \t]*)(?=#|$)""",
    re.MULTILINE,
)
# The top-level `references` line; `rest` — the value and the comment.
_REFERENCES_LINE = re.compile(r"^references:(?P<rest>[^\n]*)$", re.MULTILINE)
# The spec's first section: `references` is inserted before it (and before its header comment)
# if the spec doesn't have it.
_FIRST_SECTION_LINE = re.compile(r"^statement_draft:", re.MULTILINE)


class SpecInitError(Exception):
    """The spec draft wasn't created: a wrong `problem_id`, the spec already exists, etc."""


def set_problem_id(text: str, problem_id: str) -> str:
    """The spec text with the top-level `problem_id` replaced with `problem_id`.

    A text line is edited, not the YAML via `yaml.dump`: this way the spec
    keeps its comments. The column of the comment after the value is kept
    if `problem_id` fits into it. No such line — `SpecInitError`.
    """

    def substitute(match: re.Match) -> str:
        if not match.string.startswith("#", match.end()):
            return f"problem_id: {problem_id}"
        return "problem_id:" + f" {problem_id} ".ljust(len(match.group("value")))

    result, count = _PROBLEM_ID_LINE.subn(substitute, text, count=1)
    if count != 1:
        raise SpecInitError("the spec has no top-level 'problem_id:' line to fill in")
    return result


def _yaml_mapping(text: str) -> dict | None:
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError:
        return None
    return data if isinstance(data, dict) else None


def _is_comment_or_blank(line: str) -> bool:
    return not line.strip() or line.lstrip().startswith("#")


def add_reference(text: str, reference: str) -> str:
    """The spec text with `reference` in the shared `references` (at the spec root), if
    it isn't there yet — also by editing lines, keeping the comments.

    Understands a one-line list (`references: []`, `["a"]`), a block
    list (`references:` and `- ...` items under it), `null` and the absence of
    the field (then `references` is inserted before the `statement_draft` section
    and its header comment). The result is rechecked by parsing the YAML;
    if it didn't work — `SpecInitError`.
    """
    data = _yaml_mapping(text)
    if data is None:
        raise SpecInitError("the spec is not a valid YAML mapping")
    current = data.get("references")
    if isinstance(current, list) and reference in current:
        return text
    quoted = json.dumps(reference, ensure_ascii=False)

    lines = text.splitlines(keepends=True)
    if lines and not lines[-1].endswith("\n"):
        lines[-1] += "\n"
    match = _REFERENCES_LINE.search(text)
    if match is None:
        section = _FIRST_SECTION_LINE.search(text)
        index = len(lines) if section is None else text[: section.start()].count("\n")
        while index > 0 and _is_comment_or_blank(lines[index - 1]):
            index -= 1
        lines.insert(index, f"references:\n  - {quoted}\n")
    else:
        line_index = text[: match.start()].count("\n")
        value, sep, comment = match.group("rest").partition("#")
        value = value.strip()
        comment = f"  {sep}{comment}" if sep else ""
        if value.startswith("[") or value in ("null", "~"):
            items = (current if isinstance(current, list) else []) + [reference]
            flow = ", ".join(json.dumps(item, ensure_ascii=False) for item in items)
            lines[line_index] = f"references: [{flow}]{comment}\n"
        elif not value:
            # A block list: the new item goes after the last item
            # (with its indentation) or right under `references:`.
            insert_at, indent = line_index + 1, "  "
            for index in range(line_index + 1, len(lines)):
                line = lines[index]
                if _is_comment_or_blank(line):
                    continue
                if not line[0].isspace():
                    break
                if line.lstrip().startswith("- "):
                    insert_at = index + 1
                    indent = line[: len(line) - len(line.lstrip())]
            lines.insert(insert_at, f"{indent}- {quoted}\n")
        else:
            raise SpecInitError(f"can't add '{reference}' to 'references: {value}'")

    result = "".join(lines)
    updated = _yaml_mapping(result)
    if updated is None or reference not in (updated.get("references") or []):
        raise SpecInitError(f"failed to add '{reference}' to the spec references")
    return result


@dataclass
class SpecInit:
    """What `init` creates — checked before writing and before calling the model
    (`prepare_spec_init`).

    `base_text` — the spec's base with `problem_id` already substituted: the
    `SPEC_TEMPLATE` template or the spec of the base problem `reference` (which is already added to
    `references`). `reference` — the base problem's address (`problems/<path>`,
    `examples/<path>`); `None` — the base is the template.
    """

    problem_id: str
    path: Path
    base_text: str
    reference: str | None = None


def _reference_spec_text(reference: str, *, base_dir: Path) -> tuple[str, str]:
    """(address, spec text) of the base problem `reference`, the address — as in the CLI."""
    try:
        root, reference_path = parse_problem_address(reference)
    except ProblemPathError as exc:
        raise SpecInitError(f"--ref: {exc}") from exc
    address = problem_address(root, reference_path)
    path = find_authoring_file(Path(base_dir) / root / reference_path, SPEC_FILENAME)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise SpecInitError(f"--ref: failed to read the spec of {address}: {exc}") from exc
    return address, text


def prepare_spec_init(
    problem_path: str,
    *,
    problems_dir: Path,
    templates_dir: Path,
    reference: str | None = None,
) -> SpecInit:
    """Checks that a spec can be created in `problems_dir/<problem_path>/`, and
    assembles its base (`SpecInit`); writes nothing.

    `problem_id` is derived from the path (`problem_id_for`: in `problems/` — the name of
    the problem directory, in `examples/` — the path joined with `--`). An existing
    `spec.yaml` isn't overwritten. The problem directory may already exist
    (e.g. after `polygon pull`). A problem can't lie inside another
    problem or contain problems, and its `problem_id` must not be taken by
    a problem in another directory (`check_new_problem_location`).

    `reference` — the base problem's address (as in the CLI; the roots `problems/` and
    `examples/` are neighbors of `problems_dir`): the base is its spec as is, with
    comments, and the problem itself is added to the shared `references`, so that its
    files become samples for the steps. Without `reference` the base is `SPEC_TEMPLATE`.
    """
    problem_id = problem_id_for(problems_dir, problem_path)
    if not PROBLEM_ID_PATTERN.match(problem_id):
        raise SpecInitError(
            f"problem_id '{problem_id}' must be ASCII "
            "(letters/digits/'-'/'_', starting with a letter), see SPEC_FORMAT.md"
        )

    if reference is None:
        template_path = Path(templates_dir) / SPEC_TEMPLATE
        try:
            base_text = template_path.read_text(encoding="utf-8")
        except OSError as exc:
            raise SpecInitError(f"failed to read spec template {template_path}: {exc}") from exc
        source = f"spec template {template_path}"
    else:
        reference, base_text = _reference_spec_text(reference, base_dir=Path(problems_dir).parent)
        source = f"--ref: the spec of {reference}"
    try:
        base_text = set_problem_id(base_text, problem_id)
        if reference is not None:
            base_text = add_reference(base_text, reference)
    except SpecInitError as exc:
        raise SpecInitError(f"{source}: {exc}") from exc

    path = spec_path(problem_path, problems_dir=problems_dir)
    if path.exists():
        raise SpecInitError(f"{path} already exists, not overwriting it")
    try:
        check_new_problem_location(problems_dir, problem_path)
    except ProblemPathError as exc:
        raise SpecInitError(str(exc)) from exc
    return SpecInit(problem_id=problem_id, path=path, base_text=base_text, reference=reference)


def write_spec(init: SpecInit, text: str) -> Path:
    """Writes the spec to `init.path`, creating the problem directory; returns the path."""
    init.path.parent.mkdir(parents=True, exist_ok=True)
    init.path.write_text(text, encoding="utf-8")
    return init.path


def create_spec_draft(
    problem_path: str,
    *,
    problems_dir: Path,
    templates_dir: Path,
    reference: str | None = None,
) -> Path:
    """Creates the problem directory `problems_dir/<problem_path>/` (if it doesn't exist) and
    puts the spec into it without calling the model: the `SPEC_TEMPLATE` template with
    `problem_id` substituted (the other fields are empty — the author fills them in)
    or, with `reference`, a copy of the base problem's spec with the new `problem_id` and
    the base problem itself in `references`. The checks — as in `prepare_spec_init`.
    Returns the path to the created file.
    """
    init = prepare_spec_init(
        problem_path, problems_dir=problems_dir, templates_dir=templates_dir, reference=reference
    )
    return write_spec(init, init.base_text)


# The marks with which `init --draft` marks values in the spec that aren't from the author's
# draft (`prompts/spec_from_draft/system.md`) — YAML comments.
_REVIEW_MARK = re.compile(r"#\s*(?:PROPOSED, REVIEW ME|SUGGESTION)\b")


def spec_review_marks(path: Path) -> list[tuple[int, str]]:
    """Unreviewed marks of `init --draft` in the spec: (line number, line).
    The steps treat the spec as the author's decisions, so `llm generate` and
    `llm status` warn about them. No file — an empty list."""
    try:
        lines = Path(path).read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    return [
        (number, line.strip())
        for number, line in enumerate(lines, start=1)
        if _REVIEW_MARK.search(line)
    ]


def spec_validation_errors(path: Path, *, expected_id: str) -> list[str]:
    """The spec's validation errors (`load_spec`); an empty list — the spec is valid.
    For `init`: the created spec is a draft and may not be filled in yet."""
    try:
        load_spec(path, expected_id=expected_id)
    except SpecValidationError as exc:
        return exc.errors
    return []


def spec_path(problem_path: str, *, problems_dir: Path) -> Path:
    """The path to the problem's spec: `problems_dir/<problem_path>/authoring/spec.yaml`."""
    return authoring_path(Path(problems_dir) / problem_path, SPEC_FILENAME)


def load_problem_spec(problem_path: str, *, problems_dir: Path) -> ProblemSpec:
    """Loads the problem's spec from its directory (`spec_path`); the `problem_id` in
    the spec must match the one derived from the directory path (`problem_id_for`).
    Errors — as in `load_spec`."""
    return load_spec(
        spec_path(problem_path, problems_dir=problems_dir),
        expected_id=problem_id_for(problems_dir, problem_path),
    )
