from pathlib import Path
from unittest.mock import patch

import pytest

from orchestrator.model_router import ModelResponse
from orchestrator.spec import load_spec
from orchestrator.steps.base import StepUncertainError
from orchestrator.steps.checker_draft import (
    compute_input_hash,
    extra_context_documents,
    primary_artifact_path,
    run_step,
)

REPO_ROOT = Path(__file__).parent.parent
PROMPTS_DIR = REPO_ROOT / "prompts"
TEMPLATES_DIR = REPO_ROOT / "templates"
FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture
def spec():
    return load_spec(FIXTURES_DIR / "valid-spec.yaml")


@pytest.fixture
def spec_custom(spec):
    # Фикстура valid-spec.yaml по умолчанию заказывает стандартный чекер
    # (checker.custom_needed: false) — для тестов этого шага нужен спек,
    # где custom_needed: true.
    return spec.model_copy(
        update={
            "checker": spec.checker.model_copy(
                update={
                    "custom_needed": True,
                    "custom_comparison_notes": "Сравнивать числа с точностью 1e-6",
                }
            )
        }
    )


def test_extra_context_documents_is_none(spec_custom):
    assert extra_context_documents(spec_custom) is None


def test_primary_artifact_path_is_fixed_checker_cpp(spec_custom, tmp_path):
    path = primary_artifact_path("valid-spec", spec_custom, outputs_dir=tmp_path)
    assert path == tmp_path / "valid-spec" / "checker.cpp"


def test_custom_needed_false_raises_before_calling_model(spec, tmp_path):
    with patch("orchestrator.model_router.call_model") as mock_call:
        with pytest.raises(ValueError, match="custom_needed"):
            run_step("valid-spec", spec, None, prompts_dir=PROMPTS_DIR, outputs_dir=tmp_path)
    mock_call.assert_not_called()


def test_compute_input_hash_raises_when_custom_needed_false(spec):
    with pytest.raises(ValueError, match="custom_needed"):
        compute_input_hash("valid-spec", spec)


def test_compute_input_hash_changes_with_custom_comparison_notes(spec_custom):
    base_hash = compute_input_hash("valid-spec", spec_custom)

    edited = spec_custom.model_copy(
        update={
            "checker": spec_custom.checker.model_copy(
                update={"custom_comparison_notes": "Сравнивать числа с точностью 1e-9"}
            )
        }
    )
    edited_hash = compute_input_hash("valid-spec", edited)

    assert base_hash != edited_hash


def test_compute_input_hash_stable_for_same_input(spec_custom):
    assert compute_input_hash("valid-spec", spec_custom) == compute_input_hash(
        "valid-spec", spec_custom
    )


def test_confirmed_writes_checker_cpp(spec_custom, tmp_path):
    response = ModelResponse(
        status="confirmed",
        artifacts={"checker.cpp": "int main(){}"},
        notes=[],
    )
    with patch("orchestrator.model_router.call_model", return_value=response) as mock_call:
        result = run_step(
            "valid-spec",
            spec_custom,
            None,
            prompts_dir=PROMPTS_DIR,
            outputs_dir=tmp_path,
            templates_dir=TEMPLATES_DIR,
        )

    assert result.status == "confirmed"
    written_path = tmp_path / "valid-spec" / "checker.cpp"
    assert written_path.exists()

    user_prompt = mock_call.call_args.args[2]
    assert "Сравнивать числа с точностью 1e-6" in user_prompt


def test_uncertain_raises_and_writes_nothing(spec_custom, tmp_path):
    response = ModelResponse(
        status="uncertain",
        artifacts={},
        notes=[
            {
                "field": "checker.custom_comparison_notes",
                "kind": "uncertain",
                "explanation": "не сказано про регистр",
            }
        ],
    )
    with patch("orchestrator.model_router.call_model", return_value=response):
        with pytest.raises(StepUncertainError):
            run_step(
                "valid-spec",
                spec_custom,
                None,
                prompts_dir=PROMPTS_DIR,
                outputs_dir=tmp_path,
                templates_dir=TEMPLATES_DIR,
            )

    assert not (tmp_path / "valid-spec").exists()
