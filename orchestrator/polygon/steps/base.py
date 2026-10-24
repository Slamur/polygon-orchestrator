"""Общая логика polygon-шагов: "выполнить, если ещё не выполнялся".

Шаг (`PolygonStep`) описывает только свою специфику — как локально понять,
что он уже выполнен (`check_done`), и что делать, если нет (`execute`).
Всё остальное — порядок проверки, ленивое создание `PolygonClient`,
логирование результата — делает `run_step`, одинаково для всех шагов.

Шаги, загружающие содержимое задачи, загружают только изменившиеся с
последней синхронизации элементы (`changed_items`, база — `synced` в
`polygon_state.json`, см. `orchestrator/polygon/sync.py`).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from orchestrator.cache import PROBLEMS_DIR
from orchestrator.polygon.client import PolygonClient
from orchestrator.polygon.state import PolygonState, load_polygon_state
from orchestrator.problem_paths import problem_address
from orchestrator.spec import ProblemSpec

logger = logging.getLogger(__name__)


# Состояния polygon-шага для `orchestrator <задача> polygon status` (без сети).
STATUS_DONE = "done"
STATUS_STALE = "stale"
STATUS_NOT_RUN = "not run"

NO_CHANGES_MESSAGE = "no changes since the last sync, skipped"


class PolygonStepError(Exception):
    """Шаг не может быть выполнен из-за отсутствующей зависимости или
    противоречивых локальных данных. В отличие от `PolygonApiError` — это
    ошибка ДО обращения к сети."""


@dataclass(frozen=True)
class StepContext:
    """Всё, что нужно шагу помимо клиента Polygon.

    `synced` — база синхронизации (ключ -> sha256, см. `sync.py`), с которой
    шаг сравнивает локальные элементы; `None` — база неизвестна или
    `push --force`: загружать всё."""

    problem_id: str
    spec: ProblemSpec
    problems_dir: Path
    synced: dict[str, str] | None = None


@dataclass(frozen=True)
class StepResult:
    """Итог шага: сообщение и что именно ушло в Polygon (ключ -> sha256).
    Пустой `uploaded` — Polygon не менялся: коммитить и записывать нечего."""

    message: str
    uploaded: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class PolygonStep:
    """Описание одного polygon-шага.

    `check_done` — только локальная проверка (файлы в `problems/<id>/`), без
    сетевых вызовов: возвращает человекочитаемое сообщение, если шаг уже
    выполнен, иначе `None`.

    `execute` — реальная работа через Polygon API; возвращает `StepResult`.
    `PolygonApiError` и `PolygonStepError` не перехватывает.

    `compute_status(problem_id, problems_dir)` — только локальная проверка
    для `status`: пара (одно из `STATUS_*`, пояснение). В отличие от
    `check_done` не требует спека и может сообщить "stale" — локальные
    файлы изменились с последней синхронизации.

    `commits_changes` — коммитить ли рабочую копию задачи на Polygon после
    шага, загрузившего что-то (см. `run_polygon_pipeline`). `False` — для
    шагов, которые не меняют содержимое задачи (`create_problem`).
    """

    name: str
    check_done: Callable[[StepContext], str | None]
    execute: Callable[[StepContext, PolygonClient], StepResult]
    compute_status: Callable[[str, Path], tuple[str, str]]
    commits_changes: bool = True


def never_done(ctx: StepContext) -> str | None:
    """`check_done` шагов, загружающих содержимое: шаг запускается всегда,
    а что загружать, решает сам по `ctx.synced` (`changed_items`)."""
    return None


def require_polygon_state(ctx: StepContext) -> PolygonState:
    """Состояние задачи из `polygon_state.json` — для всех шагов после
    `create_problem` (оттуда берётся `polygon_id`). Без сети; если задача
    ещё не создана на Polygon — `PolygonStepError` с указанием, какой шаг
    запустить сначала."""
    state = load_polygon_state(ctx.problem_id, problems_dir=ctx.problems_dir)
    if state is None:
        address = problem_address(ctx.problems_dir, ctx.problem_id)
        raise PolygonStepError(
            f"'{ctx.problem_id}': the problem is not created on Polygon yet — first "
            f"run the create_problem step (`orchestrator {address} polygon push "
            "--step create_problem`)"
        )
    return state


def changed_items(ctx: StepContext, items: dict[str, str]) -> dict[str, str]:
    """Элементы из `items` (ключ -> текущий sha256), которые отличаются от
    базы синхронизации; без базы (`ctx.synced is None`) — все."""
    if ctx.synced is None:
        return dict(items)
    return {key: value for key, value in items.items() if ctx.synced.get(key) != value}


def sync_status(
    problem_id: str,
    problems_dir: Path,
    *,
    key_prefix: str,
    read_items: Callable[[], dict[str, str]],
) -> tuple[str, str]:
    """Статус шага для `polygon status` по базе синхронизации.

    `key_prefix` — префикс ключей шага в `synced` (есть ли у шага база
    вообще); `read_items` — текущие локальные элементы шага (ключ ->
    sha256), может бросить `PolygonStepError`, если файлов нет.

    "not run" — по ключам шага базы нет; "stale" — какой-то элемент
    отличается от базы (или файлы больше не читаются); "done" — все
    совпадают.
    """
    state = load_polygon_state(problem_id, problems_dir=problems_dir)
    synced = {
        key: value
        for key, value in (state.synced if state is not None else {}).items()
        if key.startswith(key_prefix)
    }
    try:
        items = read_items()
    except PolygonStepError as exc:
        return (STATUS_STALE if synced else STATUS_NOT_RUN), f"local files are unreadable: {exc}"
    if not synced:
        return STATUS_NOT_RUN, ""
    changed = sorted(key for key, value in items.items() if synced.get(key) != value)
    if changed:
        return STATUS_STALE, "changed since the last sync: " + ", ".join(changed)
    return STATUS_DONE, f"{len(items)} item(s) in sync"


def run_step(
    step: PolygonStep,
    problem_id: str,
    spec: ProblemSpec,
    *,
    problems_dir: Path = PROBLEMS_DIR,
    client: PolygonClient | None = None,
    synced: dict[str, str] | None = None,
) -> StepResult:
    """Выполняет `step` для `problem_id`, если он ещё не выполнялся.

    Если `step.check_done` сообщает, что шаг уже выполнен, — возвращает это
    сообщение, не создавая клиент и не делая сетевых вызовов. Иначе
    вызывает `step.execute`. Сообщение пишется в лог: лог уходит в
    `problems/<id>/polygon.log`, а возвращаемое значение видит только вывод
    CLI текущего запуска.

    `synced` — база синхронизации (см. `StepContext`); `None` — загружать
    всё.

    `PolygonApiError` и `PolygonStepError` из `execute` пробрасываются
    наружу — остановка пайплайна на стороне `orchestrator/polygon/pipeline.py`.
    """
    ctx = StepContext(
        problem_id=problem_id, spec=spec, problems_dir=Path(problems_dir), synced=synced
    )

    message = step.check_done(ctx)
    if message is not None:
        result = StepResult(message)
    else:
        if client is None:
            client = PolygonClient()
        result = step.execute(ctx, client)

    logger.info("[%s] %s", step.name, result.message)
    return result
