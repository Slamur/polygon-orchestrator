"""An implementation of `orchestrator.llm_client.LLMClient` on top of the Anthropic API.

Everything Anthropic-specific (concrete model IDs, the mechanism of
forced structured output through tool use, retry/backoff for their
error codes, reading `ANTHROPIC_API_KEY`) lives only here — not in
`orchestrator/model_router.py`, which stays vendor-independent and knows
only about the nominal model classes ("strong-model"/"medium-model"/
"fast-model") and `effort`.
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any

import anthropic
from dotenv import load_dotenv

from orchestrator.llm_client import LLMCallError, build_system_prompt
from orchestrator.model_contracts import (
    RESPONSE_JSON_SCHEMA,
    ModelResponse,
    model_response_from_payload,
)

logger = logging.getLogger(__name__)

# A nominal model class -> a concrete Anthropic API model ID.
MODEL_CLASS_TO_ID: dict[str, str] = {
    "strong-model": "claude-opus-5",
    "medium-model": "claude-sonnet-5",
    "fast-model": "claude-haiku-4-5-20251001",
}

# Thinking tokens on Opus 5 with high/xhigh effort count as
# output tokens and are part of max_tokens — the limit must leave room for them.
_MAX_TOKENS = 16000

# Retry only on transient errors (5xx/overloaded, network
# timeouts/connection drops) — 4xx (incl. 429) aren't transient, it's an error
# of the request itself, repeating it is pointless.
_MAX_ATTEMPTS = 3
_RETRY_BASE_DELAY_SECONDS = 1.0

# The only tool through which the model must return the response — so the
# response can't be obtained as free text (truncated/invalid JSON),
# see CLAUDE.md, "Escalation rule on uncertainty" and
# docs/PROMPTS.md (the status/artifacts/notes contract).
_RESPONSE_TOOL_NAME = "submit_step_result"

_RESPONSE_TOOL: dict[str, Any] = {
    "name": _RESPONSE_TOOL_NAME,
    "description": (
        "Верни результат генеративного шага строго по контракту "
        "status/artifacts/notes, описанному в docs/PROMPTS.md."
    ),
    "input_schema": RESPONSE_JSON_SCHEMA,
    "strict": True,
}


class AnthropicClient:
    """`LLMClient` on top of the Anthropic API (see `orchestrator/llm_client.py`)."""

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
        system = build_system_prompt(system_prompt, context_documents)

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
                        "AnthropicClient.generate(model_class=%r): attempt %d/%d "
                        "failed, not retrying (%r)",
                        model_class,
                        attempt,
                        _MAX_ATTEMPTS,
                        exc,
                    )
                    raise LLMCallError(
                        f"AnthropicClient.generate(model_class={model_class!r}): "
                        f"attempt {attempt}/{_MAX_ATTEMPTS} failed: {exc}"
                    ) from exc
                delay = _RETRY_BASE_DELAY_SECONDS * (2 ** (attempt - 1))
                logger.warning(
                    "AnthropicClient.generate(model_class=%r): attempt %d/%d "
                    "failed (%r), retrying in %.1fs",
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
        """Returns (and lazily creates) the Anthropic API client.

        The key is read from `ANTHROPIC_API_KEY` (see `.env.example`). If there's no
        key — fail right away with a clear error, not a silent failure somewhere
        inside the SDK. `max_retries=0` on the client: retry with backoff is implemented in
        `generate` explicitly, so that it can be controlled and covered by tests without
        calling the real API.
        """
        if self._sdk_client is None:
            if not self._env_loaded:
                load_dotenv()
                self._env_loaded = True
            api_key = os.environ.get("ANTHROPIC_API_KEY")
            if not api_key:
                raise LLMCallError(
                    "ANTHROPIC_API_KEY is not set. Copy .env.example to "
                    ".env and put the key there, or export the environment "
                    "variable ANTHROPIC_API_KEY directly."
                )
            self._sdk_client = anthropic.Anthropic(api_key=api_key, max_retries=0)
        return self._sdk_client


def _is_retryable(exc: Exception) -> bool:
    """Whether the error is transient: 5xx/overloaded_error or a network drop/timeout.

    4xx (incl. 429) is an error of the request itself, not transient, we don't retry
    on them (see the CLAUDE.md task and `shared/error-codes.md`).
    """
    if isinstance(exc, anthropic.APIConnectionError):
        # includes anthropic.APITimeoutError
        return True
    if isinstance(exc, anthropic.APIStatusError):
        return exc.status_code >= 500
    return False


def _log_usage(model_class: str, model_id: str, response: anthropic.types.Message) -> None:
    """Logs usage — the only way to actually see the cost of a step."""
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


def _parse_model_response(model_class: str, response: anthropic.types.Message) -> ModelResponse:
    """Parses `ModelResponse` from the `input` of the `_RESPONSE_TOOL_NAME` tool_use block.

    We don't parse free text: `tool_choice` forcibly points exactly
    to this tool, and `strict: True` guarantees that `input` is already validated
    against the schema — so the model can't return invalid/truncated JSON.
    """
    tool_use_blocks = [
        block
        for block in response.content
        if block.type == "tool_use" and block.name == _RESPONSE_TOOL_NAME
    ]
    if not tool_use_blocks:
        raise LLMCallError(
            f"AnthropicClient.generate(model_class={model_class!r}): the model "
            f"response has no tool_use block '{_RESPONSE_TOOL_NAME}' "
            f"(stop_reason={response.stop_reason!r})"
        )

    return model_response_from_payload(tool_use_blocks[0].input)
