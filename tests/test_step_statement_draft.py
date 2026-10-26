from pathlib import Path
from unittest.mock import patch

import pytest

from orchestrator.model_router import ModelResponse
from orchestrator.spec import load_spec
from orchestrator.steps.base import MissingArtifactsError, StepUncertainError
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


def _model_artifacts() -> dict[str, str]:
    """Файлы, которые возвращает модель (input примеров пишет оркестратор)."""
    return {
        "legend.tex": "legend",
        "input_format.tex": "input",
        "output_format.tex": "output",
        "notes.tex": "notes",
    }


def _example_names(n_examples: int) -> set[str]:
    return {f"examples/example_{i}.txt" for i in range(1, n_examples + 1)}


def _all_files(n_examples: int) -> dict[str, str]:
    """Итоговый набор файлов шага: файлы модели + input примеров."""
    return {**_model_artifacts(), **{name: "x\n" for name in _example_names(n_examples)}}


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
    artifacts = _model_artifacts()
    response = ModelResponse(status="confirmed", artifacts=artifacts, notes=[])
    with patch("orchestrator.model_router.call_model", return_value=response) as mock_call:
        result = run_step(
            "valid-spec",
            spec2,
            None,
            prompts_dir=PROMPTS_DIR,
            problems_dir=tmp_path,
            templates_dir=TEMPLATES_DIR,
        )

    assert result.status == "confirmed"
    statement_dir = tmp_path / "valid-spec" / "statement"
    # и плоские имена, и вложенные examples/... раскладываются под statement/
    assert result.artifact_paths == {
        name: statement_dir / name for name in [*artifacts, *_example_names(2)]
    }
    assert (statement_dir / "legend.tex").read_text(encoding="utf-8") == "legend"
    # input примеров — копия из спека, его пишет оркестратор, а не модель
    assert (statement_dir / "examples" / "example_1.txt").read_text(encoding="utf-8") == "1\n"
    assert (statement_dir / "examples" / "example_2.txt").read_text(encoding="utf-8") == "2\n"
    assert not (tmp_path / "valid-spec" / "statement.tex").exists()

    # user.md.j2 реально отрендерился из секции statement_draft спека
    user_prompt = mock_call.call_args.args[2]
    assert "Есть граф городов и дорог" in user_prompt
    assert "1-indexed" in user_prompt
    # и явно перечисляет ожидаемые от модели имена файлов — без input примеров
    expected_list = user_prompt.split("Ожидаемые файлы в artifacts:")[1].split("Задача:")[0]
    for name in ["legend.tex", "input_format.tex", "output_format.tex", "notes.tex"]:
        assert f"- {name}" in expected_list
    assert "example_" not in expected_list


def test_mismatched_artifacts_are_not_applied_at_all(spec, tmp_path):
    artifacts = _model_artifacts()
    del artifacts["notes.tex"]
    response = ModelResponse(status="confirmed", artifacts=artifacts, notes=[])
    # и дозапрос не вернул недостающий файл
    retry = ModelResponse(status="confirmed", artifacts={}, notes=[])
    with patch("orchestrator.model_router.call_model", side_effect=[response, retry]):
        result = run_step(
            "valid-spec",
            spec,
            None,
            prompts_dir=PROMPTS_DIR,
            problems_dir=tmp_path,
            templates_dir=TEMPLATES_DIR,
        )

    problem_dir = tmp_path / "valid-spec"
    assert not (problem_dir / "statement").exists()
    assert result.artifact_paths == {}
    assert "missing files: ['notes.tex']" in result.not_applied_reason
    assert all(
        reason == "the response is not applied as a whole" for _, reason in result.conflicts
    )
    assert (result.conflicts_dir / "statement" / "legend.tex").is_file()
    # Не применено ничего — ответ остаётся для `llm apply`.
    assert (problem_dir / ".cache" / "statement_draft.response.json").is_file()


def test_primary_artifact_path_is_statement_dir(spec, tmp_path):
    path = primary_artifact_path("valid-spec", spec, problems_dir=tmp_path)
    assert path == tmp_path / "valid-spec" / "statement"
    assert path.suffix == ""


def test_expected_names_exact_match_passes(spec):
    spec2 = _with_examples(spec, 2)
    expected = _expected_artifact_names(spec2)
    assert expected == set(_all_files(2))
    _validate_statement_artifacts(expected)(_all_files(2))


def test_validate_reports_missing_file(spec):
    validate = _validate_statement_artifacts(_expected_artifact_names(spec))
    artifacts = _all_files(1)
    del artifacts["notes.tex"]
    with pytest.raises(MissingArtifactsError, match="missing.*notes.tex"):
        validate(artifacts)


def test_validate_reports_extra_example(spec):
    validate = _validate_statement_artifacts(_expected_artifact_names(_with_examples(spec, 2)))
    with pytest.raises(ValueError, match="unexpected.*examples/example_3.txt"):
        validate(_all_files(3))


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
                problems_dir=tmp_path,
                templates_dir=TEMPLATES_DIR,
            )

    assert not (tmp_path / "valid-spec").exists()


def test_preserve_legend_verbatim_true_includes_instruction(spec, tmp_path):
    spec_verbatim = spec.model_copy(
        update={"statement_draft": spec.statement_draft.model_copy(update={"preserve_legend_verbatim": True})}
    )
    response = ModelResponse(status="confirmed", artifacts=_model_artifacts(), notes=[])
    with patch("orchestrator.model_router.call_model", return_value=response) as mock_call:
        run_step(
            "valid-spec",
            spec_verbatim,
            None,
            prompts_dir=PROMPTS_DIR,
            problems_dir=tmp_path,
            templates_dir=TEMPLATES_DIR,
        )

    user_prompt = mock_call.call_args.args[2]
    assert "preserve_legend_verbatim: true" in user_prompt
    assert "НЕ действует" in user_prompt


def test_preserve_legend_verbatim_false_uses_default_instruction(spec, tmp_path):
    response = ModelResponse(status="confirmed", artifacts=_model_artifacts(), notes=[])
    with patch("orchestrator.model_router.call_model", return_value=response) as mock_call:
        run_step(
            "valid-spec",
            spec,
            None,
            prompts_dir=PROMPTS_DIR,
            problems_dir=tmp_path,
            templates_dir=TEMPLATES_DIR,
        )

    user_prompt = mock_call.call_args.args[2]
    assert "preserve_legend_verbatim: false" in user_prompt
    assert "действует стандартное правило" in user_prompt


def test_renders_without_crashing_when_known_ambiguities_is_null(spec, tmp_path):
    spec_no_ambiguities = spec.model_copy(
        update={"statement_draft": spec.statement_draft.model_copy(update={"known_ambiguities": None})}
    )
    response = ModelResponse(status="confirmed", artifacts=_model_artifacts(), notes=[])
    with patch("orchestrator.model_router.call_model", return_value=response):
        result = run_step(
            "valid-spec",
            spec_no_ambiguities,
            None,
            prompts_dir=PROMPTS_DIR,
            problems_dir=tmp_path,
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
    response = ModelResponse(status="confirmed", artifacts=_model_artifacts(), notes=[])
    with patch("orchestrator.model_router.call_model", return_value=response) as mock_call:
        run_step(
            "valid-spec",
            spec_mixed,
            None,
            prompts_dir=PROMPTS_DIR,
            problems_dir=tmp_path,
            templates_dir=TEMPLATES_DIR,
        )

    user_prompt = mock_call.call_args.args[2]
    first, second = user_prompt.split("### Пример 2")
    assert "Output (пример от автора, для примечания):\n8\n" in first
    assert "автор не указал" in second
    assert "автор не указал" not in first
    assert "None" not in second.split("## Известные")[0].split("Что важно")[0]


@pytest.fixture
def interactive_spec():
    return load_spec(FIXTURES_DIR / "interactive-spec.yaml")


def _interactive_artifacts(n_examples: int) -> dict[str, str]:
    artifacts = {**_model_artifacts(), "interaction.tex": "protocol"}
    for i in range(1, n_examples + 1):
        artifacts[f"examples/example_{i}.input_for_statement.txt"] = "10\n\n<\n"
        artifacts[f"examples/example_{i}.output_for_statement.txt"] = "\n? 5\n\n"
    return artifacts


def test_interactive_expected_names_include_protocol_and_dialogues(interactive_spec):
    assert _expected_artifact_names(interactive_spec) == (
        set(_interactive_artifacts(1)) | _example_names(1)
    )


def test_interactive_extra_context_documents_has_interactive_rules(interactive_spec):
    assert extra_context_documents(interactive_spec, templates_dir=TEMPLATES_DIR) == [
        str(TEMPLATES_DIR / "tutorials" / "interactive.md")
    ]


def test_interactive_writes_protocol_and_dialogues(interactive_spec, tmp_path):
    response = ModelResponse(status="proposed", artifacts=_interactive_artifacts(1))
    with patch("orchestrator.model_router.call_model", return_value=response) as mock_call:
        run_step(
            "interactive-spec",
            interactive_spec,
            None,
            prompts_dir=PROMPTS_DIR,
            problems_dir=tmp_path,
            templates_dir=TEMPLATES_DIR,
        )

    statement_dir = tmp_path / "interactive-spec" / "statement"
    assert (statement_dir / "interaction.tex").read_text() == "protocol"
    assert (statement_dir / "examples" / "example_1.output_for_statement.txt").exists()
    user_prompt = mock_call.call_args.args[2]
    assert "## Протокол взаимодействия" in user_prompt
    assert 'Запрос "? y"' in user_prompt
    assert "- examples/example_1.input_for_statement.txt" in user_prompt
    documents = mock_call.call_args.kwargs.get("context_documents") or mock_call.call_args.args[3]
    assert "tutorials/interactive.md" in documents


def test_interactive_without_dialogues_is_not_applied(interactive_spec, tmp_path):
    response = ModelResponse(status="confirmed", artifacts=_model_artifacts())
    retry = ModelResponse(status="confirmed", artifacts={})
    with patch("orchestrator.model_router.call_model", side_effect=[response, retry]):
        result = run_step(
            "interactive-spec",
            interactive_spec,
            None,
            prompts_dir=PROMPTS_DIR,
            problems_dir=tmp_path,
            templates_dir=TEMPLATES_DIR,
        )

    assert result.artifact_paths == {}
    assert not (tmp_path / "interactive-spec" / "statement").exists()
    assert "interaction.tex" in result.not_applied_reason


def test_plain_problem_prompt_has_no_protocol(spec, tmp_path):
    response = ModelResponse(status="confirmed", artifacts=_model_artifacts())
    with patch("orchestrator.model_router.call_model", return_value=response) as mock_call:
        run_step(
            "valid-spec",
            spec,
            None,
            prompts_dir=PROMPTS_DIR,
            problems_dir=tmp_path,
            templates_dir=TEMPLATES_DIR,
        )

    user_prompt = mock_call.call_args.args[2]
    assert "Протокол взаимодействия" not in user_prompt
    assert "interaction.tex" not in user_prompt


def test_plain_problem_hash_input_has_no_interaction_field(spec):
    # Поле, которого нет у обычной задачи, не должно менять её хеш входа:
    # иначе его появление инвалидировало бы кэш условия всех задач.
    from orchestrator.steps.statement_draft import hash_inputs

    section, _ = hash_inputs("valid-spec", spec)
    assert "interaction_sketch" not in section


def _run(spec, problem_id, tmp_path, **kwargs):
    return run_step(
        problem_id,
        spec,
        None,
        prompts_dir=PROMPTS_DIR,
        problems_dir=tmp_path,
        templates_dir=TEMPLATES_DIR,
        **kwargs,
    )


def test_missing_files_are_asked_for_once_and_applied(interactive_spec, tmp_path):
    full = _interactive_artifacts(1)
    dialogues = {name: content for name, content in full.items() if "for_statement" in name}
    first = ModelResponse(
        status="confirmed",
        artifacts={name: content for name, content in full.items() if name not in dialogues},
    )
    retry = ModelResponse(
        status="proposed",
        artifacts={**dialogues, "legend.tex": "rewritten"},
        notes=[{"field": "dialogues", "kind": "proposed", "explanation": "built by the model"}],
    )
    with patch("orchestrator.model_router.call_model", side_effect=[first, retry]) as mock_call:
        result = _run(interactive_spec, "interactive-spec", tmp_path)

    assert mock_call.call_count == 2
    retry_prompt = mock_call.call_args_list[1].args[2]
    assert retry_prompt.startswith(mock_call.call_args_list[0].args[2].rstrip("\n"))
    assert "## Дозапрос" in retry_prompt
    for name in dialogues:
        assert f"- `{name}`" in retry_prompt
    assert "#### `interaction.tex`\n\n````\nprotocol\n````" in retry_prompt

    assert result.status == "proposed"
    assert result.not_applied_reason is None
    statement_dir = tmp_path / "interactive-spec" / "statement"
    for name, content in dialogues.items():
        assert (statement_dir / name).read_text(encoding="utf-8") == content
    # из дозапроса берутся только недостающие файлы
    assert (statement_dir / "legend.tex").read_text(encoding="utf-8") == "legend"
    kinds = [note["kind"] for note in result.notes]
    assert "proposed" in kinds and "retried" in kinds and "ignored" in kinds
    assert not (tmp_path / "interactive-spec" / ".cache" / "statement_draft.response.json").exists()


def test_uncertain_follow_up_stops_the_step(interactive_spec, tmp_path):
    first = ModelResponse(status="confirmed", artifacts=_model_artifacts())
    retry = ModelResponse(
        status="uncertain",
        notes=[{"field": "interaction_sketch", "kind": "uncertain", "explanation": "no protocol"}],
    )
    with patch("orchestrator.model_router.call_model", side_effect=[first, retry]):
        with pytest.raises(StepUncertainError, match="no protocol"):
            _run(interactive_spec, "interactive-spec", tmp_path)

    problem_dir = tmp_path / "interactive-spec"
    assert not (problem_dir / "statement").exists()
    assert not (problem_dir / ".cache" / "statement_draft.response.json").exists()


def test_saved_response_is_not_completed_by_a_follow_up(spec, tmp_path):
    incomplete = _model_artifacts()
    del incomplete["notes.tex"]
    saved = ModelResponse(status="confirmed", artifacts=incomplete)
    with patch("orchestrator.model_router.call_model") as mock_call:
        result = _run(spec, "valid-spec", tmp_path, saved_response=saved)

    mock_call.assert_not_called()
    assert "notes.tex" in result.not_applied_reason


def test_example_input_from_the_model_is_replaced_by_the_spec(spec, tmp_path):
    response = ModelResponse(
        status="confirmed",
        artifacts={**_model_artifacts(), "examples/example_1.txt": "changed by the model\n"},
    )
    with patch("orchestrator.model_router.call_model", return_value=response) as mock_call:
        result = _run(spec, "valid-spec", tmp_path)

    assert mock_call.call_count == 1
    example = tmp_path / "valid-spec" / "statement" / "examples" / "example_1.txt"
    assert example.read_text(encoding="utf-8") == spec.statement_draft.sample_examples[0].input
    assert any(
        note["kind"] == "ignored" and note["field"] == "examples/example_1.txt"
        for note in result.notes
    )


def test_example_inputs_no_longer_in_the_spec_are_marked_removed(spec, tmp_path):
    examples_dir = tmp_path / "valid-spec" / "statement" / "examples"
    examples_dir.mkdir(parents=True)
    for i in (1, 2, 3):
        (examples_dir / f"example_{i}.txt").write_text(f"old {i}\n", encoding="utf-8")

    response = ModelResponse(status="confirmed", artifacts=_model_artifacts())
    with patch("orchestrator.model_router.call_model", return_value=response):
        result = _run(_with_examples(spec, 1), "valid-spec", tmp_path)

    assert (examples_dir / "example_1.txt").read_text(encoding="utf-8") == "1\n"
    assert sorted(path.name for path in examples_dir.iterdir()) == [
        "example_1.txt",
        "example_2.txt.removed",
        "example_3.txt.removed",
    ]
    assert [mark.name for mark, _ in result.removed] == [
        "example_2.txt.removed",
        "example_3.txt.removed",
    ]
    # прежняя версия перезаписанного input — в бэкапе
    assert (result.backup_dir / "statement" / "examples" / "example_1.txt").is_file()
