"""Polygon-шаг 1 — создание задачи в Polygon по `problem_id` из спека,
CLAUDE.md, "Скоуп (текущий)" (создание задачи на Polygon).

У созданной задачи база синхронизации известна — она пустая, — поэтому
записывается её ревизия, и push сразу продолжает загрузку. Задача,
найденная на Polygon по имени, привязывается без ревизии: что в ней,
неизвестно, и push остановится до `polygon pull`.

Запускается через `orchestrator.polygon.steps.base.run_step(STEP, ...)`.
"""

from __future__ import annotations

import datetime
from pathlib import Path

from orchestrator.polygon.client import PolygonClient
from orchestrator.polygon.state import PolygonState, load_polygon_state, save_polygon_state
from orchestrator.polygon.sync import fetch_revision
from orchestrator.polygon.steps.base import (
    STATUS_DONE,
    STATUS_NOT_RUN,
    PolygonStep,
    StepContext,
    StepResult,
)
from orchestrator.problem_paths import problem_address, problem_id_for

STEP_NAME = "create_problem"


def _check_done(ctx: StepContext) -> str | None:
    """Локальное состояние уже есть — задача привязана, сети не нужно."""
    existing = load_polygon_state(ctx.problem_id, problems_dir=ctx.problems_dir)
    if existing is None:
        return None
    return f"already linked to Polygon id={existing.polygon_id}, skipped"


def _compute_status(problem_id: str, problems_dir: Path) -> tuple[str, str]:
    """Выполнен, если есть привязка к Polygon (`polygon_state.json`)."""
    state = load_polygon_state(problem_id, problems_dir=problems_dir)
    if state is None:
        return STATUS_NOT_RUN, ""
    return STATUS_DONE, f"polygon_id={state.polygon_id}"


def _execute(ctx: StepContext, client: PolygonClient) -> StepResult:
    """Переиспользует существующую на Polygon задачу с таким именем, иначе создаёт новую.
    Имя задачи на Polygon — её `problem_id` (`problem_id_for`)."""
    name = problem_id_for(ctx.problems_dir, ctx.problem_id)
    polygon_id = _find_polygon_id_by_name(client, name)
    if polygon_id is not None:
        _link(ctx, polygon_id, revision=None)
        address = problem_address(ctx.problems_dir, ctx.problem_id)
        return StepResult(
            f"found existing Polygon problem by name (id={polygon_id}), reused — run "
            f"`orchestrator {address} polygon pull` before pushing to it"
        )

    polygon_id = _create_polygon_problem(client, name)
    _link(ctx, polygon_id, revision=fetch_revision(client, polygon_id).revision)
    return StepResult(f"created new Polygon problem (id={polygon_id})")


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


def _link(ctx: StepContext, polygon_id: int, *, revision: int | None) -> None:
    """Сохраняет привязку `problem_id` -> Polygon problemId в `polygon_state.json`."""
    save_polygon_state(
        ctx.problem_id,
        PolygonState(
            problem_id=problem_id_for(ctx.problems_dir, ctx.problem_id),
            polygon_id=polygon_id,
            created_at=datetime.datetime.now(datetime.timezone.utc).isoformat(),
            revision=revision,
        ),
        problems_dir=ctx.problems_dir,
    )


STEP = PolygonStep(
    name=STEP_NAME,
    check_done=_check_done,
    execute=_execute,
    compute_status=_compute_status,
    # Только создаёт задачу/привязывается к существующей — в рабочей копии
    # нечего коммитить.
    commits_changes=False,
)
