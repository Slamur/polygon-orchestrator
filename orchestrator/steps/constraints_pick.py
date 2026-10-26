"""Шаг 2 — фиксация ограничений (N/TL/ML, тестовые группы) + validator.cpp,
CLAUDE.md, "Роль каждого генеративного шага", пункт 2.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from orchestrator.cache import compute_input_hash as _cache_compute_input_hash
from orchestrator.cache import compute_input_values
from orchestrator.model_contracts import ModelResponse
from orchestrator.references import reference_documents
from orchestrator.spec import SPEC_REFERENCES_FIELD, ProblemSpec
from orchestrator.steps.base import (
    PROBLEMS_DIR,
    PROMPTS_DIR,
    TEMPLATES_DIR,
    StepResult,
    StepRevision,
    existing_artifacts,
    run_generative_step,
    statement_io_section,
    with_default_lists,
)

STEP_NAME = "constraints_pick"

# Optional[list[...]]-поля секции constraints, по которым user.md.j2 делает
# {% for %} без проверки на null (см. base.with_default_lists).
_LIST_FIELDS = ["special_guarantees", "test_groups_hint", "uncertain_points"]


def _build_context_sections(spec: ProblemSpec) -> dict[str, Any]:
    """Секции спека для рендера промпта И для входа хеша кэша — единая точка
    сборки, чтобы они не разошлись. Помимо секции `constraints` — формат
    ввода из `statement_draft` (`base.statement_io_section`, без легенды и
    вывода): валидатор читает вход токен за токеном, и порядок/разбивку по
    строкам ему не из чего взять, кроме `formal_input_sketch`.
    """
    return {
        "constraints": with_default_lists(
            spec.constraints.model_dump(mode="json", exclude=SPEC_REFERENCES_FIELD), _LIST_FIELDS
        ),
        "statement": statement_io_section(spec, include_output_and_legend=False),
    }


def compute_input_hash(
    problem_id: str,
    spec: ProblemSpec,
    upstream_artifacts: Optional[dict[str, Any]] = None,
) -> str:
    """Хеш входа шага без вызова модели — см. `statement_draft.compute_input_hash`
    и CLAUDE.md, "Кэширование по хешу спека". Вход — секция `constraints` +
    формат ввода из `statement_draft` (см. `_build_context_sections`): правка
    формата входа должна инвалидировать и `validator.cpp`.
    `problem_id`/`upstream_artifacts` не используются, присутствуют ради
    единой сигнатуры у всех шагов.
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
    return _build_context_sections(spec), None


def primary_artifact_path(
    problem_id: str, spec: ProblemSpec, *, problems_dir: Path = PROBLEMS_DIR
) -> Path:
    """Путь к основному артефакту шага, см. `statement_draft.primary_artifact_path`."""
    return Path(problems_dir) / problem_id / "constraints.yaml"


def current_artifacts(
    problem_id: str, spec: ProblemSpec, *, problems_dir: Path = PROBLEMS_DIR
) -> dict[str, Path]:
    """Текущие файлы шага на диске: имя артефакта (как его вернула бы
    модель) -> путь — для режима доработки (`base.StepRevision`). `constraints.yaml` и
    `validator.cpp`."""
    problem_dir = Path(problems_dir) / problem_id
    return existing_artifacts(
        {name: problem_dir / name for name in ("constraints.yaml", "validator.cpp")}
    )


def extra_context_documents(
    spec: ProblemSpec, *, templates_dir: Path = TEMPLATES_DIR
) -> Optional[list[str]]:
    """Образцы для конкретного вызова — файлы, которые этому шагу дают
    референсы спека (общие `references` и `constraints.references`, см.
    `orchestrator/references.py`). Единая сигнатура со всеми шагами нужна
    `pipeline.py`, чтобы считать `compute_prompt_hash` одинаково.
    """
    return reference_documents(STEP_NAME, spec)


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
    """Прогоняет шаг `constraints_pick` для `problem_id`.

    Вход: секция `constraints` спека — в первую очередь
    `intended_complexity` и `complexity_reasoning`, а также `variables`,
    `special_guarantees`, `test_groups_hint`, лимиты и `uncertain_points`.
    Плюс формат ввода из `statement_draft` (`formal_input_sketch`,
    `indexing`, input примеров) — источник порядка чтения для
    `validator.cpp`; легенда не передаётся.

    Модель **не имеет права** сама выводить асимптотику решения из легенды
    задачи — единственный источник асимптотики это
    `constraints.intended_complexity`/`complexity_reasoning`, данные автором
    явно (CLAUDE.md, "Роль каждого генеративного шага", п.2). Пустой/не
    сформулированный `intended_complexity` уже отклоняется на уровне
    `orchestrator.spec.load_spec` (обязательное непустое поле), но если
    промпт всё равно не может согласовать ограничения с этим обоснованием —
    он обязан вернуть `status: uncertain`, а не догадываться, и этот вызов
    завершится `StepUncertainError` без записи файлов (см. "Правило
    эскалации при неуверенности"). Для значений, не заданных автором явно
    (например, `variables[].max: null` или `time_limit_seconds: null`),
    допускается только `status: proposed` с обоснованием в `notes`, никогда
    не `confirmed`.

    Помимо `constraints.yaml`, этот же вызов модели пишет `validator.cpp` —
    реализацию секции `validator` (`constants`/`checks`) из `constraints.yaml`
    1-в-1, по образцу `validator.cpp` учебной задачи (CLAUDE.md, "Роль каждого
    генеративного шага", п.2). Это не отдельный шаг: `status: uncertain`
    останавливает оба файла разом, `validator.cpp` не пишется без
    `constraints.yaml` и наоборот.

    При успехе пишет `problems/<problem_id>/constraints.yaml` и
    `problems/<problem_id>/validator.cpp`; `validator.cpp` дополнительно
    прогоняется через `compile_check` (как и любой другой `.cpp`-артефакт —
    см. `run_generative_step`).

    `upstream_artifacts` не используется: `constraints_pick` зависит только
    от спека (CLAUDE.md, "Кэширование по хешу спека").
    """
    context = {"problem_id": problem_id, **_build_context_sections(spec)}
    input_hash = compute_input_hash(problem_id, spec, upstream_artifacts)
    input_values = compute_input_values(*hash_inputs(problem_id, spec, upstream_artifacts))

    def resolve_artifact_path(filename: str) -> Path:
        return Path(problems_dir) / problem_id / filename

    return run_generative_step(
        step_name=STEP_NAME,
        problem_id=problem_id,
        context=context,
        input_hash=input_hash,
        input_values=input_values,
        revision=revision,
        saved_response=saved_response,
        resolve_artifact_path=resolve_artifact_path,
        prompts_dir=prompts_dir,
        problems_dir=problems_dir,
        templates_dir=templates_dir,
        extra_context_documents=extra_context_documents(spec, templates_dir=templates_dir),
    )
