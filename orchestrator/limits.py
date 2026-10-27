"""Актуальные TL/ML задачи — `problems/<путь задачи>/limits.yaml`.

`constraints.yaml` — результат `constraints_pick`: TL/ML в нём — то, что
предложила модель (или задал автор в спеке), и после генерации он не
меняется. Актуальные TL/ML — те, что выставлены на Polygon, — хранит
`limits.yaml`: его загружает `polygon push` (`set_constraints`), с ним
сверяет TL/ML задачи `polygon pull`, и его можно править руками. Из
`constraints.yaml` он выводится шагом `limits_from_constraints`
(`orchestrator/steps/limits_from_constraints.py`) — только когда TL/ML в
`constraints.yaml` изменились, так что правка TL/ML на Polygon или руками
переживает `llm generate`.

Ключи — те же, что в `constraints.yaml` (`limits.time_limit_seconds`,
`limits.memory_limit_mb`), в корне документа.
"""

from __future__ import annotations

from numbers import Real
from typing import Any

import yaml

LIMITS_FILENAME = "limits.yaml"

TIME_KEY = "time_limit_seconds"
MEMORY_KEY = "memory_limit_mb"

LIMITS_HEADER = (
    "# Current TL/ML of the problem: polygon push sets them on Polygon, polygon pull\n"
    "# updates them from Polygon. Derived from constraints.yaml by the step\n"
    "# limits_from_constraints only when the TL/ML there change — edit freely.\n"
)


class LimitsError(ValueError):
    """TL/ML не читаются: не YAML, нет ключей, не положительные числа."""


def extract_limits(data: dict[str, Any]) -> tuple[object, object]:
    """Пара (time_limit_seconds, memory_limit_mb) из разобранного YAML.

    `constraints.yaml` — свободный YAML: структурированный вывод модели
    валидирует только форму status/artifacts/notes, не содержимое файла.
    Поэтому пара встречается и внутри вложенного словаря `limits`, и прямо
    в корне документа (как в `limits.yaml`) — ищем в обоих местах.

    Если пара есть в обоих местах с РАЗНЫМИ значениями — это противоречие
    в самом файле: `LimitsError`, а не молчаливый выбор. Значения не
    проверяются (могут быть `None`) — это делает `parse_limits`.
    """
    candidates: list[tuple[str, dict]] = []
    limits = data.get("limits")
    if isinstance(limits, dict):
        candidates.append(("limits", limits))
    candidates.append(("root", data))

    found = [
        (label, (container[TIME_KEY], container[MEMORY_KEY]))
        for label, container in candidates
        if TIME_KEY in container and MEMORY_KEY in container
    ]
    if not found:
        raise LimitsError(f"{TIME_KEY}/{MEMORY_KEY} found neither in 'limits' nor at the root")

    first_values = found[0][1]
    if any(values != first_values for _, values in found[1:]):
        raise LimitsError(
            f"{TIME_KEY}/{MEMORY_KEY} are set both in 'limits' and at the root, "
            f"with different values: {dict(found)!r}"
        )
    return first_values


def parse_limits(text: str) -> tuple[int, int]:
    """(timeLimit в миллисекундах, memoryLimit в мегабайтах) — в единицах
    `problem.updateInfo` — из текста `limits.yaml` или `constraints.yaml`.

    Единицы: `time_limit_seconds` x 1000 -> миллисекунды; `memory_limit_mb`
    — как есть, в мегабайтах (конвенция Codeforces/Polygon problem.xml).
    """
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise LimitsError(f"not valid YAML: {exc}") from exc
    if not isinstance(data, dict):
        raise LimitsError(f"did not parse into a mapping (got {type(data).__name__})")

    time_limit_seconds, memory_limit_mb = extract_limits(data)
    for key, value in ((TIME_KEY, time_limit_seconds), (MEMORY_KEY, memory_limit_mb)):
        if value is None:
            raise LimitsError(f"{key} is not set (null)")
        if isinstance(value, bool) or not isinstance(value, Real) or value <= 0:
            raise LimitsError(f"{key} must be a positive number, got {value!r}")
    return round(time_limit_seconds * 1000), round(memory_limit_mb)


def render_limits(time_limit_ms: int, memory_limit_mb: int) -> str:
    """Текст `limits.yaml` для TL/ML в единицах `parse_limits`."""
    seconds = time_limit_ms / 1000
    return (
        f"{LIMITS_HEADER}"
        f"{TIME_KEY}: {int(seconds) if seconds.is_integer() else seconds}\n"
        f"{MEMORY_KEY}: {memory_limit_mb}\n"
    )
