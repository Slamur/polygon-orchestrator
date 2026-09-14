from pathlib import Path
from unittest.mock import patch

import pytest

from orchestrator.model_router import ModelResponse
from orchestrator.spec import load_spec
from orchestrator.steps.base import StepUncertainError
from orchestrator.steps.constraints_pick import extra_context_documents, run_step

REPO_ROOT = Path(__file__).parent.parent
PROMPTS_DIR = REPO_ROOT / "prompts"
TEMPLATES_DIR = REPO_ROOT / "templates"
FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture
def spec():
    return load_spec(FIXTURES_DIR / "valid-spec.yaml")


def test_extra_context_documents_is_none(spec):
    assert extra_context_documents(spec) is None


def test_confirmed_writes_constraints_yaml(spec, tmp_path):
    response = ModelResponse(status="confirmed", artifacts={"constraints.yaml": "n_max: 100000"}, notes=[])
    with patch("orchestrator.model_router.call_model", return_value=response) as mock_call:
        result = run_step(
            "valid-spec",
            spec,
            None,
            prompts_dir=PROMPTS_DIR,
            outputs_dir=tmp_path,
            templates_dir=TEMPLATES_DIR,
        )

    assert result.status == "confirmed"
    written_path = tmp_path / "valid-spec" / "constraints.yaml"
    assert written_path.read_text(encoding="utf-8") == "n_max: 100000"

    user_prompt = mock_call.call_args.args[2]
    assert "O((N + K) log N)" in user_prompt


def test_confirmed_writes_validator_cpp_and_compiles(spec, tmp_path):
    valid_validator = (TEMPLATES_DIR / "validator.cpp").read_text(encoding="utf-8")
    response = ModelResponse(
        status="confirmed",
        artifacts={"constraints.yaml": "n_max: 100000", "validator.cpp": valid_validator},
        notes=[],
    )
    with patch("orchestrator.model_router.call_model", return_value=response):
        result = run_step(
            "valid-spec",
            spec,
            None,
            prompts_dir=PROMPTS_DIR,
            outputs_dir=tmp_path,
            templates_dir=TEMPLATES_DIR,
        )

    assert result.status == "confirmed"
    validator_path = tmp_path / "valid-spec" / "validator.cpp"
    assert validator_path.read_text(encoding="utf-8") == valid_validator
    assert result.compile_results["validator.cpp"].success is True
    if result.compile_results["validator.cpp"].binary_path:
        result.compile_results["validator.cpp"].binary_path.unlink(missing_ok=True)


def test_uncertain_raises_and_writes_nothing(spec, tmp_path):
    response = ModelResponse(
        status="uncertain",
        artifacts={},
        notes=[{"field": "constraints.intended_complexity", "kind": "uncertain", "explanation": "пусто"}],
    )
    with patch("orchestrator.model_router.call_model", return_value=response):
        with pytest.raises(StepUncertainError):
            run_step(
                "valid-spec",
                spec,
                None,
                prompts_dir=PROMPTS_DIR,
                outputs_dir=tmp_path,
                templates_dir=TEMPLATES_DIR,
            )

    assert not (tmp_path / "valid-spec").exists()
    assert not (tmp_path / "valid-spec" / "validator.cpp").exists()


def test_renders_without_crashing_when_optional_lists_are_null(spec, tmp_path):
    spec_bare = spec.model_copy(
        update={
            "constraints": spec.constraints.model_copy(
                update={
                    "special_guarantees": None,
                    "test_groups_hint": None,
                    "uncertain_points": None,
                }
            )
        }
    )
    response = ModelResponse(status="confirmed", artifacts={"constraints.yaml": "ok"}, notes=[])
    with patch("orchestrator.model_router.call_model", return_value=response):
        result = run_step(
            "valid-spec",
            spec_bare,
            None,
            prompts_dir=PROMPTS_DIR,
            outputs_dir=tmp_path,
            templates_dir=TEMPLATES_DIR,
        )

    assert result.status == "confirmed"
