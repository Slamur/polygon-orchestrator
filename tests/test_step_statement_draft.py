from pathlib import Path
from unittest.mock import patch

import pytest

from orchestrator.model_router import ModelResponse
from orchestrator.spec import load_spec
from orchestrator.steps.base import StepUncertainError
from orchestrator.steps.statement_draft import (
    _expected_artifact_names,
    _validate_statement_artifacts,
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


def _full_artifacts(n_examples: int) -> dict[str, str]:
    artifacts = {
        "legend.tex": "legend",
        "input_format.tex": "input",
        "output_format.tex": "output",
        "notes.tex": "notes",
    }
    for i in range(1, n_examples + 1):
        artifacts[f"examples/example_{i}.txt"] = f"example {i}\n"
    return artifacts


def _with_examples(spec, n: int):
    base = spec.statement_draft.sample_examples[0]
    examples = [base.model_copy(update={"input": f"{i}\n"}) for i in range(1, n + 1)]
    return spec.model_copy(
        update={"statement_draft": spec.statement_draft.model_copy(update={"sample_examples": examples})}
    )


def test_extra_context_documents_is_none(spec):
    assert extra_context_documents(spec) is None


def test_confirmed_writes_files_under_statement_subdir(spec, tmp_path):
    spec2 = _with_examples(spec, 2)
    artifacts = _full_artifacts(2)
    response = ModelResponse(status="confirmed", artifacts=artifacts, notes=[])
    with patch("orchestrator.model_router.call_model", return_value=response) as mock_call:
        result = run_step(
            "valid-spec",
            spec2,
            None,
            prompts_dir=PROMPTS_DIR,
            outputs_dir=tmp_path,
            templates_dir=TEMPLATES_DIR,
        )

    assert result.status == "confirmed"
    statement_dir = tmp_path / "valid-spec" / "statement"
    # и плоские имена, и вложенные examples/... раскладываются под statement/
    assert result.artifact_paths == {name: statement_dir / name for name in artifacts}
    assert (statement_dir / "legend.tex").read_text(encoding="utf-8") == "legend"
    assert (statement_dir / "examples" / "example_1.txt").read_text(encoding="utf-8") == "example 1\n"
    assert (statement_dir / "examples" / "example_2.txt").read_text(encoding="utf-8") == "example 2\n"
    assert not (tmp_path / "valid-spec" / "statement.tex").exists()

    # user.md.j2 реально отрендерился из секции statement_draft спека
    user_prompt = mock_call.call_args.args[2]
    assert "Есть граф городов и дорог" in user_prompt
    assert "1-indexed" in user_prompt
    # и явно перечисляет ожидаемые имена файлов с правильным N
    expected_list = user_prompt.split("Ожидаемые файлы в artifacts:")[1].split("Задача:")[0]
    for name in ["legend.tex", "input_format.tex", "output_format.tex", "notes.tex",
                 "examples/example_1.txt", "examples/example_2.txt"]:
        assert f"- {name}" in expected_list
    assert "example_3" not in expected_list


def test_mismatched_artifacts_raise_and_write_nothing(spec, tmp_path):
    artifacts = _full_artifacts(1)
    del artifacts["notes.tex"]
    response = ModelResponse(status="confirmed", artifacts=artifacts, notes=[])
    with patch("orchestrator.model_router.call_model", return_value=response):
        with pytest.raises(ValueError, match="notes.tex"):
            run_step(
                "valid-spec",
                spec,
                None,
                prompts_dir=PROMPTS_DIR,
                outputs_dir=tmp_path,
                templates_dir=TEMPLATES_DIR,
            )

    assert not (tmp_path / "valid-spec").exists()


def test_primary_artifact_path_is_statement_dir(spec, tmp_path):
    path = primary_artifact_path("valid-spec", spec, outputs_dir=tmp_path)
    assert path == tmp_path / "valid-spec" / "statement"
    assert path.suffix == ""


def test_expected_names_exact_match_passes(spec):
    spec2 = _with_examples(spec, 2)
    expected = _expected_artifact_names(spec2)
    assert expected == set(_full_artifacts(2))
    _validate_statement_artifacts(expected)(_full_artifacts(2))


def test_validate_reports_missing_file(spec):
    validate = _validate_statement_artifacts(_expected_artifact_names(spec))
    artifacts = _full_artifacts(1)
    del artifacts["notes.tex"]
    with pytest.raises(ValueError, match="missing.*notes.tex"):
        validate(artifacts)


def test_validate_reports_extra_example(spec):
    validate = _validate_statement_artifacts(_expected_artifact_names(_with_examples(spec, 2)))
    with pytest.raises(ValueError, match="unexpected.*examples/example_3.txt"):
        validate(_full_artifacts(3))


def test_expected_names_without_sample_examples(spec):
    expected = _expected_artifact_names(_with_examples(spec, 0))
    assert expected == {"legend.tex", "input_format.tex", "output_format.tex", "notes.tex"}


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


def test_preserve_legend_verbatim_true_includes_instruction(spec, tmp_path):
    spec_verbatim = spec.model_copy(
        update={"statement_draft": spec.statement_draft.model_copy(update={"preserve_legend_verbatim": True})}
    )
    response = ModelResponse(status="confirmed", artifacts=_full_artifacts(1), notes=[])
    with patch("orchestrator.model_router.call_model", return_value=response) as mock_call:
        run_step(
            "valid-spec",
            spec_verbatim,
            None,
            prompts_dir=PROMPTS_DIR,
            outputs_dir=tmp_path,
            templates_dir=TEMPLATES_DIR,
        )

    user_prompt = mock_call.call_args.args[2]
    assert "preserve_legend_verbatim: true" in user_prompt
    assert "НЕ действует" in user_prompt


def test_preserve_legend_verbatim_false_uses_default_instruction(spec, tmp_path):
    response = ModelResponse(status="confirmed", artifacts=_full_artifacts(1), notes=[])
    with patch("orchestrator.model_router.call_model", return_value=response) as mock_call:
        run_step(
            "valid-spec",
            spec,
            None,
            prompts_dir=PROMPTS_DIR,
            outputs_dir=tmp_path,
            templates_dir=TEMPLATES_DIR,
        )

    user_prompt = mock_call.call_args.args[2]
    assert "preserve_legend_verbatim: false" in user_prompt
    assert "действует стандартное правило" in user_prompt


def test_renders_without_crashing_when_known_ambiguities_is_null(spec, tmp_path):
    spec_no_ambiguities = spec.model_copy(
        update={"statement_draft": spec.statement_draft.model_copy(update={"known_ambiguities": None})}
    )
    response = ModelResponse(status="confirmed", artifacts=_full_artifacts(1), notes=[])
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


def test_prompt_marks_missing_sample_output_and_keeps_given_one(spec, tmp_path):
    examples = [
        spec.statement_draft.sample_examples[0].model_copy(update={"output": "8\n"}),
        spec.statement_draft.sample_examples[0].model_copy(update={"input": "2 1\n1 2 4\n1 2\n", "output": None}),
    ]
    spec_mixed = spec.model_copy(
        update={"statement_draft": spec.statement_draft.model_copy(update={"sample_examples": examples})}
    )
    response = ModelResponse(status="confirmed", artifacts=_full_artifacts(2), notes=[])
    with patch("orchestrator.model_router.call_model", return_value=response) as mock_call:
        run_step(
            "valid-spec",
            spec_mixed,
            None,
            prompts_dir=PROMPTS_DIR,
            outputs_dir=tmp_path,
            templates_dir=TEMPLATES_DIR,
        )

    user_prompt = mock_call.call_args.args[2]
    first, second = user_prompt.split("### Пример 2")
    assert "Output (пример от автора, для примечания):\n8\n" in first
    assert "автор не указал" in second
    assert "автор не указал" not in first
    assert "None" not in second.split("## Известные")[0].split("Что важно")[0]
