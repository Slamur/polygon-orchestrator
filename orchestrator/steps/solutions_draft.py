"""Шаг 4 — черновики авторских решений (ok/wa/tl), CLAUDE.md, "Роль каждого
генеративного шага", пункт 4.
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
from orchestrator.steps.generators_and_script import CONSTRAINTS_ARTIFACT

STEP_NAME = "solutions_draft"

# Optional[list[...]]-поле секции solutions, по которому user.md.j2 делает
# {% for %} без проверки на null (см. base.with_default_lists).
_LIST_FIELDS = ["known_wrong_approaches"]


def run_step(
    problem_id: str,
    spec: ProblemSpec,
    upstream_artifacts: Optional[dict[str, Any]] = None,
    *,
    prompts_dir: Path = PROMPTS_DIR,
    outputs_dir: Path = OUTPUTS_DIR,
    templates_dir: Path = TEMPLATES_DIR,
) -> StepResult:
    """Прогоняет шаг `solutions_draft` для `problem_id`.

    Вход: секция `solutions` спека (`algorithm_hint`, `wanted_verdicts`,
    `languages`, `known_wrong_approaches`) плюс зафиксированная асимптотика
    из `constraints.intended_complexity` — она уже провалидирована как
    непустая (`orchestrator.spec.Constraints`) и не пересчитывается заново,
    только передаётся в промпт как заданный факт. Апстрим-артефакт шага
    `constraints_pick` (`upstream_artifacts["constraints.yaml"]`) сюда же
    входит хешем на кэш (см. CLAUDE.md, "Кэширование по хешу спека": вход
    шага 4 — секция `solutions` + `constraints.yaml`), хотя сам промпт его
    текстом не использует — только `constraints.intended_complexity` из
    спека.

    Требует, чтобы в спеке была включена опциональная секция `solutions`
    (`spec.solutions is not None`) — без неё черновики решений не
    заказывались автором, и шаг не имеет смысла запускать.

    Модель обязана взять алгоритм ТОЛЬКО из `solutions.algorithm_hint` и не
    имеет права изобретать другой алгоритм или менять заявленную
    асимптотику (CLAUDE.md, "Роль каждого генеративного шага", п.4: "черновики
    решений с заявленными вердиктами... это не финальные решения", и
    `docs/PROMPTS.md`, правило 1 для `solutions_draft`: "не изобретай другой
    алгоритм"). Если `algorithm_hint` пуст или слишком расплывчат — `status:
    uncertain`, и вызов завершится `StepUncertainError` без записи файлов, а
    не попыткой домыслить алгоритм по легенде/условию.

    При успехе пишет файлы решений в `outputs/<problem_id>/solutions/`.
    """
    if spec.solutions is None:
        raise ValueError(
            f"Шаг '{STEP_NAME}' для '{problem_id}': в спеке нет секции "
            "'solutions' — черновики решений не заказаны, шаг не запускается "
            "(см. SPEC_FORMAT.md, секция 4 — опциональна)"
        )
    if not upstream_artifacts or CONSTRAINTS_ARTIFACT not in upstream_artifacts:
        raise ValueError(
            f"Шаг '{STEP_NAME}' для '{problem_id}': в upstream_artifacts нет "
            f"'{CONSTRAINTS_ARTIFACT}' — сначала должен успешно отработать "
            "шаг 'constraints_pick', заявленная асимптотика фиксируется там"
        )
    constraints_yaml = upstream_artifacts[CONSTRAINTS_ARTIFACT]

    section_data = with_default_lists(spec.solutions.model_dump(mode="json"), _LIST_FIELDS)
    context = {
        "problem_id": problem_id,
        "solutions": section_data,
        "constraints": spec.constraints.model_dump(mode="json"),
    }
    input_hash = compute_input_hash(
        STEP_NAME, section_data, upstream_artifacts={CONSTRAINTS_ARTIFACT: constraints_yaml}
    )

    def resolve_artifact_path(filename: str) -> Path:
        return Path(outputs_dir) / problem_id / "solutions" / filename

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
