"""Comparing the test groups a solution passed with the `[...]` of its
`known_wrong_approaches` item or with the `passes groups: [...]` comment of a suggestion
(`solutions_run._judge_spec_groups`) — without running
anything: the group results are built by hand."""

from orchestrator.checks.solutions_run import (
    ALLOWED_VERDICTS,
    REQUIRED_VERDICTS,
    STATUS_EXPECTED,
    STATUS_UNEXPECTED,
    SolutionOutcome,
    SolutionsRunResult,
    _judge_spec_groups,
)
from orchestrator.checks.test_groups import GroupResult, TestGroup
from orchestrator.spec import find_approach_marker, find_suggested_groups

ITEMS = [
    "wa (py) [1, 2]: an off-by-one bound",
    "tl_ok [1]: a quadratic loop",
    "wa: a missed case",
    "tl [1-2]: a cubic loop",
]


def _groups(*results: tuple[str, tuple[str, str] | None]) -> list[GroupResult]:
    """(name, the first failed test or None) -> a group with two tests, both run."""
    return [
        GroupResult(TestGroup(name, 10), 2, 2 if failed is None else 1, failed, failed is None)
        for name, failed in results
    ]


def _outcome(verdict: str, groups: list[GroupResult]) -> SolutionOutcome:
    return SolutionOutcome(
        f"{verdict}_py_x_draft.py",
        verdict,
        ALLOWED_VERDICTS[verdict],
        required=REQUIRED_VERDICTS.get(verdict),
        groups=groups,
    )


def _marker(index: int) -> str:
    return f"# spec: solutions.known_wrong_approaches[{index}]\nprint(1)\n"


def test_marker_is_found_in_any_comment_style():
    assert find_approach_marker("// spec: solutions.known_wrong_approaches[12]\n") == 12
    assert find_approach_marker(_marker(0)) == 0
    assert find_approach_marker("int main() {}\n") is None
    assert find_approach_marker("// my_solutions.known_wrong_approaches[1]\n") is None


def test_named_groups_passed_and_others_failed_are_expected():
    outcome = _outcome("wa", _groups(("1", None), ("2", None), ("3", ("test 9", "WA"))))
    outcome.tests = []
    _judge_spec_groups(outcome, _marker(0), ITEMS)
    assert outcome.spec_item == 0
    assert outcome.group_mismatches == [] and outcome.group_warnings == []


def test_a_named_group_failed_makes_the_solution_unexpected():
    outcome = _outcome("wa", _groups(("1", None), ("2", ("test 5", "WA")), ("3", ("test 9", "WA"))))
    _judge_spec_groups(outcome, _marker(0), ITEMS)
    assert outcome.group_mismatches == [
        "group 2: WA on test 5, solutions.known_wrong_approaches[0] says it passes the group"
    ]
    assert outcome.status == STATUS_UNEXPECTED and outcome.failed
    result = SolutionsRunResult(solutions=[outcome])
    assert outcome.group_mismatches[0] in result.summary()["solutions"][0]["group_mismatches"]
    unexpected = next(
        lines for title, lines, _ in result.groups() if title == "unexpected verdicts"
    )
    assert "  " + outcome.group_mismatches[0] in unexpected


def test_a_group_not_named_but_passed_is_a_warning():
    outcome = _outcome("wa", _groups(("1", None), ("2", None), ("3", None)))
    _judge_spec_groups(outcome, _marker(0), ITEMS)
    assert outcome.group_mismatches == []
    assert outcome.group_warnings == [
        "passes group 3, solutions.known_wrong_approaches[0] says it doesn't: "
        "no test of the group catches it"
    ]
    assert outcome.uncaught_groups == ["3"]
    result = SolutionsRunResult(solutions=[outcome])
    assert f"{outcome.name}: {outcome.group_warnings[0]}" in result.warnings
    assert result.summary()["solutions"][0]["uncaught_groups"] == ["3"]


def test_tl_ok_may_pass_groups_it_does_not_name():
    outcome = _outcome("tl_ok", _groups(("1", None), ("2", None)))
    _judge_spec_groups(outcome, _marker(1), ITEMS)
    assert outcome.group_mismatches == [] and outcome.group_warnings == []
    assert outcome.uncaught_groups == []
    assert outcome.status == STATUS_EXPECTED


def test_own_time_verdict_in_a_named_group_is_only_a_warning():
    outcome = _outcome("tl", _groups(("1", None), ("2", ("test 6", "TL"))))
    _judge_spec_groups(outcome, _marker(3), ITEMS)
    assert outcome.group_mismatches == []
    assert outcome.group_warnings == [
        "group 2: TL on test 6, solutions.known_wrong_approaches[3] says it passes the group "
        "(local time and memory, not Polygon's)"
    ]


def test_a_tl_ml_solution_may_come_from_a_tl_item():
    outcome = _outcome("tl_ml", _groups(("1", None), ("2", ("test 6", "WA"))))
    _judge_spec_groups(outcome, _marker(3), ITEMS)
    assert outcome.group_mismatches == [
        "group 2: WA on test 6, solutions.known_wrong_approaches[3] says it passes the group"
    ]


def test_a_group_with_tests_not_run_is_not_judged():
    not_run = GroupResult(TestGroup("2", 10), 2, 1, None, False)
    outcome = _outcome("wa", [*_groups(("1", None)), not_run, *_groups(("3", ("t", "WA")))])
    _judge_spec_groups(outcome, _marker(0), ITEMS)
    assert outcome.group_mismatches == [] and outcome.group_warnings == []


def test_without_marker_or_without_groups_in_the_item_nothing_is_compared():
    outcome = _outcome("wa", _groups(("1", ("t", "WA"))))
    _judge_spec_groups(outcome, "print(1)\n", ITEMS)
    assert outcome.spec_item is None and not outcome.group_mismatches
    _judge_spec_groups(outcome, _marker(2), ITEMS)
    assert outcome.spec_item == 2
    assert outcome.group_mismatches == [] and outcome.group_warnings == []


def test_marker_of_a_missing_or_another_verdict_item_is_a_warning():
    missing = _outcome("wa", _groups(("1", ("t", "WA"))))
    _judge_spec_groups(missing, _marker(7), ITEMS)
    assert missing.group_warnings == [
        "solutions.known_wrong_approaches[7] named in the source is not in the spec"
    ]
    other = _outcome("wa", _groups(("1", ("t", "WA"))))
    _judge_spec_groups(other, _marker(1), ITEMS)
    assert other.group_warnings == [
        "solutions.known_wrong_approaches[1] named in the source is a 'tl_ok' item, "
        "the file name says 'wa'"
    ]
    assert not missing.group_mismatches and not other.group_mismatches


def test_a_named_group_the_problem_does_not_have_is_a_warning():
    outcome = _outcome("wa", _groups(("1", None), ("3", ("t", "WA"))))
    _judge_spec_groups(outcome, _marker(0), ITEMS)
    assert outcome.group_warnings == [
        "solutions.known_wrong_approaches[0] names test group(s) 2 the problem doesn't have"
    ]


def test_passes_groups_comment_is_found_in_any_comment_style():
    assert find_suggested_groups("// passes groups: [1-3, 5]\n") == ("1", "2", "3", "5")
    assert find_suggested_groups("# passes groups: []\n") == ()
    assert find_suggested_groups("# passes groups: [x]\n") is False
    assert find_suggested_groups("int main() {}\n") is None


def test_a_suggestion_failing_its_groups_is_only_a_warning():
    outcome = _outcome("wa", _groups(("1", ("test 2", "WA")), ("2", None), ("3", None)))
    outcome.tests = []
    _judge_spec_groups(outcome, "# SUGGESTION: small n\n# passes groups: [1]\n", ITEMS)
    assert outcome.spec_item is None
    assert outcome.group_mismatches == []
    assert outcome.group_warnings[0] == (
        "group 1: WA on test 2, its 'passes groups' comment says it passes the group"
    )
    assert outcome.uncaught_groups == ["2", "3"]
    assert outcome.status != STATUS_UNEXPECTED


def test_the_spec_item_marker_wins_over_a_passes_groups_comment():
    outcome = _outcome("wa", _groups(("1", None), ("2", ("test 4", "WA"))))
    outcome.tests = []
    _judge_spec_groups(outcome, _marker(0) + "# passes groups: [1]\n", ITEMS)
    assert outcome.spec_item == 0
    assert outcome.status == STATUS_UNEXPECTED
