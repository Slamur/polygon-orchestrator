from pathlib import Path
from unittest.mock import patch

import pytest

from orchestrator.cache import load_cache_entry
from orchestrator.model_router import ModelResponse
from orchestrator.steps.base import (
    STEP_CONTEXT_DOCUMENTS,
    StepUncertainError,
    load_context_documents,
    render_user_prompt,
    run_generative_step,
    with_default_lists,
)

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "cpp"
TEMPLATES_DIR = Path(__file__).parent.parent / "templates"


def _write_prompt(prompts_dir: Path, step_name: str, user_template: str) -> None:
    step_dir = prompts_dir / step_name
    step_dir.mkdir(parents=True, exist_ok=True)
    (step_dir / "system.md").write_text("system rules", encoding="utf-8")
    (step_dir / "user.md.j2").write_text(user_template, encoding="utf-8")


# --- with_default_lists -----------------------------------------------------


def test_with_default_lists_fills_none_with_empty_list():
    data = {"a": None, "b": [1, 2]}
    result = with_default_lists(data, ["a"])
    assert result == {"a": [], "b": [1, 2]}


def test_with_default_lists_leaves_present_values_untouched():
    data = {"a": ["x"]}
    result = with_default_lists(data, ["a"])
    assert result == {"a": ["x"]}


def test_with_default_lists_does_not_mutate_input():
    data = {"a": None}
    with_default_lists(data, ["a"])
    assert data == {"a": None}


# --- render_user_prompt / read_system_prompt --------------------------------


def test_render_user_prompt_substitutes_context(tmp_path):
    _write_prompt(tmp_path, "statement_draft", "hello {{ name }}")
    rendered = render_user_prompt("statement_draft", {"name": "world"}, prompts_dir=tmp_path)
    assert rendered == "hello world"


def test_render_user_prompt_for_loop_needs_non_null_list(tmp_path):
    _write_prompt(tmp_path, "statement_draft", "{% for x in items %}{{ x }}{% endfor %}")
    with pytest.raises(TypeError):
        render_user_prompt("statement_draft", {"items": None}, prompts_dir=tmp_path)
    # с нормализацией None -> [] (with_default_lists) рендеринг не падает
    rendered = render_user_prompt("statement_draft", {"items": []}, prompts_dir=tmp_path)
    assert rendered == ""


# --- load_context_documents --------------------------------------------------


def _write_template_doc(templates_dir: Path, rel_path: str, content: str) -> Path:
    path = templates_dir / rel_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def test_load_context_documents_reads_fixed_list_for_step(tmp_path):
    for rel_path in STEP_CONTEXT_DOCUMENTS["statement_draft"]:
        _write_template_doc(tmp_path, rel_path, f"content of {rel_path}")

    documents = load_context_documents("statement_draft", templates_dir=tmp_path)

    assert list(documents) == STEP_CONTEXT_DOCUMENTS["statement_draft"]
    for rel_path in STEP_CONTEXT_DOCUMENTS["statement_draft"]:
        assert documents[rel_path] == f"content of {rel_path}"


def test_load_context_documents_unknown_step_has_no_fixed_documents(tmp_path):
    assert load_context_documents("no-such-step", templates_dir=tmp_path) == {}


def test_load_context_documents_missing_fixed_document_raises(tmp_path):
    with pytest.raises(FileNotFoundError, match="tutorials/requirements.md"):
        load_context_documents("constraints_pick", templates_dir=tmp_path)


def test_load_context_documents_appends_extra_paths_after_fixed_list(tmp_path):
    for rel_path in STEP_CONTEXT_DOCUMENTS["constraints_pick"]:
        _write_template_doc(tmp_path, rel_path, "fixed content")
    extra_file = tmp_path / "extra.cpp"
    extra_file.write_text("extra content", encoding="utf-8")

    documents = load_context_documents(
        "constraints_pick", templates_dir=tmp_path, extra_paths=[str(extra_file)]
    )

    assert list(documents) == [*STEP_CONTEXT_DOCUMENTS["constraints_pick"], str(extra_file)]
    assert documents[str(extra_file)] == "extra content"


def test_load_context_documents_missing_extra_path_raises(tmp_path):
    for rel_path in STEP_CONTEXT_DOCUMENTS["constraints_pick"]:
        _write_template_doc(tmp_path, rel_path, "fixed content")

    with pytest.raises(FileNotFoundError, match="extra_context_documents"):
        load_context_documents(
            "constraints_pick", templates_dir=tmp_path, extra_paths=["no/such/file.cpp"]
        )


# --- run_generative_step -----------------------------------------------------


def test_run_generative_step_confirmed_writes_artifacts_and_cache(tmp_path):
    prompts_dir = tmp_path / "prompts"
    outputs_dir = tmp_path / "outputs"
    _write_prompt(prompts_dir, "statement_draft", "context: {{ problem_id }}")

    response = ModelResponse(status="confirmed", artifacts={"out.txt": "hello"}, notes=[])
    with patch("orchestrator.model_router.call_model", return_value=response) as mock_call:
        result = run_generative_step(
            step_name="statement_draft",
            problem_id="p1",
            context={"problem_id": "p1"},
            input_hash="hash123",
            resolve_artifact_path=lambda name: outputs_dir / "p1" / name,
            prompts_dir=prompts_dir,
            outputs_dir=outputs_dir,
            templates_dir=TEMPLATES_DIR,
        )

    mock_call.assert_called_once()
    assert mock_call.call_args.args[0] == "statement_draft"
    assert result.status == "confirmed"
    written = outputs_dir / "p1" / "out.txt"
    assert written.read_text(encoding="utf-8") == "hello"
    assert result.artifact_paths == {"out.txt": written}

    cache_entry = load_cache_entry("p1", "statement_draft", outputs_dir=outputs_dir)
    assert cache_entry is not None
    assert cache_entry.input_hash == "hash123"
    assert cache_entry.status == "confirmed"


def test_run_generative_step_proposed_also_writes_artifacts(tmp_path):
    prompts_dir = tmp_path / "prompts"
    outputs_dir = tmp_path / "outputs"
    _write_prompt(prompts_dir, "statement_draft", "x")

    response = ModelResponse(
        status="proposed",
        artifacts={"out.txt": "guessed"},
        notes=[{"field": "n.max", "kind": "proposed", "explanation": "по порядку операций"}],
    )
    with patch("orchestrator.model_router.call_model", return_value=response):
        result = run_generative_step(
            step_name="statement_draft",
            problem_id="p1",
            context={},
            input_hash="h",
            resolve_artifact_path=lambda name: outputs_dir / "p1" / name,
            prompts_dir=prompts_dir,
            outputs_dir=outputs_dir,
        )

    assert result.status == "proposed"
    assert (outputs_dir / "p1" / "out.txt").exists()
    assert result.notes == response.notes


def test_run_generative_step_uncertain_raises_and_writes_nothing(tmp_path):
    prompts_dir = tmp_path / "prompts"
    outputs_dir = tmp_path / "outputs"
    _write_prompt(prompts_dir, "statement_draft", "x")

    response = ModelResponse(
        status="uncertain",
        artifacts={},
        notes=[{"field": "constraints.intended_complexity", "kind": "uncertain", "explanation": "пусто"}],
    )
    with patch("orchestrator.model_router.call_model", return_value=response):
        with pytest.raises(StepUncertainError) as exc_info:
            run_generative_step(
                step_name="statement_draft",
                problem_id="p1",
                context={},
                input_hash="h",
                resolve_artifact_path=lambda name: outputs_dir / "p1" / name,
                prompts_dir=prompts_dir,
                outputs_dir=outputs_dir,
            )

    assert exc_info.value.step_name == "statement_draft"
    assert exc_info.value.problem_id == "p1"
    assert "constraints.intended_complexity" in str(exc_info.value)
    assert not outputs_dir.exists() or list(outputs_dir.rglob("*")) == []
    assert load_cache_entry("p1", "statement_draft", outputs_dir=outputs_dir) is None


def test_run_generative_step_unknown_status_raises_value_error(tmp_path):
    prompts_dir = tmp_path / "prompts"
    outputs_dir = tmp_path / "outputs"
    _write_prompt(prompts_dir, "statement_draft", "x")

    response = ModelResponse(status="something-else", artifacts={}, notes=[])
    with patch("orchestrator.model_router.call_model", return_value=response):
        with pytest.raises(ValueError):
            run_generative_step(
                step_name="statement_draft",
                problem_id="p1",
                context={},
                input_hash="h",
                resolve_artifact_path=lambda name: outputs_dir / "p1" / name,
                prompts_dir=prompts_dir,
                outputs_dir=outputs_dir,
            )
    assert not outputs_dir.exists() or list(outputs_dir.rglob("*")) == []


def test_run_generative_step_compiles_cpp_artifacts(tmp_path):
    prompts_dir = tmp_path / "prompts"
    outputs_dir = tmp_path / "outputs"
    _write_prompt(prompts_dir, "statement_draft", "x")

    valid_cpp = (FIXTURES_DIR / "valid_generator.cpp").read_text(encoding="utf-8")
    broken_cpp = (FIXTURES_DIR / "broken_generator.cpp").read_text(encoding="utf-8")
    response = ModelResponse(
        status="confirmed",
        artifacts={"gen_ok.cpp": valid_cpp, "gen_bad.cpp": broken_cpp, "notes.txt": "n/a"},
        notes=[],
    )
    with patch("orchestrator.model_router.call_model", return_value=response):
        result = run_generative_step(
            step_name="statement_draft",
            problem_id="p1",
            context={},
            input_hash="h",
            resolve_artifact_path=lambda name: outputs_dir / "p1" / name,
            prompts_dir=prompts_dir,
            outputs_dir=outputs_dir,
            templates_dir=TEMPLATES_DIR,
        )

    assert set(result.compile_results) == {"gen_ok.cpp", "gen_bad.cpp"}
    assert result.compile_results["gen_ok.cpp"].success is True
    assert result.compile_results["gen_bad.cpp"].success is False
    if result.compile_results["gen_ok.cpp"].binary_path:
        result.compile_results["gen_ok.cpp"].binary_path.unlink(missing_ok=True)


def test_run_generative_step_passes_context_documents_to_call_model(tmp_path):
    prompts_dir = tmp_path / "prompts"
    outputs_dir = tmp_path / "outputs"
    _write_prompt(prompts_dir, "constraints_pick", "x")

    response = ModelResponse(status="confirmed", artifacts={"constraints.yaml": "n: 1"}, notes=[])
    with patch("orchestrator.model_router.call_model", return_value=response) as mock_call:
        run_generative_step(
            step_name="constraints_pick",
            problem_id="p1",
            context={},
            input_hash="h",
            resolve_artifact_path=lambda name: outputs_dir / "p1" / name,
            prompts_dir=prompts_dir,
            outputs_dir=outputs_dir,
            templates_dir=TEMPLATES_DIR,
        )

    context_documents = mock_call.call_args.kwargs["context_documents"]
    assert "tutorials/requirements.md" in context_documents


def test_run_generative_step_extra_context_documents_none_is_backward_compatible(tmp_path):
    prompts_dir = tmp_path / "prompts"
    outputs_dir = tmp_path / "outputs"
    _write_prompt(prompts_dir, "statement_draft", "x")

    response = ModelResponse(status="confirmed", artifacts={"out.txt": "hello"}, notes=[])
    with patch("orchestrator.model_router.call_model", return_value=response) as mock_call:
        result = run_generative_step(
            step_name="statement_draft",
            problem_id="p1",
            context={},
            input_hash="h",
            resolve_artifact_path=lambda name: outputs_dir / "p1" / name,
            prompts_dir=prompts_dir,
            outputs_dir=outputs_dir,
            templates_dir=TEMPLATES_DIR,
            extra_context_documents=None,
        )

    assert result.status == "confirmed"
    context_documents = mock_call.call_args.kwargs["context_documents"]
    assert set(context_documents) == set(STEP_CONTEXT_DOCUMENTS["statement_draft"])
