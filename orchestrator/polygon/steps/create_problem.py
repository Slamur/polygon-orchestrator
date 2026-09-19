"""Polygon-шаг 1 — создание задачи в Polygon по `problem_id` из спека,
CLAUDE.md, "Скоуп (текущий)" (создание задачи на Polygon).
"""

from __future__ import annotations

import datetime
import logging
from pathlib import Path

from orchestrator.cache import OUTPUTS_DIR
from orchestrator.polygon.client import PolygonClient
from orchestrator.polygon.state import (
    PolygonState,
    load_polygon_state,
    save_polygon_state,
)
from orchestrator.spec import ProblemSpec

logger = logging.getLogger(__name__)

STEP_NAME = "create_problem"


def run_step(
    problem_id: str,
    spec: ProblemSpec,
    *,
    outputs_dir: Path = OUTPUTS_DIR,
    client: PolygonClient | None = None,
) -> str:
    """Создаёт задачу в Polygon с коротким именем `problem_id`, если она ещё
    не создана (ни локально, ни на Polygon под этим именем). Идемпотентно:
    повторный вызов с уже существующим состоянием не делает сетевых
    вызовов вообще.

    `spec` сейчас не используется этим шагом (нужен только `problem_id`),
    но принимается как обязательный параметр — единая сигнатура нужна
    `orchestrator/polygon/pipeline.py`, чтобы гонять все polygon-шаги
    одинаково, даже те, которым spec целиком не нужен (см. по аналогии
    `orchestrator/steps/*.py`).

    Возвращает короткую человекочитаемую строку для лога/CLI-вывода:
    - "already linked to Polygon id=<N>, skipped" — состояние уже было,
      сетевых вызовов не было;
    - "found existing Polygon problem by name (id=<N>), reused" —
      локального состояния не было, но `problems.list` нашёл существующую
      задачу с таким именем;
    - "created new Polygon problem (id=<N>)" — реально вызван `problem.create`.

    Бросает `PolygonApiError` наружу без перехвата, если сетевой вызов
    провалился, — обработка ошибки (остановка пайплайна) на стороне
    `orchestrator/polygon/pipeline.py`, не здесь.
    """
    existing = load_polygon_state(problem_id, outputs_dir=outputs_dir)
    if existing is not None:
        message = f"already linked to Polygon id={existing.polygon_id}, skipped"
        logger.info(message)
        return message

    if client is None:
        client = PolygonClient()

    # NOTE: предполагаем, что фильтр `name` в `problems.list` — точное
    # совпадение (либо пустой список), а не подстрочный поиск. На реальном
    # API это ещё не проверено: если окажется нечётким, `found` нужно
    # дополнительно фильтровать по `item["name"] == problem_id`.
    found = client.call("problems.list", {"name": problem_id})
    if found:
        polygon_id = found[0]["id"]
        _save(problem_id, polygon_id, outputs_dir)
        message = f"found existing Polygon problem by name (id={polygon_id}), reused"
        logger.info(message)
        return message

    result = client.call("problem.create", {"name": problem_id})
    polygon_id = result["id"]
    _save(problem_id, polygon_id, outputs_dir)
    message = f"created new Polygon problem (id={polygon_id})"
    logger.info(message)
    return message


def _save(problem_id: str, polygon_id: int, outputs_dir: Path) -> None:
    save_polygon_state(
        problem_id,
        PolygonState(
            problem_id=problem_id,
            polygon_id=polygon_id,
            created_at=datetime.datetime.now(datetime.timezone.utc).isoformat(),
        ),
        outputs_dir=outputs_dir,
    )
