from pathlib import Path
from unittest.mock import patch

import pytest

from orchestrator.model_router import ModelResponse
from orchestrator.spec import load_spec
from orchestrator.steps.base import StepUncertainError, load_context_documents
from orchestrator.steps.interactor_draft import (
    STEP_NAME,
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
    return load_spec(FIXTURES_DIR / "interactive-spec.yaml")


@pytest.fixture
def plain_spec():
    return load_spec(FIXTURES_DIR / "valid-spec.yaml")


def _with_statement(spec, **update):
    return spec.model_copy(
        update={"statement_draft": spec.statement_draft.model_copy(update=update)}
    )


def test_extra_context_documents_is_none_without_references(spec):
    assert extra_context_documents(spec) is None


def test_primary_artifact_path_is_fixed_interactor_cpp(spec, tmp_path):
    path = primary_artifact_path("interactive-spec", spec, problems_dir=tmp_path)
    assert path == tmp_path / "interactive-spec" / "interactor.cpp"


def test_non_interactive_problem_raises_before_calling_model(plain_spec, tmp_path):
    with patch("orchestrator.model_router.call_model") as mock_call:
        with pytest.raises(ValueError, match="not interactive"):
            run_step("valid-spec", plain_spec, None, prompts_dir=PROMPTS_DIR, problems_dir=tmp_path)
    mock_call.assert_not_called()


def test_compute_input_hash_raises_for_non_interactive_problem(plain_spec):
    with pytest.raises(ValueError, match="not interactive"):
        compute_input_hash("valid-spec", plain_spec)


@pytest.mark.parametrize(
    "edit",
    [
        lambda spec: spec.model_copy(
            update={
                "interactor": spec.interactor.model_copy(update={"behavior_notes": "Адаптивный."})
            }
        ),
        lambda spec: _with_statement(spec, interaction_sketch="Не больше 20 запросов."),
        lambda spec: _with_statement(spec, formal_input_sketch="Тест: N, затем X."),
    ],
    ids=["behavior_notes", "interaction_sketch", "formal_input_sketch"],
)
def test_compute_input_hash_changes_with_its_inputs(spec, edit):
    assert compute_input_hash("interactive-spec", spec) != compute_input_hash(
        "interactive-spec", edit(spec)
    )


def test_compute_input_hash_ignores_legend(spec):
    assert compute_input_hash("interactive-spec", spec) == compute_input_hash(
        "interactive-spec", _with_statement(spec, legend_sketch="Другой сюжет.")
    )


def test_interactive_rules_are_a_fixed_context_document():
    documents = load_context_documents(STEP_NAME, templates_dir=TEMPLATES_DIR)
    assert "tutorials/interactive.md" in documents


def test_confirmed_writes_interactor_cpp(spec, tmp_path):
    response = ModelResponse(status="confirmed", artifacts={"interactor.cpp": "int main(){}"})
    with patch("orchestrator.model_router.call_model", return_value=response) as mock_call:
        result = run_step(
            "interactive-spec",
            spec,
            None,
            prompts_dir=PROMPTS_DIR,
            problems_dir=tmp_path,
            templates_dir=TEMPLATES_DIR,
        )

    assert result.status == "confirmed"
    assert (tmp_path / "interactive-spec" / "interactor.cpp").exists()
    user_prompt = mock_call.call_args.args[2]
    assert "Неадаптивный: X берётся из теста." in user_prompt
    assert 'Запрос "? y"' in user_prompt
    assert "Тест: два числа N и X" in user_prompt
    assert "10 3" in user_prompt


def test_uncertain_raises_and_writes_nothing(spec, tmp_path):
    response = ModelResponse(
        status="uncertain",
        notes=[
            {
                "field": "interactor.behavior_notes",
                "kind": "uncertain",
                "explanation": "не сказано, что писать в tout",
            }
        ],
    )
    with patch("orchestrator.model_router.call_model", return_value=response):
        with pytest.raises(StepUncertainError):
            run_step(
                "interactive-spec",
                spec,
                None,
                prompts_dir=PROMPTS_DIR,
                problems_dir=tmp_path,
                templates_dir=TEMPLATES_DIR,
            )

    assert not (tmp_path / "interactive-spec").exists()
