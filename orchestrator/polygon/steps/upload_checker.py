"""Polygon-шаг 5 — назначение чекера задачи (`problem.setChecker`).

- `checker.custom_needed: true` — `outputs/<problem_id>/checker.cpp`
  (результат генеративного шага `checker_draft`) загружается как
  source-файл (`problem.saveFile`, `type=source`) и назначается чекером.
- `checker.custom_needed: false` — `problem.setChecker` вызывается с именем
  стандартного чекера из `checker.standard`, без загрузки файла
  (стандартные чекеры уже встроены в Polygon). В спеке имя пишется коротко
  (`ncmp`), Polygon ждёт `std::ncmp.cpp` — см. `_polygon_standard_name`.

Валидация спека (`Checker._check_custom_notes` в `orchestrator/spec.py`) не
требует `checker.standard` при `custom_needed: false` — такой спек проходит
`load_spec`. Здесь это ловится явной `PolygonStepError` до первого сетевого
вызова, а не уходит в API как `None`. Место для настоящего исправления —
`spec.py`, это отдельная задача.

По смыслу шаг идёт после `upload_validator`, но технически от него не
зависит — ничего из того, что тот пишет, здесь не читается.

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

STEP_NAME = "upload_checker"

_CHECKER_FILENAME = "checker.cpp"

_STANDARD_PREFIX = "std::"
_STANDARD_SUFFIX = ".cpp"


def _check_done(ctx: StepContext) -> str | None:
    """Шаг выполняется при каждом запуске: `problem.saveFile` перезаписывает
    файл, `problem.setChecker` идемпотентен, а ручные правки в UI Polygon
    локально не видны — пропускать по локальной записи нельзя."""
    return None


def _execute(ctx: StepContext, client: PolygonClient) -> str:
    """Назначает чекер (custom или стандартный, по `ctx.spec.checker`) и
    записывает в `polygon_state.json`, что было отправлено — для `status`.

    Запись делается только после успеха всех вызовов: если `saveFile`
    прошёл, а `setChecker` упал, шаг остаётся "not run"/"stale" и
    повторится целиком при следующем запуске.
    """
    polygon_id = require_polygon_state(ctx).polygon_id

    if ctx.spec.checker.custom_needed:
        content = _read_custom_checker(ctx.problem_id, ctx.outputs_dir)
        client.call(
            "problem.saveFile",
            {
                "problemId": str(polygon_id),
                "type": "source",
                "name": _CHECKER_FILENAME,
                "file": content,
            },
        )
        client.call(
            "problem.setChecker",
            {"problemId": str(polygon_id), "checker": _CHECKER_FILENAME},
        )
        _record(ctx, {"custom": True, "sha256": _sha256(content)})
        return f"uploaded and set custom {_CHECKER_FILENAME} as checker on Polygon id={polygon_id}"

    standard = _require_standard_name(ctx)
    client.call("problem.setChecker", {"problemId": str(polygon_id), "checker": standard})
    _record(ctx, {"custom": False, "checker": standard})
    return f"set standard checker {standard!r} on Polygon id={polygon_id}"


def _compute_status(problem_id: str, outputs_dir: Path) -> tuple[str, str]:
    """Для custom-чекера — как у `upload_validator`: "done", если sha256
    последнего загруженного `checker.cpp` совпадает с текущим, иначе
    "stale". Для стандартного — "done" с именем отправленного чекера: спек
    здесь недоступен, поэтому смена `checker.standard` в спеке как "stale"
    не видна. "not run" — шаг ещё ни разу не выполнялся успешно."""
    state = load_polygon_state(problem_id, outputs_dir=outputs_dir)
    record = state.steps.get(STEP_NAME) if state is not None else None
    if not isinstance(record, dict):
        return STATUS_NOT_RUN, ""

    if record.get("custom") is False and isinstance(record.get("checker"), str):
        return STATUS_DONE, f"standard {record['checker']}"

    if record.get("custom") is True and isinstance(record.get("sha256"), str):
        try:
            content = _read_custom_checker(problem_id, outputs_dir)
        except PolygonStepError as exc:
            return STATUS_STALE, f"current {_CHECKER_FILENAME} is unreadable: {exc}"
        current = _sha256(content)
        if current != record["sha256"]:
            return STATUS_STALE, f"{_CHECKER_FILENAME} changed since the last upload"
        return STATUS_DONE, f"custom {_CHECKER_FILENAME} sha256={current[:12]}"

    return STATUS_NOT_RUN, ""


def _read_custom_checker(problem_id: str, outputs_dir: Path) -> bytes:
    checker_path = Path(outputs_dir) / problem_id / _CHECKER_FILENAME
    if not checker_path.exists():
        raise PolygonStepError(
            f"'{problem_id}': checker.custom_needed=true, but "
            f"{checker_path} not found — first run the generative step checker_draft "
            f"(orchestrator run {problem_id} --step checker_draft)"
        )
    return checker_path.read_bytes()


def _require_standard_name(ctx: StepContext) -> str:
    standard = ctx.spec.checker.standard
    if not (standard and standard.strip()):
        raise PolygonStepError(
            f"'{ctx.problem_id}': checker.custom_needed=false, but "
            "checker.standard is not set in the spec — nothing to pass to "
            "problem.setChecker (spec validation does not catch this, see "
            "the upload_checker.py docstring); set checker.standard, "
            "e.g. ncmp"
        )
    return _polygon_standard_name(standard.strip())


def _polygon_standard_name(standard: str) -> str:
    """`ncmp` -> `std::ncmp.cpp`; уже полное имя (`std::ncmp.cpp`) — как есть."""
    name = standard if standard.startswith(_STANDARD_PREFIX) else _STANDARD_PREFIX + standard
    return name if name.endswith(_STANDARD_SUFFIX) else name + _STANDARD_SUFFIX


def _record(ctx: StepContext, data: dict) -> None:
    record_polygon_step(
        ctx.problem_id,
        STEP_NAME,
        {**data, "sent_at": datetime.datetime.now(datetime.timezone.utc).isoformat()},
        outputs_dir=ctx.outputs_dir,
    )


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


STEP = PolygonStep(
    name=STEP_NAME,
    check_done=_check_done,
    execute=_execute,
    compute_status=_compute_status,
)
