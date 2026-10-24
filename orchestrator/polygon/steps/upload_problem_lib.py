"""Polygon-шаг 3 — загрузка `problems/<problem_id>/problem_lib.h` в Polygon
как resource-файла (`problem.saveFile`, `type=resource`).

У каждой задачи своя копия `problem_lib.h` (см. `PROBLEM_LIB_FILENAME` в
`orchestrator/steps/base.py`): если её ещё нет, шаг сначала копирует туда
`templates/problem_lib.h`.
Генеративного шага-зависимости нет — только `create_problem`.

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
from orchestrator.steps.base import (
    PROBLEM_LIB_FILENAME,
    TEMPLATES_DIR,
    ensure_problem_lib,
    problem_lib_path,
    template_problem_lib_path,
)

STEP_NAME = "upload_problem_lib"


def make_step(templates_dir: Path = TEMPLATES_DIR) -> PolygonStep:
    """`PolygonStep`, берущий шаблон `problem_lib.h` из `templates_dir`.

    `StepContext` не несёт `templates_dir` — остальные polygon-шаги файлов
    из `templates/` не читают, — поэтому каталог фиксируется при сборке
    шага. `STEP` ниже собран с `TEMPLATES_DIR`; другой каталог нужен только
    тестам.
    """
    templates_dir = Path(templates_dir)

    def execute(ctx: StepContext, client: PolygonClient) -> StepResult:
        """Загружает `problem_lib.h` задачи как есть (`ctx.spec` не
        используется), если он отличается от базы синхронизации."""
        polygon_id = require_polygon_state(ctx).polygon_id
        try:
            path = ensure_problem_lib(
                ctx.problem_id, problems_dir=ctx.problems_dir, templates_dir=templates_dir
            )
        except FileNotFoundError as exc:
            raise PolygonStepError(str(exc)) from exc
        content = path.read_bytes()
        items = {PROBLEM_LIB_KEY: digest(content)}
        if not changed_items(ctx, items):
            return StepResult(NO_CHANGES_MESSAGE)

        client.call(
            "problem.saveFile",
            {
                "problemId": str(polygon_id),
                "type": "resource",
                "name": PROBLEM_LIB_FILENAME,
                "file": content,
            },
        )
        return StepResult(
            f"uploaded {PROBLEM_LIB_FILENAME} as resource to Polygon id={polygon_id}", items
        )

    def compute_status(problem_id: str, problems_dir: Path) -> tuple[str, str]:
        """По базе синхронизации (`sync_status`). Пока копии задачи нет,
        сравнивается шаблон — его push скопирует и загрузит."""

        def read_items() -> dict[str, str]:
            path = problem_lib_path(problem_id, problems_dir)
            if not path.exists():
                path = template_problem_lib_path(templates_dir)
            if not path.exists():
                raise PolygonStepError(f"{path} not found")
            return {PROBLEM_LIB_KEY: digest(path.read_bytes())}

        return sync_status(
            problem_id, problems_dir, key_prefix=PROBLEM_LIB_KEY, read_items=read_items
        )

    return PolygonStep(
        name=STEP_NAME,
        check_done=never_done,
        execute=execute,
        compute_status=compute_status,
    )


STEP = make_step()
