from pathlib import Path
from unittest.mock import MagicMock

import pytest
import yaml
from polygon_sync_helpers import FIXTURES_DIR, POLYGON_ID, PROBLEM_ID, link, run_and_record

from orchestrator.polygon.client import PolygonApiError
from orchestrator.polygon.state import load_polygon_state
from orchestrator.polygon.steps.base import PolygonStepError, StepContext
from orchestrator.polygon.steps.upload_validator_tests import STEP, validator_test_digest
from orchestrator.polygon.sync import describe_keys
from orchestrator.spec import load_spec

TESTS = [
    {"covers": "n", "verdict": "VALID", "input": "1\n1\n"},
    {"covers": "n", "verdict": "INVALID", "input": "0\n", "expect": "[name=n]"},
]
GROUPS_YAML = """\
problem_id: example-problem
test_groups:
  - name: "1"
    variables: {N: {max: 10}}
    points: 100
    dependencies: []
    feedback_policy: icpc
    status: confirmed
"""


@pytest.fixture
def spec():
    return load_spec(FIXTURES_DIR / "valid-spec.yaml")


def _write_tests(problems_dir: Path, tests=TESTS, groups: str | None = None) -> None:
    problem_dir = problems_dir / PROBLEM_ID
    problem_dir.mkdir(parents=True, exist_ok=True)
    (problem_dir / "validator_tests.yaml").write_text(yaml.safe_dump({"tests": tests}))
    if groups is not None:
        (problem_dir / "authoring").mkdir(exist_ok=True)
        (problem_dir / "authoring" / "constraints.yaml").write_text(groups)


def _client(remote: list[dict] | None = None) -> MagicMock:
    client = MagicMock()
    client.call.side_effect = lambda method, params: (
        list(remote or []) if method == "problem.validatorTests" else None
    )
    return client


def _saved(client) -> list[dict]:
    return [
        c.args[1] for c in client.call.call_args_list if c.args[0] == "problem.saveValidatorTest"
    ]


def _remote(test: dict, index: int, group: str | None = None) -> dict:
    remote = {
        "index": index,
        "input": test["input"].replace("\n", "\r\n"),
        "expectedVerdict": test["verdict"],
    }
    if group is not None:
        remote["group"] = group
    return remote


def test_uploads_the_tests_in_order(problems_dir, spec):
    link(problems_dir)
    _write_tests(problems_dir)
    client = _client()

    result = run_and_record(STEP, PROBLEM_ID, spec, problems_dir, client)

    assert client.call.call_args_list[0].args == (
        "problem.validatorTests",
        {"problemId": str(POLYGON_ID)},
    )
    assert _saved(client) == [
        {
            "problemId": str(POLYGON_ID),
            "testIndex": str(index),
            "testInput": test["input"],
            "testVerdict": test["verdict"],
        }
        for index, test in enumerate(TESTS, start=1)
    ]
    assert result.notice == ""
    synced = load_polygon_state(PROBLEM_ID, problems_dir=problems_dir).synced
    assert {"validator_tests/1", "validator_tests/2"} <= set(synced)
    assert describe_keys(["validator_tests/2"]) == ["validator test 2"]

    # Nothing changed — no network.
    client = _client()
    run_and_record(STEP, PROBLEM_ID, spec, problems_dir, client, incremental=True)
    client.call.assert_not_called()


def test_a_test_of_a_group_is_saved_with_its_group(problems_dir, spec):
    link(problems_dir)
    tests = [{**TESTS[0], "group": "1"}, TESTS[1]]
    _write_tests(problems_dir, tests, groups=GROUPS_YAML)
    client = _client()

    run_and_record(STEP, PROBLEM_ID, spec, problems_dir, client)

    saved = _saved(client)
    assert saved[0]["testGroup"] == "1"
    assert "testset" not in saved[0]
    assert "testGroup" not in saved[1]


def test_saves_only_the_tests_that_differ_from_polygon(problems_dir, spec):
    link(problems_dir)
    tests = [{**TESTS[0], "group": "1"}, TESTS[1]]
    _write_tests(problems_dir, tests, groups=GROUPS_YAML)
    # The first is the same on Polygon (with \r\n line endings), the second — another verdict.
    remote = [_remote(TESTS[0], 1, group="1"), _remote({**TESTS[1], "verdict": "VALID"}, 2)]
    client = _client(remote)

    run_and_record(STEP, PROBLEM_ID, spec, problems_dir, client)

    assert [p["testIndex"] for p in _saved(client)] == ["2"]


def test_another_group_on_polygon_is_a_difference(problems_dir, spec):
    link(problems_dir)
    _write_tests(problems_dir, [{**TESTS[0], "group": "1"}], groups=GROUPS_YAML)
    client = _client([_remote(TESTS[0], 1)])

    run_and_record(STEP, PROBLEM_ID, spec, problems_dir, client)

    assert [p["testIndex"] for p in _saved(client)] == ["1"]


def test_tests_after_the_last_local_one_are_reported(problems_dir, spec):
    link(problems_dir)
    _write_tests(problems_dir, TESTS[:1])
    client = _client([_remote(TESTS[0], 1), _remote(TESTS[1], 2)])

    result = run_and_record(STEP, PROBLEM_ID, spec, problems_dir, client)

    assert _saved(client) == []
    assert "validator tests 2 on Polygon" in result.notice


def test_no_file_uploads_nothing(problems_dir, spec):
    link(problems_dir)
    client = _client()

    result = run_and_record(STEP, PROBLEM_ID, spec, problems_dir, client)

    assert "nothing to upload" in result.message
    client.call.assert_not_called()


def test_an_unknown_group_is_an_error_before_the_network(problems_dir, spec):
    link(problems_dir)
    _write_tests(problems_dir, [{**TESTS[0], "group": "1"}])
    client = _client()

    with pytest.raises(PolygonStepError):
        STEP.execute(StepContext(PROBLEM_ID, spec, problems_dir), client)
    client.call.assert_not_called()


def test_digest_ignores_line_endings_and_trailing_spaces():
    assert validator_test_digest("1 \r\n2\r\n\r\n", "VALID", None) == validator_test_digest(
        "1\n2", "VALID", ""
    )
    assert validator_test_digest("1", "VALID", None) != validator_test_digest("1", "INVALID", None)
    assert validator_test_digest("1", "VALID", None) != validator_test_digest("1", "VALID", "1")


def test_the_same_input_in_another_group_is_saved_through_a_placeholder(problems_dir, spec):
    link(problems_dir)
    tests = [{**TESTS[1], "group": "1"}, {**TESTS[1], "group": "2", "covers": "g2"}]
    groups = GROUPS_YAML + '  - name: "2"\n    variables: null\n    points: 0\n'
    _write_tests(problems_dir, tests, groups=groups)
    inputs: list[str] = []

    def call(method, params):
        if method == "problem.validatorTests":
            return []
        if "testVerdict" in params and params["testInput"] in inputs:
            raise PolygonApiError(method, "testInput: You already have the same validator test")
        inputs.append(params["testInput"])
        return None

    client = MagicMock()
    client.call.side_effect = call

    run_and_record(STEP, PROBLEM_ID, spec, problems_dir, client)

    saved = _saved(client)
    assert [p["testInput"] for p in saved] == [
        "0\n",
        "0\n",
        "orchestrator placeholder of validator test 2\n",
        "0\n",
    ]
    assert saved[2]["testGroup"] == "2"
    assert set(saved[3]) == {"problemId", "testIndex", "testInput"}


def test_another_refusal_is_not_worked_around(problems_dir, spec):
    link(problems_dir)
    _write_tests(problems_dir, [{**TESTS[0], "group": "1"}], groups=GROUPS_YAML)

    def call(method, params):
        if method == "problem.validatorTests":
            return []
        raise PolygonApiError(method, "testGroup: no such group")

    client = MagicMock()
    client.call.side_effect = call

    with pytest.raises(PolygonApiError):
        STEP.execute(StepContext(PROBLEM_ID, spec, problems_dir), client)
    assert len(_saved(client)) == 1
