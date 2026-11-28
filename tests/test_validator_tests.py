import shutil
from pathlib import Path

import pytest
import yaml

from orchestrator.checks.validator_tests import (
    MAX_CHARS,
    MAX_LINES,
    VALIDATOR_TESTS_FILENAME,
    ValidatorTest,
    ValidatorTestsError,
    coverage_errors,
    judge_message,
    parse_validator_tests,
    range_violation,
    run_validator_tests,
)
from orchestrator.steps.validator_tests import check_validator_tests

REPO_ROOT = Path(__file__).parent.parent
FLAT_EXAMPLE = REPO_ROOT / "examples" / "array" / "prefix-sums"
GROUPS_EXAMPLE = REPO_ROOT / "examples" / "number" / "roots-subtasks"

INVALID = {"covers": "n", "verdict": "INVALID", "input": "0\n", "expect": "[name=n]"}
VALID = {"covers": "n", "verdict": "VALID", "input": "1\n"}


def _text(*tests: dict) -> str:
    return yaml.safe_dump({"tests": list(tests)})


def test_parse_reads_groups_and_numbers_as_texts():
    tests = parse_validator_tests(
        "tests:\n  - {covers: x, verdict: VALID, group: 2, input: 5}\n", groups=["1", "2"]
    )
    assert tests == [ValidatorTest("x", "VALID", "5", "2", None)]


@pytest.mark.parametrize(
    ("text", "groups", "match"),
    [
        ("tests: []\n", None, "non-empty list"),
        (": [\n", None, "not valid YAML"),
        (_text({**VALID, "verdict": "OK"}), None, "is not one of"),
        (_text({k: v for k, v in INVALID.items() if k != "expect"}), None, "needs 'expect'"),
        (_text({**VALID, "expect": "x"}), None, "only for an INVALID test"),
        (_text({**VALID, "extra": 1}), None, "unknown keys"),
        (_text({**VALID, "group": "1"}), None, "has no test groups"),
        (_text({**VALID, "group": "3"}), ["1", "2"], "unknown test group"),
        (_text(VALID, {**VALID, "covers": "y"}), None, "the same input and group as test 1"),
        (_text({**VALID, "input": "1\n" * (MAX_LINES + 1)}), None, f"at most {MAX_LINES} lines"),
        (_text({**VALID, "input": "1 " * MAX_CHARS}), None, f"{MAX_CHARS} characters"),
    ],
)
def test_parse_rejects(text, groups, match):
    with pytest.raises(ValidatorTestsError, match=match):
        parse_validator_tests(text, groups=groups)


def test_the_same_input_in_another_group_is_another_test():
    tests = parse_validator_tests(_text(VALID, {**VALID, "group": "1"}), groups=["1"])
    assert [test.group for test in tests] == [None, "1"]


def test_every_group_needs_a_valid_test():
    tests = parse_validator_tests(
        _text({**VALID, "group": "1"}, {**INVALID, "group": "2"}), groups=["1", "2"]
    )
    assert coverage_errors(tests, ["1", "2"]) == [
        f"{VALIDATOR_TESTS_FILENAME}: no VALID test of test group(s) 2"
    ]
    assert coverage_errors(tests, None) == []
    constraints = 'test_groups:\n  - name: "1"\n    points: 40\n  - name: "2"\n    points: 60\n'
    from orchestrator.steps.validator_tests import group_names

    groups = group_names(constraints)
    assert groups == ["1", "2"]
    with pytest.raises(ValidatorTestsError, match="no VALID test of test group"):
        check_validator_tests(_text({**VALID, "group": "1"}), groups)


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("FAIL Integer parameter [name=n] equals to 11, violates the range [1, 10]", (11, 1, 10)),
        ("Integer element a[3] equals to -1, violates the range [0, 10^9]", (-1, 0, 10**9)),
        ("Integer 5 violates the range [-3*10^7, 4]", (5, -3 * 10**7, 4)),
        ("Expected EOLN", None),
    ],
)
def test_range_violation(message, expected):
    assert range_violation(message) == expected


def test_judge_message():
    test = ValidatorTest("n", "INVALID", "11\n", None, "[name=n]")
    one_past = "Integer parameter [name=n] equals to 11, violates the range [1, 10]"
    assert judge_message(test, one_past) == []
    far = one_past.replace("11,", "100,")
    assert "not one past its bound (0 or 11)" in judge_message(test, far)[0]
    other = "Integer element a[1] equals to 0, violates the range [1, 10]"
    assert "doesn't contain '[name=n]'" in judge_message(test, other)[0]


@pytest.fixture
def copy_problem(tmp_path):
    def copy(source: Path) -> Path:
        target = tmp_path / source.name
        shutil.copytree(source, target, ignore=shutil.ignore_patterns("llm_reports", "*.log"))
        return target

    return copy


@pytest.mark.slow
@pytest.mark.parametrize("example", [FLAT_EXAMPLE, GROUPS_EXAMPLE])
def test_sample_problem_tests_pass(example, copy_problem):
    run = run_validator_tests(copy_problem(example))
    assert (run.failures, run.errors) == ([], [])
    assert run.tests > 0


@pytest.mark.slow
def test_wrong_tests_are_failures(copy_problem):
    problem = copy_problem(GROUPS_EXAMPLE)
    tests = [
        # Valid by the full limits, but not in group 1.
        {"covers": "group 1 max", "verdict": "VALID", "group": "1", "input": "11\n"},
        {"covers": "full max", "verdict": "INVALID", "input": "5\n", "expect": "[name=n]"},
        # Out of range, but not one past the bound.
        {"covers": "far", "verdict": "INVALID", "group": "1", "input": "50\n", "expect": "n"},
        # Fails on the missing end of line, not on n.
        {"covers": "eoln", "verdict": "INVALID", "input": "5", "expect": "[name=n]"},
        *({"covers": f"g{g}", "verdict": "VALID", "group": g, "input": "2\n"} for g in "12345"),
    ]
    (problem / VALIDATOR_TESTS_FILENAME).write_text(_text(*tests))

    run = run_validator_tests(problem)

    assert run.errors == []
    assert [failure["test"] for failure in run.failures] == [1, 2, 3, 4]
    assert "the validator rejects it" in run.failures[0]["problems"][0]
    assert "the validator accepts it" in run.failures[1]["problems"][0]
    assert "not one past its bound" in run.failures[2]["problems"][0]
    assert "doesn't contain '[name=n]'" in run.failures[3]["problems"][0]


@pytest.mark.slow
def test_problem_errors(copy_problem):
    problem = copy_problem(FLAT_EXAMPLE)
    (problem / VALIDATOR_TESTS_FILENAME).write_text("tests: []\n")
    assert "non-empty list" in run_validator_tests(problem).errors[0]

    (problem / VALIDATOR_TESTS_FILENAME).write_text(_text(VALID))
    (problem / "validator.cpp").write_text("int main( {")
    assert "compilation failed" in run_validator_tests(problem).errors[0]

    (problem / VALIDATOR_TESTS_FILENAME).unlink()
    assert run_validator_tests(problem).errors == [f"no {VALIDATOR_TESTS_FILENAME}"]
