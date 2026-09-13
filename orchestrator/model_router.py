"""Маршрутизация генеративных шагов по "классу модели" (см. CLAUDE.md,
раздел "Маршрутизация моделей по критичности шага") и реальный вызов
Anthropic API.

Промпты не должны зависеть от того, какая именно модель их выполняет —
поэтому шаги мапятся на условные классы модели ("strong-model" /
"medium-model" / "fast-model"), а уже классы — на конкретные ID моделей
(`MODEL_CLASS_TO_ID`). Конкретные имена моделей и параметры (effort,
max_tokens) — только здесь, не в промптах.
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any

import anthropic
from dotenv import load_dotenv

logger = logging.getLogger(__name__)

# Соответствие шага и условного класса модели. Критичные по содержанию шаги
# (constraints_pick, solutions_draft) — через сильную модель.
# generators_and_script — через среднюю: реализация уже заданных
# generator_ideas механическая, но дизайн adversarial-тестов под
# `solutions.known_wrong_approaches` — самостоятельное рассуждение о том, как
# сломать конкретный неверный алгоритм, не менее содержательное, чем
# constraints_pick/solutions_draft (см. CLAUDE.md, "Маршрутизация моделей по
# критичности шага") — поэтому шаг поднят с быстрой модели до средней; если
# качество предложенных тестов окажется слабым, следующий шаг эскалации —
# до сильной модели.
# statement_draft — средняя модель: задача там в основном про форматирование
# по явным правилам из requirements.md/polygon.md, а не про рассуждение, так
# что Opus-уровень для неё не нужен (см. CLAUDE.md).
STEP_TO_MODEL: dict[str, str] = {
    "statement_draft": "medium-model",
    "constraints_pick": "strong-model",
    "generators_and_script": "medium-model",
    "solutions_draft": "strong-model",
}

# Условный класс модели -> конкретный ID модели Anthropic API.
MODEL_CLASS_TO_ID: dict[str, str] = {
    "strong-model": "claude-opus-5",
    "medium-model": "claude-sonnet-5",
    "fast-model": "claude-haiku-4-5-20251001",
}

# `output_config.effort` для Opus 5 / Sonnet 5 (low/medium/high/xhigh/max,
# дефолт high) — см. CLAUDE.md, "Маршрутизация моделей по критичности шага".
# constraints_pick/solutions_draft — самые рискованные по цене ошибки;
# generators_and_script — high из-за дизайна adversarial-тестов;
# statement_draft — medium, это оформление, а не рассуждение.
STEP_TO_EFFORT: dict[str, str] = {
    "statement_draft": "medium",
    "constraints_pick": "high",
    "generators_and_script": "high",
    "solutions_draft": "high",
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
                "type": "object",
                "additionalProperties": {"type": "string"},
                "description": "Имя файла -> полное содержимое файла.",
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


_env_loaded = False
_client: anthropic.Anthropic | None = None


def _ensure_env_loaded() -> None:
    """Подгружает `.env` (если есть) через python-dotenv, один раз за процесс."""
    global _env_loaded
    if not _env_loaded:
        load_dotenv()
        _env_loaded = True


def _get_client() -> anthropic.Anthropic:
    """Возвращает (и лениво создаёт) клиент Anthropic API.

    Ключ читается из `ANTHROPIC_API_KEY` (см. `.env.example`). Если ключа
    нет — падаем сразу с понятной ошибкой, а не молчаливым фейлом где-то
    внутри SDK. `max_retries=0` на клиенте: retry с backoff реализован в
    `call_model` явно (см. ниже), чтобы им можно было управлять и покрыть
    тестами без обращения к реальному API.
    """
    global _client
    if _client is None:
        _ensure_env_loaded()
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            raise RuntimeError(
                "ANTHROPIC_API_KEY не задан. Скопируйте .env.example в .env "
                "и впишите туда ключ, либо экспортируйте переменную "
                "окружения ANTHROPIC_API_KEY напрямую."
            )
        _client = anthropic.Anthropic(api_key=api_key, max_retries=0)
    return _client


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


def _log_usage(step: str, model_id: str, response: anthropic.types.Message) -> None:
    """Логирует usage — единственный способ реально видеть стоимость шага."""
    usage = response.usage
    logger.info(
        "call_model('%s'): model=%s input_tokens=%s output_tokens=%s "
        "cache_creation_input_tokens=%s cache_read_input_tokens=%s stop_reason=%s",
        step,
        model_id,
        getattr(usage, "input_tokens", None),
        getattr(usage, "output_tokens", None),
        getattr(usage, "cache_creation_input_tokens", None),
        getattr(usage, "cache_read_input_tokens", None),
        response.stop_reason,
    )


def _parse_model_response(step: str, response: anthropic.types.Message) -> ModelResponse:
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
            f"call_model('{step}'): в ответе модели нет tool_use-блока "
            f"'{_RESPONSE_TOOL_NAME}' (stop_reason={response.stop_reason!r})"
        )

    payload = tool_use_blocks[0].input
    return ModelResponse(
        status=payload["status"],
        artifacts=dict(payload.get("artifacts") or {}),
        notes=list(payload.get("notes") or []),
    )


def call_model(step: str, system_prompt: str, user_prompt: str) -> ModelResponse:
    """Вызывает модель, назначенную шагу `step` через `STEP_TO_MODEL`.

    `system_prompt` и `user_prompt` — уже отрендеренные тексты промптов
    (`prompts/<step>/system.md` и `prompts/<step>/user.md.j2` после
    подстановки контекста); эта функция их не читает и не рендерит сама.

    Модель и `output_config.effort` берутся из `STEP_TO_MODEL`/
    `STEP_TO_EFFORT` по `step`. Ответ запрашивается через единственный
    принудительный tool (`_RESPONSE_TOOL`), поэтому результат гарантированно
    приходит как `tool_use.input`, а не свободным текстом.

    Возвращает `ModelResponse`, разобранный из JSON-ответа модели по
    контракту status/artifacts/notes (`docs/PROMPTS.md`).
    """
    model_id = MODEL_CLASS_TO_ID[STEP_TO_MODEL[step]]
    effort = STEP_TO_EFFORT[step]
    client = _get_client()

    attempt = 0
    while True:
        attempt += 1
        try:
            response = client.messages.create(
                model=model_id,
                max_tokens=_MAX_TOKENS,
                system=system_prompt,
                messages=[{"role": "user", "content": user_prompt}],
                output_config={"effort": effort},
                tools=[_RESPONSE_TOOL],
                tool_choice={"type": "tool", "name": _RESPONSE_TOOL_NAME},
            )
            break
        except Exception as exc:
            if not _is_retryable(exc) or attempt >= _MAX_ATTEMPTS:
                logger.error(
                    "call_model('%s'): попытка %d/%d провалена без retry (%r)",
                    step,
                    attempt,
                    _MAX_ATTEMPTS,
                    exc,
                )
                raise
            delay = _RETRY_BASE_DELAY_SECONDS * (2 ** (attempt - 1))
            logger.warning(
                "call_model('%s'): попытка %d/%d провалена (%r), retry через %.1fs",
                step,
                attempt,
                _MAX_ATTEMPTS,
                exc,
                delay,
            )
            time.sleep(delay)

    _log_usage(step, model_id, response)
    return _parse_model_response(step, response)
