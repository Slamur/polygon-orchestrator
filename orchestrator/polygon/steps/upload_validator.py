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
    require_polygon_state,
)

STEP_NAME = "upload_validator"

_VALIDATOR_FILENAME = "validator.cpp"


def _check_done(ctx: StepContext) -> str | None:
    """Шаг выполняется при каждом запуске: `problem.saveFile` перезаписывает
    файл, `problem.setValidator` идемпотентен, а ручные правки в UI Polygon
    локально не видны — пропускать загрузку по локальной записи нельзя."""
    return None


def _execute(ctx: StepContext, client: PolygonClient) -> str:
    """Загружает `validator.cpp` и назначает его валидатором (`ctx.spec` не
    используется); записывает sha256 загруженного содержимого в
    `polygon_state.json` — для `status`.

    Запись делается только после успеха обоих вызовов: если `saveFile`
    прошёл, а `setValidator` упал, шаг остаётся "not run"/"stale" и
    повторится целиком при следующем запуске.
    """
    polygon_id = require_polygon_state(ctx).polygon_id
    content = _read_validator(ctx.problem_id, ctx.outputs_dir)
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
    record_polygon_step(
        ctx.problem_id,
        STEP_NAME,
        {
            "sha256": _sha256(content),
            "sent_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        },
        outputs_dir=ctx.outputs_dir,
    )
    return f"uploaded and set {_VALIDATOR_FILENAME} as validator on Polygon id={polygon_id}"


def _compute_status(problem_id: str, outputs_dir: Path) -> tuple[str, str]:
    """"done", если последний загруженный `validator.cpp` совпадает по sha256
    с текущим в `outputs/<problem_id>/`; "stale", если файл с тех пор
    изменился (или пропал); "not run", если шаг ещё ни разу не выполнялся
    успешно."""
    state = load_polygon_state(problem_id, outputs_dir=outputs_dir)
    record = state.steps.get(STEP_NAME) if state is not None else None
    if not isinstance(record, dict) or not isinstance(record.get("sha256"), str):
        return STATUS_NOT_RUN, ""

    try:
        content = _read_validator(problem_id, outputs_dir)
    except PolygonStepError as exc:
        return STATUS_STALE, f"текущий {_VALIDATOR_FILENAME} не читается: {exc}"
    current = _sha256(content)
    if current != record["sha256"]:
        return STATUS_STALE, f"{_VALIDATOR_FILENAME} изменился после последней загрузки"
    return STATUS_DONE, f"{_VALIDATOR_FILENAME} sha256={current[:12]}"


def _read_validator(problem_id: str, outputs_dir: Path) -> bytes:
    validator_path = Path(outputs_dir) / problem_id / _VALIDATOR_FILENAME
    if not validator_path.exists():
        raise PolygonStepError(
            f"'{problem_id}': не найден {validator_path} — сначала выполните "
            f"генеративный шаг constraints_pick (orchestrator run {problem_id} "
            "--step constraints_pick)"
        )

    return validator_path.read_bytes()


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


STEP = PolygonStep(
    name=STEP_NAME,
    check_done=_check_done,
    execute=_execute,
    compute_status=_compute_status,
)
