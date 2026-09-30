from pathlib import Path
from unittest.mock import MagicMock

import pytest

from orchestrator.polygon.client import PolygonApiError
from orchestrator.polygon.state import PolygonState, load_polygon_state, save_polygon_state
from orchestrator.polygon.steps.base import PolygonStep, PolygonStepError, run_step
from orchestrator.polygon.steps.upload_validator import STEP, STEP_NAME
from orchestrator.spec import load_spec

FIXTURES_DIR = Path(__file__).parent / "fixtures"
PROBLEM_ID = "example-problem"
POLYGON_ID = 4242
VALIDATOR_CONTENT = b'#include "testlib.h"\nint main() { registerValidation(); }\n'


@pytest.fixture
def spec():
    return load_spec(FIXTURES_DIR / "valid-spec.yaml")


@pytest.fixture
def outputs_dir(tmp_path):
    return tmp_path / "outputs"


def _link(outputs_dir: Path) -> None:
    save_polygon_state(
        PROBLEM_ID,
        PolygonState(
            problem_id=PROBLEM_ID,
            polygon_id=POLYGON_ID,
            created_at="2026-09-19T12:00:00+00:00",
        ),
        outputs_dir=outputs_dir,
    )


def _write_validator(outputs_dir: Path, content: bytes = VALIDATOR_CONTENT) -> Path:
    path = outputs_dir / PROBLEM_ID / "validator.cpp"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


def _run(spec, outputs_dir, client) -> str:
    return run_step(STEP, PROBLEM_ID, spec, outputs_dir=outputs_dir, client=client)


def test_step_is_a_polygon_step_with_expected_name():
    assert isinstance(STEP, PolygonStep)
    assert STEP.name == STEP_NAME == "upload_validator"


def test_missing_polygon_state_points_to_create_problem(outputs_dir, spec):
    _write_validator(outputs_dir)
    client = MagicMock()

    with pytest.raises(PolygonStepError, match="create_problem"):
        _run(spec, outputs_dir, client)

    client.call.assert_not_called()


def test_missing_validator_points_to_constraints_pick(outputs_dir, spec):
    _link(outputs_dir)
    client = MagicMock()

    with pytest.raises(PolygonStepError, match="constraints_pick"):
        _run(spec, outputs_dir, client)

    client.call.assert_not_called()


def test_uploads_then_sets_validator(outputs_dir, spec):
    _link(outputs_dir)
    _write_validator(outputs_dir)
    client = MagicMock()

    message = _run(spec, outputs_dir, client)

    assert client.call.call_count == 2
    save_call, set_call = client.call.call_args_list
    assert save_call.args == (
        "problem.saveFile",
        {"problemId": str(POLYGON_ID), "type": "source", "name": "validator.cpp"},
    )
    assert save_call.kwargs["files"]["file"] == ("validator.cpp", VALIDATOR_CONTENT)
    assert set_call.args == (
        "problem.setValidator",
        {"problemId": str(POLYGON_ID), "validator": "validator.cpp"},
    )
    assert str(POLYGON_ID) in message


def test_save_file_error_propagates_and_skips_set_validator(outputs_dir, spec):
    _link(outputs_dir)
    _write_validator(outputs_dir)
    client = MagicMock()
    client.call.side_effect = [PolygonApiError("problem.saveFile", "boom")]

    with pytest.raises(PolygonApiError):
        _run(spec, outputs_dir, client)

    assert [c.args[0] for c in client.call.call_args_list] == ["problem.saveFile"]
    assert STEP_NAME not in load_polygon_state(PROBLEM_ID, outputs_dir=outputs_dir).steps


def test_upload_is_recorded_in_polygon_state(outputs_dir, spec):
    _link(outputs_dir)
    _write_validator(outputs_dir)

    _run(spec, outputs_dir, MagicMock())

    record = load_polygon_state(PROBLEM_ID, outputs_dir=outputs_dir).steps[STEP_NAME]
    assert set(record) == {"sha256", "sent_at"}


def test_status_not_run_then_done_then_stale(outputs_dir, spec):
    _link(outputs_dir)
    validator_path = _write_validator(outputs_dir)
    assert STEP.compute_status(PROBLEM_ID, outputs_dir)[0] == "not run"

    _run(spec, outputs_dir, MagicMock())
    assert STEP.compute_status(PROBLEM_ID, outputs_dir)[0] == "done"

    validator_path.write_bytes(VALIDATOR_CONTENT + b"// changed\n")
    assert STEP.compute_status(PROBLEM_ID, outputs_dir)[0] == "stale"

    validator_path.unlink()
    assert STEP.compute_status(PROBLEM_ID, outputs_dir)[0] == "stale"
