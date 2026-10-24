"""Инкрементальная синхронизация с Polygon: что считается "тем же самым".

`polygon_state.json` хранит базу синхронизации (как `origin/...` в git):

- `revision` — ревизия задачи на Polygon, с которой локальная база
  последний раз совпадала (после коммита `polygon push` или после
  `polygon pull`); `None` — неизвестна (задача создана/привязана до
  появления этого поля или ещё ни разу не синхронизировалась);
- `synced` — ключ элемента задачи -> sha256 его содержимого в этой ревизии.

`polygon push` загружает только элементы, чей текущий локальный хеш
отличается от `synced` — и только если ревизия на Polygon всё ещё равна
`revision`, а в рабочей копии нет незакоммиченных правок (иначе Polygon
меняли в обход оркестратора — см. `orchestrator/polygon/pipeline.py`). `polygon pull`
переписывает базу по тому, что сейчас на Polygon.

Ключ — путь файла относительно `problems/<problem_id>/` (или
`templates/` для `problem_lib.h`), кроме нескольких "не-файлов":
TL/ML, имя назначенного валидатора/чекера, название задачи в условии.
Хеш всегда считается от того, что лежит/легло бы локально, — поэтому
`push` и `pull` получают одинаковые значения для одинакового содержимого.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from orchestrator.polygon.client import PolygonClient
from orchestrator.polygon.steps.base import PolygonStepError

LIMITS_KEY = "limits"
PROBLEM_LIB_KEY = "problem_lib.h"
VALIDATOR_FILE_KEY = "validator.cpp"
VALIDATOR_KEY = "validator"  # имя файла, назначенного валидатором
CHECKER_FILE_KEY = "checker.cpp"
CHECKER_KEY = "checker"  # имя назначенного чекера (`checker.cpp` или `std::...`)
STATEMENT_NAME_KEY = "statement/name"
SCRIPT_KEY = "test_script"  # один на testset, какой бы локальный файл ни был
SPEC_KEY = "spec.yaml"


def statement_file_key(filename: str) -> str:
    return f"statement/{filename}"


def example_key(number: int) -> str:
    return f"statement/examples/example_{number}.txt"


def generator_key(name: str) -> str:
    return f"generators/{name}"


def solution_key(name: str) -> str:
    return f"solutions/{name}"


def digest(data: bytes | str) -> str:
    if isinstance(data, str):
        data = data.encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def limits_digest(time_limit_ms: int, memory_limit_mb: int) -> str:
    return digest(f"timeLimit={time_limit_ms};memoryLimit={memory_limit_mb}")


@dataclass(frozen=True)
class PolygonRevision:
    """Ревизия задачи на Polygon (поля объекта `Problem`)."""

    revision: int
    modified: bool  # есть незакоммиченные правки в рабочей копии


def fetch_revision(client: PolygonClient, polygon_id: int) -> PolygonRevision:
    """Текущая ревизия задачи `polygon_id` — один вызов `problems.list`."""
    found = [
        item
        for item in client.call("problems.list", {"id": str(polygon_id)}) or []
        if item.get("id") == polygon_id
    ]
    if not found:
        raise PolygonStepError(f"Polygon problem id={polygon_id} is not available to this API key")
    problem = found[0]
    return PolygonRevision(
        revision=int(problem.get("revision", 0)), modified=bool(problem.get("modified"))
    )
