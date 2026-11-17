import shutil
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

from orchestrator.checks.test_groups import TestGroup
from orchestrator.checks.test_script_check import (
    ScriptCheckUnavailable,
    ScriptLine,
    ScriptRenderError,
)
from orchestrator.model_router import ModelResponse
from orchestrator.spec import load_spec
from orchestrator.steps.test_script_draft import (
    compute_input_hash,
    fix_script_comparisons,
    restore_script_functions,
    run_step,
    script_group_errors,
)

REPO_ROOT = Path(__file__).parent.parent
PROMPTS_DIR = REPO_ROOT / "prompts"
TEMPLATES_DIR = REPO_ROOT / "templates"
FIXTURES_DIR = Path(__file__).parent / "fixtures"
PLAN_YAML = (FIXTURES_DIR / "tests-plan.yaml").read_text(encoding="utf-8")
SCRIPT_FUNCTIONS = (TEMPLATES_DIR / "test_script_functions.ftl").read_text(encoding="utf-8")


@pytest.fixture
def spec():
    # script_style: groups — the test_script_groups file.
    return load_spec(FIXTURES_DIR / "valid-spec.yaml")


def _upstream(plan_yaml=PLAN_YAML):
    return {
        "constraints.yaml": "n_max: 100000\ntime_limit_seconds: 2\n",
        "tests_plan.yaml": plan_yaml,
    }


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
    assert "the file `test_script_groups`" in user_prompt
    assert "gen_rand -n=maxN" in user_prompt
    assert "n_max: 100000" in user_prompt
    assert "time_limit_seconds" not in user_prompt
    # The script doesn't need the generator's algorithm — only how to call it.
    assert "A random array of length n." not in user_prompt


def test_response_without_the_script_is_incomplete(spec, tmp_path):
    first = ModelResponse(status="confirmed", artifacts={"test_script": SCRIPT_FUNCTIONS})
    retry = ModelResponse(status="confirmed", artifacts={"test_script_groups": SCRIPT_FUNCTIONS})
    with patch("orchestrator.model_router.call_model", side_effect=[first, retry]) as mock_call:
        run_step(
            "valid-spec",
            spec,
            _upstream(),
            prompts_dir=PROMPTS_DIR,
            problems_dir=tmp_path,
            templates_dir=TEMPLATES_DIR,
        )

    assert mock_call.call_count == 2
    assert (tmp_path / "valid-spec" / "test_script_groups").exists()


def test_compute_input_hash_ignores_generator_algorithms(spec):
    plan = yaml.safe_load(PLAN_YAML)
    plan["generators"][0]["algorithm"] = "Another algorithm.\n"
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
    script = "x\n<#function pow base power>\n    <#return 2>\n</#function>\n" + _DIV + "\n"
    assert restore_script_functions(script, _BLOCK) == (script, [])


def test_restore_script_functions_inserts_after_functions_marker():
    script = '<#assign g = "gen"/>\n\n<#assign desc = "Functions"/>\n' + _POW + "\n\nbody\n"
    restored, names = restore_script_functions(script, _BLOCK)
    assert names == ["div"]
    assert restored == (
        '<#assign g = "gen"/>\n\n<#assign desc = "Functions"/>\n'
        + _DIV
        + "\n"
        + _POW
        + "\n\nbody\n"
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
    result, _ = _run(
        spec, tmp_path, {"test_script_groups": '<#assign desc = "Functions"/>\n\nbody\n'}
    )

    script = (tmp_path / "valid-spec" / "test_script_groups").read_text(encoding="utf-8")
    for name in ("div", "ceil", "min", "max", "pow", "fact", "contains"):
        assert f"<#function {name} " in script
    assert [(note["field"], note["kind"]) for note in result.notes] == [
        ("test_script_groups", "restored")
    ]


@pytest.mark.parametrize(
    "line, fixed",
    [
        ("    <#if value >= n>", "    <#if value gte n>"),
        ("<#if (i * i) <= n>", "<#if (i * i) lte n>"),
        ("<#assign big = a > b/>", "<#assign big = a gt b/>"),
        ("<#elseif a<b >", "<#elseif a lt b >"),
        ('<#if s == ">=" && a < b>', '<#if s == ">=" && a lt b>'),
        ("<#assign r = xs?filter(x -> x > 1)/>", "<#assign r = xs?filter(x -> x gt 1)/>"),
    ],
)
def test_fix_script_comparisons_replaces_signs_in_directives(line, fixed):
    assert fix_script_comparisons(line + "\nbody\n") == (fixed + "\nbody\n", 1)


@pytest.mark.parametrize(
    "line",
    [
        "<#list 0..<n as i>",
        "<#if a gte b>",
        "<#return (a / b)?long>",
        "gen -n=${a > b} > $",
        "<#-- a > b -->",
    ],
)
def test_fix_script_comparisons_keeps_non_comparisons(line):
    assert fix_script_comparisons(line) == (line, 0)


def test_run_step_fixes_script_comparisons_with_note(spec, tmp_path):
    result, _ = _run(
        spec, tmp_path, {"test_script_groups": SCRIPT_FUNCTIONS + "<#if n >= 2>\ngen > $\n</#if>\n"}
    )

    written = (tmp_path / "valid-spec" / "test_script_groups").read_text(encoding="utf-8")
    assert "<#if n gte 2>" in written
    assert [(note["field"], note["kind"]) for note in result.notes] == [
        ("test_script_groups", "restored")
    ]


GROUPS_CONSTRAINTS = (
    "n_max: 100000\ntest_groups:\n"
    '  - name: "1"\n    points: 40\n  - name: "2"\n    points: 60\n    dependencies: ["1"]\n'
)


def test_script_group_errors():
    groups = [TestGroup("1", 40), TestGroup("2", 60), TestGroup("3", 0)]
    lines = ["gen -n=1 -group=1 > $", "gen -n=2 > $", "gen -n=3 -group=7 > $"]

    errors = script_group_errors(groups, lines)

    assert errors == [
        "line 2: no -group=<N> marker, the problem has test groups\n    gen -n=2 > $",
        "line 3: unknown test group '7'\n    gen -n=3 -group=7 > $",
        "test group 2 has no tests in the script",
        "test group 3 has no tests in the script",
    ]


def _run_with_groups(spec, tmp_path, rendered):
    response = ModelResponse(
        status="confirmed", artifacts={"test_script_groups": SCRIPT_FUNCTIONS + "body\n"}
    )
    lines = [ScriptLine(index, text) for index, text in enumerate(rendered, 1)]
    with (
        patch("orchestrator.model_router.call_model", return_value=response),
        patch("orchestrator.steps.test_script_draft.expand_script", return_value=lines),
    ):
        return run_step(
            "valid-spec",
            spec,
            {**_upstream(), "constraints.yaml": GROUPS_CONSTRAINTS},
            prompts_dir=PROMPTS_DIR,
            problems_dir=tmp_path,
            templates_dir=TEMPLATES_DIR,
        )


def test_script_with_lines_of_every_group_is_written(spec, tmp_path):
    result = _run_with_groups(spec, tmp_path, ["gen -group=1 > $", "gen -group=2 > $"])

    assert result.not_applied_reason is None
    assert (tmp_path / "valid-spec" / "test_script_groups").is_file()


def test_script_without_group_markers_is_not_written(spec, tmp_path):
    result = _run_with_groups(spec, tmp_path, ["gen -group=1 > $", "gen -n=5 > $"])

    assert not (tmp_path / "valid-spec" / "test_script_groups").exists()
    assert "line 2: no -group=<N> marker" in result.not_applied_reason
    assert "test group 2 has no tests in the script" in result.not_applied_reason
    assert (tmp_path / "valid-spec" / ".cache" / "test_script_draft.response.json").is_file()


@pytest.mark.parametrize("exc", [ScriptCheckUnavailable("no java"), ScriptRenderError("bad")])
def test_script_that_cannot_be_expanded_is_written_unchecked(spec, tmp_path, exc):
    response = ModelResponse(status="confirmed", artifacts={"test_script_groups": "body\n"})
    with (
        patch("orchestrator.model_router.call_model", return_value=response),
        patch("orchestrator.steps.test_script_draft.expand_script", side_effect=exc),
    ):
        result = run_step(
            "valid-spec",
            spec,
            {**_upstream(), "constraints.yaml": GROUPS_CONSTRAINTS},
            prompts_dir=PROMPTS_DIR,
            problems_dir=tmp_path,
            templates_dir=TEMPLATES_DIR,
        )

    assert result.not_applied_reason is None
    assert (tmp_path / "valid-spec" / "test_script_groups").is_file()


@pytest.mark.slow
def test_script_groups_are_checked_on_the_expanded_script(spec, tmp_path):
    if shutil.which("java") is None:
        pytest.skip("java not found")
    script = (
        SCRIPT_FUNCTIONS
        + "<#list 1..2 as g>\n    gen -n=${g} -group=${g} > $\n</#list>\ngen -n=9 > $\n"
    )
    response = ModelResponse(status="confirmed", artifacts={"test_script_groups": script})
    with patch("orchestrator.model_router.call_model", return_value=response):
        result = run_step(
            "valid-spec",
            spec,
            {**_upstream(), "constraints.yaml": GROUPS_CONSTRAINTS},
            prompts_dir=PROMPTS_DIR,
            problems_dir=tmp_path,
            templates_dir=TEMPLATES_DIR,
        )

    assert "no -group=<N> marker" in result.not_applied_reason
    assert "gen -n=9 > $" in result.not_applied_reason
