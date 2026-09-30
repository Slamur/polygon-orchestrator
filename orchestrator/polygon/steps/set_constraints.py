"""Polygon-шаг 2 — перенос TL/ML из `outputs/<problem_id>/constraints.yaml`
(результат генеративного шага `constraints_pick`) в Polygon через
`problem.updateInfo`.

Запускается через `orchestrator.polygon.steps.base.run_step(STEP, ...)`.
"""

from __future__ import annotations

import datetime
from numbers import Real
from pathlib import Path

import yaml

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

STEP_NAME = "set_constraints"

_TIME_KEY = "time_limit_seconds"
_MEMORY_KEY = "memory_limit_mb"


def _check_done(ctx: StepContext) -> str | None:
    """Шаг выполняется при каждом запуске: `problem.updateInfo` идемпотентен,
    а локальная запись в `polygon_state.json` не знает о ручных правках в
    UI Polygon — пропускать по ней вызов нельзя (у `polygon run` нет
    `--force`, чтобы это обойти)."""
    return None


def _execute(ctx: StepContext, client: PolygonClient) -> str:
    """Переносит TL/ML в Polygon (`ctx.spec` не используется) и записывает
    отправленные параметры в `polygon_state.json` — для `status`."""
    params = _build_update_params(ctx.problem_id, ctx.outputs_dir)
    client.call("problem.updateInfo", params)
    record_polygon_step(
        ctx.problem_id,
        STEP_NAME,
        {
            "sent": params,
            "sent_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        },
        outputs_dir=ctx.outputs_dir,
    )
    return f"set {_describe(params)} on Polygon id={params['problemId']}"


def _compute_status(problem_id: str, outputs_dir: Path) -> tuple[str, str]:
    """"done", если последние отправленные параметры совпадают с теми, что
    получились бы из текущего `constraints.yaml`; "stale", если нет (или
    текущий `constraints.yaml` больше не читается); "not run", если шаг ещё
    ни разу не выполнялся успешно.

    Ручные правки TL/ML в UI Polygon отсюда не видны — это только сравнение
    с тем, что отправлял сам оркестратор.
    """
    state = load_polygon_state(problem_id, outputs_dir=outputs_dir)
    record = state.steps.get(STEP_NAME) if state is not None else None
    if not isinstance(record, dict) or not isinstance(record.get("sent"), dict):
        return STATUS_NOT_RUN, ""

    sent = record["sent"]
    try:
        current = _build_update_params(problem_id, outputs_dir)
    except PolygonStepError as exc:
        return STATUS_STALE, f"текущие лимиты не читаются: {exc}"
    if current != sent:
        return STATUS_STALE, f"отправлено {_describe(sent)}, сейчас {_describe(current)}"
    return STATUS_DONE, _describe(sent)


def _describe(params: dict) -> str:
    return f"timeLimit={params.get('timeLimit')}ms memoryLimit={params.get('memoryLimit')}MB"


def _build_update_params(problem_id: str, outputs_dir: Path) -> dict[str, str]:
    """Параметры `problem.updateInfo` из `polygon_state.json` и
    `constraints.yaml`. Без сети; обе зависимости (`create_problem`,
    `constraints_pick`) проверяются здесь — иначе `PolygonStepError` с
    указанием, какой шаг запустить сначала.

    Жёстко хардкодит interactive=false, inputFile="", outputFile="": в формате
    спека (docs/SPEC_FORMAT.md) нет полей для интерактивных задач и файлового
    ввода/вывода, значения отражают только дефолт "обычная задача,
    stdin/stdout". Если такие поля появятся — менять нужно здесь.

    Единицы: `time_limit_seconds` x 1000 -> `timeLimit` в миллисекундах;
    `memory_limit_mb` -> `memoryLimit` как есть, в мегабайтах (конвенция
    Codeforces/Polygon problem.xml). В тексте документации
    `problem.updateInfo` это прямо не сказано — после первого реального
    вызова стоит сверить с вкладкой General в UI Polygon.
    """
    state = load_polygon_state(problem_id, outputs_dir=outputs_dir)
    if state is None:
        raise PolygonStepError(
            f"'{problem_id}': задача ещё не создана на Polygon — сначала "
            f"выполните шаг create_problem (orchestrator polygon {problem_id} "
            "run --step create_problem)"
        )

    constraints_path = Path(outputs_dir) / problem_id / "constraints.yaml"
    if not constraints_path.exists():
        raise PolygonStepError(
            f"'{problem_id}': не найден {constraints_path} — сначала выполните "
            f"генеративный шаг constraints_pick (orchestrator run {problem_id} "
            "--step constraints_pick)"
        )

    try:
        constraints_data = yaml.safe_load(constraints_path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise PolygonStepError(
            f"'{problem_id}': {constraints_path} не является корректным YAML: {exc}"
        ) from exc
    if not isinstance(constraints_data, dict):
        raise PolygonStepError(
            f"'{problem_id}': {constraints_path} не разобрался в словарь "
            f"(получено {type(constraints_data).__name__})"
        )

    time_limit_seconds, memory_limit_mb = _extract_limits(constraints_data)
    for key, value in ((_TIME_KEY, time_limit_seconds), (_MEMORY_KEY, memory_limit_mb)):
        if value is None:
            raise PolygonStepError(
                f"'{problem_id}': {key} в {constraints_path} не заполнен (null) — "
                "constraints_pick должен был остановиться с status: uncertain"
            )
        if isinstance(value, bool) or not isinstance(value, Real) or value <= 0:
            raise PolygonStepError(
                f"'{problem_id}': {key} в {constraints_path} должен быть "
                f"положительным числом, получено {value!r}"
            )

    return {
        "problemId": str(state.polygon_id),
        "timeLimit": str(round(time_limit_seconds * 1000)),
        "memoryLimit": str(round(memory_limit_mb)),
        "inputFile": "",
        "outputFile": "",
        "interactive": "false",
    }


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
            f"не найдены {_TIME_KEY}/{_MEMORY_KEY} ни в 'limits', ни в корне "
            "constraints.yaml"
        )

    first_values = found[0][1]
    if any(values != first_values for _, values in found[1:]):
        raise PolygonStepError(
            f"{_TIME_KEY}/{_MEMORY_KEY} заданы и в 'limits', и в корне "
            f"constraints.yaml, но с разными значениями: {dict(found)!r}"
        )
    return first_values


STEP = PolygonStep(
    name=STEP_NAME,
    check_done=_check_done,
    execute=_execute,
    compute_status=_compute_status,
)
