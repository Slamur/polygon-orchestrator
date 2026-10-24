from pathlib import Path
from unittest.mock import MagicMock

import pytest

from orchestrator.polygon.client import PolygonApiError
from orchestrator.polygon.state import (
    PolygonState,
    load_polygon_state,
    save_polygon_state,
    update_sync,
)
from orchestrator.polygon.steps.base import PolygonStep, PolygonStepError
from orchestrator.polygon.steps.upload_validator import STEP, STEP_NAME
from orchestrator.spec import load_spec

from polygon_sync_helpers import run_and_record

FIXTURES_DIR = Path(__file__).parent / "fixtures"
PROBLEM_ID = "example-problem"
POLYGON_ID = 4242
VALIDATOR_CONTENT = b'#include "testlib.h"\nint main() { registerValidation(); }\n'


@pytest.fixture
def spec():
    return load_spec(FIXTURES_DIR / "valid-spec.yaml")


@pytest.fixture
def problems_dir(tmp_path):
    return tmp_path / "problems"


def _link(problems_dir: Path) -> None:
    save_polygon_state(
        PROBLEM_ID,
        PolygonState(
            problem_id=PROBLEM_ID,
            polygon_id=POLYGON_ID,
            created_at="2026-09-19T12:00:00+00:00",
        ),
        problems_dir=problems_dir,
    )


def _write_validator(problems_dir: Path, content: bytes = VALIDATOR_CONTENT) -> Path:
    path = problems_dir / PROBLEM_ID / "validator.cpp"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


def _run(spec, problems_dir, client, *, incremental: bool = False) -> str:
    return run_and_record(
        STEP, PROBLEM_ID, spec, problems_dir, client, incremental=incremental
    ).message


def test_step_is_a_polygon_step_with_expected_name():
    assert isinstance(STEP, PolygonStep)
    assert STEP.name == STEP_NAME == "upload_validator"


def test_missing_polygon_state_points_to_create_problem(problems_dir, spec):
    _write_validator(problems_dir)
    client = MagicMock()

    with pytest.raises(PolygonStepError, match="create_problem"):
        _run(spec, problems_dir, client)

    client.call.assert_not_called()


def test_missing_validator_points_to_constraints_pick(problems_dir, spec):
    _link(problems_dir)
    client = MagicMock()

    with pytest.raises(PolygonStepError, match="constraints_pick"):
        _run(spec, problems_dir, client)

    client.call.assert_not_called()


def test_uploads_then_sets_validator(problems_dir, spec):
    _link(problems_dir)
    _write_validator(problems_dir)
    client = MagicMock()

    message = _run(spec, problems_dir, client)

    assert client.call.call_count == 2
    save_call, set_call = client.call.call_args_list
    assert save_call.args == (
        "problem.saveFile",
        {
            "problemId": str(POLYGON_ID),
            "type": "source",
            "name": "validator.cpp",
            "file": VALIDATOR_CONTENT,
        },
    )
    assert set_call.args == (
        "problem.setValidator",
        {"problemId": str(POLYGON_ID), "validator": "validator.cpp"},
    )
    assert str(POLYGON_ID) in message


def test_save_file_error_propagates_and_skips_set_validator(problems_dir, spec):
    _link(problems_dir)
    _write_validator(problems_dir)
    client = MagicMock()
    client.call.side_effect = [PolygonApiError("problem.saveFile", "boom")]

    with pytest.raises(PolygonApiError):
        _run(spec, problems_dir, client)

    assert [c.args[0] for c in client.call.call_args_list] == ["problem.saveFile"]
    assert load_polygon_state(PROBLEM_ID, problems_dir=problems_dir).synced == {}


def test_upload_is_recorded_in_polygon_state(problems_dir, spec):
    _link(problems_dir)
    _write_validator(problems_dir)

    _run(spec, problems_dir, MagicMock())

    assert sorted(load_polygon_state(PROBLEM_ID, problems_dir=problems_dir).synced) == [
        "validator",
        "validator.cpp",
    ]


def test_incremental_run_skips_unchanged_validator(problems_dir, spec):
    _link(problems_dir)
    _write_validator(problems_dir)
    _run(spec, problems_dir, MagicMock())
    client = MagicMock()

    assert _run(spec, problems_dir, client, incremental=True) == "no changes since the last sync, skipped"
    client.call.assert_not_called()


def test_other_assigned_validator_is_reuploaded_and_set(problems_dir, spec):
    # после pull назначенным может быть файл с другим именем
    _link(problems_dir)
    _write_validator(problems_dir)
    _run(spec, problems_dir, MagicMock())
    update_sync(PROBLEM_ID, synced={"validator": "other"}, revision=1, problems_dir=problems_dir)
    client = MagicMock()

    _run(spec, problems_dir, client, incremental=True)

    assert [c.args[0] for c in client.call.call_args_list] == [
        "problem.saveFile",
        "problem.setValidator",
    ]


def test_status_not_run_then_done_then_stale(problems_dir, spec):
    _link(problems_dir)
    validator_path = _write_validator(problems_dir)
    assert STEP.compute_status(PROBLEM_ID, problems_dir)[0] == "not run"

    _run(spec, problems_dir, MagicMock())
    assert STEP.compute_status(PROBLEM_ID, problems_dir)[0] == "done"

    validator_path.write_bytes(VALIDATOR_CONTENT + b"// changed\n")
    assert STEP.compute_status(PROBLEM_ID, problems_dir)[0] == "stale"

    validator_path.unlink()
    assert STEP.compute_status(PROBLEM_ID, problems_dir)[0] == "stale"
