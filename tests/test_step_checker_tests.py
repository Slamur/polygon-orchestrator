from pathlib import Path
from unittest.mock import patch

import pytest

from orchestrator.checks.checker_tests import CHECKER_TESTS_FILENAME, CheckerTestsRun
from orchestrator.model_router import ModelResponse
from orchestrator.spec import load_spec
from orchestrator.steps import checker_tests, checker_tests_check
from orchestrator.steps.base import CONSTRAINTS_ARTIFACT
from orchestrator.steps.checker_tests_check import CHECKER_TESTS_GATE
from orchestrator.steps.solutions_check import GateFailedError

REPO_ROOT = Path(__file__).parent.parent
PROMPTS_DIR = REPO_ROOT / "prompts"
TEMPLATES_DIR = REPO_ROOT / "templates"
FIXTURES_DIR = Path(__file__).parent / "fixtures"

PROBLEM_ID = "example-problem"
CHECKER_CPP = '#include "problem_lib.h"\nint main() { /* stream.quitif(x > 1, _wa) */ }\n'
TESTS_YAML = (
    "tests:\n  - covers: 'quitif(x > 1), participant'\n    verdict: WA\n"
    "    input: |\n      1\n    output: |\n      2\n    answer: |\n      1\n"
)


@pytest.fixture
def spec():
    spec = load_spec(FIXTURES_DIR / "valid-spec.yaml")
    return spec.model_copy(
        update={
            "problem_id": PROBLEM_ID,
            "checker": spec.checker.model_copy(
                update={"custom_needed": True, "custom_comparison_notes": "Any valid x."}
            ),
        }
    )


@pytest.fixture
def problem_dir(tmp_path):
    directory = tmp_path / PROBLEM_ID
    directory.mkdir()
    (directory / "checker.cpp").write_text(CHECKER_CPP, encoding="utf-8")
    return directory


def _upstream(spec, problems_dir: Path) -> dict:
    return {
        CONSTRAINTS_ARTIFACT: "n_max: 10\n",
        checker_tests.INPUTS_KEY: checker_tests.load_inputs(
            PROBLEM_ID, spec, problems_dir=problems_dir
        ),
    }


def _run_step(spec, problems_dir, response):
    with patch("orchestrator.model_router.call_model", return_value=response) as mock_call:
        result = checker_tests.run_step(
            PROBLEM_ID,
            spec,
            _upstream(spec, problems_dir),
            prompts_dir=PROMPTS_DIR,
            problems_dir=problems_dir,
            templates_dir=TEMPLATES_DIR,
        )
    return result, mock_call


def _run_gate(spec, problems_dir):
    upstream = {
        CHECKER_TESTS_GATE.INPUTS_KEY: CHECKER_TESTS_GATE.load_inputs(
            PROBLEM_ID, spec, problems_dir=problems_dir
        )
    }
    return CHECKER_TESTS_GATE.run_step(PROBLEM_ID, spec, upstream, problems_dir=problems_dir)


def test_skipped_for_a_standard_checker_and_an_interactive_problem(spec):
    assert checker_tests.skip_detail(spec) is None
    standard = spec.model_copy(
        update={"checker": spec.checker.model_copy(update={"custom_needed": False})}
    )
    assert "standard checker" in checker_tests.skip_detail(standard)
    assert "standard checker" in CHECKER_TESTS_GATE.skip_detail(standard)
    with patch.object(type(spec), "interactive", True):
        assert "interactive" in checker_tests.skip_detail(spec)


def test_no_checker_is_an_input_error(spec, tmp_path):
    (tmp_path / PROBLEM_ID).mkdir()
    with pytest.raises(ValueError, match="checker_draft"):
        checker_tests.compute_input_hash(PROBLEM_ID, spec, _upstream(spec, tmp_path))


def test_writes_tests_and_shows_the_checker_to_the_model(spec, problem_dir):
    response = ModelResponse(
        status="confirmed", artifacts={CHECKER_TESTS_FILENAME: TESTS_YAML}, notes=[]
    )
    result, mock_call = _run_step(spec, problem_dir.parent, response)

    assert result.status == "confirmed"
    assert (problem_dir / CHECKER_TESTS_FILENAME).read_text() == TESTS_YAML
    user_prompt = mock_call.call_args.args[2]
    assert CHECKER_CPP.strip() in user_prompt
    assert "Any valid x." in user_prompt
    assert "The local run of the checker tests failed" not in user_prompt


def test_tests_that_do_not_parse_are_not_written(spec, problem_dir):
    response = ModelResponse(
        status="confirmed", artifacts={CHECKER_TESTS_FILENAME: "tests: []\n"}, notes=[]
    )
    _run_step(spec, problem_dir.parent, response)

    assert not (problem_dir / CHECKER_TESTS_FILENAME).exists()


def test_checker_edit_changes_the_input_hash(spec, problem_dir):
    before = checker_tests.compute_input_hash(PROBLEM_ID, spec, _upstream(spec, problem_dir.parent))
    (problem_dir / "checker.cpp").write_text(CHECKER_CPP + "// edit\n")
    after = checker_tests.compute_input_hash(PROBLEM_ID, spec, _upstream(spec, problem_dir.parent))
    assert before != after


def test_failed_run_makes_the_step_revise_with_its_report(spec, problem_dir, monkeypatch):
    problems_dir = problem_dir.parent
    (problem_dir / CHECKER_TESTS_FILENAME).write_text(TESTS_YAML)
    failure = {"test": 1, "covers": "quitif(x > 1), participant", "problems": ["says OK"]}
    monkeypatch.setattr(
        checker_tests_check,
        "run_checker_tests",
        lambda *args, **kwargs: CheckerTestsRun(failures=[failure]),
    )

    with pytest.raises(GateFailedError, match="1 checker test"):
        _run_gate(spec, problems_dir)

    report = CHECKER_TESTS_GATE.failed_report(PROBLEM_ID, spec, problems_dir=problems_dir)
    assert report["failures"] == [failure]
    reason = checker_tests.forced_rerun_reason(PROBLEM_ID, spec, _upstream(spec, problems_dir))
    assert "run_checker_tests" in reason

    response = ModelResponse(
        status="confirmed", artifacts={CHECKER_TESTS_FILENAME: TESTS_YAML}, notes=[]
    )
    _, mock_call = _run_step(spec, problems_dir, response)
    user_prompt = mock_call.call_args.args[2]
    assert "The local run of the checker tests failed" in user_prompt
    assert "says OK" in user_prompt

    # The revised tests make the report outdated.
    (problem_dir / CHECKER_TESTS_FILENAME).write_text(TESTS_YAML.replace("2\n", "3\n"))
    assert CHECKER_TESTS_GATE.failed_report(PROBLEM_ID, spec, problems_dir=problems_dir) is None
    assert (
        checker_tests.forced_rerun_reason(PROBLEM_ID, spec, _upstream(spec, problems_dir)) is None
    )


def test_problem_errors_fail_the_run_without_a_revision(spec, problem_dir, monkeypatch):
    problems_dir = problem_dir.parent
    monkeypatch.setattr(
        checker_tests_check,
        "run_checker_tests",
        lambda *args, **kwargs: CheckerTestsRun(errors=["the checker rejects example_1"]),
    )

    with pytest.raises(GateFailedError, match="fix the checker") as error:
        _run_gate(spec, problems_dir)

    assert error.value.rerun_hint == ""
    assert CHECKER_TESTS_GATE.failed_report(PROBLEM_ID, spec, problems_dir=problems_dir) is None


def test_passed_run(spec, problem_dir, monkeypatch):
    monkeypatch.setattr(
        checker_tests_check,
        "run_checker_tests",
        lambda *args, **kwargs: CheckerTestsRun(tests=3),
    )

    result = _run_gate(spec, problem_dir.parent)

    assert result.status == "confirmed"
    assert "3 checker test(s)" in result.notes[0]["explanation"]
