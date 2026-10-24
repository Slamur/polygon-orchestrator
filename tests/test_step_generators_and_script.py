from pathlib import Path
from unittest.mock import patch

import pytest

from orchestrator.model_router import ModelResponse
from orchestrator.spec import load_spec
from orchestrator.steps.base import StepUncertainError
from orchestrator.steps.generators_and_script import (
    _build_context_sections,
    compute_input_hash,
    extra_context_documents,
    restore_script_functions,
    run_step,
)

REPO_ROOT = Path(__file__).parent.parent
PROMPTS_DIR = REPO_ROOT / "prompts"
TEMPLATES_DIR = REPO_ROOT / "templates"
FIXTURES_DIR = Path(__file__).parent / "fixtures"
CPP_FIXTURES_DIR = FIXTURES_DIR / "cpp"


@pytest.fixture
def spec():
    return load_spec(FIXTURES_DIR / "valid-spec.yaml")


# Путь от корня репозитория — так референс записывается в спеке.
EXAMPLE_REF = "examples/array--prefix-sums"
EXAMPLE_DIR = REPO_ROOT / EXAMPLE_REF
SCRIPT_FUNCTIONS = (TEMPLATES_DIR / "common" / "test_script_functions.ftl").read_text(encoding="utf-8")


def test_extra_context_documents_returns_generation_file_references(spec):
    assert extra_context_documents(spec) == spec.generation.references


def test_run_step_passes_references_as_extra_context_documents(spec, tmp_path):
    response = ModelResponse(status="confirmed", artifacts={"test_script_groups": "ok"}, notes=[])
    upstream = {"constraints.yaml": "n_max: 100000"}
    with patch("orchestrator.model_router.call_model", return_value=response) as mock_call:
        run_step(
            "valid-spec",
            spec,
            upstream,
            prompts_dir=PROMPTS_DIR,
            outputs_dir=tmp_path,
            templates_dir=TEMPLATES_DIR,
        )

    # Ссылки вне templates/ прикладываются под путём, как он записан в спеке.
    context_documents = mock_call.call_args.kwargs["context_documents"]
    for ref in spec.generation.references:
        assert ref in context_documents


def test_run_step_attaches_every_generator_of_reference_directory(spec, tmp_path):
    spec = spec.model_copy(update={"references": [EXAMPLE_REF]})
    response = ModelResponse(status="confirmed", artifacts={"test_script_groups": "ok"}, notes=[])
    upstream = {"constraints.yaml": "n_max: 100000"}
    with patch("orchestrator.model_router.call_model", return_value=response) as mock_call:
        run_step(
            "valid-spec",
            spec,
            upstream,
            prompts_dir=PROMPTS_DIR,
            outputs_dir=tmp_path,
            templates_dir=TEMPLATES_DIR,
        )

    context_documents = mock_call.call_args.kwargs["context_documents"]
    expected = sorted(
        f"examples/array--prefix-sums/generators/{p.name}"
        for p in (EXAMPLE_DIR / "generators").glob("*.cpp")
    )
    assert expected
    attached = [d for d in context_documents if d.startswith("examples/array--prefix-sums/generators/")]
    assert attached == expected
    assert "examples/array--prefix-sums/spec.yaml" in context_documents


def test_missing_constraints_upstream_artifact_raises(spec, tmp_path):
    with pytest.raises(ValueError, match="constraints.yaml"):
        run_step("valid-spec", spec, None, prompts_dir=PROMPTS_DIR, outputs_dir=tmp_path)

    with pytest.raises(ValueError, match="constraints.yaml"):
        run_step("valid-spec", spec, {}, prompts_dir=PROMPTS_DIR, outputs_dir=tmp_path)


def test_confirmed_splits_cpp_and_script_artifacts(spec, tmp_path):
    valid_cpp = (CPP_FIXTURES_DIR / "valid_generator.cpp").read_text(encoding="utf-8")
    response = ModelResponse(
        status="confirmed",
        artifacts={"gen_rand.cpp": valid_cpp, "test_script_groups": SCRIPT_FUNCTIONS + "<#-- freemarker --#>"},
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
    assert script_path.read_text(encoding="utf-8") == SCRIPT_FUNCTIONS + "<#-- freemarker --#>"
    assert result.notes == []
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
        notes=[{"field": "statement_draft.formal_input_sketch", "kind": "uncertain", "explanation": "неясна форма"}],
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
                update={
                    "generator_ideas": None,
                    "specific_test_ideas": None,
                    "base_template_refs": None,
                }
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


def test_renders_without_crashing_when_solutions_section_missing(spec, tmp_path):
    spec_no_solutions = spec.model_copy(update={"solutions": None})
    response = ModelResponse(status="confirmed", artifacts={"test_script_groups": "ok"}, notes=[])
    upstream = {"constraints.yaml": "n_max: 1"}
    with patch("orchestrator.model_router.call_model", return_value=response) as mock_call:
        result = run_step(
            "valid-spec",
            spec_no_solutions,
            upstream,
            prompts_dir=PROMPTS_DIR,
            outputs_dir=tmp_path,
            templates_dir=TEMPLATES_DIR,
        )

    assert result.status == "confirmed"
    user_prompt = mock_call.call_args.args[2]
    assert "не заказаны явно" in user_prompt


def test_known_wrong_approaches_render_into_user_prompt(spec, tmp_path):
    response = ModelResponse(status="confirmed", artifacts={"test_script_groups": "ok"}, notes=[])
    upstream = {"constraints.yaml": "n_max: 100000"}
    with patch("orchestrator.model_router.call_model", return_value=response) as mock_call:
        run_step(
            "valid-spec",
            spec,
            upstream,
            prompts_dir=PROMPTS_DIR,
            outputs_dir=tmp_path,
            templates_dir=TEMPLATES_DIR,
        )

    user_prompt = mock_call.call_args.args[2]
    assert "O(N^2) без кучи — TLE на больших N" in user_prompt


def test_specific_test_ideas_render_into_user_prompt(spec, tmp_path):
    response = ModelResponse(status="confirmed", artifacts={"test_script_groups": "ok"}, notes=[])
    upstream = {"constraints.yaml": "n_max: 100000"}
    with patch("orchestrator.model_router.call_model", return_value=response) as mock_call:
        run_step(
            "valid-spec",
            spec,
            upstream,
            prompts_dir=PROMPTS_DIR,
            outputs_dir=tmp_path,
            templates_dir=TEMPLATES_DIR,
        )

    user_prompt = mock_call.call_args.args[2]
    for idea in spec.generation.specific_test_ideas:
        assert idea in user_prompt


def test_missing_specific_test_ideas_notes_manual_review_in_prompt(spec, tmp_path):
    spec_no_ideas = spec.model_copy(
        update={"generation": spec.generation.model_copy(update={"specific_test_ideas": None})}
    )
    response = ModelResponse(status="confirmed", artifacts={"test_script_groups": "ok"}, notes=[])
    upstream = {"constraints.yaml": "n_max: 100000"}
    with patch("orchestrator.model_router.call_model", return_value=response) as mock_call:
        run_step(
            "valid-spec",
            spec_no_ideas,
            upstream,
            prompts_dir=PROMPTS_DIR,
            outputs_dir=tmp_path,
            templates_dir=TEMPLATES_DIR,
        )

    user_prompt = mock_call.call_args.args[2]
    assert "автор не указал конкретных тестовых сценариев" in user_prompt


# --- _build_context_sections ------------------------------------------------


def test_build_context_sections_takes_input_format_from_statement_draft(spec):
    sections = _build_context_sections(spec)

    assert sections["generation"]["script_style"] == spec.generation.script_style
    assert "input_shape" not in sections["generation"]
    statement = sections["statement"]
    assert statement["formal_input_sketch"] == spec.statement_draft.formal_input_sketch
    assert statement["indexing"] == spec.statement_draft.indexing
    assert statement["sample_examples"] == [
        {"input": ex.input} for ex in spec.statement_draft.sample_examples
    ]
    # вывод и легенда генераторам не нужны
    assert "formal_output_sketch" not in statement
    assert "legend_sketch" not in statement


def test_build_context_sections_takes_only_known_wrong_approaches_from_solutions(spec):
    sections = _build_context_sections(spec)

    assert sections["solutions"] == {
        "known_wrong_approaches": spec.solutions.known_wrong_approaches
    }


def test_run_step_renders_input_format_from_statement_draft(spec, tmp_path):
    upstream = {"constraints.yaml": "n_max: 100000"}
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

    user_prompt = mock_call.call_args.args[2]
    assert spec.statement_draft.formal_input_sketch.strip() in user_prompt
    assert f"Индексация: {spec.statement_draft.indexing}" in user_prompt
    for ex in spec.statement_draft.sample_examples:
        assert ex.input.strip() in user_prompt


# --- compute_input_hash ------------------------------------------------------


def test_compute_input_hash_changes_when_known_wrong_approaches_change(spec):
    upstream = {"constraints.yaml": "n_max: 100000"}
    h1 = compute_input_hash("valid-spec", spec, upstream)

    spec_more_wrong = spec.model_copy(
        update={
            "solutions": spec.solutions.model_copy(
                update={
                    "known_wrong_approaches": [
                        *spec.solutions.known_wrong_approaches,
                        "off-by-one на правой границе N",
                    ]
                }
            )
        }
    )
    h2 = compute_input_hash("valid-spec", spec_more_wrong, upstream)

    assert h1 != h2


def test_compute_input_hash_changes_when_specific_test_ideas_change(spec):
    upstream = {"constraints.yaml": "n_max: 100000"}
    h1 = compute_input_hash("valid-spec", spec, upstream)

    spec_more_ideas = spec.model_copy(
        update={
            "generation": spec.generation.model_copy(
                update={
                    "specific_test_ideas": [
                        *spec.generation.specific_test_ideas,
                        "все элементы массива равны",
                    ]
                }
            )
        }
    )
    h2 = compute_input_hash("valid-spec", spec_more_ideas, upstream)

    assert h1 != h2


def test_compute_input_hash_treats_missing_solutions_as_empty_list(spec):
    upstream = {"constraints.yaml": "n_max: 100000"}
    spec_no_solutions = spec.model_copy(update={"solutions": None})

    h_missing = compute_input_hash("valid-spec", spec_no_solutions, upstream)

    spec_empty_wrong = spec.model_copy(
        update={"solutions": spec.solutions.model_copy(update={"known_wrong_approaches": []})}
    )
    h_empty = compute_input_hash("valid-spec", spec_empty_wrong, upstream)

    assert h_missing == h_empty


def test_compute_input_hash_changes_when_only_formal_input_sketch_changes(spec):
    upstream = {"constraints.yaml": "n_max: 100000"}
    h1 = compute_input_hash("valid-spec", spec, upstream)

    spec_other_sketch = spec.model_copy(
        update={
            "statement_draft": spec.statement_draft.model_copy(
                update={"formal_input_sketch": "Первая строка: N. Вторая строка: N чисел."}
            )
        }
    )
    # секция generation у обоих спеков идентична — различается только формат ввода
    assert spec_other_sketch.generation == spec.generation
    h2 = compute_input_hash("valid-spec", spec_other_sketch, upstream)

    assert h1 != h2


def test_compute_input_hash_changes_when_sample_input_changes(spec):
    upstream = {"constraints.yaml": "n_max: 100000"}
    h1 = compute_input_hash("valid-spec", spec, upstream)

    first = spec.statement_draft.sample_examples[0]
    spec_other_sample = spec.model_copy(
        update={
            "statement_draft": spec.statement_draft.model_copy(
                update={
                    "sample_examples": [
                        first.model_copy(update={"input": "1\n42\n"}),
                        *spec.statement_draft.sample_examples[1:],
                    ]
                }
            )
        }
    )
    h2 = compute_input_hash("valid-spec", spec_other_sample, upstream)

    assert h1 != h2


def test_compute_input_hash_ignores_algorithm_hint(spec):
    upstream = {"constraints.yaml": "n_max: 100000"}
    h1 = compute_input_hash("valid-spec", spec, upstream)

    spec_other_hint = spec.model_copy(
        update={"solutions": spec.solutions.model_copy(update={"algorithm_hint": "другая идея"})}
    )
    h2 = compute_input_hash("valid-spec", spec_other_hint, upstream)

    assert h1 == h2


# --- стандартные функции test-script'а ------------------------------------------

_BLOCK = (
    '<#assign desc = "Functions"/>\n'
    "<#function div a b>\n    <#return (a / b)?long>\n</#function>\n"
    "<#function pow base power>\n    <#return 1>\n</#function>\n"
)
_DIV = "<#function div a b>\n    <#return (a / b)?long>\n</#function>"
_POW = "<#function pow base power>\n    <#return 1>\n</#function>"


def test_restore_script_functions_nothing_missing_keeps_script():
    script = 'x\n<#function pow base power>\n    <#return 2>\n</#function>\n' + _DIV + "\n"
    assert restore_script_functions(script, _BLOCK) == (script, [])


def test_restore_script_functions_inserts_after_functions_marker():
    script = '<#assign g = "gen"/>\n\n<#assign desc = "Functions"/>\n' + _POW + "\n\nbody\n"
    restored, names = restore_script_functions(script, _BLOCK)
    assert names == ["div"]
    assert restored == (
        '<#assign g = "gen"/>\n\n<#assign desc = "Functions"/>\n' + _DIV + "\n" + _POW + "\n\nbody\n"
    )


def test_restore_script_functions_inserts_before_first_function_without_marker():
    script = "head\n" + _POW + "\nbody\n"
    restored, names = restore_script_functions(script, _BLOCK)
    assert names == ["div"]
    assert restored == "head\n" + _DIV + "\n" + _POW + "\nbody\n"


def test_restore_script_functions_without_functions_goes_before_constraints():
    script = 'gens\n\n<#assign desc = "Constraints"/>\nbody\n'
    restored, names = restore_script_functions(script, _BLOCK)
    assert names == ["div", "pow"]
    assert restored == (
        'gens\n\n<#assign desc = "Functions"/>\n' + _DIV + "\n" + _POW + "\n\n"
        '<#assign desc = "Constraints"/>\nbody\n'
    )


def test_restore_script_functions_without_sections_goes_to_top():
    restored, names = restore_script_functions("body\n", _BLOCK)
    assert names == ["div", "pow"]
    assert restored == '<#assign desc = "Functions"/>\n' + _DIV + "\n" + _POW + "\n\nbody\n"


def test_run_step_restores_dropped_script_functions_with_note(spec, tmp_path):
    response = ModelResponse(
        status="confirmed",
        artifacts={"test_script_groups": '<#assign desc = "Functions"/>\n\nbody\n'},
        notes=[],
    )
    upstream = {"constraints.yaml": "n_max: 100000"}
    with patch("orchestrator.model_router.call_model", return_value=response):
        result = run_step(
            "valid-spec",
            spec,
            upstream,
            prompts_dir=PROMPTS_DIR,
            outputs_dir=tmp_path,
            templates_dir=TEMPLATES_DIR,
        )

    script = (tmp_path / "valid-spec" / "test_script_groups").read_text(encoding="utf-8")
    for name in ("div", "ceil", "min", "max", "pow", "fact", "contains"):
        assert f"<#function {name} " in script
    assert [note["field"] for note in result.notes] == ["test_script_groups"]
    assert result.notes[0]["kind"] == "restored"


def _run_with_artifacts(spec, tmp_path, artifacts):
    response = ModelResponse(status="confirmed", artifacts=artifacts, notes=[])
    upstream = {"constraints.yaml": "n_max: 100000\ntime_limit_seconds: 2"}
    with patch("orchestrator.model_router.call_model", return_value=response):
        return run_step(
            "valid-spec",
            spec,
            upstream,
            prompts_dir=PROMPTS_DIR,
            outputs_dir=tmp_path,
            templates_dir=TEMPLATES_DIR,
        )


def test_generator_name_with_generators_prefix_is_not_nested(spec, tmp_path):
    valid_cpp = (CPP_FIXTURES_DIR / "valid_generator.cpp").read_text(encoding="utf-8")

    result = _run_with_artifacts(
        spec, tmp_path, {"generators/gen_rand.cpp": valid_cpp, "test_script_groups": SCRIPT_FUNCTIONS}
    )

    generators_dir = tmp_path / "valid-spec" / "generators"
    assert (generators_dir / "gen_rand.cpp").exists()
    assert not (generators_dir / "generators").exists()
    binary_path = result.compile_results["generators/gen_rand.cpp"].binary_path
    if binary_path:
        binary_path.unlink(missing_ok=True)


@pytest.mark.parametrize(
    "bad_name", ["other/gen_rand.cpp", "../gen_rand.cpp", "generators/sub/gen_rand.cpp", "dir/test_script"]
)
def test_artifact_name_with_foreign_path_raises_and_writes_nothing(spec, tmp_path, bad_name):
    with pytest.raises(ValueError, match="not a plain file name"):
        _run_with_artifacts(spec, tmp_path, {"test_script_groups": SCRIPT_FUNCTIONS, bad_name: "x"})

    assert not (tmp_path / "valid-spec").exists()
