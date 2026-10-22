"""Polygon-шаг 8 — загрузка FreeMarker test-script'а (результат генеративного
шага `generators_and_script`) в Polygon (`problem.saveScript`,
`testset=tests`).

Путь к файлу скрипта берётся из
`orchestrator.steps.generators_and_script.primary_artifact_path` — единый
источник истины для имени (`test_script`/`test_script_groups` по
`generation.script_style`), здесь ветвление flat/groups не дублируется.

Ограничение MVP: `generation.script_style == "groups"` сознательно
отклоняется `PolygonStepError` до любой другой логики — groups-режим в
Polygon дополнительно требует `problem.enableGroups` (не спроектирован), а
groups-шаблонов в `templates/` сейчас нет. Явная ошибка лучше тихого
неверного поведения.

По смыслу шаг идёт после `upload_generators` — скрипт ссылается на
генераторы по имени, — хотя Polygon при сохранении скрипта этого не
проверяет.

Запускается через `orchestrator.polygon.steps.base.run_step(STEP, ...)`.
"""

from __future__ import annotations

from pathlib import Path

from orchestrator.polygon.client import PolygonClient
from orchestrator.polygon.steps.base import (
    NO_CHANGES_MESSAGE,
    PolygonStep,
    PolygonStepError,
    StepContext,
    StepResult,
    changed_items,
    never_done,
    require_polygon_state,
    sync_status,
)
from orchestrator.polygon.sync import SCRIPT_KEY, digest
from orchestrator.steps.generators_and_script import (
    primary_artifact_path as script_artifact_path,
)

STEP_NAME = "upload_test_script"

_TESTSET = "tests"

# Возможные имена файла скрипта — для `_compute_status`, где спека нет.
SCRIPT_FILENAMES = ("test_script", "test_script_groups")


def _execute(ctx: StepContext, client: PolygonClient) -> StepResult:
    """Загружает test-script, если он отличается от базы синхронизации.

    Все три проверки (`script_style != "groups"`, `create_problem`,
    наличие файла скрипта) — до первого сетевого вызова.
    """
    if ctx.spec.generation.script_style == "groups":
        raise PolygonStepError(
            f"'{ctx.problem_id}': script_style='groups' is not supported by this "
            "step (requires a separate problem.enableGroups, not implemented) — "
            "see the upload_test_script.py docstring"
        )

    polygon_id = require_polygon_state(ctx).polygon_id
    script_path = script_artifact_path(ctx.problem_id, ctx.spec, outputs_dir=ctx.outputs_dir)
    content = _read_script(ctx.problem_id, script_path)
    items = {SCRIPT_KEY: digest(content)}
    if not changed_items(ctx, items):
        return StepResult(NO_CHANGES_MESSAGE)

    client.call(
        "problem.saveScript",
        {
            "problemId": str(polygon_id),
            "testset": _TESTSET,
            "source": content.decode("utf-8"),
        },
    )
    return StepResult(
        f"uploaded test script ({script_path.name}) to Polygon id={polygon_id}", items
    )


def _compute_status(problem_id: str, outputs_dir: Path) -> tuple[str, str]:
    """По базе синхронизации (`sync_status`). Спек здесь недоступен,
    поэтому сравнивается первый существующий из `SCRIPT_FILENAMES`."""

    def read_items() -> dict[str, str]:
        problem_dir = Path(outputs_dir) / problem_id
        existing = [name for name in SCRIPT_FILENAMES if (problem_dir / name).exists()]
        script_path = problem_dir / (existing[0] if existing else SCRIPT_FILENAMES[0])
        return {SCRIPT_KEY: digest(_read_script(problem_id, script_path))}

    return sync_status(problem_id, outputs_dir, key_prefix=SCRIPT_KEY, read_items=read_items)


def _read_script(problem_id: str, script_path: Path) -> bytes:
    """Содержимое test-script. Путь передаётся снаружи: в `_execute` он
    зависит от `script_style` спека (`primary_artifact_path`), а в
    `_compute_status` спека нет."""
    if not script_path.exists():
        raise PolygonStepError(
            f"'{problem_id}': {script_path} not found — first run "
            f"the generative step generators_and_script (orchestrator llm generate "
            f"{problem_id} --step generators_and_script)"
        )
    return script_path.read_bytes()


STEP = PolygonStep(
    name=STEP_NAME,
    check_done=never_done,
    execute=_execute,
    compute_status=_compute_status,
)
