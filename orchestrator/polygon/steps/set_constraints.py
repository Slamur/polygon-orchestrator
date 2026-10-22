"""Polygon-шаг 2 — перенос TL/ML из `outputs/<problem_id>/constraints.yaml`
(результат генеративного шага `constraints_pick`) в Polygon через
`problem.updateInfo`.

Запускается через `orchestrator.polygon.steps.base.run_step(STEP, ...)`.
"""

from __future__ import annotations

from numbers import Real
from pathlib import Path

import yaml

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
from orchestrator.polygon.sync import LIMITS_KEY, limits_digest

STEP_NAME = "set_constraints"

_TIME_KEY = "time_limit_seconds"
_MEMORY_KEY = "memory_limit_mb"


def _execute(ctx: StepContext, client: PolygonClient) -> StepResult:
    """Переносит TL/ML в Polygon (`ctx.spec` не используется), если они
    отличаются от базы синхронизации."""
    polygon_id = require_polygon_state(ctx).polygon_id
    time_limit_ms, memory_limit_mb = read_limits(ctx.problem_id, ctx.outputs_dir)
    items = {LIMITS_KEY: limits_digest(time_limit_ms, memory_limit_mb)}
    if not changed_items(ctx, items):
        return StepResult(NO_CHANGES_MESSAGE)

    params = {
        "problemId": str(polygon_id),
        "timeLimit": str(time_limit_ms),
        "memoryLimit": str(memory_limit_mb),
        "inputFile": "",
        "outputFile": "",
        "interactive": "false",
    }
    client.call("problem.updateInfo", params)
    return StepResult(
        f"set {_describe(time_limit_ms, memory_limit_mb)} on Polygon id={polygon_id}", items
    )


def _compute_status(problem_id: str, outputs_dir: Path) -> tuple[str, str]:
    """По базе синхронизации (`sync_status`). Ручные правки TL/ML в UI
    Polygon отсюда не видны."""

    def read_items() -> dict[str, str]:
        return {LIMITS_KEY: limits_digest(*read_limits(problem_id, outputs_dir))}

    return sync_status(problem_id, outputs_dir, key_prefix=LIMITS_KEY, read_items=read_items)


def _describe(time_limit_ms: int, memory_limit_mb: int) -> str:
    return f"timeLimit={time_limit_ms}ms memoryLimit={memory_limit_mb}MB"


def read_limits(problem_id: str, outputs_dir: Path) -> tuple[int, int]:
    """(timeLimit в миллисекундах, memoryLimit в мегабайтах) из
    `constraints.yaml` — в единицах `problem.updateInfo`. Без сети; если
    `constraints.yaml` нет или он некорректен — `PolygonStepError` (при
    отсутствии — с указанием, какой шаг запустить сначала). Нужно и
    `polygon pull` — сравнить локальные TL/ML с Polygon.

    `problem.updateInfo` получает ещё interactive=false, inputFile="",
    outputFile="" (см. `_execute`): в формате спека (docs/SPEC_FORMAT.md)
    нет полей для интерактивных задач и файлового ввода/вывода, значения
    отражают только дефолт "обычная задача, stdin/stdout". Если такие поля
    появятся — менять нужно там.

    Единицы: `time_limit_seconds` x 1000 -> `timeLimit` в миллисекундах;
    `memory_limit_mb` -> `memoryLimit` как есть, в мегабайтах (конвенция
    Codeforces/Polygon problem.xml). В тексте документации
    `problem.updateInfo` это прямо не сказано — после первого реального
    вызова стоит сверить с вкладкой General в UI Polygon.
    """
    constraints_path = Path(outputs_dir) / problem_id / "constraints.yaml"
    if not constraints_path.exists():
        raise PolygonStepError(
            f"'{problem_id}': {constraints_path} not found — first run "
            f"the generative step constraints_pick (orchestrator llm generate {problem_id} "
            "--step constraints_pick)"
        )

    try:
        constraints_data = yaml.safe_load(constraints_path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise PolygonStepError(
            f"'{problem_id}': {constraints_path} is not valid YAML: {exc}"
        ) from exc
    if not isinstance(constraints_data, dict):
        raise PolygonStepError(
            f"'{problem_id}': {constraints_path} did not parse into a mapping "
            f"(got {type(constraints_data).__name__})"
        )

    time_limit_seconds, memory_limit_mb = _extract_limits(constraints_data)
    for key, value in ((_TIME_KEY, time_limit_seconds), (_MEMORY_KEY, memory_limit_mb)):
        if value is None:
            raise PolygonStepError(
                f"'{problem_id}': {key} in {constraints_path} is not set (null) — "
                "constraints_pick should have stopped with status: uncertain"
            )
        if isinstance(value, bool) or not isinstance(value, Real) or value <= 0:
            raise PolygonStepError(
                f"'{problem_id}': {key} in {constraints_path} must be "
                f"a positive number, got {value!r}"
            )

    return round(time_limit_seconds * 1000), round(memory_limit_mb)


def _extract_limits(constraints_data: dict) -> tuple[object, object]:
    """Пара (time_limit_seconds, memory_limit_mb) из `constraints.yaml`.

    `constraints.yaml` — свободный YAML: структурированный вывод модели
    валидирует только форму status/artifacts/notes, не содержимое файла.
    Поэтому пара встречается и внутри вложенного словаря `limits`, и прямо
    в корне документа — ищем в обоих местах.

    Если пара есть в обоих местах с РАЗНЫМИ значениями — это противоречие
    в самом `constraints.yaml`: `PolygonStepError`, а не молчаливый выбор.
    Значения не проверяются (могут быть `None`) — это делает вызывающий.
    """
    candidates: list[tuple[str, dict]] = []
    limits = constraints_data.get("limits")
    if isinstance(limits, dict):
        candidates.append(("limits", limits))
    candidates.append(("root", constraints_data))

    found = [
        (label, (container[_TIME_KEY], container[_MEMORY_KEY]))
        for label, container in candidates
        if _TIME_KEY in container and _MEMORY_KEY in container
    ]
    if not found:
        raise PolygonStepError(
            f"{_TIME_KEY}/{_MEMORY_KEY} found neither in 'limits' nor at the root of "
            "constraints.yaml"
        )

    first_values = found[0][1]
    if any(values != first_values for _, values in found[1:]):
        raise PolygonStepError(
            f"{_TIME_KEY}/{_MEMORY_KEY} are set both in 'limits' and at the root of "
            f"constraints.yaml, with different values: {dict(found)!r}"
        )
    return first_values


STEP = PolygonStep(
    name=STEP_NAME,
    check_done=never_done,
    execute=_execute,
    compute_status=_compute_status,
)
