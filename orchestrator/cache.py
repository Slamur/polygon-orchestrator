"""Кэш по хешу спека для генеративных шагов (см. CLAUDE.md, раздел
"Кэширование по хешу спека").

Шаг не должен вызывать модель повторно, если ничего из того, что на него
влияет, не изменилось с прошлого успешного запуска: ни соответствующая
секция спека (+ апстрим-артефакты предыдущих шагов), ни содержимое
промпт-шаблонов, ни модель из `model_router.py`.

Этот модуль только считает хеши и читает/пишет `outputs/<id>/.cache/<step>.json`
— решение о том, вызывать ли модель, принимает `pipeline.py`.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Optional

PROMPTS_DIR = Path("prompts")
OUTPUTS_DIR = Path("outputs")

# Статус, который никогда не считается валидным кэшем (см. CLAUDE.md,
# "Кэширование по хешу спека", пункт 3): провал по недостатку данных должен
# всплывать при каждом запуске, пока автор не поправит спек.
UNCERTAIN_STATUS = "uncertain"


def _canonical_json(data: Any) -> str:
    return json.dumps(data, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def compute_input_hash(
    step_name: str,
    spec_section: Any,
    upstream_artifacts: Any = None,
) -> str:
    """sha256 от канонического JSON-представления входа шага.

    `spec_section` — часть спека, от которой зависит шаг (см. CLAUDE.md:
    `statement_draft` -> секция `statement_draft`, `constraints_pick` ->
    секция `constraints`, `generators_and_script` -> секция `generation`,
    `solutions_draft` -> секция `solutions`).
    `upstream_artifacts` — артефакты предыдущих шагов, от которых зависит
    текущий (например, содержимое `constraints.yaml` для шагов 3 и 4);
    `None` для шагов без такой зависимости.
    """
    payload = {
        "step": step_name,
        "spec_section": spec_section,
        "upstream_artifacts": upstream_artifacts,
    }
    canonical = _canonical_json(payload)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def compute_prompt_hash(step_name: str, *, prompts_dir: Path = PROMPTS_DIR) -> str:
    """sha256 от содержимого `prompts/<step_name>/system.md` + `user.md.j2`.

    Читает файлы с диска, так что правка промпта инвалидирует кэш шага, даже
    если сам спек не менялся.
    """
    step_dir = Path(prompts_dir) / step_name
    system_text = (step_dir / "system.md").read_text(encoding="utf-8")
    user_text = (step_dir / "user.md.j2").read_text(encoding="utf-8")

    digest = hashlib.sha256()
    digest.update(system_text.encode("utf-8"))
    digest.update(user_text.encode("utf-8"))
    return digest.hexdigest()


@dataclass
class CacheEntry:
    """Содержимое `outputs/<problem_id>/.cache/<step>.json`."""

    input_hash: str
    prompt_hash: str
    model: str
    status: str
    timestamp: str


def _cache_file_path(
    problem_id: str, step_name: str, *, outputs_dir: Path = OUTPUTS_DIR
) -> Path:
    return Path(outputs_dir) / problem_id / ".cache" / f"{step_name}.json"


def load_cache_entry(
    problem_id: str, step_name: str, *, outputs_dir: Path = OUTPUTS_DIR
) -> Optional[CacheEntry]:
    """Читает кэш шага, если файл существует и является валидным CacheEntry.

    Отсутствующий файл, битый JSON или не соответствующая формату структура
    трактуются одинаково — как "кэша нет" (`None`), а не как ошибка: в этом
    случае пайплайн просто вызовет модель заново.
    """
    path = _cache_file_path(problem_id, step_name, outputs_dir=outputs_dir)
    if not path.exists():
        return None

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None

    if not isinstance(raw, dict):
        return None

    try:
        return CacheEntry(**raw)
    except TypeError:
        return None


def save_cache_entry(
    problem_id: str,
    step_name: str,
    entry: CacheEntry,
    *,
    outputs_dir: Path = OUTPUTS_DIR,
) -> None:
    """Перезаписывает `outputs/<problem_id>/.cache/<step_name>.json`."""
    path = _cache_file_path(problem_id, step_name, outputs_dir=outputs_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(asdict(entry), ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )


def is_cache_valid(
    entry: Optional[CacheEntry],
    current_input_hash: str,
    current_prompt_hash: str,
) -> bool:
    """Можно ли пропустить вызов модели и переиспользовать `entry`.

    `uncertain` никогда не считается валидным кэшем, независимо от хешей
    (см. CLAUDE.md, "Кэширование по хешу спека", пункт 3).
    """
    if entry is None:
        return False
    if entry.status == UNCERTAIN_STATUS:
        return False
    return (
        entry.input_hash == current_input_hash
        and entry.prompt_hash == current_prompt_hash
    )
