from pathlib import Path
from unittest.mock import MagicMock

import pytest
import yaml
from polygon_sync_helpers import FIXTURES_DIR, POLYGON_ID, PROBLEM_ID, link, run_and_record

from orchestrator.polygon.state import load_polygon_state
from orchestrator.polygon.steps.upload_checker_tests import (
    NOT_NEEDED_MESSAGE,
    STEP,
    checker_test_digest,
)
from orchestrator.polygon.sync import describe_keys
from orchestrator.spec import load_spec

AUTO = [{"covers": "example_1", "verdict": "OK", "input": "1\n", "output": "1\n", "answer": "1\n"}]
MODEL = [
    {"covers": "x, participant", "verdict": "WA", "input": "2\n", "output": "3\n", "answer": "2\n"},
    {"covers": "x, jury", "verdict": "FAIL", "input": "2\n", "output": "2\n", "answer": "3\n"},
]


@pytest.fixture
def spec():
    spec = load_spec(FIXTURES_DIR / "valid-spec.yaml")
    return spec.model_copy(
        update={
            "checker": spec.checker.model_copy(
                update={"custom_needed": True, "custom_comparison_notes": "Any."}
            )
        }
    )


def _write_tests(problems_dir: Path, auto=AUTO, model=MODEL) -> None:
    problem_dir = problems_dir / PROBLEM_ID
    problem_dir.mkdir(parents=True, exist_ok=True)
    for name, tests in (("checker_tests.auto.yaml", auto), ("checker_tests.yaml", model)):
        if tests:
            (problem_dir / name).write_text(yaml.safe_dump({"tests": tests}))


def _client(remote: list[dict] | None = None) -> MagicMock:
    client = MagicMock()
    client.call.side_effect = lambda method, params: (
        list(remote or []) if method == "problem.checkerTests" else None
    )
    return client


def _saved(client) -> list[dict]:
    return [c.args[1] for c in client.call.call_args_list if c.args[0] == "problem.saveCheckerTest"]


def _remote(test: dict, index: int, verdict: str) -> dict:
    return {
        "index": index,
        "input": test["input"].replace("\n", "\r\n"),
        "output": test["output"],
        "answer": test["answer"],
        "expectedVerdict": verdict,
    }


def test_standard_checker_is_skipped_without_network(problems_dir, spec):
    standard = spec.model_copy(
        update={"checker": spec.checker.model_copy(update={"custom_needed": False})}
    )
    client = _client()

    result = run_and_record(STEP, PROBLEM_ID, standard, problems_dir, client)

    assert result.message == NOT_NEEDED_MESSAGE
    client.call.assert_not_called()


def test_uploads_the_tests_of_both_files_in_order(problems_dir, spec):
    link(problems_dir)
    _write_tests(problems_dir)
    client = _client()

    result = run_and_record(STEP, PROBLEM_ID, spec, problems_dir, client)

    assert client.call.call_args_list[0].args == (
        "problem.checkerTests",
        {"problemId": str(POLYGON_ID)},
    )
    assert _saved(client) == [
        {
            "problemId": str(POLYGON_ID),
            "testIndex": str(index),
            "testInput": test["input"],
            "testOutput": test["output"],
            "testAnswer": test["answer"],
            "testVerdict": verdict,
        }
        for index, test, verdict in [
            (1, AUTO[0], "OK"),
            (2, MODEL[0], "WRONG_ANSWER"),
            (3, MODEL[1], "CRASHED"),
        ]
    ]
    assert result.notice == ""
    synced = load_polygon_state(PROBLEM_ID, problems_dir=problems_dir).synced
    assert {"checker_tests/1", "checker_tests/2", "checker_tests/3"} <= set(synced)
    assert describe_keys(["checker_tests/2"]) == ["checker test 2"]

    # Nothing changed — no network.
    client = _client()
    run_and_record(STEP, PROBLEM_ID, spec, problems_dir, client, incremental=True)
    client.call.assert_not_called()


def test_saves_only_the_tests_that_differ_from_polygon(problems_dir, spec):
    link(problems_dir)
    _write_tests(problems_dir)
    # The same test on Polygon with \r\n line endings, another one — changed there.
    remote = [_remote(AUTO[0], 1, "OK"), _remote({**MODEL[0], "output": "4\n"}, 2, "WRONG_ANSWER")]
    client = _client(remote)

    run_and_record(STEP, PROBLEM_ID, spec, problems_dir, client)

    assert [p["testIndex"] for p in _saved(client)] == ["2", "3"]


def test_tests_after_the_last_local_one_are_reported(problems_dir, spec):
    link(problems_dir)
    _write_tests(problems_dir, model=[])
    remote = [_remote(AUTO[0], 1, "OK"), _remote(MODEL[0], 2, "WRONG_ANSWER")]
    client = _client(remote)

    result = run_and_record(STEP, PROBLEM_ID, spec, problems_dir, client)

    assert _saved(client) == []
    assert "checker tests 2 on Polygon" in result.notice


def test_no_files_uploads_nothing(problems_dir, spec):
    link(problems_dir)
    client = _client()

    result = run_and_record(STEP, PROBLEM_ID, spec, problems_dir, client)

    assert "nothing to upload" in result.message
    client.call.assert_not_called()


def test_digest_ignores_line_endings_and_trailing_spaces():
    assert checker_test_digest("1 \r\n2\r\n\r\n", "a", "b", "OK") == checker_test_digest(
        "1\n2", "a\n", "b", "OK"
    )
    assert checker_test_digest("1", "a", "b", "OK") != checker_test_digest("1", "a", "b", "CRASHED")
