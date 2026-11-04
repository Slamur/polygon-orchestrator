import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from polygon_sync_helpers import FIXTURES_DIR, run_and_record

from orchestrator.polygon.client import PolygonApiError
from orchestrator.polygon.state import (
    PolygonState,
    load_polygon_state,
    save_polygon_state,
)
from orchestrator.polygon.steps.create_problem import STEP
from orchestrator.spec import load_spec

PROBLEM_ID = "p1"


@pytest.fixture
def spec():
    return load_spec(FIXTURES_DIR / "valid-spec.yaml")


def _state_path(problems_dir: Path) -> Path:
    return problems_dir / PROBLEM_ID / "polygon_state.json"


def _run(spec, problems_dir, client, *, incremental: bool = False) -> str:
    return run_and_record(
        STEP, PROBLEM_ID, spec, problems_dir, client, incremental=incremental
    ).message


def test_creates_new_problem_when_nothing_exists(tmp_path, spec):
    client = MagicMock()
    client.call.side_effect = lambda method, params: (
        ([{"id": 555, "revision": 1}] if "id" in params else [])
        if method == "problems.list"
        else {"id": 555}
    )

    message = _run(spec, tmp_path, client)

    assert "created new" in message
    assert "555" in message
    assert "auto-update" in message
    create_calls = [c for c in client.call.call_args_list if c.args[0] == "problem.create"]
    assert len(create_calls) == 1
    assert create_calls[0].args[1] == {"name": PROBLEM_ID}
    state = load_polygon_state(PROBLEM_ID, problems_dir=tmp_path)
    assert state is not None
    assert (state.problem_id, state.polygon_id) == (PROBLEM_ID, 555)
    # a new problem is empty — the base is known, push doesn't require pull
    assert state.revision == 1
    assert state.created_at


def test_nested_example_is_created_under_joined_problem_id(tmp_path, spec):
    examples = tmp_path / "examples"
    client = MagicMock()
    client.call.side_effect = lambda method, params: (
        ([{"id": 555, "revision": 1}] if "id" in params else [])
        if method == "problems.list"
        else {"id": 555}
    )

    run_and_record(STEP, "array/prefix-sums", spec, examples, client)

    create_calls = [c for c in client.call.call_args_list if c.args[0] == "problem.create"]
    assert create_calls[0].args[1] == {"name": "array--prefix-sums"}
    state = load_polygon_state("array/prefix-sums", problems_dir=examples)
    assert state.problem_id == "array--prefix-sums"


def test_reuses_existing_polygon_problem_found_by_name(tmp_path, spec):
    client = MagicMock()
    client.call.return_value = [{"id": 123, "name": PROBLEM_ID}]

    message = _run(spec, tmp_path, client)

    assert "reused" in message
    assert "polygon pull" in message
    assert "auto-update" not in message
    client.call.assert_called_once_with("problems.list", {"name": PROBLEM_ID})
    state = load_polygon_state(PROBLEM_ID, problems_dir=tmp_path)
    assert state is not None
    assert state.polygon_id == 123
    # what's in the found problem is unknown — push will require pull
    assert state.revision is None


def test_existing_local_state_makes_no_network_calls(tmp_path, spec):
    save_polygon_state(
        PROBLEM_ID,
        PolygonState(
            problem_id=PROBLEM_ID,
            polygon_id=777,
            created_at="2026-09-19T12:00:00+00:00",
        ),
        problems_dir=tmp_path,
    )
    client = MagicMock()

    message = _run(spec, tmp_path, client)

    assert "already linked" in message
    assert "777" in message
    client.call.assert_not_called()
    assert json.loads(_state_path(tmp_path).read_text(encoding="utf-8"))["polygon_id"] == 777


def test_create_failure_propagates_and_saves_nothing(tmp_path, spec):
    def fake_call(method, params):
        if method == "problems.list":
            return []
        raise PolygonApiError(method, "boom")

    client = MagicMock()
    client.call.side_effect = fake_call

    with pytest.raises(PolygonApiError, match="boom"):
        _run(spec, tmp_path, client)

    assert not _state_path(tmp_path).exists()
