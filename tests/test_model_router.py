import pytest

from orchestrator import model_router
from orchestrator.model_router import STEP_TO_EFFORT, STEP_TO_MODEL, ModelResponse, call_model


class _FakeLLMClient:
    """Тестовый дублёр `LLMClient` — доказывает, что `model_router.call_model`
    не завязан ни на какого конкретного вендора и умеет работать с любым
    объектом, реализующим протокол `generate(...)`.
    """

    def __init__(self, response: ModelResponse):
        self.response = response
        self.calls: list[dict] = []

    def generate(self, *, model_class, effort, system_prompt, user_prompt, context_documents=None):
        self.calls.append(
            {
                "model_class": model_class,
                "effort": effort,
                "system_prompt": system_prompt,
                "user_prompt": user_prompt,
                "context_documents": context_documents,
            }
        )
        return self.response


@pytest.fixture(autouse=True)
def _reset_client():
    model_router._client = None
    yield
    model_router._client = None


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


def test_call_model_delegates_to_registered_client_with_step_routing():
    fake_response = ModelResponse(status="confirmed", artifacts={"a.tex": "x"})
    fake_client = _FakeLLMClient(fake_response)
    model_router.set_client(fake_client)

    result = call_model("constraints_pick", "system prompt", "user prompt")

    assert result is fake_response
    assert fake_client.calls == [
        {
            "model_class": "strong-model",
            "effort": "high",
            "system_prompt": "system prompt",
            "user_prompt": "user prompt",
            "context_documents": None,
        }
    ]


def test_call_model_routes_different_steps_to_their_own_class_and_effort():
    fake_client = _FakeLLMClient(ModelResponse(status="confirmed"))
    model_router.set_client(fake_client)

    call_model("statement_draft", "sys", "usr")

    assert fake_client.calls[-1]["model_class"] == "medium-model"
    assert fake_client.calls[-1]["effort"] == "medium"


def test_set_client_lets_a_different_vendor_replace_the_default(monkeypatch):
    # Ничего Anthropic-специфичного не импортируется/не требуется, если
    # клиент подменён явно — это и есть точка расширения под другого
    # вендора (см. `orchestrator/llm_client.py`).
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    fake_client = _FakeLLMClient(ModelResponse(status="proposed"))
    model_router.set_client(fake_client)

    result = call_model("solutions_draft", "sys", "usr")

    assert result.status == "proposed"
    assert len(fake_client.calls) == 1


def test_call_model_forwards_context_documents_to_client():
    fake_client = _FakeLLMClient(ModelResponse(status="confirmed"))
    model_router.set_client(fake_client)

    call_model("constraints_pick", "sys", "usr", context_documents={"a.md": "content"})

    assert fake_client.calls[-1]["context_documents"] == {"a.md": "content"}


def test_call_model_uses_default_client_when_none_registered(monkeypatch):
    fake_client = _FakeLLMClient(ModelResponse(status="confirmed"))
    monkeypatch.setattr(model_router, "_default_client", lambda: fake_client)

    call_model("constraints_pick", "sys", "usr")

    assert fake_client.calls
