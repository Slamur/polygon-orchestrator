"""Реализация `orchestrator.llm_client.LLMClient` поверх Anthropic API.

Всё специфичное для Anthropic (конкретные ID моделей, механизм
принудированного structured-вывода через tool use, retry/backoff под их
коды ошибок, чтение `ANTHROPIC_API_KEY`) живёт только здесь — не в
`orchestrator/model_router.py`, который остаётся вендоронезависимым и знает
только про условные классы моделей ("strong-model"/"medium-model"/
"fast-model") и `effort`.
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any

import anthropic
from dotenv import load_dotenv

from orchestrator.model_contracts import ModelResponse

logger = logging.getLogger(__name__)

# Условный класс модели -> конкретный ID модели Anthropic API.
MODEL_CLASS_TO_ID: dict[str, str] = {
    "strong-model": "claude-opus-5",
    "medium-model": "claude-sonnet-5",
    "fast-model": "claude-haiku-4-5-20251001",
}

# Thinking-токены на Opus 5 при high/xhigh effort считаются как
# output-токены и входят в max_tokens — лимит должен оставлять им место.
_MAX_TOKENS = 16000

# Retry только на транзиентные ошибки (5xx/overloaded, сетевые
# таймауты/обрывы соединения) — 4xx (в т.ч. 429) не транзиентны, это ошибка
# самого запроса, повторять его бессмысленно.
_MAX_ATTEMPTS = 3
_RETRY_BASE_DELAY_SECONDS = 1.0

# Единственный tool, через который модель обязана вернуть ответ — так
# ответ невозможно получить свободным текстом (усечённым/невалидным JSON),
# см. CLAUDE.md, "Правило эскалации при неуверенности" и
# docs/PROMPTS.md (контракт status/artifacts/notes).
_RESPONSE_TOOL_NAME = "submit_step_result"

_RESPONSE_TOOL: dict[str, Any] = {
    "name": _RESPONSE_TOOL_NAME,
    "description": (
        "Верни результат генеративного шага строго по контракту "
        "status/artifacts/notes, описанному в docs/PROMPTS.md."
    ),
    "input_schema": {
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
                    "Список файлов-артефактов. Anthropic strict tool use не "
                    "поддерживает object-схему с произвольными ключами "
                    "(additionalProperties должен быть false, а не схемой) — "
                    "поэтому карта 'имя файла' -> 'содержимое' из "
                    "docs/PROMPTS.md здесь представлена списком пар; "
                    "`_parse_model_response` ниже собирает её обратно в dict "
                    "для вендоронезависимого `ModelResponse.artifacts`."
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
    },
    "strict": True,
}


class AnthropicClient:
    """`LLMClient` поверх Anthropic API (см. `orchestrator/llm_client.py`)."""

    def __init__(self) -> None:
        self._sdk_client: anthropic.Anthropic | None = None
        self._env_loaded = False

    def generate(
        self,
        *,
        model_class: str,
        effort: str,
        system_prompt: str,
        user_prompt: str,
        context_documents: dict[str, str] | None = None,
    ) -> ModelResponse:
        model_id = MODEL_CLASS_TO_ID[model_class]
        client = self._get_sdk_client()
        system = _build_system_prompt(system_prompt, context_documents)

        attempt = 0
        while True:
            attempt += 1
            try:
                response = client.messages.create(
                    model=model_id,
                    max_tokens=_MAX_TOKENS,
                    system=system,
                    messages=[{"role": "user", "content": user_prompt}],
                    output_config={"effort": effort},
                    tools=[_RESPONSE_TOOL],
                    tool_choice={"type": "tool", "name": _RESPONSE_TOOL_NAME},
                )
                break
            except Exception as exc:
                if not _is_retryable(exc) or attempt >= _MAX_ATTEMPTS:
                    logger.error(
                        "AnthropicClient.generate(model_class=%r): попытка %d/%d "
                        "провалена без retry (%r)",
                        model_class,
                        attempt,
                        _MAX_ATTEMPTS,
                        exc,
                    )
                    raise
                delay = _RETRY_BASE_DELAY_SECONDS * (2 ** (attempt - 1))
                logger.warning(
                    "AnthropicClient.generate(model_class=%r): попытка %d/%d "
                    "провалена (%r), retry через %.1fs",
                    model_class,
                    attempt,
                    _MAX_ATTEMPTS,
                    exc,
                    delay,
                )
                time.sleep(delay)

        _log_usage(model_class, model_id, response)
        return _parse_model_response(model_class, response)

    def _get_sdk_client(self) -> anthropic.Anthropic:
        """Возвращает (и лениво создаёт) клиент Anthropic API.

        Ключ читается из `ANTHROPIC_API_KEY` (см. `.env.example`). Если ключа
        нет — падаем сразу с понятной ошибкой, а не молчаливым фейлом где-то
        внутри SDK. `max_retries=0` на клиенте: retry с backoff реализован в
        `generate` явно, чтобы им можно было управлять и покрыть тестами без
        обращения к реальному API.
        """
        if self._sdk_client is None:
            if not self._env_loaded:
                load_dotenv()
                self._env_loaded = True
            api_key = os.environ.get("ANTHROPIC_API_KEY")
            if not api_key:
                raise RuntimeError(
                    "ANTHROPIC_API_KEY не задан. Скопируйте .env.example в "
                    ".env и впишите туда ключ, либо экспортируйте переменную "
                    "окружения ANTHROPIC_API_KEY напрямую."
                )
            self._sdk_client = anthropic.Anthropic(api_key=api_key, max_retries=0)
        return self._sdk_client


def _build_system_prompt(
    system_prompt: str, context_documents: dict[str, str] | None
) -> str:
    """Склеивает `system_prompt` с приложенными документами контекста.

    Документы идут после `system_prompt` в порядке `context_documents`
    (см. `orchestrator/steps/base.py:load_context_documents` — фиксированный
    список STEP_CONTEXT_DOCUMENTS, затем extra_paths), каждый со своим путём
    как заголовком, чтобы модель могла на него сослаться. `None`/пустая карта
    — `system` остаётся просто `system_prompt` (обратная совместимость).
    """
    if not context_documents:
        return system_prompt

    parts = [system_prompt]
    for path, content in context_documents.items():
        parts.append(f"--- Приложенный документ: {path} ---\n{content}")
    return "\n\n".join(parts)


def _is_retryable(exc: Exception) -> bool:
    """Транзиентная ли ошибка: 5xx/overloaded_error или сетевой обрыв/таймаут.

    4xx (в т.ч. 429) — ошибка самого запроса, не транзиентная, retry на них
    не делаем (см. CLAUDE.md-задачу и `shared/error-codes.md`).
    """
    if isinstance(exc, anthropic.APIConnectionError):
        # включает anthropic.APITimeoutError
        return True
    if isinstance(exc, anthropic.APIStatusError):
        return exc.status_code >= 500
    return False


def _log_usage(model_class: str, model_id: str, response: anthropic.types.Message) -> None:
    """Логирует usage — единственный способ реально видеть стоимость шага."""
    usage = response.usage
    logger.info(
        "AnthropicClient.generate(model_class=%r): model=%s input_tokens=%s "
        "output_tokens=%s cache_creation_input_tokens=%s "
        "cache_read_input_tokens=%s stop_reason=%s",
        model_class,
        model_id,
        getattr(usage, "input_tokens", None),
        getattr(usage, "output_tokens", None),
        getattr(usage, "cache_creation_input_tokens", None),
        getattr(usage, "cache_read_input_tokens", None),
        response.stop_reason,
    )


def _parse_model_response(
    model_class: str, response: anthropic.types.Message
) -> ModelResponse:
    """Разбирает `ModelResponse` из `input` tool_use-блока `_RESPONSE_TOOL_NAME`.

    Не парсим свободный текст: `tool_choice` принудительно указывает именно
    на этот tool, а `strict: True` гарантирует, что `input` уже провалидирован
    по схеме — так модель не может вернуть невалидный/усечённый JSON.
    """
    tool_use_blocks = [
        block
        for block in response.content
        if block.type == "tool_use" and block.name == _RESPONSE_TOOL_NAME
    ]
    if not tool_use_blocks:
        raise RuntimeError(
            f"AnthropicClient.generate(model_class={model_class!r}): в ответе "
            f"модели нет tool_use-блока '{_RESPONSE_TOOL_NAME}' "
            f"(stop_reason={response.stop_reason!r})"
        )

    payload = tool_use_blocks[0].input
    artifacts = {
        entry["filename"]: entry["content"] for entry in payload.get("artifacts") or []
    }
    return ModelResponse(
        status=payload["status"],
        artifacts=artifacts,
        notes=list(payload.get("notes") or []),
    )
