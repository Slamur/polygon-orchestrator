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
    `kind` — "proposed" | "uncertain".
    """

    status: str
    artifacts: dict[str, str] = field(default_factory=dict)
    notes: list[dict[str, Any]] = field(default_factory=list)


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
            "description": "Список файлов-артефактов: пары 'имя файла' + 'содержимое'.",
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
                        "enum": ["proposed", "uncertain"],
                    },
                    "explanation": {"type": "string"},
                },
                "required": ["field", "kind", "explanation"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["status", "artifacts", "notes"],
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
    )
