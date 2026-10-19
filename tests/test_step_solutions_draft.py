from pathlib import Path
from unittest.mock import patch

import pytest

from orchestrator.model_router import ModelResponse
from orchestrator.spec import load_spec
from orchestrator.steps.base import StepUncertainError
from orchestrator.steps.solutions_draft import extra_context_documents, run_step

REPO_ROOT = Path(__file__).parent.parent
PROMPTS_DIR = REPO_ROOT / "prompts"
TEMPLATES_DIR = REPO_ROOT / "templates"
FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture
def spec():
    return load_spec(FIXTURES_DIR / "valid-spec.yaml")


def _write_solution_templates(templates_dir: Path) -> None:
    solutions_dir = templates_dir / "solutions"
    solutions_dir.mkdir(parents=True)
    for name in ["ok_cpp_a_draft.cpp", "wa_cpp_b_draft.cpp", "ok_py_a_draft.py", "ok_java_a_draft.java"]:
        (solutions_dir / name).write_text(f"// {name}", encoding="utf-8")


def _with_languages(spec, languages):
    solutions = spec.solutions.model_copy(update={"languages": languages})
    return spec.model_copy(update={"solutions": solutions})


def test_extra_context_documents_filters_templates_by_language(spec, tmp_path):
    _write_solution_templates(tmp_path)

    documents = extra_context_documents(_with_languages(spec, ["cpp"]), templates_dir=tmp_path)

    assert documents == [
        str(tmp_path / "solutions" / "ok_cpp_a_draft.cpp"),
        str(tmp_path / "solutions" / "wa_cpp_b_draft.cpp"),
    ]


def test_extra_context_documents_accepts_language_aliases(spec, tmp_path):
    _write_solution_templates(tmp_path)

    documents = extra_context_documents(_with_languages(spec, ["Python"]), templates_dir=tmp_path)

    assert documents == [str(tmp_path / "solutions" / "ok_py_a_draft.py")]


def test_extra_context_documents_none_without_matching_templates(spec, tmp_path):
    _write_solution_templates(tmp_path)

    assert extra_context_documents(_with_languages(spec, ["kotlin"]), templates_dir=tmp_path) is None
    assert extra_context_documents(spec, templates_dir=tmp_path / "no-such-dir") is None
    spec_no_solutions = spec.model_copy(update={"solutions": None})
    assert extra_context_documents(spec_no_solutions, templates_dir=tmp_path) is None


def test_run_step_attaches_solution_templates_of_requested_languages(spec, tmp_path):
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

    # Фикстура заказывает только cpp — шаблоны на других языках не прикладываются.
    attached = [d for d in mock_call.call_args.kwargs["context_documents"] if d.startswith("solutions/")]
    expected = sorted(f"solutions/{p.name}" for p in (TEMPLATES_DIR / "solutions").glob("*_cpp_*"))
    assert expected
    assert attached == expected


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
