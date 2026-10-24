"""Polygon-шаг — загрузка спека задачи (`problems/<путь задачи>/spec.yaml`)
в Polygon как aux-файла (`problem.saveFile`, `type=aux` — раздел
"Attachments" на странице Files).

Спек хранится на Polygon, чтобы `polygon pull` мог восстановить задачу
целиком, вместе со входом генеративных шагов. Загружается файл как есть,
побайтно (`ctx.spec` — уже разобранный спек — не используется: комментарии
и форматирование автора должны сохраниться). Генеративного шага-зависимости
нет — только `create_problem`.

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
from orchestrator.polygon.sync import SPEC_KEY, digest
from orchestrator.problem_paths import SPEC_FILENAME

STEP_NAME = "upload_spec"


def read_spec(problem_id: str, problems_dir: Path) -> bytes:
    path = Path(problems_dir) / problem_id / SPEC_FILENAME
    if not path.exists():
        raise PolygonStepError(f"{path} not found")
    return path.read_bytes()


def _execute(ctx: StepContext, client: PolygonClient) -> StepResult:
    polygon_id = require_polygon_state(ctx).polygon_id
    content = read_spec(ctx.problem_id, ctx.problems_dir)
    items = {SPEC_KEY: digest(content)}
    if not changed_items(ctx, items):
        return StepResult(NO_CHANGES_MESSAGE)

    client.call(
        "problem.saveFile",
        {
            "problemId": str(polygon_id),
            "type": "aux",
            "name": SPEC_FILENAME,
            "file": content,
        },
    )
    return StepResult(f"uploaded {SPEC_FILENAME} as attachment to Polygon id={polygon_id}", items)


def _compute_status(problem_id: str, problems_dir: Path) -> tuple[str, str]:
    return sync_status(
        problem_id,
        problems_dir,
        key_prefix=SPEC_KEY,
        read_items=lambda: {SPEC_KEY: digest(read_spec(problem_id, problems_dir))},
    )


STEP = PolygonStep(
    name=STEP_NAME,
    check_done=never_done,
    execute=_execute,
    compute_status=_compute_status,
)
