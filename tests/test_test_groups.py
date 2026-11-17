from pathlib import Path

import pytest

from orchestrator.checks.test_groups import (
    TestGroup,
    check_test_groups,
    count_by_group,
    group_results,
    load_test_groups,
)
from orchestrator.test_groups import group_marker

CHAIN = [
    TestGroup("1", 30),
    TestGroup("2", 30, ("1",)),
    TestGroup("3", 40, ("2",)),
]


@pytest.mark.parametrize(
    "command, expected",
    [
        ("gen -n=5 -group=2", "2"),
        ("gen -group=12 -n=5 > $", "12"),
        ("gen -group=1 -n=5 | upd -group=3", "3"),
        ("gen -n=5", None),
        ("gen -subgroup=2", None),
        ("gen 1 > -group=3", None),
    ],
)
def test_group_marker(command, expected):
    assert group_marker(command) == expected


def _constraints(tmp_path: Path, text: str) -> Path:
    problem_dir = tmp_path / "example-problem"
    (problem_dir / "authoring").mkdir(parents=True)
    (problem_dir / "authoring" / "constraints.yaml").write_text(text, encoding="utf-8")
    return problem_dir


def test_load_test_groups(tmp_path):
    problem_dir = _constraints(
        tmp_path,
        "test_groups:\n"
        '  - name: "1"\n    points: 30\n    dependencies: []\n'
        '  - name: "2"\n    points: 70\n    dependencies: ["1"]\n',
    )
    assert load_test_groups(problem_dir) == (
        [TestGroup("1", 30), TestGroup("2", 70, ("1",))],
        [],
    )


def test_no_test_groups(tmp_path):
    assert load_test_groups(tmp_path) == (None, [])
    assert load_test_groups(_constraints(tmp_path, "limits: {}\n")) == (None, [])


@pytest.mark.parametrize(
    "groups_yaml, message",
    [
        ('  - name: "small"\n    points: 10\n', "positive number"),
        ('  - name: "1"\n    points: -1\n', "points"),
        ('  - name: "1"\n    points: 1\n  - name: "1"\n    points: 1\n', "repeat"),
        ('  - name: "1"\n    dependencies: ["2"]\n', "dependencies: '2' must name"),
        ('  - name: "1"\n    feedback_policy: "first"\n', "feedback_policy"),
    ],
)
def test_invalid_test_groups(tmp_path, groups_yaml, message):
    groups, errors = load_test_groups(_constraints(tmp_path, "test_groups:\n" + groups_yaml))
    assert groups is None
    assert len(errors) == 1 and message in errors[0]
    assert errors[0].startswith("constraints.yaml: test_groups")


def test_check_test_groups():
    errors = check_test_groups(
        CHAIN,
        [("test 4", "1", "gen -group=1"), ("test 5", None, "gen"), ("test 6", "7", "gen -group=7")],
    )
    assert any("test 5: no -group=<N> marker" in e for e in errors)
    assert any("test 6: unknown test group '7'" in e for e in errors)
    assert "test group 2 has no tests in the script" in errors
    assert "test group 3 has no tests in the script" in errors
    assert not any("test 4" in e for e in errors)


def test_count_by_group():
    tests = [("t", "2", ""), ("t", "10", ""), ("t", "2", ""), ("t", None, "")]
    assert count_by_group(tests, 3) == "test groups: 0 (examples): 3, 2: 2, 10: 1, ?: 1"


def test_points_need_the_dependencies():
    totals = {"1": 1, "2": 2, "3": 1}
    results = group_results(
        CHAIN,
        totals,
        [("test 1", "1", "OK"), ("test 2", "2", "WA"), ("test 4", "3", "OK")],
    )
    assert [r.passed for r in results] == [True, False, True]
    assert [r.points for r in results] == [30, 0, 0]
    assert [r.describe() for r in results] == [
        "1 OK",
        "2 WA on test 2",
        "3 OK, no points (depends on 2)",
    ]


def test_group_with_tests_not_run_is_not_passed():
    results = group_results(CHAIN[:1], {"1": 2}, [("test 1", "1", "OK")])
    assert not results[0].passed
    assert results[0].points == 0
    assert results[0].describe() == "1 not judged (1 test(s) not run)"


def test_full_score():
    results = group_results(
        CHAIN,
        {"1": 1, "2": 1, "3": 1},
        [("test 1", "1", "OK"), ("test 2", "2", "OK"), ("test 3", "3", "OK")],
    )
    assert sum(r.points for r in results) == 100
