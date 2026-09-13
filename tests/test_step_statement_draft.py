from pathlib import Path
from unittest.mock import patch

import pytest

from orchestrator.model_router import ModelResponse
from orchestrator.spec import load_spec
from orchestrator.steps.base import StepUncertainError
from orchestrator.steps.statement_draft import run_step

REPO_ROOT = Path(__file__).parent.parent
PROMPTS_DIR = REPO_ROOT / "prompts"
TEMPLATES_DIR = REPO_ROOT / "templates"
FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture
def spec():
    return load_spec(FIXTURES_DIR / "valid-spec.yaml")


def test_confirmed_writes_statement_tex(spec, tmp_path):
    response = ModelResponse(status="confirmed", artifacts={"statement.tex": "\\section{...}"}, notes=[])
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
    written_path = tmp_path / "valid-spec" / "statement.tex"
    assert written_path.read_text(encoding="utf-8") == "\\section{...}"
    assert result.artifact_paths == {"statement.tex": written_path}

    # user.md.j2 реально отрендерился из секции statement_draft спека
    user_prompt = mock_call.call_args.args[2]
    assert "Есть граф городов и дорог" in user_prompt
    assert "1-indexed" in user_prompt


def test_uncertain_raises_and_writes_nothing(spec, tmp_path):
    response = ModelResponse(
        status="uncertain",
        artifacts={},
        notes=[{"field": "statement_draft.formal_output_sketch", "kind": "uncertain", "explanation": "нет точности"}],
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


def test_renders_without_crashing_when_known_ambiguities_is_null(spec, tmp_path):
    spec_no_ambiguities = spec.model_copy(
        update={"statement_draft": spec.statement_draft.model_copy(update={"known_ambiguities": None})}
    )
    response = ModelResponse(status="confirmed", artifacts={"statement.tex": "ok"}, notes=[])
    with patch("orchestrator.model_router.call_model", return_value=response):
        result = run_step(
            "valid-spec",
            spec_no_ambiguities,
            None,
            prompts_dir=PROMPTS_DIR,
            outputs_dir=tmp_path,
            templates_dir=TEMPLATES_DIR,
        )

    assert result.status == "confirmed"
