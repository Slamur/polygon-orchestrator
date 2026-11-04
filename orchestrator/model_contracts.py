"""The vendor-independent contract of a generative step's response.

`ModelResponse` is the only thing that must be shared between
`orchestrator/model_router.py` and any `LLMClient` implementation
(`orchestrator/llm_client.py`): the status/artifacts/notes JSON contract itself is
described in `docs/PROMPTS.md` and doesn't depend on which vendor exactly
(the Anthropic API, a local model, anything else) fills it. Moved into
a separate module so as not to create an import cycle between `model_router.py`
(which needs the `LLMClient` type) and `llm_client.py`/the concrete clients
(which need the `ModelResponse` type).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ModelResponse:
    """The model's parsed response by the JSON contract from `docs/PROMPTS.md`.

    `status` — "confirmed" | "proposed" | "uncertain".
    `artifacts` — a "file name" -> "contents" dict (may be empty or
    partial when `status == "uncertain"`).
    `notes` — a list of notes of the form {"field", "kind", "explanation"}, where
    `kind` — "proposed" | "uncertain" | "changed" | "suggestion".

    `unchanged`/`removed`/`renamed` — only for revision mode, when
    the model is shown the step's current files (`orchestrator/steps/base.py`):
    the names of files left as is; files the model proposes to
    delete (`{"filename", "reason"}`), and renames
    (`{"from", "to", "reason"}`). Without current files — empty.
    """

    status: str
    artifacts: dict[str, str] = field(default_factory=dict)
    notes: list[dict[str, Any]] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)
    removed: list[dict[str, str]] = field(default_factory=list)
    renamed: list[dict[str, str]] = field(default_factory=list)


# The JSON Schema of the response by the status/artifacts/notes contract (`docs/PROMPTS.md`)
# — shared by all clients that can force structured
# output by a schema (the Anthropic API — via strict tool use, the Claude Code CLI —
# via `--json-schema`). The schema is deliberately compatible with the strictest of them
# (Anthropic strict tool use): every object schema's `additionalProperties`
# is literally `False`, so the "file name" -> "contents" map from
# `docs/PROMPTS.md` is represented here as a list of pairs, and
# `model_response_from_payload` assembles it back into a dict.
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
    """Assembles `ModelResponse` from a JSON object by `RESPONSE_JSON_SCHEMA`
    (a list of pairs `artifacts` -> a "file name" -> "contents" dict)."""
    artifacts = {entry["filename"]: entry["content"] for entry in payload.get("artifacts") or []}
    return ModelResponse(
        status=payload["status"],
        artifacts=artifacts,
        notes=list(payload.get("notes") or []),
        unchanged=list(payload.get("unchanged") or []),
        removed=list(payload.get("removed") or []),
        renamed=list(payload.get("renamed") or []),
    )
