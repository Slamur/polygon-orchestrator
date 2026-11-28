from pathlib import Path
from unittest.mock import patch

import pytest

from orchestrator.checks.validator_tests import VALIDATOR_TESTS_FILENAME, ValidatorTestsRun
from orchestrator.model_router import ModelResponse
from orchestrator.spec import load_spec
from orchestrator.steps import validator_tests, validator_tests_check
from orchestrator.steps.base import CONSTRAINTS_ARTIFACT
from orchestrator.steps.solutions_check import GateFailedError
from orchestrator.steps.validator_tests_check import VALIDATOR_TESTS_GATE

REPO_ROOT = Path(__file__).parent.parent
PROMPTS_DIR = REPO_ROOT / "prompts"
TEMPLATES_DIR = REPO_ROOT / "templates"
FIXTURES_DIR = Path(__file__).parent / "fixtures"

PROBLEM_ID = "example-problem"
VALIDATOR_CPP = '#include "problem_lib.h"\nint main() { /* inf.readInt(1, 10, "n") */ }\n'
TESTS_YAML = (
    "tests:\n  - covers: 'n = maxN + 1'\n    verdict: INVALID\n"
    "    input: |\n      11\n    expect: '[name=n]'\n"
)
GROUPS_YAML = (
    "n_max: 10\ntest_groups:\n"
    '  - name: "1"\n    points: 40\n  - name: "2"\n    points: 60\n'
    "limits:\n  time_limit_seconds: 1\n"
)


@pytest.fixture
def spec():
    return load_spec(FIXTURES_DIR / "valid-spec.yaml").model_copy(update={"problem_id": PROBLEM_ID})


@pytest.fixture
def problem_dir(tmp_path):
    directory = tmp_path / PROBLEM_ID
    directory.mkdir()
    (directory / "validator.cpp").write_text(VALIDATOR_CPP, encoding="utf-8")
    return directory


def _upstream(spec, problems_dir: Path, constraints: str = "n_max: 10\n") -> dict:
    return {
        CONSTRAINTS_ARTIFACT: constraints,
        validator_tests.INPUTS_KEY: validator_tests.load_inputs(
            PROBLEM_ID, spec, problems_dir=problems_dir
        ),
    }


def _run_step(spec, problems_dir, response, constraints: str = "n_max: 10\n"):
    with patch("orchestrator.model_router.call_model", return_value=response) as mock_call:
        result = validator_tests.run_step(
            PROBLEM_ID,
            spec,
            _upstream(spec, problems_dir, constraints),
            prompts_dir=PROMPTS_DIR,
            problems_dir=problems_dir,
            templates_dir=TEMPLATES_DIR,
        )
    return result, mock_call


def _response(text: str) -> ModelResponse:
    return ModelResponse(status="confirmed", artifacts={VALIDATOR_TESTS_FILENAME: text}, notes=[])


def _run_gate(spec, problems_dir):
    upstream = {
        VALIDATOR_TESTS_GATE.INPUTS_KEY: VALIDATOR_TESTS_GATE.load_inputs(
            PROBLEM_ID, spec, problems_dir=problems_dir
        )
    }
    return VALIDATOR_TESTS_GATE.run_step(PROBLEM_ID, spec, upstream, problems_dir=problems_dir)


def test_no_validator_is_an_input_error(spec, tmp_path):
    (tmp_path / PROBLEM_ID).mkdir()
    with pytest.raises(ValueError, match="constraints_pick"):
        validator_tests.compute_input_hash(PROBLEM_ID, spec, _upstream(spec, tmp_path))


def test_writes_tests_and_shows_the_validator_to_the_model(spec, problem_dir):
    result, mock_call = _run_step(spec, problem_dir.parent, _response(TESTS_YAML))

    assert result.status == "confirmed"
    assert (problem_dir / VALIDATOR_TESTS_FILENAME).read_text() == TESTS_YAML
    user_prompt = mock_call.call_args.args[2]
    assert VALIDATOR_CPP.strip() in user_prompt
    assert "At most 20 lines and 500 characters" in user_prompt
    assert "## Test groups" not in user_prompt
    assert "The local run of the validator tests failed" not in user_prompt


def test_with_groups_every_group_needs_a_valid_test(spec, problem_dir):
    _, mock_call = _run_step(spec, problem_dir.parent, _response(TESTS_YAML), GROUPS_YAML)

    assert "The groups of `constraints.yaml`: 1, 2" in mock_call.call_args.args[2]
    assert not (problem_dir / VALIDATOR_TESTS_FILENAME).exists()

    covered = TESTS_YAML + "".join(
        f"  - covers: g{g}\n    verdict: VALID\n    group: {g}\n    input: |\n      5\n"
        for g in (1, 2)
    )
    _run_step(spec, problem_dir.parent, _response(covered), GROUPS_YAML)
    assert (problem_dir / VALIDATOR_TESTS_FILENAME).read_text() == covered


def test_tests_that_do_not_parse_are_not_written(spec, problem_dir):
    _run_step(spec, problem_dir.parent, _response("tests: []\n"))
    assert not (problem_dir / VALIDATOR_TESTS_FILENAME).exists()


def test_validator_edit_changes_the_input_hash(spec, problem_dir):
    problems_dir = problem_dir.parent
    before = validator_tests.compute_input_hash(PROBLEM_ID, spec, _upstream(spec, problems_dir))
    (problem_dir / "validator.cpp").write_text(VALIDATOR_CPP + "// edit\n")
    after = validator_tests.compute_input_hash(PROBLEM_ID, spec, _upstream(spec, problems_dir))
    assert before != after


def test_failed_run_makes_the_step_revise_with_its_report(spec, problem_dir, monkeypatch):
    problems_dir = problem_dir.parent
    (problem_dir / VALIDATOR_TESTS_FILENAME).write_text(TESTS_YAML)
    failure = {"test": 1, "covers": "n = maxN + 1", "problems": ["the validator accepts it"]}
    monkeypatch.setattr(
        validator_tests_check,
        "run_validator_tests",
        lambda *args, **kwargs: ValidatorTestsRun(failures=[failure]),
    )

    with pytest.raises(GateFailedError, match="1 validator test"):
        _run_gate(spec, problems_dir)

    report = VALIDATOR_TESTS_GATE.failed_report(PROBLEM_ID, spec, problems_dir=problems_dir)
    assert report["failures"] == [failure]
    reason = validator_tests.forced_rerun_reason(PROBLEM_ID, spec, _upstream(spec, problems_dir))
    assert "run_validator_tests" in reason

    _, mock_call = _run_step(spec, problems_dir, _response(TESTS_YAML))
    user_prompt = mock_call.call_args.args[2]
    assert "The local run of the validator tests failed" in user_prompt
    assert "the validator accepts it" in user_prompt

    # The revised tests make the report outdated.
    (problem_dir / VALIDATOR_TESTS_FILENAME).write_text(TESTS_YAML.replace("11", "0"))
    assert VALIDATOR_TESTS_GATE.failed_report(PROBLEM_ID, spec, problems_dir=problems_dir) is None


def test_problem_errors_fail_the_run_without_a_revision(spec, problem_dir, monkeypatch):
    problems_dir = problem_dir.parent
    monkeypatch.setattr(
        validator_tests_check,
        "run_validator_tests",
        lambda *args, **kwargs: ValidatorTestsRun(errors=["validator.cpp: compilation failed"]),
    )

    with pytest.raises(GateFailedError, match="fix the validator") as error:
        _run_gate(spec, problems_dir)

    assert error.value.rerun_hint == ""
    assert VALIDATOR_TESTS_GATE.failed_report(PROBLEM_ID, spec, problems_dir=problems_dir) is None


def test_passed_run(spec, problem_dir, monkeypatch):
    monkeypatch.setattr(
        validator_tests_check, "run_validator_tests", lambda *a, **k: ValidatorTestsRun(tests=3)
    )
    result = _run_gate(spec, problem_dir.parent)
    assert result.status == "confirmed"
    assert "3 validator test(s)" in result.notes[0]["explanation"]
