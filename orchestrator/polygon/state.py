"""Состояние привязки `problem_id` -> Polygon problemId.

Хранится в `problems/<problem_id>/polygon_state.json` — на уровне
`problems/<problem_id>/`, а не внутри `.cache/`: это не результат-кэш
модельного шага, а факт состояния на стороне Polygon.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

from orchestrator.problem_paths import POLYGON_STATE_FILENAME, PROBLEMS_DIR

# Поля старого формата, которые больше не используются и при чтении
# отбрасываются: `steps` — запись "что шаг последним отправил" одним хешем
# на шаг, до появления `revision`/`synced`.
_LEGACY_FIELDS = ("steps",)


@dataclass
class PolygonState:
    """Содержимое `problems/<problem_id>/polygon_state.json`."""

    problem_id: str
    polygon_id: int
    created_at: str  # ISO8601 UTC
    # База синхронизации — см. `orchestrator/polygon/sync.py`: ревизия на
    # Polygon, с которой совпадали записи `synced` (`None` — неизвестна), и
    # ключ элемента задачи -> sha256 его содержимого в этой ревизии.
    revision: int | None = None
    synced: dict[str, str] = field(default_factory=dict)


def _state_file_path(problem_id: str, *, problems_dir: Path = PROBLEMS_DIR) -> Path:
    return Path(problems_dir) / problem_id / POLYGON_STATE_FILENAME


def load_polygon_state(
    problem_id: str, *, problems_dir: Path = PROBLEMS_DIR
) -> PolygonState | None:
    """Читает состояние, если файл существует и является валидным PolygonState.

    Отсутствующий файл, битый JSON или не соответствующая формату структура
    трактуются одинаково — как "состояния нет" (`None`), а не как ошибка.
    Файл старого формата (с `steps`, без `revision`/`synced`) читается: база
    синхронизации у него просто неизвестна.
    """
    path = _state_file_path(problem_id, problems_dir=problems_dir)
    if not path.exists():
        return None

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None

    if not isinstance(raw, dict):
        return None
    for legacy in _LEGACY_FIELDS:
        raw.pop(legacy, None)

    try:
        state = PolygonState(**raw)
    except TypeError:
        return None
    if not isinstance(state.synced, dict):
        return None
    if state.revision is not None and not isinstance(state.revision, int):
        return None
    return state


def save_polygon_state(
    problem_id: str, state: PolygonState, *, problems_dir: Path = PROBLEMS_DIR
) -> None:
    """Перезаписывает `problems/<problem_id>/polygon_state.json`."""
    path = _state_file_path(problem_id, problems_dir=problems_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(asdict(state), ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )


def update_sync(
    problem_id: str,
    *,
    synced: dict[str, str | None],
    revision: int | None,
    problems_dir: Path = PROBLEMS_DIR,
) -> None:
    """Обновляет базу синхронизации в `polygon_state.json`: ключи из `synced`
    перезаписываются (`None` — ключ удаляется), `revision` заменяется.

    Требует, чтобы состояние уже существовало (задача привязана шагом
    `create_problem` или `polygon pull`) — иначе `ValueError`.
    """
    state = load_polygon_state(problem_id, problems_dir=problems_dir)
    if state is None:
        raise ValueError(f"'{problem_id}': polygon_state.json is missing or corrupted")
    for key, value in synced.items():
        if value is None:
            state.synced.pop(key, None)
        else:
            state.synced[key] = value
    state.revision = revision
    save_polygon_state(problem_id, state, problems_dir=problems_dir)
