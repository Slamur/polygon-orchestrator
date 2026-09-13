import os
from unittest.mock import MagicMock, patch

import anthropic
import httpx2
import pytest

from orchestrator import model_router
from orchestrator.model_router import (
    MODEL_CLASS_TO_ID,
    STEP_TO_EFFORT,
    STEP_TO_MODEL,
    ModelResponse,
    call_model,
)


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
        "artifacts": {"statement.tex": "content"},
        "notes": [],
    }


@pytest.fixture(autouse=True)
def _reset_singleton_client():
    # `_get_client()` caches a client on the module; every test replaces it
    # directly, so make sure no stale client leaks between tests.
    model_router._client = None
    yield
    model_router._client = None


@pytest.fixture(autouse=True)
def _no_sleep():
    with patch("orchestrator.model_router.time.sleep") as mock_sleep:
        yield mock_sleep


def test_step_to_model_covers_all_four_steps():
    assert set(STEP_TO_MODEL) == {
        "statement_draft",
        "constraints_pick",
        "generators_and_script",
        "solutions_draft",
    }


def test_critical_steps_use_strong_model():
    assert STEP_TO_MODEL["constraints_pick"] == "strong-model"
    assert STEP_TO_MODEL["solutions_draft"] == "strong-model"


def test_generators_and_script_uses_medium_model():
    assert STEP_TO_MODEL["generators_and_script"] == "medium-model"


def test_statement_draft_uses_medium_model():
    assert STEP_TO_MODEL["statement_draft"] == "medium-model"


def test_model_class_to_id_has_entry_for_every_used_class():
    for step, model_class in STEP_TO_MODEL.items():
        assert model_class in MODEL_CLASS_TO_ID, f"no model id for class of {step}"


def test_step_to_effort_covers_all_four_steps():
    assert set(STEP_TO_EFFORT) == set(STEP_TO_MODEL)
    assert STEP_TO_EFFORT["constraints_pick"] == "high"
    assert STEP_TO_EFFORT["solutions_draft"] == "high"
    assert STEP_TO_EFFORT["generators_and_script"] == "high"
    assert STEP_TO_EFFORT["statement_draft"] == "medium"


def test_model_response_defaults():
    response = ModelResponse(status="uncertain")
    assert response.artifacts == {}
    assert response.notes == []


def test_get_client_raises_readable_error_without_api_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(model_router, "_ensure_env_loaded", lambda: None)
    with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY"):
        model_router._get_client()


def test_call_model_uses_model_and_effort_for_step(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    fake_client = MagicMock()
    fake_client.messages.create.return_value = _fake_tool_use_message(_payload())
    monkeypatch.setattr(model_router, "_get_client", lambda: fake_client)

    call_model("constraints_pick", "system prompt", "user prompt")

    fake_client.messages.create.assert_called_once()
    kwargs = fake_client.messages.create.call_args.kwargs
    assert kwargs["model"] == MODEL_CLASS_TO_ID["strong-model"]
    assert kwargs["output_config"] == {"effort": "high"}
    assert kwargs["system"] == "system prompt"
    assert kwargs["messages"] == [{"role": "user", "content": "user prompt"}]


def test_call_model_forces_tool_choice_on_response_tool(monkeypatch):
    fake_client = MagicMock()
    fake_client.messages.create.return_value = _fake_tool_use_message(_payload())
    monkeypatch.setattr(model_router, "_get_client", lambda: fake_client)

    call_model("statement_draft", "system", "user")

    kwargs = fake_client.messages.create.call_args.kwargs
    assert kwargs["tool_choice"] == {"type": "tool", "name": "submit_step_result"}
    assert len(kwargs["tools"]) == 1
    assert kwargs["tools"][0]["name"] == "submit_step_result"
    assert kwargs["tools"][0]["strict"] is True


def test_call_model_parses_response_from_tool_use_input(monkeypatch):
    fake_client = MagicMock()
    payload = {
        "status": "proposed",
        "artifacts": {"constraints.yaml": "n: 100"},
        "notes": [{"field": "n.max", "kind": "proposed", "explanation": "guessed"}],
    }
    fake_client.messages.create.return_value = _fake_tool_use_message(payload)
    monkeypatch.setattr(model_router, "_get_client", lambda: fake_client)

    result = call_model("constraints_pick", "system", "user")

    assert isinstance(result, ModelResponse)
    assert result.status == "proposed"
    assert result.artifacts == {"constraints.yaml": "n: 100"}
    assert result.notes == [{"field": "n.max", "kind": "proposed", "explanation": "guessed"}]


def test_call_model_raises_if_no_matching_tool_use_block(monkeypatch):
    fake_client = MagicMock()
    message = MagicMock()
    message.content = []
    message.stop_reason = "end_turn"
    fake_client.messages.create.return_value = message
    monkeypatch.setattr(model_router, "_get_client", lambda: fake_client)

    with pytest.raises(RuntimeError, match="submit_step_result"):
        call_model("constraints_pick", "system", "user")


def test_call_model_retries_on_5xx_and_eventually_succeeds(monkeypatch, _no_sleep):
    fake_client = MagicMock()
    fake_client.messages.create.side_effect = [
        _status_error(500),
        _fake_tool_use_message(_payload()),
    ]
    monkeypatch.setattr(model_router, "_get_client", lambda: fake_client)

    result = call_model("constraints_pick", "system", "user")

    assert result.status == "confirmed"
    assert fake_client.messages.create.call_count == 2
    _no_sleep.assert_called_once()


def test_call_model_retries_on_overloaded_error(monkeypatch, _no_sleep):
    fake_client = MagicMock()
    fake_client.messages.create.side_effect = [
        _status_error(529),
        _fake_tool_use_message(_payload()),
    ]
    monkeypatch.setattr(model_router, "_get_client", lambda: fake_client)

    call_model("constraints_pick", "system", "user")

    assert fake_client.messages.create.call_count == 2


def test_call_model_retries_on_connection_error(monkeypatch, _no_sleep):
    fake_client = MagicMock()
    fake_client.messages.create.side_effect = [
        _connection_error(),
        _fake_tool_use_message(_payload()),
    ]
    monkeypatch.setattr(model_router, "_get_client", lambda: fake_client)

    call_model("constraints_pick", "system", "user")

    assert fake_client.messages.create.call_count == 2


def test_call_model_gives_up_after_max_attempts(monkeypatch, _no_sleep):
    fake_client = MagicMock()
    fake_client.messages.create.side_effect = _status_error(500)
    monkeypatch.setattr(model_router, "_get_client", lambda: fake_client)

    with pytest.raises(anthropic.APIStatusError):
        call_model("constraints_pick", "system", "user")

    assert fake_client.messages.create.call_count == model_router._MAX_ATTEMPTS


def test_call_model_does_not_retry_on_4xx(monkeypatch, _no_sleep):
    fake_client = MagicMock()
    fake_client.messages.create.side_effect = _status_error(400)
    monkeypatch.setattr(model_router, "_get_client", lambda: fake_client)

    with pytest.raises(anthropic.APIStatusError):
        call_model("constraints_pick", "system", "user")

    fake_client.messages.create.assert_called_once()
    _no_sleep.assert_not_called()


def test_call_model_does_not_retry_on_rate_limit_429(monkeypatch, _no_sleep):
    fake_client = MagicMock()
    fake_client.messages.create.side_effect = _status_error(429)
    monkeypatch.setattr(model_router, "_get_client", lambda: fake_client)

    with pytest.raises(anthropic.APIStatusError):
        call_model("constraints_pick", "system", "user")

    fake_client.messages.create.assert_called_once()


@pytest.mark.integration
def test_call_model_hits_real_api():
    """Реальный вызов Anthropic API — по умолчанию не запускается.

    Прогонять руками (`pytest -m integration`) перед первым реальным
    прогоном на задаче, с валидным ANTHROPIC_API_KEY в окружении/.env.
    """
    model_router._client = None
    result = call_model(
        "statement_draft",
        "Ты тестовый ассистент. Верни status: confirmed, один артефакт "
        "'ping.txt' с содержимым 'pong', notes: [].",
        "Подтверди, что вызов API работает.",
    )
    assert result.status in {"confirmed", "proposed", "uncertain"}
