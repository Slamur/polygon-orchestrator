"""Polygon-шаг 3 — загрузка `templates/problem_lib.h` в Polygon как
resource-файла (`problem.saveFile`, `type=resource`).

`problem_lib.h` общий для всех задач и не генерируется под конкретную
задачу (CLAUDE.md, "Как использовать приложенные материалы"), поэтому
генеративного шага-зависимости нет — только `create_problem`.

Запускается через `orchestrator.polygon.steps.base.run_step(STEP, ...)`.
"""

from __future__ import annotations

import datetime
import hashlib
from pathlib import Path

from orchestrator.polygon.client import PolygonClient
from orchestrator.polygon.state import load_polygon_state, record_polygon_step
from orchestrator.polygon.steps.base import (
    STATUS_DONE,
    STATUS_NOT_RUN,
    STATUS_STALE,
    PolygonStep,
    PolygonStepError,
    StepContext,
)
from orchestrator.steps.base import TEMPLATES_DIR

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

    def check_done(ctx: StepContext) -> str | None:
        """Шаг выполняется при каждом запуске: `problem.saveFile`
        перезаписывает файл, а ручные правки в UI Polygon локально не
        видны — пропускать загрузку по локальной записи нельзя."""
        return None

    def execute(ctx: StepContext, client: PolygonClient) -> str:
        """Загружает `problem_lib.h` как есть (`ctx.spec` не используется) и
        записывает sha256 загруженного содержимого в `polygon_state.json` —
        для `status`."""
        polygon_id, content = _prepare_upload(ctx.problem_id, ctx.outputs_dir, templates_dir)
        client.call(
            "problem.saveFile",
            {
                "problemId": str(polygon_id),
                "type": "resource",
                "name": _RESOURCE_FILENAME,
            },
            files={"file": (_RESOURCE_FILENAME, content)},
        )
        record_polygon_step(
            ctx.problem_id,
            STEP_NAME,
            {
                "sha256": _sha256(content),
                "sent_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            },
            outputs_dir=ctx.outputs_dir,
        )
        return f"uploaded {_RESOURCE_FILENAME} as resource to Polygon id={polygon_id}"

    def compute_status(problem_id: str, outputs_dir: Path) -> tuple[str, str]:
        """"done", если последний загруженный `problem_lib.h` совпадает по
        sha256 с текущим в `templates_dir`; "stale", если шаблон с тех пор
        изменился (или пропал); "not run", если шаг ещё ни разу не
        выполнялся успешно."""
        state = load_polygon_state(problem_id, outputs_dir=outputs_dir)
        record = state.steps.get(STEP_NAME) if state is not None else None
        if not isinstance(record, dict) or not isinstance(record.get("sha256"), str):
            return STATUS_NOT_RUN, ""

        resource_path = templates_dir / _RESOURCE_FILENAME
        if not resource_path.exists():
            return STATUS_STALE, f"не найден шаблон {resource_path}"
        current = _sha256(resource_path.read_bytes())
        if current != record["sha256"]:
            return STATUS_STALE, f"{_RESOURCE_FILENAME} изменился после последней загрузки"
        return STATUS_DONE, f"{_RESOURCE_FILENAME} sha256={current[:12]}"

    return PolygonStep(
        name=STEP_NAME,
        check_done=check_done,
        execute=execute,
        compute_status=compute_status,
    )


def _prepare_upload(problem_id: str, outputs_dir: Path, templates_dir: Path) -> tuple[int, bytes]:
    """Polygon problemId и содержимое `problem_lib.h`. Без сети; зависимости
    проверяются здесь — иначе `PolygonStepError`."""
    state = load_polygon_state(problem_id, outputs_dir=outputs_dir)
    if state is None:
        raise PolygonStepError(
            f"'{problem_id}': задача ещё не создана на Polygon — сначала "
            f"выполните шаг create_problem (orchestrator polygon {problem_id} "
            "run --step create_problem)"
        )

    resource_path = templates_dir / _RESOURCE_FILENAME
    if not resource_path.exists():
        raise PolygonStepError(f"не найден шаблон {resource_path} — проверьте templates_dir")

    return state.polygon_id, resource_path.read_bytes()


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


STEP = make_step()
