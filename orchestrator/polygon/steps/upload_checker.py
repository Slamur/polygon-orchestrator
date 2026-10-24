"""Polygon-шаг 5 — назначение чекера задачи (`problem.setChecker`).

- `checker.custom_needed: true` — `problems/<problem_id>/checker.cpp`
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

from pathlib import Path

from orchestrator.polygon.client import PolygonClient
from orchestrator.polygon.state import load_polygon_state
from orchestrator.polygon.steps.base import (
    NO_CHANGES_MESSAGE,
    STATUS_DONE,
    PolygonStep,
    PolygonStepError,
    StepContext,
    StepResult,
    changed_items,
    never_done,
    require_polygon_state,
    sync_status,
)
from orchestrator.polygon.sync import CHECKER_FILE_KEY, CHECKER_KEY, digest

STEP_NAME = "upload_checker"

_CHECKER_FILENAME = "checker.cpp"

_STANDARD_PREFIX = "std::"
_STANDARD_SUFFIX = ".cpp"


def _execute(ctx: StepContext, client: PolygonClient) -> StepResult:
    """Назначает чекер (custom или стандартный, по `ctx.spec.checker`), если
    он отличается от базы синхронизации. Для custom отличие файла или
    назначенного чекера — оба вызова, как в `upload_validator`."""
    polygon_id = require_polygon_state(ctx).polygon_id

    if ctx.spec.checker.custom_needed:
        content = _read_custom_checker(ctx.problem_id, ctx.problems_dir)
        items = _custom_items(content)
        if not changed_items(ctx, items):
            return StepResult(NO_CHANGES_MESSAGE)
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
        return StepResult(
            f"uploaded and set custom {_CHECKER_FILENAME} as checker on Polygon id={polygon_id}",
            items,
        )

    standard = _require_standard_name(ctx)
    items = {CHECKER_KEY: digest(standard)}
    if not changed_items(ctx, items):
        return StepResult(NO_CHANGES_MESSAGE)
    client.call("problem.setChecker", {"problemId": str(polygon_id), "checker": standard})
    return StepResult(f"set standard checker {standard!r} on Polygon id={polygon_id}", items)


def _compute_status(problem_id: str, problems_dir: Path) -> tuple[str, str]:
    """По базе синхронизации. Спек здесь недоступен, поэтому custom или
    стандартный — по базе: есть в ней `checker.cpp` — сравнивается
    локальный `checker.cpp`, иначе стандартный чекер считается "done"
    (смена `checker.standard` в спеке отсюда не видна)."""
    state = load_polygon_state(problem_id, problems_dir=problems_dir)
    synced = state.synced if state is not None else {}
    if CHECKER_FILE_KEY not in synced and CHECKER_KEY in synced:
        return STATUS_DONE, "standard checker"
    return sync_status(
        problem_id,
        problems_dir,
        key_prefix=CHECKER_KEY,
        read_items=lambda: _custom_items(_read_custom_checker(problem_id, problems_dir)),
    )


def _custom_items(content: bytes) -> dict[str, str]:
    return {CHECKER_FILE_KEY: digest(content), CHECKER_KEY: digest(_CHECKER_FILENAME)}


def _read_custom_checker(problem_id: str, problems_dir: Path) -> bytes:
    checker_path = Path(problems_dir) / problem_id / _CHECKER_FILENAME
    if not checker_path.exists():
        raise PolygonStepError(
            f"'{problem_id}': checker.custom_needed=true, but "
            f"{checker_path} not found — first run the generative step checker_draft "
            f"(`orchestrator {problem_id} llm generate --step checker_draft`)"
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


STEP = PolygonStep(
    name=STEP_NAME,
    check_done=never_done,
    execute=_execute,
    compute_status=_compute_status,
)
