"""Шаг 2 — фиксация ограничений (N/TL/ML, тестовые группы), CLAUDE.md,
"Роль каждого генеративного шага", пункт 2.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from orchestrator.cache import compute_input_hash as _cache_compute_input_hash
from orchestrator.spec import ProblemSpec
from orchestrator.steps.base import (
    OUTPUTS_DIR,
    PROMPTS_DIR,
    TEMPLATES_DIR,
    StepResult,
    run_generative_step,
    with_default_lists,
)

STEP_NAME = "constraints_pick"

# Optional[list[...]]-поля секции constraints, по которым user.md.j2 делает
# {% for %} без проверки на null (см. base.with_default_lists).
_LIST_FIELDS = ["special_guarantees", "test_groups_hint", "uncertain_points"]


def compute_input_hash(
    problem_id: str,
    spec: ProblemSpec,
    upstream_artifacts: Optional[dict[str, Any]] = None,
) -> str:
    """Хеш входа шага без вызова модели — см. `statement_draft.compute_input_hash`
    и CLAUDE.md, "Кэширование по хешу спека". `problem_id`/`upstream_artifacts`
    не используются (шаг зависит только от секции `constraints`), присутствуют
    ради единой сигнатуры у всех пяти шагов.
    """
    section_data = with_default_lists(
        spec.constraints.model_dump(mode="json"), _LIST_FIELDS
    )
    return _cache_compute_input_hash(STEP_NAME, section_data)


def primary_artifact_path(
    problem_id: str, spec: ProblemSpec, *, outputs_dir: Path = OUTPUTS_DIR
) -> Path:
    """Путь к основному артефакту шага, см. `statement_draft.primary_artifact_path`."""
    return Path(outputs_dir) / problem_id / "constraints.yaml"


def extra_context_documents(spec: ProblemSpec) -> Optional[list[str]]:
    """См. `statement_draft.extra_context_documents` — у `constraints_pick`
    тоже нет документов контекста, специфичных для конкретного вызова.
    """
    return None


def run_step(
    problem_id: str,
    spec: ProblemSpec,
    upstream_artifacts: Optional[dict[str, Any]] = None,
    *,
    prompts_dir: Path = PROMPTS_DIR,
    outputs_dir: Path = OUTPUTS_DIR,
    templates_dir: Path = TEMPLATES_DIR,
) -> StepResult:
    """Прогоняет шаг `constraints_pick` для `problem_id`.

    Вход: секция `constraints` спека — в первую очередь
    `intended_complexity` и `complexity_reasoning`, а также `variables`,
    `special_guarantees`, `test_groups_hint`, лимиты и `uncertain_points`.

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

    При успехе пишет `outputs/<problem_id>/constraints.yaml`.

    `upstream_artifacts` не используется: `constraints_pick` зависит только
    от секции `constraints` спека (CLAUDE.md, "Кэширование по хешу спека").
    """
    section_data = with_default_lists(
        spec.constraints.model_dump(mode="json"), _LIST_FIELDS
    )
    context = {"problem_id": problem_id, "constraints": section_data}
    input_hash = compute_input_hash(problem_id, spec, upstream_artifacts)

    def resolve_artifact_path(filename: str) -> Path:
        return Path(outputs_dir) / problem_id / filename

    return run_generative_step(
        step_name=STEP_NAME,
        problem_id=problem_id,
        context=context,
        input_hash=input_hash,
        resolve_artifact_path=resolve_artifact_path,
        prompts_dir=prompts_dir,
        outputs_dir=outputs_dir,
        templates_dir=templates_dir,
    )
