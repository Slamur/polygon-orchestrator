"""Polygon-шаг 2 — перенос актуальных TL/ML из `problems/<problem_id>/limits.yaml`
(см. `orchestrator/limits.py`; из `constraints.yaml` его выводит шаг
`limits_from_constraints`) в Polygon через `problem.updateInfo` вместе с
флагом интерактивности задачи (есть ли в спеке секция `interactor`).

Флаг — отдельный элемент базы синхронизации (`INTERACTIVE_KEY`), но
сравнивается, только если задача интерактивная или флаг уже есть в базе:
у обычных задач, синхронизированных до его появления, его нет, и push не
должен из-за этого заново отправлять `problem.updateInfo`.

Запускается через `orchestrator.polygon.steps.base.run_step(STEP, ...)`.
"""

from __future__ import annotations

from pathlib import Path

from orchestrator.limits import LIMITS_FILENAME, LimitsError, parse_limits
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
from orchestrator.polygon.sync import (
    INTERACTIVE_KEY,
    LIMITS_KEY,
    interactive_digest,
    limits_digest,
)
from orchestrator.problem_paths import problem_address

STEP_NAME = "set_constraints"


def _execute(ctx: StepContext, client: PolygonClient) -> StepResult:
    """Переносит TL/ML и флаг интерактивности в Polygon, если они отличаются
    от базы синхронизации (`problem.updateInfo` получает их вместе)."""
    polygon_id = require_polygon_state(ctx).polygon_id
    time_limit_ms, memory_limit_mb = read_limits(ctx.problem_id, ctx.problems_dir)
    interactive = ctx.spec.interactive
    items = {LIMITS_KEY: limits_digest(time_limit_ms, memory_limit_mb)}
    if interactive or INTERACTIVE_KEY in (ctx.synced or {}):
        items[INTERACTIVE_KEY] = interactive_digest(interactive)
    if not changed_items(ctx, items):
        return StepResult(NO_CHANGES_MESSAGE)

    params = {
        "problemId": str(polygon_id),
        "timeLimit": str(time_limit_ms),
        "memoryLimit": str(memory_limit_mb),
        "inputFile": "",
        "outputFile": "",
        "interactive": str(interactive).lower(),
    }
    client.call("problem.updateInfo", params)
    return StepResult(
        f"set {_describe(time_limit_ms, memory_limit_mb)} interactive={params['interactive']} "
        f"on Polygon id={polygon_id}",
        items,
    )


def _compute_status(problem_id: str, problems_dir: Path) -> tuple[str, str]:
    """По базе синхронизации (`sync_status`). Ручные правки TL/ML в UI
    Polygon отсюда не видны; флаг интерактивности — тоже (спек здесь
    недоступен, смену флага в спеке заметит push)."""

    def read_items() -> dict[str, str]:
        return {LIMITS_KEY: limits_digest(*read_limits(problem_id, problems_dir))}

    return sync_status(problem_id, problems_dir, key_prefix=LIMITS_KEY, read_items=read_items)


def _describe(time_limit_ms: int, memory_limit_mb: int) -> str:
    return f"timeLimit={time_limit_ms}ms memoryLimit={memory_limit_mb}MB"


def read_limits(problem_id: str, problems_dir: Path) -> tuple[int, int]:
    """(timeLimit в миллисекундах, memoryLimit в мегабайтах) из
    `limits.yaml` — в единицах `problem.updateInfo` (`limits.parse_limits`).
    Без сети; если `limits.yaml` нет или он некорректен — `PolygonStepError`
    (при отсутствии — с указанием, как его получить). Нужно и `polygon pull`
    — сравнить локальные TL/ML с Polygon.

    `problem.updateInfo` получает ещё inputFile="", outputFile="" (см.
    `_execute`): в формате спека (docs/SPEC_FORMAT.md) нет полей для
    файлового ввода/вывода, значения отражают дефолт stdin/stdout. Если
    такие поля появятся — менять нужно там.

    NOTE: в тексте документации `problem.updateInfo` единицы прямо не
    названы — после первого реального вызова стоит сверить с вкладкой
    General в UI Polygon.
    """
    limits_path = Path(problems_dir) / problem_id / LIMITS_FILENAME
    if not limits_path.exists():
        address = problem_address(problems_dir, problem_id)
        raise PolygonStepError(
            f"'{problem_id}': {limits_path} not found — derive it from constraints.yaml "
            f"(`orchestrator {address} llm generate --step limits_from_constraints`, "
            "the model is not called) or take it from Polygon "
            f"(`orchestrator {address} polygon pull`)"
        )
    try:
        return parse_limits(limits_path.read_text(encoding="utf-8"))
    except LimitsError as exc:
        raise PolygonStepError(f"'{problem_id}': {limits_path}: {exc}") from exc


STEP = PolygonStep(
    name=STEP_NAME,
    check_done=never_done,
    execute=_execute,
    compute_status=_compute_status,
)
