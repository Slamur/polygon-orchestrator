from pathlib import Path
from unittest.mock import patch

import pytest

from orchestrator.model_router import ModelResponse
from orchestrator.spec import load_spec
from orchestrator.steps.base import StepUncertainError
from orchestrator.steps.generators_and_script import run_step

REPO_ROOT = Path(__file__).parent.parent
PROMPTS_DIR = REPO_ROOT / "prompts"
TEMPLATES_DIR = REPO_ROOT / "templates"
FIXTURES_DIR = Path(__file__).parent / "fixtures"
CPP_FIXTURES_DIR = FIXTURES_DIR / "cpp"


@pytest.fixture
def spec():
    return load_spec(FIXTURES_DIR / "valid-spec.yaml")


def test_missing_constraints_upstream_artifact_raises(spec, tmp_path):
    with pytest.raises(ValueError, match="constraints.yaml"):
        run_step("valid-spec", spec, None, prompts_dir=PROMPTS_DIR, outputs_dir=tmp_path)

    with pytest.raises(ValueError, match="constraints.yaml"):
        run_step("valid-spec", spec, {}, prompts_dir=PROMPTS_DIR, outputs_dir=tmp_path)


def test_confirmed_splits_cpp_and_script_artifacts(spec, tmp_path):
    valid_cpp = (CPP_FIXTURES_DIR / "valid_generator.cpp").read_text(encoding="utf-8")
    response = ModelResponse(
        status="confirmed",
        artifacts={"gen_rand.cpp": valid_cpp, "test_script_groups": "<#-- freemarker --#>"},
        notes=[],
    )
    upstream = {"constraints.yaml": "n_max: 100000\ntime_limit_seconds: 2"}
    with patch("orchestrator.model_router.call_model", return_value=response) as mock_call:
        result = run_step(
            "valid-spec",
            spec,
            upstream,
            prompts_dir=PROMPTS_DIR,
            outputs_dir=tmp_path,
            templates_dir=TEMPLATES_DIR,
        )

    assert result.status == "confirmed"
    gen_path = tmp_path / "valid-spec" / "generators" / "gen_rand.cpp"
    script_path = tmp_path / "valid-spec" / "test_script_groups"
    assert gen_path.exists()
    assert script_path.read_text(encoding="utf-8") == "<#-- freemarker --#>"
    assert result.compile_results["gen_rand.cpp"].success is True
    if result.compile_results["gen_rand.cpp"].binary_path:
        result.compile_results["gen_rand.cpp"].binary_path.unlink(missing_ok=True)

    user_prompt = mock_call.call_args.args[2]
    assert "n_max: 100000" in user_prompt
    assert "groups" in user_prompt


def test_uncertain_raises_and_writes_nothing(spec, tmp_path):
    response = ModelResponse(
        status="uncertain",
        artifacts={},
        notes=[{"field": "generation.input_shape", "kind": "uncertain", "explanation": "неясна форма"}],
    )
    upstream = {"constraints.yaml": "n_max: 100000"}
    with patch("orchestrator.model_router.call_model", return_value=response):
        with pytest.raises(StepUncertainError):
            run_step(
                "valid-spec",
                spec,
                upstream,
                prompts_dir=PROMPTS_DIR,
                outputs_dir=tmp_path,
                templates_dir=TEMPLATES_DIR,
            )

    assert not (tmp_path / "valid-spec").exists()


def test_renders_without_crashing_when_optional_lists_are_null(spec, tmp_path):
    spec_bare = spec.model_copy(
        update={
            "generation": spec.generation.model_copy(
                update={"generator_ideas": None, "base_template_refs": None}
            )
        }
    )
    response = ModelResponse(status="confirmed", artifacts={"test_script": "ok"}, notes=[])
    upstream = {"constraints.yaml": "n_max: 1"}
    with patch("orchestrator.model_router.call_model", return_value=response):
        result = run_step(
            "valid-spec",
            spec_bare,
            upstream,
            prompts_dir=PROMPTS_DIR,
            outputs_dir=tmp_path,
            templates_dir=TEMPLATES_DIR,
        )

    assert result.status == "confirmed"
