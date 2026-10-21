from pathlib import Path
from unittest.mock import patch

import pytest

from orchestrator.model_router import ModelResponse
from orchestrator.spec import load_spec
from orchestrator.references import SpecReferenceError
from orchestrator.steps.base import StepUncertainError
from orchestrator.steps.solutions_draft import (
    compute_input_hash,
    extra_context_documents,
    run_step,
)

REPO_ROOT = Path(__file__).parent.parent
PROMPTS_DIR = REPO_ROOT / "prompts"
TEMPLATES_DIR = REPO_ROOT / "templates"
FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture
def spec():
    return load_spec(FIXTURES_DIR / "valid-spec.yaml")


EXAMPLE_DIR = TEMPLATES_DIR / "examples" / "array--prefix-sums"


def _write_reference(tmp_path: Path) -> Path:
    reference_dir = tmp_path / "old-task"
    solutions_dir = reference_dir / "solutions"
    solutions_dir.mkdir(parents=True)
    for name in ["ok_cpp_a_draft.cpp", "wa_cpp_b_draft.cpp", "ok_py_a_draft.py", "ok_java_a_draft.java"]:
        (solutions_dir / name).write_text(f"// {name}", encoding="utf-8")
    return reference_dir


def _with_solutions(spec, languages, references):
    solutions = spec.solutions.model_copy(update={"languages": languages, "references": references})
    return spec.model_copy(update={"solutions": solutions})


def test_extra_context_documents_filters_solutions_by_language(spec, tmp_path):
    reference_dir = _write_reference(tmp_path)

    documents = extra_context_documents(_with_solutions(spec, ["cpp"], [str(reference_dir)]))

    assert documents == [
        str(reference_dir / "solutions" / "ok_cpp_a_draft.cpp"),
        str(reference_dir / "solutions" / "wa_cpp_b_draft.cpp"),
    ]


def test_extra_context_documents_accepts_language_aliases(spec, tmp_path):
    reference_dir = _write_reference(tmp_path)

    documents = extra_context_documents(_with_solutions(spec, ["Python"], [str(reference_dir)]))

    assert documents == [str(reference_dir / "solutions" / "ok_py_a_draft.py")]


def test_extra_context_documents_without_matching_solutions(spec, tmp_path):
    reference_dir = _write_reference(tmp_path)

    # Шаговый референс, не давший шагу ни одного файла, — ошибка спека.
    with pytest.raises(SpecReferenceError, match="solutions.references"):
        extra_context_documents(_with_solutions(spec, ["kotlin"], [str(reference_dir)]))
    # Общий референс без подходящих файлов для этого шага просто пропускается.
    common_only = _with_solutions(spec, ["kotlin"], None).model_copy(
        update={"references": [str(reference_dir)]}
    )
    assert extra_context_documents(common_only) is None
    assert extra_context_documents(_with_solutions(spec, ["cpp"], None)) is None
    spec_no_solutions = spec.model_copy(update={"solutions": None})
    assert extra_context_documents(spec_no_solutions) is None


def test_run_step_attaches_reference_solutions_of_requested_languages(spec, tmp_path):
    spec = spec.model_copy(update={"references": [str(EXAMPLE_DIR)]})
    response = ModelResponse(status="confirmed", artifacts={"ok_cpp_draft.cpp": "int main(){}"}, notes=[])
    upstream = {"constraints.yaml": "n_max: 100000"}
    with patch("orchestrator.model_router.call_model", return_value=response) as mock_call:
        result = run_step(
            "valid-spec",
            spec,
            upstream,
            prompts_dir=PROMPTS_DIR,
            outputs_dir=tmp_path,
            templates_dir=TEMPLATES_DIR,
        )
    for compiled in result.compile_results.values():
        if compiled.binary_path:
            compiled.binary_path.unlink(missing_ok=True)

    # Фикстура заказывает только cpp — решения на других языках не прикладываются.
    context_documents = mock_call.call_args.kwargs["context_documents"]
    attached = [d for d in context_documents if d.startswith("examples/array--prefix-sums/solutions/")]
    expected = sorted(
        f"examples/array--prefix-sums/solutions/{p.name}"
        for p in (EXAMPLE_DIR / "solutions").glob("*_cpp_*")
    )
    assert expected
    assert attached == expected
    assert "examples/array--prefix-sums/spec.yaml" in context_documents


def test_missing_solutions_section_raises(spec, tmp_path):
    spec_no_solutions = spec.model_copy(update={"solutions": None})
    upstream = {"constraints.yaml": "n_max: 100000"}
    with pytest.raises(ValueError, match="solutions"):
        run_step(
            "valid-spec",
            spec_no_solutions,
            upstream,
            prompts_dir=PROMPTS_DIR,
            outputs_dir=tmp_path,
        )


def test_missing_constraints_upstream_artifact_raises(spec, tmp_path):
    with pytest.raises(ValueError, match="constraints.yaml"):
        run_step("valid-spec", spec, None, prompts_dir=PROMPTS_DIR, outputs_dir=tmp_path)


def test_confirmed_writes_solution_files(spec, tmp_path):
    response = ModelResponse(
        status="confirmed",
        artifacts={"ok_cpp_draft.cpp": "int main(){}"},
        notes=[],
    )
    upstream = {"constraints.yaml": "time_limit_seconds: 2"}
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
    written_path = tmp_path / "valid-spec" / "solutions" / "ok_cpp_draft.cpp"
    assert written_path.exists()

    user_prompt = mock_call.call_args.args[2]
    assert "Дейкстра" in user_prompt
    assert "O((N + K) log N)" in user_prompt


def test_uncertain_raises_and_writes_nothing(spec, tmp_path):
    response = ModelResponse(
        status="uncertain",
        artifacts={},
        notes=[{"field": "solutions.algorithm_hint", "kind": "uncertain", "explanation": "слишком расплывчато"}],
    )
    upstream = {"constraints.yaml": "time_limit_seconds: 2"}
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


def test_renders_without_crashing_when_known_wrong_approaches_is_null(spec, tmp_path):
    spec_bare = spec.model_copy(
        update={"solutions": spec.solutions.model_copy(update={"known_wrong_approaches": None})}
    )
    response = ModelResponse(status="confirmed", artifacts={"ok_cpp_draft.cpp": "int main(){}"}, notes=[])
    upstream = {"constraints.yaml": "time_limit_seconds: 2"}
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


def _run_and_get_user_prompt(spec, tmp_path, upstream):
    response = ModelResponse(status="confirmed", artifacts={}, notes=[])
    with patch("orchestrator.model_router.call_model", return_value=response) as mock_call:
        run_step(
            "valid-spec",
            spec,
            upstream,
            prompts_dir=PROMPTS_DIR,
            outputs_dir=tmp_path,
            templates_dir=TEMPLATES_DIR,
        )
    return mock_call.call_args.args[2]


def test_user_prompt_includes_statement_io(spec, tmp_path):
    user_prompt = _run_and_get_user_prompt(spec, tmp_path, {"constraints.yaml": "n_max: 100000"})

    statement = spec.statement_draft
    assert statement.legend_sketch.strip() in user_prompt
    assert statement.formal_input_sketch.strip() in user_prompt
    assert statement.formal_output_sketch.strip() in user_prompt
    assert statement.indexing in user_prompt
    for ex in statement.sample_examples:
        assert ex.input.strip() in user_prompt
        assert ex.output.strip() in user_prompt


def test_user_prompt_includes_constraints_yaml(spec, tmp_path):
    constraints_yaml = "variables:\n  n: {min: 1, max: 200000}\nlimits:\n  time_limit_seconds: 2\n"
    user_prompt = _run_and_get_user_prompt(spec, tmp_path, {"constraints.yaml": constraints_yaml})

    assert constraints_yaml.strip() in user_prompt


def test_user_prompt_marks_missing_sample_output(spec, tmp_path):
    examples = [ex.model_copy(update={"output": None}) for ex in spec.statement_draft.sample_examples]
    spec_no_output = spec.model_copy(
        update={"statement_draft": spec.statement_draft.model_copy(update={"sample_examples": examples})}
    )
    user_prompt = _run_and_get_user_prompt(spec_no_output, tmp_path, {"constraints.yaml": "n_max: 1"})

    assert "Output: автор не указал" in user_prompt


@pytest.mark.parametrize(
    "field, value",
    [
        ("formal_input_sketch", "Одна строка: N."),
        ("formal_output_sketch", "Выведите YES или NO."),
        ("indexing", "0-indexed"),
        ("legend_sketch", "Другая легенда."),
    ],
)
def test_compute_input_hash_changes_when_statement_io_changes(spec, field, value):
    upstream = {"constraints.yaml": "n_max: 100000"}
    h1 = compute_input_hash("valid-spec", spec, upstream)

    spec_changed = spec.model_copy(
        update={"statement_draft": spec.statement_draft.model_copy(update={field: value})}
    )
    h2 = compute_input_hash("valid-spec", spec_changed, upstream)

    assert h1 != h2


def test_compute_input_hash_changes_when_sample_output_changes(spec):
    upstream = {"constraints.yaml": "n_max: 100000"}
    h1 = compute_input_hash("valid-spec", spec, upstream)

    examples = [ex.model_copy(update={"output": "-1\n"}) for ex in spec.statement_draft.sample_examples]
    spec_changed = spec.model_copy(
        update={"statement_draft": spec.statement_draft.model_copy(update={"sample_examples": examples})}
    )
    h2 = compute_input_hash("valid-spec", spec_changed, upstream)

    assert h1 != h2
