import json
import logging
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from orchestrator.polygon.client import PolygonApiError
from orchestrator.polygon.state import (
    PolygonState,
    load_polygon_state,
    save_polygon_state,
)
from orchestrator.polygon.steps.create_problem import STEP_NAME, run_step
from orchestrator.spec import load_spec

FIXTURES_DIR = Path(__file__).parent / "fixtures"
PROBLEM_ID = "p1"


@pytest.fixture
def spec():
    return load_spec(FIXTURES_DIR / "valid-spec.yaml")


def _state_path(outputs_dir: Path) -> Path:
    return outputs_dir / PROBLEM_ID / "polygon_state.json"


def test_step_name():
    assert STEP_NAME == "create_problem"


def test_creates_new_problem_when_nothing_exists(tmp_path, spec):
    client = MagicMock()
    client.call.side_effect = lambda method, params: {
        "problems.list": [],
        "problem.create": {"id": 555},
    }[method]

    message = run_step(PROBLEM_ID, spec, outputs_dir=tmp_path, client=client)

    assert "created new" in message
    assert "555" in message
    create_calls = [c for c in client.call.call_args_list if c.args[0] == "problem.create"]
    assert len(create_calls) == 1
    assert create_calls[0].args[1] == {"name": PROBLEM_ID}
    state = load_polygon_state(PROBLEM_ID, outputs_dir=tmp_path)
    assert state is not None
    assert (state.problem_id, state.polygon_id) == (PROBLEM_ID, 555)
    assert state.created_at


def test_reuses_existing_polygon_problem_found_by_name(tmp_path, spec):
    client = MagicMock()
    client.call.return_value = [{"id": 123, "name": PROBLEM_ID}]

    message = run_step(PROBLEM_ID, spec, outputs_dir=tmp_path, client=client)

    assert "reused" in message
    client.call.assert_called_once_with("problems.list", {"name": PROBLEM_ID})
    state = load_polygon_state(PROBLEM_ID, outputs_dir=tmp_path)
    assert state is not None
    assert state.polygon_id == 123


def test_existing_local_state_makes_no_network_calls(tmp_path, spec):
    save_polygon_state(
        PROBLEM_ID,
        PolygonState(
            problem_id=PROBLEM_ID,
            polygon_id=777,
            created_at="2026-09-19T12:00:00+00:00",
        ),
        outputs_dir=tmp_path,
    )
    client = MagicMock()

    message = run_step(PROBLEM_ID, spec, outputs_dir=tmp_path, client=client)

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
        run_step(PROBLEM_ID, spec, outputs_dir=tmp_path, client=client)

    assert not _state_path(tmp_path).exists()


def test_logs_the_returned_message(tmp_path, spec, caplog):
    client = MagicMock()
    client.call.return_value = [{"id": 123}]

    with caplog.at_level(logging.INFO, logger="orchestrator.polygon.steps.create_problem"):
        message = run_step(PROBLEM_ID, spec, outputs_dir=tmp_path, client=client)

    assert message in caplog.messages
