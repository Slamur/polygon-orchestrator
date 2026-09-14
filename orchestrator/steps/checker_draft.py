"""Шаг 5 (опциональный) — черновик кастомного чекера, CLAUDE.md, "Роль
каждого генеративного шага", пункт 5.
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
)

STEP_NAME = "checker_draft"


def _require_custom_checker(problem_id: str, spec: ProblemSpec) -> None:
    if spec.checker.custom_needed is not True:
        raise ValueError(
            f"Шаг '{STEP_NAME}' для '{problem_id}': в спеке 'checker.custom_needed' "
            "не True — используется стандартный чекер, черновик не заказан, шаг не "
            "запускается (см. CLAUDE.md, 'Роль каждого генеративного шага', пункт 5)"
        )


def compute_input_hash(
    problem_id: str,
    spec: ProblemSpec,
    upstream_artifacts: Optional[dict[str, Any]] = None,
) -> str:
    """Хеш входа шага без вызова модели, см. `constraints_pick.compute_input_hash`
    и CLAUDE.md, "Кэширование по хешу спека". Как и `run_step`, требует
    `checker.custom_needed: true` в спеке. `upstream_artifacts` не
    используется — шаг ни от чего не зависит, кроме
    `checker.custom_comparison_notes`, присутствует ради единой сигнатуры
    у всех пяти шагов.
    """
    _require_custom_checker(problem_id, spec)
    return _cache_compute_input_hash(STEP_NAME, spec.checker.custom_comparison_notes)


def primary_artifact_path(
    problem_id: str, spec: ProblemSpec, *, outputs_dir: Path = OUTPUTS_DIR
) -> Path:
    """Путь к основному артефакту шага — в отличие от `solutions_draft`
    (каталог с произвольным набором файлов), у `checker_draft` ровно один
    файл с фиксированным именем, см. `constraints_pick.primary_artifact_path`.
    """
    return Path(outputs_dir) / problem_id / "checker.cpp"


def extra_context_documents(spec: ProblemSpec) -> Optional[list[str]]:
    """См. `constraints_pick.extra_context_documents` — у `checker_draft`
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
    """Прогоняет шаг `checker_draft` для `problem_id`.

    Вход: секция `checker` спека — единственное поле, которое реально
    определяет содержимое, это `checker.custom_comparison_notes`
    (самодостаточное текстовое описание формата ответа и правила сравнения,
    см. CLAUDE.md, "Роль каждого генеративного шага", пункт 5). Модель не
    имеет права выводить формат ответа/правило сравнения из легенды или
    условия — только из этого поля; если оно неполно, шаг обязан вернуть
    `status: uncertain`, и этот вызов завершится `StepUncertainError` без
    записи файлов.

    Требует, чтобы `spec.checker.custom_needed is True` — при стандартном
    чекере (`custom_needed: false`) черновик не заказан, и шаг не имеет
    смысла запускать (в отличие от `solutions_draft`, где отсутствует целая
    опциональная секция, здесь секция `checker` обязательна всегда, а
    "шаг не нужен" выражается конкретным значением `custom_needed: false`).

    При успехе пишет `outputs/<problem_id>/checker.cpp`.

    `upstream_artifacts` не используется: `checker_draft` ни от чего не
    зависит (CLAUDE.md, "Кэширование по хешу спека").
    """
    _require_custom_checker(problem_id, spec)

    context = {
        "problem_id": problem_id,
        "checker": spec.checker.model_dump(mode="json"),
    }
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
