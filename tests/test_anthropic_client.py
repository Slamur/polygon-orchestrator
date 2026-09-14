from unittest.mock import MagicMock, patch

import anthropic
import httpx2
import pytest

from orchestrator.llm_clients import anthropic_client
from orchestrator.llm_clients.anthropic_client import AnthropicClient
from orchestrator.model_contracts import ModelResponse


def _status_error(status_code: int) -> anthropic.APIStatusError:
    request = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    response = httpx2.Response(
        status_code,
        request=request,
        json={"type": "error", "error": {"type": "api_error", "message": "boom"}},
    )
    return anthropic.APIStatusError("boom", response=response, body={"type": "error"})


def _connection_error() -> anthropic.APIConnectionError:
    request = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    return anthropic.APIConnectionError(message="timed out", request=request)


def _fake_tool_use_message(payload: dict) -> MagicMock:
    tool_use_block = MagicMock()
    tool_use_block.type = "tool_use"
    tool_use_block.name = "submit_step_result"
    tool_use_block.input = payload

    message = MagicMock()
    message.content = [tool_use_block]
    message.stop_reason = "tool_use"
    message.usage = MagicMock(
        input_tokens=100,
        output_tokens=50,
        cache_creation_input_tokens=0,
        cache_read_input_tokens=0,
    )
    return message


def _payload(status: str = "confirmed") -> dict:
    return {
        "status": status,
        "artifacts": [{"filename": "statement.tex", "content": "content"}],
        "notes": [],
    }


def _client_with_fake_sdk(fake_sdk_client: MagicMock) -> AnthropicClient:
    client = AnthropicClient()
    client._sdk_client = fake_sdk_client
    client._env_loaded = True
    return client


@pytest.fixture(autouse=True)
def _no_sleep():
    with patch("orchestrator.llm_clients.anthropic_client.time.sleep") as mock_sleep:
        yield mock_sleep


def test_model_class_to_id_has_entry_for_every_known_class():
    assert set(anthropic_client.MODEL_CLASS_TO_ID) == {
        "strong-model",
        "medium-model",
        "fast-model",
    }


def test_get_sdk_client_raises_readable_error_without_api_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    client = AnthropicClient()
    client._env_loaded = True  # skip touching a real .env file
    with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY"):
        client._get_sdk_client()


def test_generate_uses_model_and_effort_for_class(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    fake_sdk_client = MagicMock()
    fake_sdk_client.messages.create.return_value = _fake_tool_use_message(_payload())
    client = _client_with_fake_sdk(fake_sdk_client)

    client.generate(
        model_class="strong-model",
        effort="high",
        system_prompt="system prompt",
        user_prompt="user prompt",
    )

    fake_sdk_client.messages.create.assert_called_once()
    kwargs = fake_sdk_client.messages.create.call_args.kwargs
    assert kwargs["model"] == anthropic_client.MODEL_CLASS_TO_ID["strong-model"]
    assert kwargs["output_config"] == {"effort": "high"}
    assert kwargs["system"] == "system prompt"
    assert kwargs["messages"] == [{"role": "user", "content": "user prompt"}]


def test_generate_forces_tool_choice_on_response_tool():
    fake_sdk_client = MagicMock()
    fake_sdk_client.messages.create.return_value = _fake_tool_use_message(_payload())
    client = _client_with_fake_sdk(fake_sdk_client)

    client.generate(model_class="medium-model", effort="medium", system_prompt="s", user_prompt="u")

    kwargs = fake_sdk_client.messages.create.call_args.kwargs
    assert kwargs["tool_choice"] == {"type": "tool", "name": "submit_step_result"}
    assert len(kwargs["tools"]) == 1
    assert kwargs["tools"][0]["name"] == "submit_step_result"
    assert kwargs["tools"][0]["strict"] is True


def _assert_strict_schema_compatible(schema: dict, *, path: str = "$") -> None:
    """Рекурсивно проверяет ограничения Anthropic strict tool use.

    Реальный API 400-ит на `tools.0.custom`, если у object-схемы
    `additionalProperties` — не буквально `False` (например, схема для
    значений, как раньше было у `artifacts`) — с моком это не ловится,
    только с реальным вызовом (см. `test_generate_hits_real_api`). Эта
    проверка ловит ту же ошибку в unit-тестах, без похода в сеть.
    """
    if schema.get("type") == "object":
        assert schema.get("additionalProperties") is False, (
            f"{path}: object-схема должна иметь additionalProperties: False "
            "для strict tool use (Anthropic API отклоняет схему-значение)"
        )
        for name, subschema in schema.get("properties", {}).items():
            _assert_strict_schema_compatible(subschema, path=f"{path}.{name}")
    elif schema.get("type") == "array":
        _assert_strict_schema_compatible(schema["items"], path=f"{path}[]")


def test_response_tool_schema_is_strict_compatible():
    _assert_strict_schema_compatible(anthropic_client._RESPONSE_TOOL["input_schema"])


def test_generate_parses_response_from_tool_use_input():
    fake_sdk_client = MagicMock()
    payload = {
        "status": "proposed",
        "artifacts": [{"filename": "constraints.yaml", "content": "n: 100"}],
        "notes": [{"field": "n.max", "kind": "proposed", "explanation": "guessed"}],
    }
    fake_sdk_client.messages.create.return_value = _fake_tool_use_message(payload)
    client = _client_with_fake_sdk(fake_sdk_client)

    result = client.generate(model_class="strong-model", effort="high", system_prompt="s", user_prompt="u")

    assert isinstance(result, ModelResponse)
    assert result.status == "proposed"
    assert result.artifacts == {"constraints.yaml": "n: 100"}
    assert result.notes == [{"field": "n.max", "kind": "proposed", "explanation": "guessed"}]


def test_generate_raises_if_no_matching_tool_use_block():
    fake_sdk_client = MagicMock()
    message = MagicMock()
    message.content = []
    message.stop_reason = "end_turn"
    fake_sdk_client.messages.create.return_value = message
    client = _client_with_fake_sdk(fake_sdk_client)

    with pytest.raises(RuntimeError, match="submit_step_result"):
        client.generate(model_class="strong-model", effort="high", system_prompt="s", user_prompt="u")


def test_generate_retries_on_5xx_and_eventually_succeeds(_no_sleep):
    fake_sdk_client = MagicMock()
    fake_sdk_client.messages.create.side_effect = [
        _status_error(500),
        _fake_tool_use_message(_payload()),
    ]
    client = _client_with_fake_sdk(fake_sdk_client)

    result = client.generate(model_class="strong-model", effort="high", system_prompt="s", user_prompt="u")

    assert result.status == "confirmed"
    assert fake_sdk_client.messages.create.call_count == 2
    _no_sleep.assert_called_once()


def test_generate_retries_on_overloaded_error(_no_sleep):
    fake_sdk_client = MagicMock()
    fake_sdk_client.messages.create.side_effect = [
        _status_error(529),
        _fake_tool_use_message(_payload()),
    ]
    client = _client_with_fake_sdk(fake_sdk_client)

    client.generate(model_class="strong-model", effort="high", system_prompt="s", user_prompt="u")

    assert fake_sdk_client.messages.create.call_count == 2


def test_generate_retries_on_connection_error(_no_sleep):
    fake_sdk_client = MagicMock()
    fake_sdk_client.messages.create.side_effect = [
        _connection_error(),
        _fake_tool_use_message(_payload()),
    ]
    client = _client_with_fake_sdk(fake_sdk_client)

    client.generate(model_class="strong-model", effort="high", system_prompt="s", user_prompt="u")

    assert fake_sdk_client.messages.create.call_count == 2


def test_generate_gives_up_after_max_attempts(_no_sleep):
    fake_sdk_client = MagicMock()
    fake_sdk_client.messages.create.side_effect = _status_error(500)
    client = _client_with_fake_sdk(fake_sdk_client)

    with pytest.raises(anthropic.APIStatusError):
        client.generate(model_class="strong-model", effort="high", system_prompt="s", user_prompt="u")

    assert fake_sdk_client.messages.create.call_count == anthropic_client._MAX_ATTEMPTS


def test_generate_does_not_retry_on_4xx(_no_sleep):
    fake_sdk_client = MagicMock()
    fake_sdk_client.messages.create.side_effect = _status_error(400)
    client = _client_with_fake_sdk(fake_sdk_client)

    with pytest.raises(anthropic.APIStatusError):
        client.generate(model_class="strong-model", effort="high", system_prompt="s", user_prompt="u")

    fake_sdk_client.messages.create.assert_called_once()
    _no_sleep.assert_not_called()


def test_generate_does_not_retry_on_rate_limit_429(_no_sleep):
    fake_sdk_client = MagicMock()
    fake_sdk_client.messages.create.side_effect = _status_error(429)
    client = _client_with_fake_sdk(fake_sdk_client)

    with pytest.raises(anthropic.APIStatusError):
        client.generate(model_class="strong-model", effort="high", system_prompt="s", user_prompt="u")

    fake_sdk_client.messages.create.assert_called_once()


@pytest.mark.integration
def test_generate_hits_real_api():
    """Реальный вызов Anthropic API — по умолчанию не запускается.

    Прогонять руками (`pytest -m integration`) перед первым реальным
    прогоном на задаче, с валидным ANTHROPIC_API_KEY в окружении/.env.
    """
    client = AnthropicClient()
    result = client.generate(
        model_class="medium-model",
        effort="medium",
        system_prompt=(
            "Ты тестовый ассистент. Верни status: confirmed, один артефакт "
            "'ping.txt' с содержимым 'pong', notes: []."
        ),
        user_prompt="Подтверди, что вызов API работает.",
    )
    assert result.status in {"confirmed", "proposed", "uncertain"}
