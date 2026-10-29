from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

from orchestrator.model_router import ModelResponse
from orchestrator.spec import load_spec
from orchestrator.steps.base import StepUncertainError
from orchestrator.steps.tests_plan import (
    TestsPlanError,
    _build_context_sections,
    compute_input_hash,
    extra_context_documents,
    parse_tests_plan,
    run_step,
    uncovered_notes,
)

REPO_ROOT = Path(__file__).parent.parent
PROMPTS_DIR = REPO_ROOT / "prompts"
TEMPLATES_DIR = REPO_ROOT / "templates"
FIXTURES_DIR = Path(__file__).parent / "fixtures"
PLAN_YAML = (FIXTURES_DIR / "tests-plan.yaml").read_text(encoding="utf-8")
UPSTREAM = {"constraints.yaml": "n_max: 100000\nlimits:\n  time_limit_seconds: 1\n"}


@pytest.fixture
def spec():
    return load_spec(FIXTURES_DIR / "valid-spec.yaml")


def _plan(**changes) -> dict:
    plan = yaml.safe_load(PLAN_YAML)
    plan.update(changes)
    return plan


def _run(spec, tmp_path, artifacts=None, status="confirmed", upstream=UPSTREAM):
    response = ModelResponse(
        status=status, artifacts={"tests_plan.yaml": PLAN_YAML} if artifacts is None else artifacts
    )
    with patch("orchestrator.model_router.call_model", return_value=response) as mock_call:
        result = run_step(
            "valid-spec",
            spec,
            upstream,
            prompts_dir=PROMPTS_DIR,
            problems_dir=tmp_path,
            templates_dir=TEMPLATES_DIR,
        )
    return result, mock_call


def test_parse_tests_plan_accepts_the_fixture():
    assert parse_tests_plan(PLAN_YAML)["generators"][0]["name"] == "gen_rand"


@pytest.mark.parametrize(
    "change, message",
    [
        ({"generators": []}, "'generators' must be a non-empty list"),
        ({"tests": None}, "'tests' must be a non-empty list"),
        (
            {"generators": [{"name": "rand", "status": "confirmed", "params": []}]},
            "gen_<...> or upd_<...>",
        ),
        (
            {"generators": [{"name": "gen_rand", "status": "confirmed", "params": [{"name": "n", "type": "long"}]}]},
            "type must be one of",
        ),
        (
            {"generators": [{"name": "gen_rand", "status": "maybe", "params": [{"name": "n", "type": "int"}]}]},
            "status must be one of",
        ),
        (
            {"generators": [{"name": "gen_rand", "status": "confirmed", "params": []}] * 2},
            "listed twice",
        ),
    ],
)
def test_parse_tests_plan_rejects_broken_structure(change, message):
    with pytest.raises(TestsPlanError, match=message):
        parse_tests_plan(yaml.safe_dump(_plan(**change)))


@pytest.mark.parametrize(
    "command, message",
    [
        ("gen_other -n=1", "generator 'gen_other' is not in 'generators'"),
        ("gen_rand -m=1", "'gen_rand' has no parameter 'm'"),
        ("gen_rand n=1", "is not -name=value or --name"),
        ("gen_rand -n=1 | ", "empty part of the chain"),
    ],
)
def test_parse_tests_plan_checks_commands_against_generators(command, message):
    plan = _plan()
    plan["tests"][0]["commands"] = [command]
    with pytest.raises(TestsPlanError, match=message):
        parse_tests_plan(yaml.safe_dump(plan))


def test_parse_tests_plan_rejects_invalid_yaml():
    with pytest.raises(TestsPlanError, match="not valid YAML"):
        parse_tests_plan("covers: [specific_test_ideas[0]]")


def test_uncovered_notes_name_spec_items_without_tests_and_unused_generators(spec):
    plan = _plan()
    plan["generators"].append({"name": "upd_shuffle", "status": "proposed", "params": []})
    plan["tests"][0]["covers"] = ["specific_test_ideas[1]", "known_wrong_approaches[5]"]

    notes = uncovered_notes(plan, spec)

    assert {note["kind"] for note in notes} == {"uncovered"}
    assert [note["field"] for note in notes] == [
        "known_wrong_approaches[5]",
        "specific_test_ideas[0]",
        "known_wrong_approaches[0]",
        "generators/upd_shuffle",
    ]


def test_extra_context_documents_returns_generation_file_references(spec):
    assert extra_context_documents(spec) == spec.generation.references


def test_missing_constraints_upstream_artifact_raises(spec, tmp_path):
    with pytest.raises(ValueError, match="constraints.yaml"):
        run_step("valid-spec", spec, None, prompts_dir=PROMPTS_DIR, problems_dir=tmp_path)


def test_confirmed_writes_plan_to_authoring_with_problem_id_from_spec(spec, tmp_path):
    result, mock_call = _run(spec, tmp_path)

    assert result.status == "confirmed"
    written = (tmp_path / "valid-spec" / "authoring" / "tests_plan.yaml").read_text(encoding="utf-8")
    assert yaml.safe_load(written)["problem_id"] == "valid-spec"
    assert ("problem_id", "restored") in [(n["field"], n["kind"]) for n in result.notes]

    user_prompt = mock_call.call_args.args[2]
    assert "n_max: 100000" in user_prompt
    # TL/ML плану не нужны.
    assert "time_limit_seconds" not in user_prompt
    assert "- [0] N = 1 (минимальный случай)" in user_prompt
    assert "- [0] O(N^2) без кучи — TLE на больших N" in user_prompt
    assert "tests_plan_format.yaml" in mock_call.call_args.kwargs["context_documents"]


def test_plan_that_does_not_parse_is_not_written(spec, tmp_path):
    result, _ = _run(spec, tmp_path, artifacts={"tests_plan.yaml": "generators: []\n"})

    assert not (tmp_path / "valid-spec" / "authoring" / "tests_plan.yaml").exists()
    assert "'generators' must be a non-empty list" in result.not_applied_reason
    assert (tmp_path / "valid-spec" / ".cache" / "tests_plan.response.json").is_file()


def test_uncertain_raises_and_writes_nothing(spec, tmp_path):
    with pytest.raises(StepUncertainError):
        _run(spec, tmp_path, artifacts={}, status="uncertain")

    assert not (tmp_path / "valid-spec" / "authoring").exists()


def test_renders_without_optional_lists_and_solutions(spec, tmp_path):
    bare = spec.model_copy(
        update={
            "solutions": None,
            "generation": spec.generation.model_copy(
                update={"generator_ideas": None, "specific_test_ideas": None}
            ),
        }
    )

    result, mock_call = _run(bare, tmp_path)

    assert result.status == "confirmed"
    user_prompt = mock_call.call_args.args[2]
    assert "known_wrong_approaches пуст" in user_prompt
    assert "автор не указал конкретных тестовых сценариев" in user_prompt


def test_build_context_sections_takes_input_format_hint_and_wrong_approaches(spec):
    sections = _build_context_sections(spec)

    assert sections["statement"]["formal_input_sketch"] == spec.statement_draft.formal_input_sketch
    assert sections["statement"]["legend_sketch"] == spec.statement_draft.legend_sketch
    assert sections["solutions"] == {
        "algorithm_hint": spec.solutions.algorithm_hint,
        "known_wrong_approaches": spec.solutions.known_wrong_approaches,
    }


def _hash(spec, upstream=UPSTREAM):
    return compute_input_hash("valid-spec", spec, upstream)


@pytest.mark.parametrize(
    "section, update",
    [
        ("solutions", {"known_wrong_approaches": ["другое"]}),
        ("solutions", {"algorithm_hint": "другая идея"}),
        ("generation", {"specific_test_ideas": ["N = 2"]}),
        ("statement_draft", {"formal_input_sketch": "другой формат"}),
    ],
)
def test_compute_input_hash_changes_with_inputs_of_the_plan(spec, section, update):
    changed = spec.model_copy(update={section: getattr(spec, section).model_copy(update=update)})
    assert _hash(changed) != _hash(spec)


def test_compute_input_hash_ignores_languages_and_limits(spec):
    other_languages = spec.model_copy(
        update={"solutions": spec.solutions.model_copy(update={"languages": ["py"]})}
    )
    other_tl = {"constraints.yaml": UPSTREAM["constraints.yaml"].replace("1\n", "3\n")}

    assert _hash(other_languages) == _hash(spec)
    assert _hash(spec, other_tl) == _hash(spec)
    assert _hash(spec, {"constraints.yaml": "n_max: 5\n"}) != _hash(spec)
