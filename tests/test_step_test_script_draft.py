from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

from orchestrator.model_router import ModelResponse
from orchestrator.spec import load_spec
from orchestrator.steps.test_script_draft import (
    compute_input_hash,
    fix_script_comparisons,
    restore_script_functions,
    run_step,
)

REPO_ROOT = Path(__file__).parent.parent
PROMPTS_DIR = REPO_ROOT / "prompts"
TEMPLATES_DIR = REPO_ROOT / "templates"
FIXTURES_DIR = Path(__file__).parent / "fixtures"
PLAN_YAML = (FIXTURES_DIR / "tests-plan.yaml").read_text(encoding="utf-8")
SCRIPT_FUNCTIONS = (TEMPLATES_DIR / "test_script_functions.ftl").read_text(encoding="utf-8")


@pytest.fixture
def spec():
    # script_style: groups — файл test_script_groups.
    return load_spec(FIXTURES_DIR / "valid-spec.yaml")


def _upstream(plan_yaml=PLAN_YAML):
    return {"constraints.yaml": "n_max: 100000\ntime_limit_seconds: 2\n", "tests_plan.yaml": plan_yaml}


def _run(spec, tmp_path, artifacts):
    response = ModelResponse(status="confirmed", artifacts=artifacts)
    with patch("orchestrator.model_router.call_model", return_value=response) as mock_call:
        result = run_step(
            "valid-spec",
            spec,
            _upstream(),
            prompts_dir=PROMPTS_DIR,
            problems_dir=tmp_path,
            templates_dir=TEMPLATES_DIR,
        )
    return result, mock_call


def test_writes_script_by_style_from_the_plan(spec, tmp_path):
    result, mock_call = _run(spec, tmp_path, {"test_script_groups": SCRIPT_FUNCTIONS + "body\n"})

    assert (tmp_path / "valid-spec" / "test_script_groups").read_text(encoding="utf-8") == (
        SCRIPT_FUNCTIONS + "body\n"
    )
    assert result.notes == []
    user_prompt = mock_call.call_args.args[2]
    assert "файл `test_script_groups`" in user_prompt
    assert "gen_rand -n=maxN" in user_prompt
    assert "n_max: 100000" in user_prompt
    assert "time_limit_seconds" not in user_prompt
    # Скрипту не нужен алгоритм генератора — только как его вызывать.
    assert "Случайный массив длины n." not in user_prompt


def test_response_without_the_script_is_incomplete(spec, tmp_path):
    first = ModelResponse(status="confirmed", artifacts={"test_script": SCRIPT_FUNCTIONS})
    retry = ModelResponse(status="confirmed", artifacts={"test_script_groups": SCRIPT_FUNCTIONS})
    with patch("orchestrator.model_router.call_model", side_effect=[first, retry]) as mock_call:
        run_step(
            "valid-spec", spec, _upstream(), prompts_dir=PROMPTS_DIR, problems_dir=tmp_path,
            templates_dir=TEMPLATES_DIR,
        )

    assert mock_call.call_count == 2
    assert (tmp_path / "valid-spec" / "test_script_groups").exists()


def test_compute_input_hash_ignores_generator_algorithms(spec):
    plan = yaml.safe_load(PLAN_YAML)
    plan["generators"][0]["algorithm"] = "Другой алгоритм.\n"
    other_algorithm = yaml.safe_dump(plan, allow_unicode=True, sort_keys=False)
    plan["tests"][0]["commands"] = ["gen_rand -n=1"]
    other_tests = yaml.safe_dump(plan, allow_unicode=True, sort_keys=False)

    base = compute_input_hash("valid-spec", spec, _upstream())
    assert compute_input_hash("valid-spec", spec, _upstream(other_algorithm)) == base
    assert compute_input_hash("valid-spec", spec, _upstream(other_tests)) != base


@pytest.mark.parametrize(
    "bad_name", ["other/test_script_groups", "../test_script", "dir/test_script", "gen_rand.cpp"]
)
def test_foreign_file_is_set_aside_and_the_script_applied(spec, tmp_path, bad_name):
    result, _ = _run(spec, tmp_path, {"test_script_groups": SCRIPT_FUNCTIONS, bad_name: "x"})

    assert (tmp_path / "valid-spec" / "test_script_groups").is_file()
    [(filename, _)] = result.conflicts
    assert filename == bad_name
    assert result.conflicts_dir is not None


_DIV = "<#function div a b>\n    <#return (a / b)?long>\n</#function>"
_POW = "<#function pow base power>\n    <#return 1>\n</#function>"
_BLOCK = '<#assign desc = "Functions"/>\n' + _DIV + "\n" + _POW + "\n"


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
    result, _ = _run(spec, tmp_path, {"test_script_groups": '<#assign desc = "Functions"/>\n\nbody\n'})

    script = (tmp_path / "valid-spec" / "test_script_groups").read_text(encoding="utf-8")
    for name in ("div", "ceil", "min", "max", "pow", "fact", "contains"):
        assert f"<#function {name} " in script
    assert [(note["field"], note["kind"]) for note in result.notes] == [("test_script_groups", "restored")]


@pytest.mark.parametrize("line, fixed", [
    ("    <#if value >= n>", "    <#if value gte n>"),
    ("<#if (i * i) <= n>", "<#if (i * i) lte n>"),
    ("<#assign big = a > b/>", "<#assign big = a gt b/>"),
    ("<#elseif a<b >", "<#elseif a lt b >"),
    ('<#if s == ">=" && a < b>', '<#if s == ">=" && a lt b>'),
    ("<#assign r = xs?filter(x -> x > 1)/>", "<#assign r = xs?filter(x -> x gt 1)/>"),
])
def test_fix_script_comparisons_replaces_signs_in_directives(line, fixed):
    assert fix_script_comparisons(line + "\nbody\n") == (fixed + "\nbody\n", 1)


@pytest.mark.parametrize("line", [
    "<#list 0..<n as i>",
    "<#if a gte b>",
    "<#return (a / b)?long>",
    "gen -n=${a > b} > $",
    "<#-- a > b -->",
])
def test_fix_script_comparisons_keeps_non_comparisons(line):
    assert fix_script_comparisons(line) == (line, 0)


def test_run_step_fixes_script_comparisons_with_note(spec, tmp_path):
    result, _ = _run(
        spec, tmp_path, {"test_script_groups": SCRIPT_FUNCTIONS + "<#if n >= 2>\ngen > $\n</#if>\n"}
    )

    written = (tmp_path / "valid-spec" / "test_script_groups").read_text(encoding="utf-8")
    assert "<#if n gte 2>" in written
    assert [(note["field"], note["kind"]) for note in result.notes] == [("test_script_groups", "restored")]
