from pathlib import Path

import pytest
from polygon_sync_helpers import FIXTURES_DIR, POLYGON_ID, PROBLEM_ID, link, run_and_record

from orchestrator.polygon.client import PolygonApiError
from orchestrator.polygon.state import load_polygon_state
from orchestrator.polygon.steps.base import PolygonStepError
from orchestrator.polygon.steps.upload_test_groups import (
    NO_GROUPS_MESSAGE,
    STEP,
    read_groups,
)
from orchestrator.polygon.sync import TEST_GROUPS_KEY, TEST_GROUPS_TESTS_KEY
from orchestrator.spec import load_spec

GROUPS_YAML = """\
problem_id: example-problem
test_groups:
  - name: "1"
    max_n: 10
    points: 30
    dependencies: []
    feedback_policy: icpc
    status: confirmed
  - name: "2"
    max_n: null
    points: 70
    dependencies: ["1"]
    feedback_policy: points
    status: confirmed
"""


class FakeTests:
    """The tests and groups of a problem on Polygon: applies what the step sends."""

    def __init__(self, script_lines: list[str]) -> None:
        self.tests = [{"index": 1, "manual": True, "useInStatements": True}] + [
            {"index": i, "manual": False, "scriptLine": line}
            for i, line in enumerate(script_lines, start=2)
        ]
        self.groups: dict[str, dict] = {}
        self.groups_enabled = False
        self.calls: list[tuple[str, dict]] = []

    def call(self, method_name, params, **kwargs):
        self.calls.append((method_name, params))
        assert params["problemId"] == str(POLYGON_ID)
        by_index = {test["index"]: test for test in self.tests}
        if method_name == "problem.tests":
            assert params["testset"] == "tests"
            return [dict(test) for test in self.tests]
        if method_name == "problem.enableGroups":
            assert (params["testset"], params["enable"]) == ("tests", "true")
            self.groups_enabled = True
        elif method_name == "problem.enablePoints":
            assert params["enable"] == "true"
        elif method_name == "problem.setTestGroup":
            assert self.groups_enabled
            for index in params["testIndices"].split(","):
                by_index[int(index)]["group"] = params["testGroup"]
                self.groups.setdefault(params["testGroup"], {"name": params["testGroup"]})
        elif method_name == "problem.saveTest":
            by_index[int(params["testIndex"])]["points"] = float(params["testPoints"])
        elif method_name == "problem.saveTestGroup":
            group = self.groups[params["group"]]
            group["pointsPolicy"] = params["pointsPolicy"]
            group["feedbackPolicy"] = params["feedbackPolicy"]
            deps = params["dependencies"]
            group["dependencies"] = deps.split(",") if deps else []
        elif method_name == "problem.viewTestGroup":
            if not self.groups_enabled:
                raise PolygonApiError(method_name, "groups are disabled")
            return list(self.groups.values())
        else:
            raise AssertionError(method_name)
        return None

    def methods(self) -> list[str]:
        return [name for name, _ in self.calls]

    def calls_of(self, method_name: str) -> list[dict]:
        return [params for name, params in self.calls if name == method_name]


LINES = ["gen 1 -group=1", "gen 2 -group=1", "gen 3 -group=2", "gen 4 -group=2"]


@pytest.fixture
def spec():
    return load_spec(FIXTURES_DIR / "valid-spec.yaml")


def _write_constraints(problems_dir: Path, content: str = GROUPS_YAML) -> Path:
    path = problems_dir / PROBLEM_ID / "authoring" / "constraints.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def _run(spec, problems_dir, client, *, incremental: bool = False) -> str:
    return run_and_record(
        STEP, PROBLEM_ID, spec, problems_dir, client, incremental=incremental
    ).message


def test_without_groups_does_nothing(problems_dir, spec):
    link(problems_dir)
    _write_constraints(problems_dir, "problem_id: example-problem\n")
    client = FakeTests(LINES)

    assert _run(spec, problems_dir, client) == NO_GROUPS_MESSAGE
    assert client.calls == []
    assert STEP.compute_status(PROBLEM_ID, problems_dir)[0] == "done"


def test_sets_groups_points_and_policies(problems_dir, spec):
    link(problems_dir)
    _write_constraints(problems_dir)
    client = FakeTests(LINES)

    message = _run(spec, problems_dir, client)

    assert client.methods()[:3] == ["problem.tests", "problem.enableGroups", "problem.enablePoints"]
    assert {test["index"]: test["group"] for test in client.tests} == {
        1: "0",
        2: "1",
        3: "1",
        4: "2",
        5: "2",
    }
    # The group's points — on its last test.
    assert {test["index"]: test.get("points", 0) for test in client.tests} == {
        1: 0,
        2: 0,
        3: 30,
        4: 0,
        5: 70,
    }
    assert client.groups == {
        "0": {
            "name": "0",
            "pointsPolicy": "COMPLETE_GROUP",
            "feedbackPolicy": "COMPLETE",
            "dependencies": [],
        },
        "1": {
            "name": "1",
            "pointsPolicy": "COMPLETE_GROUP",
            "feedbackPolicy": "ICPC",
            "dependencies": [],
        },
        "2": {
            "name": "2",
            "pointsPolicy": "COMPLETE_GROUP",
            "feedbackPolicy": "POINTS",
            "dependencies": ["1"],
        },
    }
    assert [p["testIndices"] for p in client.calls_of("problem.setTestGroup")] == [
        "1",
        "2,3",
        "4,5",
    ]
    assert "3 test group(s)" in message
    synced = load_polygon_state(PROBLEM_ID, problems_dir=problems_dir).synced
    assert {TEST_GROUPS_KEY, TEST_GROUPS_TESTS_KEY} <= set(synced)
    assert STEP.compute_status(PROBLEM_ID, problems_dir)[0] == "done"


def test_unchanged_groups_only_read_the_tests(problems_dir, spec):
    link(problems_dir)
    _write_constraints(problems_dir)
    client = FakeTests(LINES)
    _run(spec, problems_dir, client)
    client.calls.clear()

    assert "no changes" in _run(spec, problems_dir, client, incremental=True)
    assert client.methods() == ["problem.tests"]


def test_regenerated_tests_are_assigned_again(problems_dir, spec):
    link(problems_dir)
    _write_constraints(problems_dir)
    client = FakeTests(LINES)
    _run(spec, problems_dir, client)

    # A new script: Polygon regenerated the tests, without groups and points.
    client.tests = FakeTests([*LINES, "gen 5 -group=2"]).tests
    client.calls.clear()
    _run(spec, problems_dir, client, incremental=True)

    assert client.tests[-1]["group"] == "2" and client.tests[-1]["points"] == 70
    assert client.tests[-2].get("points", 0) == 0


def test_changed_points_are_stale_and_only_differences_are_sent(problems_dir, spec):
    link(problems_dir)
    path = _write_constraints(problems_dir)
    client = FakeTests(LINES)
    _run(spec, problems_dir, client)

    path.write_text(
        GROUPS_YAML.replace("points: 30", "points: 40").replace("points: 70", "points: 60")
    )
    assert STEP.compute_status(PROBLEM_ID, problems_dir)[0] == "stale"
    client.calls.clear()
    result = run_and_record(STEP, PROBLEM_ID, spec, problems_dir, client, incremental=True)

    # Only the settings changed: the tests' layout isn't reported as pushed.
    assert set(result.uploaded) == {TEST_GROUPS_KEY}
    assert client.calls_of("problem.setTestGroup") == []
    assert [(p["testIndex"], p["testPoints"]) for p in client.calls_of("problem.saveTest")] == [
        ("3", "40"),
        ("5", "60"),
    ]


@pytest.mark.parametrize(
    "lines, message",
    [
        (["gen 1 -group=1", "gen 2"], "test 3 has no -group"),
        (["gen 1 -group=1", "gen 2 -group=7"], "group '7' is not in test_groups"),
        (["gen 1 -group=1", "gen 2 -group=0"], "group '0' is not in test_groups"),
        (["gen 1 -group=1"], r"\['2'\] .* have no tests"),
    ],
)
def test_unassignable_tests_fail_before_any_change(problems_dir, spec, lines, message):
    link(problems_dir)
    _write_constraints(problems_dir)
    client = FakeTests(lines)

    with pytest.raises(PolygonStepError, match=message):
        _run(spec, problems_dir, client)

    assert client.methods() == ["problem.tests"]


def test_invalid_groups_fail_before_network(problems_dir, spec):
    link(problems_dir)
    _write_constraints(problems_dir, GROUPS_YAML.replace('["1"]', '["3"]'))
    client = FakeTests(LINES)

    with pytest.raises(PolygonStepError, match="dependencies"):
        _run(spec, problems_dir, client)

    assert client.calls == []


def test_groups_without_points_are_rejected(problems_dir):
    _write_constraints(problems_dir, GROUPS_YAML.replace("    points: 30\n", ""))

    with pytest.raises(PolygonStepError, match="without points"):
        read_groups(problems_dir / PROBLEM_ID)


def test_numeric_yaml_names_are_read_as_strings(problems_dir):
    _write_constraints(
        problems_dir,
        "test_groups:\n  - name: 1\n    points: 100\n  - name: 2\n    points: 0\n"
        "    dependencies: [1]\n",
    )

    groups = read_groups(problems_dir / PROBLEM_ID)

    assert [(g.name, g.dependencies) for g in groups] == [("0", ()), ("1", ()), ("2", ("1",))]
