"""Общая логика polygon-шагов: "выполнить, если ещё не выполнялся".

Шаг (`PolygonStep`) описывает только свою специфику — как локально понять,
что он уже выполнен (`check_done`), и что делать, если нет (`execute`).
Всё остальное — порядок проверки, ленивое создание `PolygonClient`,
логирование результата — делает `run_step`, одинаково для всех шагов.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from orchestrator.cache import OUTPUTS_DIR
from orchestrator.polygon.client import PolygonClient
from orchestrator.spec import ProblemSpec

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class StepContext:
    """Всё, что нужно шагу помимо клиента Polygon."""

    problem_id: str
    spec: ProblemSpec
    outputs_dir: Path


@dataclass(frozen=True)
class PolygonStep:
    """Описание одного polygon-шага.

    `check_done` — только локальная проверка (файлы в `outputs/<id>/`), без
    сетевых вызовов: возвращает человекочитаемое сообщение, если шаг уже
    выполнен, иначе `None`.

    `execute` — реальная работа через Polygon API; возвращает сообщение о
    том, что было сделано. `PolygonApiError` не перехватывает.
    """

    name: str
    check_done: Callable[[StepContext], str | None]
    execute: Callable[[StepContext, PolygonClient], str]


def run_step(
    step: PolygonStep,
    problem_id: str,
    spec: ProblemSpec,
    *,
    outputs_dir: Path = OUTPUTS_DIR,
    client: PolygonClient | None = None,
) -> str:
    """Выполняет `step` для `problem_id`, если он ещё не выполнялся.

    Если `step.check_done` сообщает, что шаг уже выполнен, — возвращает это
    сообщение, не создавая клиент и не делая сетевых вызовов. Иначе
    вызывает `step.execute`. Сообщение (оно же возвращаемое значение)
    пишется в лог: лог уходит в `outputs/<id>/log.txt`, а возвращаемое
    значение видит только вывод CLI текущего запуска.

    `PolygonApiError` из `execute` пробрасывается наружу — остановка
    пайплайна на стороне `orchestrator/polygon/pipeline.py`.
    """
    ctx = StepContext(problem_id=problem_id, spec=spec, outputs_dir=Path(outputs_dir))

    message = step.check_done(ctx)
    if message is None:
        if client is None:
            client = PolygonClient()
        message = step.execute(ctx, client)

    logger.info("[%s] %s", step.name, message)
    return message
