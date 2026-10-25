"""Загрузка и валидация спека задачи (`problems/<путь задачи>/spec.yaml`) по формату из docs/SPEC_FORMAT.md.

Модуль только парсит и проверяет спек — никаких вызовов модели и никакой
генерации артефактов здесь нет (см. CLAUDE.md).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Optional, Union

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from orchestrator.problem_paths import (
    PROBLEM_ID_PATTERN,
    SPEC_FILENAME,
    ProblemPathError,
    check_new_problem_location,
    parse_problem_address,
    problem_address,
    problem_id_for,
)

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
        "also be a directory laid out like problems/<problem_id>/), see SPEC_FORMAT.md"
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
# ускорения ввода-вывода (`templates/tutorials/solutions.md`).
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


# Спек задачи в её каталоге `SPEC_FILENAME` (`problems/<путь задачи>/spec.yaml`,
# как у учебных задач `examples/<путь задачи>/spec.yaml`), см.
# `orchestrator/problem_paths.py`.

# Шаблон черновика спека для `orchestrator <задача> init` (путь
# относительно templates_dir). Шагам не прикладывается.
SPEC_TEMPLATE = "spec_template.yaml"

# Строка `problem_id` верхнего уровня: значение (в кавычках или без) с
# пробелами вокруг него — до комментария или конца строки.
_PROBLEM_ID_LINE = re.compile(
    r"""^problem_id:(?P<value>[ \t]*(?:"[^"\n]*"|'[^'\n]*'|[^#\n]*?)[ \t]*)(?=#|$)""",
    re.MULTILINE,
)
# Строка `references` верхнего уровня; `rest` — значение и комментарий.
_REFERENCES_LINE = re.compile(r"^references:(?P<rest>[^\n]*)$", re.MULTILINE)
# Первая секция спека: перед ней (и перед её заголовком-комментарием)
# вставляется `references`, если в спеке его нет.
_FIRST_SECTION_LINE = re.compile(r"^statement_draft:", re.MULTILINE)


class SpecInitError(Exception):
    """Черновик спека не создан: неверный `problem_id`, спек уже есть и т.п."""


def set_problem_id(text: str, problem_id: str) -> str:
    """Текст спека с `problem_id` верхнего уровня, заменённым на `problem_id`.

    Правится строка текста, а не YAML через `yaml.dump`: так в спеке
    остаются комментарии. Колонка комментария после значения сохраняется,
    если `problem_id` в неё помещается. Нет такой строки — `SpecInitError`.
    """

    def substitute(match: re.Match) -> str:
        return "problem_id:" + f" {problem_id} ".ljust(len(match.group("value")))

    result, count = _PROBLEM_ID_LINE.subn(substitute, text, count=1)
    if count != 1:
        raise SpecInitError("the spec has no top-level 'problem_id:' line to fill in")
    return result


def _yaml_mapping(text: str) -> Optional[dict]:
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError:
        return None
    return data if isinstance(data, dict) else None


def _is_comment_or_blank(line: str) -> bool:
    return not line.strip() or line.lstrip().startswith("#")


def add_reference(text: str, reference: str) -> str:
    """Текст спека с `reference` в общих `references` (в корне спека), если
    его там ещё нет, — тоже правкой строк, с сохранением комментариев.

    Понимает список в одну строку (`references: []`, `["a"]`), блочный
    список (`references:` и элементы `- ...` под ним), `null` и отсутствие
    поля (тогда `references` вставляется перед секцией `statement_draft`
    и её заголовком-комментарием). Результат перепроверяется разбором YAML;
    не получилось — `SpecInitError`.
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
            # Блочный список: новый элемент — после последнего элемента
            # (с его отступом) или сразу под `references:`.
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
    """Что создаёт `init` — проверено до записи и до вызова модели
    (`prepare_spec_init`).

    `base_text` — основа спека с уже подставленным `problem_id`: шаблон
    `SPEC_TEMPLATE` или спек задачи-основы `reference` (она уже добавлена в
    `references`). `reference` — адрес задачи-основы (`problems/<путь>`,
    `examples/<путь>`); `None` — основа — шаблон.
    """

    problem_id: str
    path: Path
    base_text: str
    reference: Optional[str] = None


def _reference_spec_text(reference: str, *, base_dir: Path) -> tuple[str, str]:
    """(адрес, текст спека) задачи-основы `reference`, адрес — как в CLI."""
    try:
        root, reference_path = parse_problem_address(reference)
    except ProblemPathError as exc:
        raise SpecInitError(f"--ref: {exc}") from exc
    address = problem_address(root, reference_path)
    path = spec_path(reference_path, problems_dir=Path(base_dir) / root)
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
    reference: Optional[str] = None,
) -> SpecInit:
    """Проверяет, что в `problems_dir/<problem_path>/` можно создать спек, и
    собирает его основу (`SpecInit`); ничего не пишет.

    `problem_id` выводится из пути (`problem_id_for`: в `problems/` — имя
    каталога задачи, в `examples/` — путь через `--`). Существующий
    `spec.yaml` не перезаписывается. Каталог задачи может уже существовать
    (например, после `polygon pull`). Задача не может лежать внутри другой
    задачи или содержать задачи, а её `problem_id` не должен быть занят
    задачей в другом каталоге (`check_new_problem_location`).

    `reference` — адрес задачи-основы (как в CLI; корни `problems/` и
    `examples/` — соседи `problems_dir`): основа — её спек как есть, с
    комментариями, а сама она добавляется в общие `references`, чтобы её
    файлы стали образцами для шагов. Без `reference` основа — `SPEC_TEMPLATE`.
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
    """Пишет спек в `init.path`, создавая каталог задачи; возвращает путь."""
    init.path.parent.mkdir(parents=True, exist_ok=True)
    init.path.write_text(text, encoding="utf-8")
    return init.path


def create_spec_draft(
    problem_path: str,
    *,
    problems_dir: Path,
    templates_dir: Path,
    reference: Optional[str] = None,
) -> Path:
    """Создаёт каталог задачи `problems_dir/<problem_path>/` (если его нет) и
    кладёт в него спек без вызова модели: шаблон `SPEC_TEMPLATE` с
    подставленным `problem_id` (остальные поля пустые — их заполняет автор)
    или, с `reference`, копию спека задачи-основы с новым `problem_id` и
    ею самой в `references`. Проверки — как у `prepare_spec_init`.
    Возвращает путь к созданному файлу.
    """
    init = prepare_spec_init(
        problem_path, problems_dir=problems_dir, templates_dir=templates_dir, reference=reference
    )
    return write_spec(init, init.base_text)


def spec_validation_errors(path: Path, *, expected_id: str) -> list[str]:
    """Ошибки валидации спека (`load_spec`); пустой список — спек валиден.
    Для `init`: созданный спек — черновик и может быть ещё не заполнен."""
    try:
        load_spec(path, expected_id=expected_id)
    except SpecValidationError as exc:
        return exc.errors
    return []


def spec_path(problem_path: str, *, problems_dir: Path) -> Path:
    """Путь к спеку задачи: `problems_dir/<problem_path>/spec.yaml`."""
    return Path(problems_dir) / problem_path / SPEC_FILENAME


def load_problem_spec(problem_path: str, *, problems_dir: Path) -> ProblemSpec:
    """Загружает спек задачи из её каталога (`spec_path`); `problem_id` в
    спеке должен совпадать с выведенным из пути каталога (`problem_id_for`).
    Ошибки — как у `load_spec`."""
    return load_spec(
        spec_path(problem_path, problems_dir=problems_dir),
        expected_id=problem_id_for(problems_dir, problem_path),
    )
