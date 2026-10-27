"""Шаг 4 — черновики авторских решений (ok/wa/tl), CLAUDE.md, "Роль каждого
генеративного шага", пункт 4.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Optional

from orchestrator.cache import compute_input_hash as _cache_compute_input_hash
from orchestrator.cache import compute_input_values
from orchestrator.model_contracts import ModelResponse
from orchestrator.references import reference_documents
from orchestrator.spec import SPEC_REFERENCES_FIELD, ProblemSpec, split_solution_stem
from orchestrator.steps.base import (
    INTERACTIVE_TUTORIAL,
    PROBLEMS_DIR,
    PROMPTS_DIR,
    TEMPLATES_DIR,
    StepResult,
    StepRevision,
    directory_artifacts,
    artifact_filename,
    run_generative_step,
    statement_io_section,
    with_default_lists,
)
from orchestrator.steps.generators_and_script import (
    CONSTRAINTS_ARTIFACT,
    _require_constraints_yaml,
)

STEP_NAME = "solutions_draft"

# Optional[list[...]]-поля секции solutions, по которым user.md.j2 делает
# {% for %} без проверки на null (см. base.with_default_lists).
_LIST_FIELDS = ["alternative_approaches", "known_wrong_approaches"]

# Каталог решений внутри каталога-референса, см. `extra_context_documents`.
SOLUTIONS_SUBDIR = "solutions"

# Скелеты решений в `templates/` — `<verdict>_<description>.<language>`
# (`ok.cpp`, `tl_ok_slowio.java`): ввод-вывод и структура решения без
# алгоритма; язык скелета — его расширение (как и в именах решений, совпадает
# с языком), см. `extra_context_documents`.
SOLUTION_TEMPLATES_SUBDIR = "solutions"

# `solutions.languages` — свободные строки; приводим распространённые
# варианты записи к тому, как язык назван в именах файлов шаблонов.
_LANGUAGE_ALIASES = {"c++": "cpp", "python": "py", "python3": "py"}

# TL/ML в `constraints.yaml`: секция `limits` и пара ключей, которую
# `set_constraints` принимает и прямо в корне документа. Шагу они не
# передаются, см. `_without_limits`.
_LIMITS_BLOCK_RE = re.compile(r"limits\s*:")
_LIMITS_KEY_RE = re.compile(r"(time_limit_seconds|memory_limit_mb)\s*:")


def _require_solutions_section(problem_id: str, spec: ProblemSpec) -> None:
    if spec.solutions is None:
        raise ValueError(
            f"Step '{STEP_NAME}' for '{problem_id}': the spec has no "
            "'solutions' section — solution drafts not requested, the step is not run "
            "(see SPEC_FORMAT.md, section 4 — optional)"
        )


def _without_limits(constraints_yaml: str) -> str:
    """`constraints.yaml` без TL/ML: секции `limits` верхнего уровня (вместе
    с вложенными строками) и ключей `time_limit_seconds`/`memory_limit_mb`
    в корне. Решения пишутся по асимптотике и диапазонам переменных: TL
    модель проверить не может, а его подгонка (в том числе на Polygon) не
    должна инвалидировать решения. Вырезается текстом, а не через YAML,
    чтобы остальной файл, с комментариями, модель видела как есть.
    """
    kept: list[str] = []
    in_limits = False
    for line in constraints_yaml.splitlines(keepends=True):
        if in_limits and (line[:1] in (" ", "\t") or not line.strip()):
            continue
        in_limits = bool(_LIMITS_BLOCK_RE.match(line))
        if in_limits or _LIMITS_KEY_RE.match(line):
            continue
        kept.append(line)
    return "".join(kept)


def _build_context_sections(spec: ProblemSpec) -> dict[str, Any]:
    """Секции спека для рендера промпта И для входа хеша кэша — единая точка
    сборки, чтобы они не разошлись (CLAUDE.md, "Кэширование по хешу спека"):
    секция `solutions`, `constraints.intended_complexity` и условие из
    `statement_draft` целиком в части ввода-вывода (`formal_input_sketch`,
    `formal_output_sketch`, `indexing`, примеры с ответами, легенда —
    `base.statement_io_section`). Без последнего модели неоткуда взять
    формат ввода-вывода своей задачи, кроме формата учебной из
    `spec.yaml`.
    """
    return {
        "solutions": with_default_lists(
            spec.solutions.model_dump(mode="json", exclude=SPEC_REFERENCES_FIELD), _LIST_FIELDS
        ),
        "constraints": {"intended_complexity": spec.constraints.intended_complexity},
        "statement": statement_io_section(spec, include_output_and_legend=True),
    }


def compute_input_hash(
    problem_id: str,
    spec: ProblemSpec,
    upstream_artifacts: Optional[dict[str, Any]] = None,
) -> str:
    """Хеш входа шага без вызова модели, см. `statement_draft.compute_input_hash`
    и CLAUDE.md, "Кэширование по хешу спека". Как и `run_step`, требует
    секцию `solutions` в спеке и `upstream_artifacts["constraints.yaml"]`.
    Вход — `_build_context_sections` + содержимое `constraints.yaml` без
    TL/ML (`_without_limits`).
    """
    return _cache_compute_input_hash(STEP_NAME, *hash_inputs(problem_id, spec, upstream_artifacts))


def hash_inputs(
    problem_id: str,
    spec: ProblemSpec,
    upstream_artifacts: Optional[dict[str, Any]] = None,
) -> tuple[Any, Optional[dict[str, Any]]]:
    """Что входит в хеш входа шага: `(секция спека, апстрим-артефакты)` —
    общий источник для `compute_input_hash` и хешей по частям
    (`cache.compute_input_values`), чтобы они не разошлись."""
    _require_solutions_section(problem_id, spec)
    constraints_yaml = _without_limits(
        _require_constraints_yaml(STEP_NAME, problem_id, upstream_artifacts)
    )
    return _build_context_sections(spec), {CONSTRAINTS_ARTIFACT: constraints_yaml}


def primary_artifact_path(
    problem_id: str, spec: ProblemSpec, *, problems_dir: Path = PROBLEMS_DIR
) -> Path:
    """Каталог с черновиками решений — точный список файлов модель выбирает
    сама (зависит от `solutions.wanted_verdicts`), поэтому в отличие от
    остальных шагов здесь нет фиксированного имени файла: `pipeline.py`
    считает кэш валидным, только если каталог существует и не пуст.
    """
    return Path(problems_dir) / problem_id / "solutions"


def current_artifacts(
    problem_id: str, spec: ProblemSpec, *, problems_dir: Path = PROBLEMS_DIR
) -> dict[str, Path]:
    """Текущие файлы шага на диске: имя артефакта (как его вернула бы
    модель) -> путь — для режима доработки (`base.StepRevision`). Все файлы
    `solutions/`, кроме помеченных как удалённые."""
    return directory_artifacts(Path(problems_dir) / problem_id / SOLUTIONS_SUBDIR)


def extra_context_documents(
    spec: ProblemSpec, *, templates_dir: Path = TEMPLATES_DIR
) -> Optional[list[str]]:
    """Образцы для конкретного вызова — файлы, которые этому шагу дают
    референсы спека (общие `references` и `solutions.references`, см.
    `orchestrator/references.py`). Из `solutions/` каталога-референса берутся
    только решения на языках, заказанных в `solutions.languages`, — образец
    структуры решения и ввода/вывода для каждого языка (см.
    `prompts/solutions_draft/system.md`). Язык решения — второй компонент
    имени файла по схеме `<verdict>_<language>_...` (`ok_cpp_prefs_draft.cpp`
    -> `cpp`); решения на незаказанных языках не прикладываются, чтобы не
    тянуть в контекст чужой стиль ввода/вывода.

    Заказанному языку, для которого референсы не дали ни одного
    `ok`-решения, вместо образца прикладываются все скелеты решений этого
    языка из `templates/solutions/*.<language>` (если они есть): то, как
    автор хочет видеть ввод-вывод на этом языке, задаётся и без
    референсов. Скелеты — перед файлами референсов. Интерактивной задаче
    первыми прикладываются правила `INTERACTIVE_TUTORIAL` (как у
    `statement_draft`). Если секции `solutions` нет — `None`: шаг
    всё равно не запускается.
    """
    if spec.solutions is None:
        return None

    languages = {_LANGUAGE_ALIASES.get(lang.lower(), lang.lower()) for lang in spec.solutions.languages}

    def keep(rel_path: str) -> bool:
        if not rel_path.startswith(f"{SOLUTIONS_SUBDIR}/"):
            return True
        return _solution_parts(Path(rel_path))[1] in languages

    rules = [str(Path(templates_dir) / INTERACTIVE_TUTORIAL)] if spec.interactive else []
    references = reference_documents(STEP_NAME, spec, keep=keep) or []
    covered = {
        language
        for verdict, language in map(_solution_parts, map(Path, references))
        if verdict == "ok"
    }
    templates = [
        str(path)
        for language in sorted(languages - covered)
        for path in sorted((Path(templates_dir) / SOLUTION_TEMPLATES_SUBDIR).glob(f"*.{language}"))
    ]
    return [*rules, *templates, *references] or None


def _solution_parts(path: Path) -> tuple[Optional[str], Optional[str]]:
    """(вердикт, язык) по имени решения `<verdict>_<language>_...`
    (`tl_ok_java_slowio_draft.java` -> `("tl_ok", "java")`); `None` там, где
    имя схеме не следует."""
    parts = split_solution_stem(path.stem)
    return (parts[0] if parts else None), (parts[1] if len(parts) >= 2 else None)


def run_step(
    problem_id: str,
    spec: ProblemSpec,
    upstream_artifacts: Optional[dict[str, Any]] = None,
    *,
    revision: Optional[StepRevision] = None,
    saved_response: Optional[ModelResponse] = None,
    prompts_dir: Path = PROMPTS_DIR,
    problems_dir: Path = PROBLEMS_DIR,
    templates_dir: Path = TEMPLATES_DIR,
) -> StepResult:
    """Прогоняет шаг `solutions_draft` для `problem_id`.

    Вход: секция `solutions` спека (`algorithm_hint`, `wanted_verdicts`,
    `languages`, `alternative_approaches`, `known_wrong_approaches`) плюс зафиксированная асимптотика
    из `constraints.intended_complexity` — она уже провалидирована как
    непустая (`orchestrator.spec.Constraints`) и не пересчитывается заново,
    только передаётся в промпт как заданный факт. Плюс условие из
    `statement_draft` в части ввода-вывода (формат ввода и вывода,
    индексация, примеры с ответами, легенда — см. `_build_context_sections`)
    и полный текст апстрим-артефакта `constraints_pick`
    (`upstream_artifacts["constraints.yaml"]`: диапазоны переменных, группы,
    гарантии валидатора; без TL/ML — `_without_limits`) — без них модель
    вынуждена угадывать формат ввода-вывода и типы величин.

    Требует, чтобы в спеке была включена опциональная секция `solutions`
    (`spec.solutions is not None`) — без неё черновики решений не
    заказывались автором, и шаг не имеет смысла запускать.

    Модель обязана взять алгоритм ТОЛЬКО из `solutions.algorithm_hint` (и
    `solutions.alternative_approaches` для дополнительных `ok`-решений) и не
    имеет права изобретать другой алгоритм или менять заявленную
    асимптотику (CLAUDE.md, "Роль каждого генеративного шага", п.4: "черновики
    решений с заявленными вердиктами... это не финальные решения", и
    `docs/PROMPTS.md`, правило 1 для `solutions_draft`: "не изобретай другой
    алгоритм"). Если `algorithm_hint` пуст или слишком расплывчат — `status:
    uncertain`, и вызов завершится `StepUncertainError` без записи файлов, а
    не попыткой домыслить алгоритм по легенде/условию.

    При успехе пишет файлы решений в `problems/<problem_id>/solutions/`.
    """
    _require_solutions_section(problem_id, spec)
    constraints_yaml = _without_limits(
        _require_constraints_yaml(STEP_NAME, problem_id, upstream_artifacts)
    )

    context = {
        "problem_id": spec.problem_id,
        **_build_context_sections(spec),
        "constraints_pick_result": {"artifacts": {CONSTRAINTS_ARTIFACT: constraints_yaml}},
    }
    input_hash = compute_input_hash(problem_id, spec, upstream_artifacts)
    input_values = compute_input_values(*hash_inputs(problem_id, spec, upstream_artifacts))

    def resolve_artifact_path(filename: str) -> Path:
        return (
            Path(problems_dir)
            / problem_id
            / SOLUTIONS_SUBDIR
            / artifact_filename(filename, SOLUTIONS_SUBDIR)
        )

    def validate_artifacts(artifacts: dict[str, str]) -> None:
        for filename in artifacts:
            resolve_artifact_path(filename)

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
        # Файлы независимы: генераторы/скрипт и решения применяются по одному.
        partial_apply=True,
        prompts_dir=prompts_dir,
        problems_dir=problems_dir,
        templates_dir=templates_dir,
        extra_context_documents=extra_context_documents(spec, templates_dir=templates_dir),
    )
