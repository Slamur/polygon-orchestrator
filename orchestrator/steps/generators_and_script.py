"""Шаг 3 — генераторы (C++/testlib) и FreeMarker test-script, CLAUDE.md,
"Роль каждого генеративного шага", пункт 3.
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

STEP_NAME = "generators_and_script"

# Имя артефакта шага constraints_pick, от которого зависит этот шаг
# (CLAUDE.md, "Кэширование по хешу спека": вход шага 3 — секция generation +
# содержимое outputs/<id>/constraints.yaml, полученное на шаге 2).
CONSTRAINTS_ARTIFACT = "constraints.yaml"

# Optional[list[...]]-поля секции generation, по которым user.md.j2 делает
# {% for %} без проверки на null (см. base.with_default_lists).
_LIST_FIELDS = ["generator_ideas", "base_template_refs"]


def run_step(
    problem_id: str,
    spec: ProblemSpec,
    upstream_artifacts: Optional[dict[str, Any]] = None,
    *,
    prompts_dir: Path = PROMPTS_DIR,
    outputs_dir: Path = OUTPUTS_DIR,
    templates_dir: Path = TEMPLATES_DIR,
) -> StepResult:
    """Прогоняет шаг `generators_and_script` для `problem_id`.

    Вход: секция `generation` спека (форма входных данных, идеи генераторов,
    `reuse_existing_generators`/`base_template_refs`, `script_style`) плюс
    зафиксированные ограничения из шага `constraints_pick` — они передаются
    через `upstream_artifacts["constraints.yaml"]` (содержимое файла,
    записанного шагом 2) и подставляются в промпт как единственный источник
    диапазонов N/TL/ML и тестовых групп.

    Шагу запрещено придумывать собственные диапазоны переменных или тестовые
    группы — они уже зафиксированы шагом `constraints_pick`, этот шаг обязан
    их использовать как есть, не выводя ограничения заново из спека
    (CLAUDE.md, "Роль каждого генеративного шага", п.3: "FreeMarker-скрипт
    должен использовать зафиксированные ограничения... из шага 2"). Если для
    генерации не хватает данных (например, не описана форма входных данных
    для многомерной структуры) — `status: uncertain`, и вызов завершится
    `StepUncertainError` без записи файлов.

    При успехе пишет сгенерированные `.cpp`-файлы в
    `outputs/<problem_id>/generators/`, а `test_script`/`test_script_groups`
    — в `outputs/<problem_id>/` (см. CLAUDE.md, "Структура каталогов").

    `upstream_artifacts` обязателен и должен содержать ключ
    `"constraints.yaml"` с содержимым артефакта шага `constraints_pick` —
    без него шаг не может быть запущен (нарушится п.3 выше), поэтому
    отсутствие ключа — ошибка вызывающего кода (обычно пайплайна,
    запустившего шаги не по порядку), а не `StepUncertainError`.
    """
    if not upstream_artifacts or CONSTRAINTS_ARTIFACT not in upstream_artifacts:
        raise ValueError(
            f"Шаг '{STEP_NAME}' для '{problem_id}': в upstream_artifacts нет "
            f"'{CONSTRAINTS_ARTIFACT}' — сначала должен успешно отработать "
            "шаг 'constraints_pick' (CLAUDE.md, 'Роль каждого генеративного "
            "шага', п.3)"
        )
    constraints_yaml = upstream_artifacts[CONSTRAINTS_ARTIFACT]

    section_data = with_default_lists(
        spec.generation.model_dump(mode="json"), _LIST_FIELDS
    )
    context = {
        "problem_id": problem_id,
        "generation": section_data,
        "constraints_pick_result": {"artifacts": {CONSTRAINTS_ARTIFACT: constraints_yaml}},
    }
    input_hash = compute_input_hash(
        STEP_NAME, section_data, upstream_artifacts={CONSTRAINTS_ARTIFACT: constraints_yaml}
    )

    def resolve_artifact_path(filename: str) -> Path:
        base = Path(outputs_dir) / problem_id
        if filename.endswith(".cpp"):
            return base / "generators" / filename
        return base / filename

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
