"""Шаг 1 — черновик условия задачи в LaTeX (CLAUDE.md, "Роль каждого
генеративного шага", пункт 1).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from orchestrator.cache import compute_input_hash
from orchestrator.spec import ProblemSpec
from orchestrator.steps.base import (
    OUTPUTS_DIR,
    PROMPTS_DIR,
    TEMPLATES_DIR,
    StepResult,
    run_generative_step,
    with_default_lists,
)

STEP_NAME = "statement_draft"

# Единственное Optional[list[...]]-поле секции, по которому user.md.j2
# делает {% for %} без проверки на null (см. base.with_default_lists).
_LIST_FIELDS = ["known_ambiguities"]


def run_step(
    problem_id: str,
    spec: ProblemSpec,
    upstream_artifacts: Optional[dict[str, Any]] = None,
    *,
    prompts_dir: Path = PROMPTS_DIR,
    outputs_dir: Path = OUTPUTS_DIR,
    templates_dir: Path = TEMPLATES_DIR,
) -> StepResult:
    """Прогоняет шаг `statement_draft` для `problem_id`.

    Вход: секция `statement_draft` спека (легенда, формальные черновики
    ввода/вывода, индексация, `sample_examples`, `known_ambiguities`) —
    больше ничего из спека этому шагу не передаётся.

    Шагу запрещено додумывать асимптотику решения по легенде задачи — это
    исключительная зона шага `constraints_pick` (CLAUDE.md, "Роль каждого
    генеративного шага", п.1: "модель **не имеет права** сама придумывать
    асимптотику по легенде"). Если в спеке не хватает данных для формального
    описания ввода/вывода, индексации или точности вещественного ответа,
    промпт обязан вернуть `status: uncertain`, и этот вызов завершится
    `StepUncertainError`, не записав ни одного файла (см. "Правило эскалации
    при неуверенности").

    При успехе пишет `outputs/<problem_id>/statement.tex`.

    `upstream_artifacts` не используется: `statement_draft` не зависит от
    результатов других шагов (см. CLAUDE.md, "Кэширование по хешу спека" —
    для этого шага апстрим-артефактов в хеше нет). Параметр присутствует
    только ради общей сигнатуры `run_step(problem_id, spec,
    upstream_artifacts)` у всех четырёх шагов.
    """
    section_data = with_default_lists(
        spec.statement_draft.model_dump(mode="json"), _LIST_FIELDS
    )
    context = {"problem_id": problem_id, "statement_draft": section_data}
    input_hash = compute_input_hash(STEP_NAME, section_data)

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
