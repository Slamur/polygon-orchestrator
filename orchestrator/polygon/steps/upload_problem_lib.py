"""Polygon-шаг 3 — загрузка `templates/common/problem_lib.h` в Polygon как
resource-файла (`problem.saveFile`, `type=resource`).

`problem_lib.h` общий для всех задач и не генерируется под конкретную
задачу (CLAUDE.md, "Как использовать приложенные материалы"), поэтому
генеративного шага-зависимости нет — только `create_problem`.

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
from orchestrator.polygon.sync import PROBLEM_LIB_KEY, digest
from orchestrator.steps.base import COMMON_SUBDIR, TEMPLATES_DIR

STEP_NAME = "upload_problem_lib"

_RESOURCE_FILENAME = "problem_lib.h"


def make_step(templates_dir: Path = TEMPLATES_DIR) -> PolygonStep:
    """`PolygonStep`, читающий `problem_lib.h` из `templates_dir`.

    `StepContext` не несёт `templates_dir` — остальные polygon-шаги файлов
    из `templates/` не читают, — поэтому каталог фиксируется при сборке
    шага. `STEP` ниже собран с `TEMPLATES_DIR`; другой каталог нужен только
    тестам.
    """
    templates_dir = Path(templates_dir)

    def execute(ctx: StepContext, client: PolygonClient) -> StepResult:
        """Загружает `problem_lib.h` как есть (`ctx.spec` не используется),
        если он отличается от базы синхронизации."""
        polygon_id = require_polygon_state(ctx).polygon_id
        content = read_problem_lib(templates_dir)
        items = {PROBLEM_LIB_KEY: digest(content)}
        if not changed_items(ctx, items):
            return StepResult(NO_CHANGES_MESSAGE)

        client.call(
            "problem.saveFile",
            {
                "problemId": str(polygon_id),
                "type": "resource",
                "name": _RESOURCE_FILENAME,
                "file": content,
            },
        )
        return StepResult(
            f"uploaded {_RESOURCE_FILENAME} as resource to Polygon id={polygon_id}", items
        )

    def compute_status(problem_id: str, outputs_dir: Path) -> tuple[str, str]:
        """По базе синхронизации (`sync_status`) — с текущим `problem_lib.h`
        в `templates_dir`."""
        return sync_status(
            problem_id,
            outputs_dir,
            key_prefix=PROBLEM_LIB_KEY,
            read_items=lambda: {PROBLEM_LIB_KEY: digest(read_problem_lib(templates_dir))},
        )

    return PolygonStep(
        name=STEP_NAME,
        check_done=never_done,
        execute=execute,
        compute_status=compute_status,
    )


def read_problem_lib(templates_dir: Path = TEMPLATES_DIR) -> bytes:
    """Содержимое шаблона `problem_lib.h`; нужно и `polygon pull` — сравнить
    с resource-файлом на Polygon."""
    resource_path = Path(templates_dir) / COMMON_SUBDIR / _RESOURCE_FILENAME
    if not resource_path.exists():
        raise PolygonStepError(f"template {resource_path} not found — check templates_dir")
    return resource_path.read_bytes()


STEP = make_step()
