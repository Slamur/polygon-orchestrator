import pytest

from orchestrator.model_router import STEP_TO_MODEL, ModelResponse, call_model


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


def test_model_response_defaults():
    response = ModelResponse(status="uncertain")
    assert response.artifacts == {}
    assert response.notes == []


def test_call_model_raises_not_implemented():
    with pytest.raises(NotImplementedError):
        call_model("constraints_pick", "system", "user")
