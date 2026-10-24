"""Состояние привязки `problem_id` -> Polygon problemId.

Хранится в `problems/<problem_id>/polygon_state.json` — на уровне
`problems/<problem_id>/`, а не внутри `.cache/`: это не результат-кэш
модельного шага, а факт состояния на стороне Polygon.
"""

from __future__ import annotations

import json
import os
import tempfile
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
    # Ключ из `synced` -> отметка версии файла на Polygon, которой
    # соответствует его хеш (`sync.file_stamp`: имя, время изменения,
    # длина). Есть только у файлов и только парой с `synced` — см.
    # `update_sync`; отметки без хеша не бывает.
    stamps: dict[str, str] = field(default_factory=dict)


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
    if not isinstance(state.synced, dict) or not isinstance(state.stamps, dict):
        return None
    if state.revision is not None and not isinstance(state.revision, int):
        return None
    return state


def save_polygon_state(
    problem_id: str, state: PolygonState, *, problems_dir: Path = PROBLEMS_DIR
) -> None:
    """Перезаписывает `problems/<problem_id>/polygon_state.json` атомарно:
    через временный файл рядом и `os.replace` — прерванная запись не
    оставляет обрезанный JSON (его `load_polygon_state` счёл бы
    отсутствием привязки)."""
    path = _state_file_path(problem_id, problems_dir=problems_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    content = json.dumps(asdict(state), ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as tmp:
            tmp.write(content)
        os.replace(tmp_name, path)
    except BaseException:
        Path(tmp_name).unlink(missing_ok=True)
        raise


def update_sync(
    problem_id: str,
    *,
    synced: dict[str, str | None],
    revision: int | None,
    stamps: dict[str, str | None] | None = None,
    replace: bool = False,
    problems_dir: Path = PROBLEMS_DIR,
) -> None:
    """Обновляет базу синхронизации в `polygon_state.json`: ключи из `synced`
    перезаписываются (`None` — ключ удаляется), `revision` заменяется.
    `replace` — база целиком заменяется на `synced` (остальные ключи
    удаляются).

    Отметка (`stamps`) пишется только вместе с хешем того же ключа: у
    каждого ключа из `synced` отметка заменяется на `stamps[key]`, а если
    её там нет (или ключ удаляется) — удаляется. Так отметка и хеш всегда
    описывают одну и ту же версию файла на Polygon, даже если запись
    прервалась на полпути (pull упал на части файлов): загрузка без
    отметки (push) убирает старую.

    Требует, чтобы состояние уже существовало (задача привязана шагом
    `create_problem` или `polygon pull`) — иначе `ValueError`.
    """
    state = load_polygon_state(problem_id, problems_dir=problems_dir)
    if state is None:
        raise ValueError(f"'{problem_id}': polygon_state.json is missing or corrupted")
    stamps = stamps or {}
    if replace:
        state.synced, state.stamps = {}, {}
    for key, value in synced.items():
        stamp = stamps.get(key)
        if value is None:
            state.synced.pop(key, None)
            state.stamps.pop(key, None)
            continue
        state.synced[key] = value
        if stamp is None:
            state.stamps.pop(key, None)
        else:
            state.stamps[key] = stamp
    state.revision = revision
    save_polygon_state(problem_id, state, problems_dir=problems_dir)


def record_stamps(
    problem_id: str, stamps: dict[str, str], *, problems_dir: Path = PROBLEMS_DIR
) -> None:
    """Записывает отметки `stamps` ключам, у которых уже есть хеш в базе
    (остальные отбрасываются), не меняя ни хешей, ни ревизии. Вызывающий
    отвечает за то, что отметки сняты с той же ревизии Polygon, что и
    `revision` в базе (см. `pipeline._record_stamps`)."""
    state = load_polygon_state(problem_id, problems_dir=problems_dir)
    if state is None:
        raise ValueError(f"'{problem_id}': polygon_state.json is missing or corrupted")
    state.stamps.update({key: value for key, value in stamps.items() if key in state.synced})
    save_polygon_state(problem_id, state, problems_dir=problems_dir)
