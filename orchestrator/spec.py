"""Загрузка и валидация specs/<problem_id>.yaml по формату из docs/SPEC_FORMAT.md.

Модуль только парсит и проверяет спек — никаких вызовов модели и никакой
генерации артефактов здесь нет (см. CLAUDE.md).
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Literal, Optional, Union

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

PROBLEM_ID_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_-]*$")

# Подсказки для конкретных полей — добавляются к сообщению об ошибке
# валидации, чтобы явно указать, зачем поле нужно и где про это написано
# (см. "Правило эскалации при неуверенности" в CLAUDE.md).
_FIELD_HINTS: dict[str, str] = {
    "constraints.intended_complexity": (
        "обязательно для шага constraints_pick — модель не имеет права "
        "выводить асимптотику сама из легенды, см. CLAUDE.md и SPEC_FORMAT.md"
    ),
    "constraints.complexity_reasoning": (
        "обязательно для шага constraints_pick — нужно, чтобы модель "
        "проверяла N/TL на согласованность с обоснованием автора, а не "
        "гадала заново, см. CLAUDE.md и SPEC_FORMAT.md"
    ),
}


def _non_blank(value: str, field_name: str) -> str:
    if not value.strip():
        raise ValueError(f"поле '{field_name}' обязательно и не может быть пустым")
    return value


class SpecValidationError(Exception):
    """Ошибка валидации спека с человекочитаемым списком проблем."""

    def __init__(self, path: Path, errors: list[str]):
        self.path = path
        self.errors = errors
        message = f"Спек {path} не прошёл валидацию:\n" + "\n".join(
            f"  - {error}" for error in errors
        )
        super().__init__(message)


class Title(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ru: str
    en: Optional[str] = None

    @model_validator(mode="after")
    def _check_ru(self) -> "Title":
        _non_blank(self.ru, "title.ru")
        return self


class SampleExample(BaseModel):
    model_config = ConfigDict(extra="forbid")

    input: str
    output: str
    note_hint: Optional[str] = None

    @model_validator(mode="after")
    def _check_non_blank(self) -> "SampleExample":
        _non_blank(self.input, "sample_examples[].input")
        _non_blank(self.output, "sample_examples[].output")
        return self


class StatementDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    legend_sketch: str
    formal_input_sketch: str
    formal_output_sketch: str
    indexing: Literal["0-indexed", "1-indexed", "не имеет значения"]
    sample_examples: list[SampleExample] = Field(min_length=1)
    known_ambiguities: Optional[list[str]] = None

    @model_validator(mode="after")
    def _check_non_blank(self) -> "StatementDraft":
        _non_blank(self.legend_sketch, "statement_draft.legend_sketch")
        _non_blank(self.formal_input_sketch, "statement_draft.formal_input_sketch")
        _non_blank(self.formal_output_sketch, "statement_draft.formal_output_sketch")
        return self


class Variable(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    min: Union[int, str]
    max: Optional[Union[int, str]] = None
    description: str

    @model_validator(mode="after")
    def _check_non_blank(self) -> "Variable":
        _non_blank(self.name, "constraints.variables[].name")
        _non_blank(self.description, "constraints.variables[].description")
        return self


class TestGroupHint(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    subset_of: Optional[str] = None
    max_n: Optional[int] = None
    special_property: Optional[str] = None


class Constraints(BaseModel):
    model_config = ConfigDict(extra="forbid")

    intended_complexity: str
    complexity_reasoning: str
    variables: list[Variable] = Field(min_length=1)
    special_guarantees: Optional[list[str]] = None
    test_groups_hint: Optional[list[TestGroupHint]] = None
    time_limit_seconds: Optional[float] = None
    memory_limit_mb: Optional[int] = None
    uncertain_points: Optional[list[str]] = None

    @model_validator(mode="after")
    def _check_non_blank(self) -> "Constraints":
        # Это и есть жёсткая проверка из CLAUDE.md: без асимптотики и
        # обоснования, данных автором явно, шаг constraints_pick не имеет
        # права запускаться и должен падать с понятной ошибкой.
        _non_blank(self.intended_complexity, "constraints.intended_complexity")
        _non_blank(self.complexity_reasoning, "constraints.complexity_reasoning")
        return self


class GeneratorIdea(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    params: list[str] = Field(default_factory=list)
    purpose: str

    @model_validator(mode="after")
    def _check_non_blank(self) -> "GeneratorIdea":
        _non_blank(self.name, "generation.generator_ideas[].name")
        _non_blank(self.purpose, "generation.generator_ideas[].purpose")
        return self


class Generation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    input_shape: str
    generator_ideas: Optional[list[GeneratorIdea]] = None
    specific_test_ideas: Optional[list[str]] = None
    reuse_existing_generators: bool
    base_template_refs: Optional[list[str]] = None
    script_style: Literal["flat", "groups"]

    @model_validator(mode="after")
    def _check_non_blank(self) -> "Generation":
        _non_blank(self.input_shape, "generation.input_shape")
        return self


_ALLOWED_VERDICTS = {"ok", "wa", "tl", "tle", "ml", "mle", "re"}


class Solutions(BaseModel):
    model_config = ConfigDict(extra="forbid")

    wanted_verdicts: list[str] = Field(min_length=1)
    languages: list[str] = Field(min_length=1)
    algorithm_hint: str
    known_wrong_approaches: Optional[list[str]] = None

    @model_validator(mode="after")
    def _check_fields(self) -> "Solutions":
        _non_blank(self.algorithm_hint, "solutions.algorithm_hint")
        unknown = sorted(set(self.wanted_verdicts) - _ALLOWED_VERDICTS)
        if unknown:
            raise ValueError(
                "solutions.wanted_verdicts содержит неизвестные вердикты: "
                f"{unknown} (допустимые: {sorted(_ALLOWED_VERDICTS)})"
            )
        return self


class Checker(BaseModel):
    model_config = ConfigDict(extra="forbid")

    standard: Optional[str] = None
    custom_needed: bool
    custom_comparison_notes: Optional[str] = None

    @model_validator(mode="after")
    def _check_custom_notes(self) -> "Checker":
        if self.custom_needed and not (
            self.custom_comparison_notes and self.custom_comparison_notes.strip()
        ):
            raise ValueError(
                "checker.custom_comparison_notes обязателен, если "
                "checker.custom_needed=true, см. SPEC_FORMAT.md"
            )
        return self


class Meta(BaseModel):
    model_config = ConfigDict(extra="forbid")

    requirements_docs: list[str] = Field(default_factory=list)
    status: Literal["draft", "ready_for_generation", "reviewed"]


class ProblemSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    problem_id: str
    title: Title
    statement_draft: StatementDraft
    constraints: Constraints
    generation: Generation
    solutions: Optional[Solutions] = None
    checker: Checker
    meta: Meta

    @model_validator(mode="after")
    def _check_problem_id(self) -> "ProblemSpec":
        if not PROBLEM_ID_PATTERN.match(self.problem_id):
            raise ValueError(
                f"problem_id '{self.problem_id}' должен быть латиницей "
                "(буквы/цифры/'-'/'_', начинается с буквы), см. SPEC_FORMAT.md"
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


def load_spec(path: Path) -> ProblemSpec:
    """Загружает и валидирует спек задачи.

    Кидает SpecValidationError с человекочитаемым списком проблем вместо
    трудночитаемого traceback pydantic — вызывающий код (пайплайн, CLI)
    должен показать эти ошибки автору спека и остановиться, а не гадать.
    """
    path = Path(path)
    if not path.exists():
        raise SpecValidationError(path, [f"файл не найден: {path}"])

    try:
        raw_text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise SpecValidationError(path, [f"не удалось прочитать файл: {exc}"]) from exc

    try:
        raw = yaml.safe_load(raw_text)
    except yaml.YAMLError as exc:
        raise SpecValidationError(path, [f"невалидный YAML: {exc}"]) from exc

    if not isinstance(raw, dict):
        raise SpecValidationError(
            path, ["корневой элемент спека должен быть YAML-словарём (mapping)"]
        )

    try:
        spec = ProblemSpec.model_validate(raw)
    except ValidationError as exc:
        raise SpecValidationError(path, _format_pydantic_errors(exc)) from exc

    expected_id = path.stem
    if spec.problem_id != expected_id:
        raise SpecValidationError(
            path,
            [
                f"problem_id ('{spec.problem_id}') не совпадает с именем файла "
                f"('{expected_id}.yaml'), см. SPEC_FORMAT.md"
            ],
        )

    return spec
