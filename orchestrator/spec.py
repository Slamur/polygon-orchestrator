"""Загрузка и валидация спека задачи (`outputs/<problem_id>/spec.yaml`) по формату из docs/SPEC_FORMAT.md.

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

# `references` секций — какие образцы приложить к вызову (их содержимое входит
# в `compute_prompt_hash`, см. orchestrator/references.py), а не содержание
# задачи: из `model_dump` секций для промпта и `input_hash` поле исключается.
SPEC_REFERENCES_FIELD = {"references"}

# Подсказки для конкретных полей — добавляются к сообщению об ошибке
# валидации, чтобы явно указать, зачем поле нужно и где про это написано
# (см. "Правило эскалации при неуверенности" в CLAUDE.md).
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
        "also be a directory laid out like outputs/<problem_id>/), see SPEC_FORMAT.md"
    ),
}


def _non_blank(value: str, field_name: str) -> str:
    if not value.strip():
        raise ValueError(f"field '{field_name}' is required and must not be empty")
    return value


class SpecValidationError(Exception):
    """Ошибка валидации спека с человекочитаемым списком проблем."""

    def __init__(self, path: Path, errors: list[str]):
        self.path = path
        self.errors = errors
        message = f"Spec {path} failed validation:\n" + "\n".join(
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
    output: Optional[str] = None
    note_hint: Optional[str] = None

    @model_validator(mode="after")
    def _check_non_blank(self) -> "SampleExample":
        _non_blank(self.input, "sample_examples[].input")
        return self


class StatementDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    legend_sketch: str
    formal_input_sketch: str
    formal_output_sketch: str
    indexing: Literal["0-indexed", "1-indexed", "irrelevant"]
    sample_examples: list[SampleExample] = Field(min_length=1)
    known_ambiguities: Optional[list[str]] = None
    preserve_legend_verbatim: bool = False
    references: Optional[list[str]] = None

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
    test_groups_needed: bool = False
    time_limit_seconds: Optional[float] = None
    memory_limit_mb: Optional[int] = None
    uncertain_points: Optional[list[str]] = None
    references: Optional[list[str]] = None

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

    generator_ideas: Optional[list[GeneratorIdea]] = None
    specific_test_ideas: Optional[list[str]] = None
    script_style: Literal["flat", "groups"]
    references: Optional[list[str]] = None


# `tl_ok` — "TL или OK": пограничное по времени решение или решение без
# ускорения ввода-вывода (`templates/common/tutorials/solutions.md`).
_ALLOWED_VERDICTS = {"ok", "wa", "tl", "tl_ok", "tle", "ml", "mle", "re"}


def split_solution_stem(stem: str) -> list[str]:
    """Токены имени решения `<verdict>_<language>_<description>_<author>`
    (`stem` — без расширения). Вердикт сам может содержать `_` (`tl_ok`) —
    такой склеивается обратно в один первый токен."""
    parts = stem.split("_")
    if len(parts) >= 2 and f"{parts[0]}_{parts[1]}".lower() in _ALLOWED_VERDICTS:
        return [f"{parts[0]}_{parts[1]}", *parts[2:]]
    return parts


class Solutions(BaseModel):
    model_config = ConfigDict(extra="forbid")

    wanted_verdicts: list[str] = Field(min_length=1)
    languages: list[str] = Field(min_length=1)
    algorithm_hint: str
    alternative_approaches: Optional[list[str]] = None
    known_wrong_approaches: Optional[list[str]] = None
    references: Optional[list[str]] = None

    @model_validator(mode="after")
    def _check_fields(self) -> "Solutions":
        _non_blank(self.algorithm_hint, "solutions.algorithm_hint")
        unknown = sorted(set(self.wanted_verdicts) - _ALLOWED_VERDICTS)
        if unknown:
            raise ValueError(
                "solutions.wanted_verdicts contains unknown verdicts: "
                f"{unknown} (allowed: {sorted(_ALLOWED_VERDICTS)})"
            )
        return self


class Checker(BaseModel):
    model_config = ConfigDict(extra="forbid")

    standard: Optional[str] = None
    custom_needed: bool
    custom_comparison_notes: Optional[str] = None
    references: Optional[list[str]] = None

    @model_validator(mode="after")
    def _check_custom_notes(self) -> "Checker":
        if self.custom_needed and not (
            self.custom_comparison_notes and self.custom_comparison_notes.strip()
        ):
            raise ValueError(
                "checker.custom_comparison_notes is required when "
                "checker.custom_needed=true, see SPEC_FORMAT.md"
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
    references: Optional[list[str]] = None
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
                f"problem_id '{self.problem_id}' must be ASCII "
                "(letters/digits/'-'/'_', starting with a letter), see SPEC_FORMAT.md"
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
    """Загружает и валидирует спек задачи.

    Кидает SpecValidationError с человекочитаемым списком проблем вместо
    трудночитаемого traceback pydantic — вызывающий код (пайплайн, CLI)
    должен показать эти ошибки автору спека и остановиться, а не гадать.

    `problem_id` спека должен совпадать с `expected_id` — по умолчанию это
    имя файла без `.yaml`; спек задачи лежит в её каталоге
    (`<каталог задачи>/spec.yaml`, см. `load_problem_spec`), и `problem_id`
    сверяется с именем каталога.
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
        raise SpecValidationError(
            path, ["spec root must be a YAML mapping"]
        )

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
            [
                f"problem_id ('{spec.problem_id}') does not match {source}, "
                "see SPEC_FORMAT.md"
            ],
        )

    return spec


# Спек задачи в её каталоге: `outputs/<problem_id>/spec.yaml` (как у учебных
# задач `templates/examples/<problem_id>/spec.yaml`).
SPEC_FILENAME = "spec.yaml"

# Шаблон черновика спека для `orchestrator <problem_id> init` (путь
# относительно templates_dir). Шагам не прикладывается.
SPEC_TEMPLATE = "common/spec_template.yaml"

# Значение `""` вместе с пробелами вокруг него — до комментария или конца строки.
_TEMPLATE_PROBLEM_ID = re.compile(r'^problem_id:( *"" *)(?=#|$)', re.MULTILINE)


class SpecInitError(Exception):
    """Черновик спека не создан: неверный `problem_id`, спек уже есть и т.п."""


def create_spec_draft(problem_id: str, *, outputs_dir: Path, templates_dir: Path) -> Path:
    """Создаёт каталог задачи `outputs_dir/<problem_id>/` (если его нет) и
    кладёт в него черновик `spec.yaml` из `SPEC_TEMPLATE` с подставленным
    `problem_id`; остальные поля остаются пустыми — их заполняет автор.

    Существующий `spec.yaml` не перезаписывается. Каталог задачи может уже
    существовать (например, после `polygon pull`) — тогда в нём просто
    появляется спек. Возвращает путь к созданному файлу.
    """
    if not PROBLEM_ID_PATTERN.match(problem_id):
        raise SpecInitError(
            f"problem_id '{problem_id}' must be ASCII "
            "(letters/digits/'-'/'_', starting with a letter), see SPEC_FORMAT.md"
        )

    template_path = Path(templates_dir) / SPEC_TEMPLATE
    try:
        template = template_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise SpecInitError(f"failed to read spec template {template_path}: {exc}") from exc

    # Подставляем значение в строку шаблона, а не через yaml.dump: так в
    # черновике остаются комментарии шаблона. Колонка комментария после
    # значения сохраняется, если `problem_id` в неё помещается.
    def substitute(match: re.Match) -> str:
        value = f" {problem_id} "
        return "problem_id:" + value.ljust(len(match.group(1)))

    draft, count = _TEMPLATE_PROBLEM_ID.subn(substitute, template, count=1)
    if count != 1:
        raise SpecInitError(
            f"spec template {template_path} has no 'problem_id: \"\"' line to fill in"
        )

    path = spec_path(problem_id, outputs_dir=outputs_dir)
    if path.exists():
        raise SpecInitError(f"{path} already exists, not overwriting it")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(draft, encoding="utf-8")
    return path


def spec_path(problem_id: str, *, outputs_dir: Path) -> Path:
    """Путь к спеку задачи: `outputs_dir/<problem_id>/spec.yaml`."""
    return Path(outputs_dir) / problem_id / SPEC_FILENAME


def load_problem_spec(problem_id: str, *, outputs_dir: Path) -> ProblemSpec:
    """Загружает спек задачи из её каталога (`spec_path`); `problem_id` в
    спеке должен совпадать с именем каталога. Ошибки — как у `load_spec`."""
    return load_spec(spec_path(problem_id, outputs_dir=outputs_dir), expected_id=problem_id)
