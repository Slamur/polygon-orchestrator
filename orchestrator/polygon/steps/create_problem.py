"""Polygon-шаг 1 — создание задачи в Polygon по `problem_id` из спека,
CLAUDE.md, "Скоуп (текущий)" (создание задачи на Polygon).

Запускается через `orchestrator.polygon.steps.base.run_step(STEP, ...)`.
"""

from __future__ import annotations

import datetime

from orchestrator.polygon.client import PolygonClient
from orchestrator.polygon.state import PolygonState, load_polygon_state, save_polygon_state
from orchestrator.polygon.steps.base import PolygonStep, StepContext

STEP_NAME = "create_problem"


def _check_done(ctx: StepContext) -> str | None:
    """Локальное состояние уже есть — задача привязана, сети не нужно."""
    existing = load_polygon_state(ctx.problem_id, outputs_dir=ctx.outputs_dir)
    if existing is None:
        return None
    return f"already linked to Polygon id={existing.polygon_id}, skipped"


def _execute(ctx: StepContext, client: PolygonClient) -> str:
    """Переиспользует существующую на Polygon задачу с таким именем, иначе создаёт новую."""
    polygon_id = _find_polygon_id_by_name(client, ctx.problem_id)
    if polygon_id is not None:
        _link(ctx, polygon_id)
        return f"found existing Polygon problem by name (id={polygon_id}), reused"

    polygon_id = _create_polygon_problem(client, ctx.problem_id)
    _link(ctx, polygon_id)
    return f"created new Polygon problem (id={polygon_id})"


def _find_polygon_id_by_name(client: PolygonClient, name: str) -> int | None:
    """id задачи на Polygon с именем `name` или `None`, если такой нет.

    NOTE: предполагаем, что фильтр `name` в `problems.list` — точное
    совпадение (либо пустой список), а не подстрочный поиск. На реальном
    API это ещё не проверено: если окажется нечётким, результат нужно
    дополнительно фильтровать по `item["name"] == name`.
    """
    found = client.call("problems.list", {"name": name})
    return found[0]["id"] if found else None


def _create_polygon_problem(client: PolygonClient, name: str) -> int:
    """Создаёт задачу на Polygon и возвращает её id."""
    return client.call("problem.create", {"name": name})["id"]


def _link(ctx: StepContext, polygon_id: int) -> None:
    """Сохраняет привязку `problem_id` -> Polygon problemId в `polygon_state.json`."""
    save_polygon_state(
        ctx.problem_id,
        PolygonState(
            problem_id=ctx.problem_id,
            polygon_id=polygon_id,
            created_at=datetime.datetime.now(datetime.timezone.utc).isoformat(),
        ),
        outputs_dir=ctx.outputs_dir,
    )


STEP = PolygonStep(name=STEP_NAME, check_done=_check_done, execute=_execute)
