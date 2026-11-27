import shutil
from pathlib import Path

import pytest
import yaml

from orchestrator.checks.checker_tests import (
    AUTO_CHECKER_TESTS_FILENAME,
    CHECKER_TESTS_FILENAME,
    CheckerTest,
    CheckerTestsError,
    dump_checker_tests,
    parse_checker_tests,
    run_checker_tests,
)
from orchestrator.steps.checker_tests import check_checker_tests

REPO_ROOT = Path(__file__).parent.parent
EXAMPLE_DIR = REPO_ROOT / "examples" / "array" / "prefix-sums"

TEST = {"covers": "x", "verdict": "WA", "input": "1\n", "output": "2\n", "answer": "1\n"}


def _text(*tests: dict) -> str:
    return yaml.safe_dump({"tests": list(tests)})


def test_parse_and_dump_round_trip():
    tests = [
        CheckerTest("a branch", "WA", "5 6\n2 1 3 5 4\n", "YES\n3\n", "YES\n4\n"),
        CheckerTest("one line", "FAIL", "1", "", "2"),
    ]
    assert parse_checker_tests(dump_checker_tests(tests, "# header\n")) == tests


def test_parse_accepts_numbers_as_texts():
    tests = parse_checker_tests(
        "tests:\n  - {covers: x, verdict: OK, input: 1, output: 2, answer: 2}\n"
    )
    assert tests == [CheckerTest("x", "OK", "1", "2", "2")]


@pytest.mark.parametrize(
    ("text", "match"),
    [
        ("tests: []\n", "non-empty list"),
        ("- 1\n", "non-empty list"),
        (": [\n", "not valid YAML"),
        (_text({**TEST, "verdict": "CRASHED"}), "is not one of"),
        (_text({**TEST, "answer": ""}), "'answer' must be a non-empty string"),
        (_text({k: v for k, v in TEST.items() if k != "covers"}), "'covers'"),
        (_text({**TEST, "extra": 1}), "unknown keys"),
        (_text(TEST, {**TEST, "covers": "y"}), "the same input, output and answer as test 1"),
    ],
)
def test_parse_rejects(text, match):
    with pytest.raises(CheckerTestsError, match=match):
        parse_checker_tests(text)


def test_step_check_rejects_extra_data_after_the_answer():
    extra = {**TEST, "verdict": "PE", "output": "1\n2\n"}
    with pytest.raises(CheckerTestsError, match="only add data after the answer"):
        check_checker_tests(_text(TEST, extra))
    # A malformed answer, not extra data: allowed.
    check_checker_tests(_text({**TEST, "verdict": "PE", "output": "x\n"}))


@pytest.fixture
def problem(tmp_path):
    """A copy of the sample problem with a custom checker and its checker tests."""
    problem_dir = tmp_path / "prefix-sums"
    shutil.copytree(
        EXAMPLE_DIR,
        problem_dir,
        ignore=shutil.ignore_patterns("llm_reports", "*.log", AUTO_CHECKER_TESTS_FILENAME),
    )
    return problem_dir


def _model_tests(problem_dir: Path) -> list[dict]:
    return yaml.safe_load((problem_dir / CHECKER_TESTS_FILENAME).read_text())["tests"]


def _write_model_tests(problem_dir: Path, tests: list[dict]) -> None:
    (problem_dir / CHECKER_TESTS_FILENAME).write_text(_text(*tests), encoding="utf-8")


@pytest.mark.slow
def test_sample_problem_tests_pass_and_examples_become_ok_tests(problem):
    run = run_checker_tests(problem)

    assert (run.failures, run.errors) == ([], [])
    assert run.tests == len(_model_tests(problem)) + 2
    auto = parse_checker_tests((problem / AUTO_CHECKER_TESTS_FILENAME).read_text())
    assert [(t.verdict, t.output, t.answer) for t in auto] == [
        ("OK", "YES\n4\n", "YES\n4\n"),
        ("OK", "NO\n", "NO\n"),
    ]
    # The file in the sample is the same as what the run writes.
    assert (problem / AUTO_CHECKER_TESTS_FILENAME).read_text() == (
        EXAMPLE_DIR / AUTO_CHECKER_TESTS_FILENAME
    ).read_text()


@pytest.mark.slow
def test_correct_solution_with_another_output_gives_an_ok_test(problem):
    # The same tokens as the main solution — no test; other tokens — a test (here it isn't
    # a correct answer, so the checker rejects it: an error of the problem).
    alt = (problem / "solutions" / "ok_py_prefs_draft.py").read_text() + "\nprint()\n"
    (problem / "solutions" / "ok_py_alt_draft.py").write_text(alt)
    assert len(run_checker_tests(problem).auto) == 2

    (problem / "solutions" / "ok_py_alt_draft.py").write_text("print('NO')\n")
    run = run_checker_tests(problem)
    assert [t.covers for t in run.auto if t.output == "NO\n" and t.answer != "NO\n"] == [
        "example_1: the output of ok_py_alt_draft.py"
    ]
    assert any("example_1: the output of ok_py_alt_draft.py" in e for e in run.errors)
    assert run.failures == []


@pytest.mark.slow
def test_wrong_verdict_invalid_input_and_wrong_correct_side_are_failures(problem):
    tests = _model_tests(problem)
    wrong_verdict = {**tests[0], "verdict": "OK"}
    invalid_input = {**tests[2], "input": "0 6\n\n"}
    # A FAIL test whose participant output isn't a correct answer either.
    wrong_side = {**tests[5], "output": "YES\n5\n"}
    _write_model_tests(problem, [wrong_verdict, tests[1], invalid_input, wrong_side])

    run = run_checker_tests(problem)

    assert run.errors == []
    by_test = {f["test"]: " ".join(f["problems"]) for f in run.failures}
    assert sorted(by_test) == [1, 3, 4]
    assert "the checker says WA, expected OK" in by_test[1]
    assert "validator.cpp rejects the input" in by_test[3]
    assert "its output is not a correct answer" in by_test[4]


@pytest.mark.slow
def test_checker_rejecting_an_example_is_a_problem_error(problem):
    checker = (problem / "checker.cpp").read_text()
    (problem / "checker.cpp").write_text(
        checker.replace('quitf(_ok, "OK (answer %d)", pa);', 'quitf(_wa, "always");')
    )

    run = run_checker_tests(problem)

    assert any(AUTO_CHECKER_TESTS_FILENAME in error for error in run.errors)


@pytest.mark.slow
def test_unparsable_tests_file_is_a_problem_error(problem):
    (problem / CHECKER_TESTS_FILENAME).write_text("tests: []\n")

    run = run_checker_tests(problem)

    assert any("non-empty list" in error for error in run.errors)
    assert len(run.auto) == 2
