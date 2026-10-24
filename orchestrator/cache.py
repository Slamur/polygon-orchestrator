"""Кэш по хешу спека для генеративных шагов (см. CLAUDE.md, раздел
"Кэширование по хешу спека").

Шаг не должен вызывать модель повторно, если ничего из того, что на него
влияет, не изменилось с прошлого успешного запуска: ни соответствующая
секция спека (+ апстрим-артефакты предыдущих шагов), ни содержимое
промпт-шаблонов, ни модель из `model_router.py`.

Этот модуль только считает хеши и читает/пишет `problems/<id>/.cache/<step>.json`
— решение о том, вызывать ли модель, принимает `pipeline.py`.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Optional

from orchestrator.problem_paths import PROBLEMS_DIR

PROMPTS_DIR = Path("prompts")
TEMPLATES_DIR = Path("templates")

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


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def compute_input_parts(spec_section: Any, upstream_artifacts: Any = None) -> dict[str, str]:
    """Хеши отдельных частей входа шага — тех же данных, что
    `compute_input_hash`, но по частям: `spec: <ключ>[.<подключ>]` для полей
    секции спека (на два уровня вглубь) и `upstream: <имя>` для апстрим-
    артефактов. Решение "валиден ли кэш" принимается только по общему хешу,
    части нужны, чтобы `llm status`/`llm generate` могли сказать, что
    именно изменилось (`describe_changed_parts`).
    """
    parts: dict[str, str] = {}
    if isinstance(spec_section, dict):
        for key, value in spec_section.items():
            if isinstance(value, dict):
                for sub_key, sub_value in value.items():
                    parts[f"spec: {key}.{sub_key}"] = _sha256(_canonical_json(sub_value))
            else:
                parts[f"spec: {key}"] = _sha256(_canonical_json(value))
    else:
        parts["spec"] = _sha256(_canonical_json(spec_section))
    for name, content in (upstream_artifacts or {}).items():
        parts[f"upstream: {name}"] = _sha256(_canonical_json(content))
    return parts


def compute_prompt_hash(
    step_name: str,
    *,
    prompts_dir: Path = PROMPTS_DIR,
    templates_dir: Path = TEMPLATES_DIR,
    extra_context_documents: list[str] | None = None,
) -> str:
    """sha256 от `prompts/<step_name>/{system.md,user.md.j2}` + документов контекста.

    Читает файлы с диска, так что правка промпта инвалидирует кэш шага, даже
    если сам спек не менялся. Помимо самих промптов, в хеш также входит
    содержимое всех документов контекста этого шага
    (`orchestrator.steps.base.STEP_CONTEXT_DOCUMENTS[step_name]` +
    `extra_context_documents`, см. `orchestrator.steps.base.
    load_context_documents`) — иначе правка tutorials/*.md/problem_lib.h/
    generators/*.cpp и т.п. не инвалидирует кэш шагов, которые от них зависят.
    Это наш собственный результат-кэш (`problems/<id>/.cache/<step>.json`),
    не связан с Anthropic prompt caching.
    """
    system_text, user_text, context_documents = _read_prompt_inputs(
        step_name,
        prompts_dir=prompts_dir,
        templates_dir=templates_dir,
        extra_context_documents=extra_context_documents,
    )

    digest = hashlib.sha256()
    digest.update(system_text.encode("utf-8"))
    digest.update(user_text.encode("utf-8"))
    for doc_path, content in context_documents.items():
        digest.update(doc_path.encode("utf-8"))
        digest.update(content.encode("utf-8"))

    return digest.hexdigest()


def compute_prompt_parts(
    step_name: str,
    *,
    prompts_dir: Path = PROMPTS_DIR,
    templates_dir: Path = TEMPLATES_DIR,
    extra_context_documents: list[str] | None = None,
) -> dict[str, str]:
    """Хеши по файлам того же набора, что `compute_prompt_hash`: оба
    промпт-шаблона шага и каждый документ контекста по отдельности. Как и
    `compute_input_parts`, только для объяснения, что изменилось."""
    system_text, user_text, context_documents = _read_prompt_inputs(
        step_name,
        prompts_dir=prompts_dir,
        templates_dir=templates_dir,
        extra_context_documents=extra_context_documents,
    )
    parts = {
        f"prompt: {step_name}/system.md": _sha256(system_text),
        f"prompt: {step_name}/user.md.j2": _sha256(user_text),
    }
    for doc_path, content in context_documents.items():
        parts[f"document: {doc_path}"] = _sha256(content)
    return parts


def _read_prompt_inputs(
    step_name: str,
    *,
    prompts_dir: Path,
    templates_dir: Path,
    extra_context_documents: list[str] | None,
) -> tuple[str, str, dict[str, str]]:
    step_dir = Path(prompts_dir) / step_name
    system_text = (step_dir / "system.md").read_text(encoding="utf-8")
    user_text = (step_dir / "user.md.j2").read_text(encoding="utf-8")

    # Локальный импорт разрывает цикл base.py (импортирует cache.py) <->
    # cache.py (импортировал бы base.py на уровне модуля).
    from orchestrator.steps import base as steps_base

    context_documents = steps_base.load_context_documents(
        step_name, templates_dir=templates_dir, extra_paths=extra_context_documents
    )
    return system_text, user_text, context_documents


@dataclass
class CacheEntry:
    """Содержимое `problems/<problem_id>/.cache/<step>.json`.

    `input_parts`/`prompt_parts` — хеши по частям (`compute_input_parts`/
    `compute_prompt_parts`), только для объяснения, что изменилось; `None` у
    записей, сделанных до их появления. `accepted` — запись сделана
    `llm accept` (текущие файлы приняты без вызова модели), а не
    генерацией.
    """

    input_hash: str
    prompt_hash: str
    model: str
    status: str
    timestamp: str
    input_parts: Optional[dict[str, str]] = None
    prompt_parts: Optional[dict[str, str]] = None
    accepted: bool = False


def _cache_file_path(
    problem_id: str, step_name: str, *, problems_dir: Path = PROBLEMS_DIR
) -> Path:
    return Path(problems_dir) / problem_id / ".cache" / f"{step_name}.json"


def load_cache_entry(
    problem_id: str, step_name: str, *, problems_dir: Path = PROBLEMS_DIR
) -> Optional[CacheEntry]:
    """Читает кэш шага, если файл существует и является валидным CacheEntry.

    Отсутствующий файл, битый JSON или не соответствующая формату структура
    трактуются одинаково — как "кэша нет" (`None`), а не как ошибка: в этом
    случае пайплайн просто вызовет модель заново.
    """
    path = _cache_file_path(problem_id, step_name, problems_dir=problems_dir)
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
    problems_dir: Path = PROBLEMS_DIR,
) -> None:
    """Перезаписывает `problems/<problem_id>/.cache/<step_name>.json`."""
    path = _cache_file_path(problem_id, step_name, problems_dir=problems_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(asdict(entry), ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )


def is_cache_valid(
    entry: Optional[CacheEntry],
    current_input_hash: str,
    current_prompt_hash: str,
    current_model: Optional[str] = None,
) -> bool:
    """Можно ли пропустить вызов модели и переиспользовать `entry`.

    `uncertain` никогда не считается валидным кэшем, независимо от хешей
    (см. CLAUDE.md, "Кэширование по хешу спека", пункт 3). Смена модели
    шага (`current_model`, класс модели из `model_router.STEP_TO_MODEL`)
    тоже инвалидирует кэш; `None` — не сравнивать модель.
    """
    if entry is None:
        return False
    if entry.status == UNCERTAIN_STATUS:
        return False
    if current_model is not None and entry.model != current_model:
        return False
    return (
        entry.input_hash == current_input_hash
        and entry.prompt_hash == current_prompt_hash
    )


def describe_changed_parts(
    old_parts: Optional[dict[str, str]], new_parts: dict[str, str]
) -> list[str]:
    """Какие части изменились между двумя наборами хешей по частям: имя
    части, с пометкой ` (added)`/` (removed)` для появившихся и исчезнувших.
    `old_parts=None` (запись кэша без частей) — пустой список: сказать
    нечего, вызывающий код сообщает об этом сам."""
    if old_parts is None:
        return []
    names = [
        name
        for name in new_parts
        if name in old_parts and old_parts[name] != new_parts[name]
    ]
    names += [f"{name} (added)" for name in new_parts if name not in old_parts]
    names += [f"{name} (removed)" for name in old_parts if name not in new_parts]
    return names
