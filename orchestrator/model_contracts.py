"""Вендоронезависимый контракт ответа генеративного шага.

`ModelResponse` — единственное, что должно быть общим между
`orchestrator/model_router.py` и любой реализацией `LLMClient`
(`orchestrator/llm_client.py`): сам JSON-контракт status/artifacts/notes
описан в `docs/PROMPTS.md` и не зависит от того, какой именно вендор
(Anthropic API, локальная модель, что угодно ещё) его наполняет. Вынесено в
отдельный модуль, чтобы не создавать цикл импортов между `model_router.py`
(которому нужен тип `LLMClient`) и `llm_client.py`/конкретными клиентами
(которым нужен тип `ModelResponse`).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ModelResponse:
    """Разобранный ответ модели по JSON-контракту из `docs/PROMPTS.md`.

    `status` — "confirmed" | "proposed" | "uncertain".
    `artifacts` — словарь "имя файла" -> "содержимое" (может быть пустым или
    частичным при `status == "uncertain"`).
    `notes` — список заметок вида {"field", "kind", "explanation"}, где
    `kind` — "proposed" | "uncertain" | "changed" | "suggestion".

    `unchanged`/`removed`/`renamed` — только для режима доработки, когда
    модели показаны текущие файлы шага (`orchestrator/steps/base.py`):
    имена файлов, оставленных как есть; файлы, которые модель предлагает
    удалить (`{"filename", "reason"}`), и переименования
    (`{"from", "to", "reason"}`). Без текущих файлов — пустые.
    """

    status: str
    artifacts: dict[str, str] = field(default_factory=dict)
    notes: list[dict[str, Any]] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)
    removed: list[dict[str, str]] = field(default_factory=list)
    renamed: list[dict[str, str]] = field(default_factory=list)


# JSON Schema ответа по контракту status/artifacts/notes (`docs/PROMPTS.md`)
# — общая для всех клиентов, которые умеют принудительный структурированный
# вывод по схеме (Anthropic API — через strict tool use, Claude Code CLI —
# через `--json-schema`). Схема намеренно совместима с самым строгим из них
# (Anthropic strict tool use): у каждой object-схемы `additionalProperties`
# — буквально `False`, поэтому карта "имя файла" -> "содержимое" из
# `docs/PROMPTS.md` здесь представлена списком пар, а
# `model_response_from_payload` собирает её обратно в dict.
RESPONSE_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "status": {
            "type": "string",
            "enum": ["confirmed", "proposed", "uncertain"],
            "description": (
                "confirmed — все нужные данные были в спеке; proposed — "
                "что-то намеренно домыслено там, где это разрешено (см. "
                "notes); uncertain — не хватает обязательных данных, "
                "artifacts может быть пустым/частичным."
            ),
        },
        "artifacts": {
            "type": "array",
            "description": (
                "Список файлов-артефактов: пары 'имя файла' + 'содержимое'. "
                "В режиме доработки — только новые и изменённые файлы."
            ),
            "items": {
                "type": "object",
                "properties": {
                    "filename": {"type": "string"},
                    "content": {"type": "string"},
                },
                "required": ["filename", "content"],
                "additionalProperties": False,
            },
        },
        "notes": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "field": {
                        "type": "string",
                        "description": (
                            "Путь до поля спека/артефакта, к которому "
                            "относится заметка, например "
                            "'constraints.variables[1].max'."
                        ),
                    },
                    "kind": {
                        "type": "string",
                        "enum": ["proposed", "uncertain", "changed", "suggestion"],
                        "description": (
                            "changed — только в режиме доработки: что и "
                            "почему изменено в файле (field — имя файла). "
                            "suggestion — идея модели сверх спека (своё "
                            "решение, тест, лучшая асимптотика), не замена "
                            "решений автора."
                        ),
                    },
                    "explanation": {"type": "string"},
                },
                "required": ["field", "kind", "explanation"],
                "additionalProperties": False,
            },
        },
        "unchanged": {
            "type": "array",
            "description": (
                "Режим доработки: имена текущих файлов шага, оставленных как "
                "есть (в artifacts их не повторять). Иначе — пустой список."
            ),
            "items": {"type": "string"},
        },
        "removed": {
            "type": "array",
            "description": (
                "Режим доработки: текущие файлы шага, которые больше не нужны. "
                "Оркестратор их не удаляет, а помечает для автора. Иначе — "
                "пустой список."
            ),
            "items": {
                "type": "object",
                "properties": {
                    "filename": {"type": "string"},
                    "reason": {"type": "string"},
                },
                "required": ["filename", "reason"],
                "additionalProperties": False,
            },
        },
        "renamed": {
            "type": "array",
            "description": (
                "Режим доработки: переименования текущих файлов шага. Если "
                "меняется и содержимое — файл кладётся в artifacts под новым "
                "именем `to`. Иначе — пустой список."
            ),
            "items": {
                "type": "object",
                "properties": {
                    "from": {"type": "string"},
                    "to": {"type": "string"},
                    "reason": {"type": "string"},
                },
                "required": ["from", "to", "reason"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["status", "artifacts", "notes", "unchanged", "removed", "renamed"],
    "additionalProperties": False,
}


def model_response_from_payload(payload: dict[str, Any]) -> ModelResponse:
    """Собирает `ModelResponse` из JSON-объекта по `RESPONSE_JSON_SCHEMA`
    (список пар `artifacts` -> dict "имя файла" -> "содержимое")."""
    artifacts = {
        entry["filename"]: entry["content"] for entry in payload.get("artifacts") or []
    }
    return ModelResponse(
        status=payload["status"],
        artifacts=artifacts,
        notes=list(payload.get("notes") or []),
        unchanged=list(payload.get("unchanged") or []),
        removed=list(payload.get("removed") or []),
        renamed=list(payload.get("renamed") or []),
    )
