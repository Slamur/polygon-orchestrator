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
                "confirmed — all the needed data was in the spec; proposed — "
                "something was deliberately inferred where it's allowed (see "
                "notes); uncertain — mandatory data is missing, "
                "artifacts may be empty/partial."
            ),
        },
        "artifacts": {
            "type": "array",
            "description": (
                "The list of artifact files: pairs of 'file name' + 'contents'. "
                "In revision mode — only new and changed files."
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
                            "The path to the spec/artifact field the note "
                            "refers to, e.g. "
                            "'constraints.variables[1].max'."
                        ),
                    },
                    "kind": {
                        "type": "string",
                        "enum": ["proposed", "uncertain", "changed", "suggestion"],
                        "description": (
                            "changed — only in revision mode: what was changed "
                            "in the file and why (field — the file name). "
                            "suggestion — the model's idea beyond the spec (its own "
                            "solution, test, better asymptotics), not a replacement "
                            "of the author's decisions."
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
                "Revision mode: the names of the step's current files left as "
                "is (don't repeat them in artifacts). Otherwise — an empty list."
            ),
            "items": {"type": "string"},
        },
        "removed": {
            "type": "array",
            "description": (
                "Revision mode: the step's current files that are no longer needed. "
                "The orchestrator doesn't delete them but marks them for the author. Otherwise — "
                "an empty list."
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
                "Revision mode: renames of the step's current files. If "
                "the contents change too — the file goes into artifacts under the new "
                "name `to`. Otherwise — an empty list."
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
