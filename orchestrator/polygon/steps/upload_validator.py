"""Polygon-шаг 4 — загрузка `outputs/<problem_id>/validator.cpp` (результат
генеративного шага `constraints_pick`) в Polygon как source-файла
(`problem.saveFile`, `type=source`) и назначение его валидатором задачи
(`problem.setValidator`).

`sourceType` в `problem.saveFile` не передаётся — по описанию метода он
опционален, и предполагается, что Polygon определит язык по расширению
`.cpp`. После первого реального вызова стоит проверить во вкладке Files
UI Polygon, что у файла выставлен компилятор C++; если нет — передавать
`sourceType` явно (значение взять из выпадающего списка в UI).

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
from orchestrator.polygon.sync import VALIDATOR_FILE_KEY, VALIDATOR_KEY, digest

STEP_NAME = "upload_validator"

_VALIDATOR_FILENAME = "validator.cpp"


def _execute(ctx: StepContext, client: PolygonClient) -> StepResult:
    """Загружает `validator.cpp` и назначает его валидатором (`ctx.spec` не
    используется), если файл или назначенный валидатор отличаются от базы
    синхронизации. Отличие любого из двух — оба вызова: после `polygon pull`
    назначенным может оказаться файл с другим именем, и на Polygon может не
    быть `validator.cpp` вовсе."""
    polygon_id = require_polygon_state(ctx).polygon_id
    content = _read_validator(ctx.problem_id, ctx.outputs_dir)
    items = _items(content)
    if not changed_items(ctx, items):
        return StepResult(NO_CHANGES_MESSAGE)

    client.call(
        "problem.saveFile",
        {
            "problemId": str(polygon_id),
            "type": "source",
            "name": _VALIDATOR_FILENAME,
            "file": content,
        },
    )
    client.call(
        "problem.setValidator",
        {"problemId": str(polygon_id), "validator": _VALIDATOR_FILENAME},
    )
    return StepResult(
        f"uploaded and set {_VALIDATOR_FILENAME} as validator on Polygon id={polygon_id}", items
    )


def _compute_status(problem_id: str, outputs_dir: Path) -> tuple[str, str]:
    """По базе синхронизации (`sync_status`)."""
    return sync_status(
        problem_id,
        outputs_dir,
        key_prefix=VALIDATOR_KEY,
        read_items=lambda: _items(_read_validator(problem_id, outputs_dir)),
    )


def _items(content: bytes) -> dict[str, str]:
    return {VALIDATOR_FILE_KEY: digest(content), VALIDATOR_KEY: digest(_VALIDATOR_FILENAME)}


def _read_validator(problem_id: str, outputs_dir: Path) -> bytes:
    validator_path = Path(outputs_dir) / problem_id / _VALIDATOR_FILENAME
    if not validator_path.exists():
        raise PolygonStepError(
            f"'{problem_id}': {validator_path} not found — first run "
            f"the generative step constraints_pick (`orchestrator llm generate {problem_id} "
            "--step constraints_pick`)"
        )

    return validator_path.read_bytes()


STEP = PolygonStep(
    name=STEP_NAME,
    check_done=never_done,
    execute=_execute,
    compute_status=_compute_status,
)
