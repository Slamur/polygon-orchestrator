"""Состояние привязки `problem_id` -> Polygon problemId.

Хранится в `outputs/<problem_id>/polygon_state.json` — на уровне
`outputs/<problem_id>/`, а не внутри `.cache/`: это не результат-кэш
модельного шага, а факт состояния на стороне Polygon.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

from orchestrator.cache import OUTPUTS_DIR


@dataclass
class PolygonState:
    """Содержимое `outputs/<problem_id>/polygon_state.json`."""

    problem_id: str
    polygon_id: int
    created_at: str  # ISO8601 UTC
    # Имя polygon-шага -> что он в последний раз успешно сделал (формат
    # записи — забота самого шага). Нужно для `polygon status`, см.
    # `PolygonStep.compute_status`; на поведение `run` не влияет.
    steps: dict[str, dict] = field(default_factory=dict)


def _state_file_path(problem_id: str, *, outputs_dir: Path = OUTPUTS_DIR) -> Path:
    return Path(outputs_dir) / problem_id / "polygon_state.json"


def load_polygon_state(
    problem_id: str, *, outputs_dir: Path = OUTPUTS_DIR
) -> PolygonState | None:
    """Читает состояние, если файл существует и является валидным PolygonState.

    Отсутствующий файл, битый JSON или не соответствующая формату структура
    трактуются одинаково — как "состояния нет" (`None`), а не как ошибка.
    """
    path = _state_file_path(problem_id, outputs_dir=outputs_dir)
    if not path.exists():
        return None

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None

    if not isinstance(raw, dict):
        return None

    try:
        state = PolygonState(**raw)
    except TypeError:
        return None
    if not isinstance(state.steps, dict):
        return None
    return state


def save_polygon_state(
    problem_id: str, state: PolygonState, *, outputs_dir: Path = OUTPUTS_DIR
) -> None:
    """Перезаписывает `outputs/<problem_id>/polygon_state.json`."""
    path = _state_file_path(problem_id, outputs_dir=outputs_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(asdict(state), ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )


def record_polygon_step(
    problem_id: str, step_name: str, record: dict, *, outputs_dir: Path = OUTPUTS_DIR
) -> None:
    """Записывает `record` как результат шага `step_name` в `polygon_state.json`.

    Требует, чтобы состояние уже существовало (задача привязана шагом
    `create_problem`) — иначе `ValueError`.
    """
    state = load_polygon_state(problem_id, outputs_dir=outputs_dir)
    if state is None:
        raise ValueError(f"'{problem_id}': polygon_state.json is missing or corrupted")
    state.steps[step_name] = record
    save_polygon_state(problem_id, state, outputs_dir=outputs_dir)
