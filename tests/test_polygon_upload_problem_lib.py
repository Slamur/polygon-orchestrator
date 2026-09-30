from pathlib import Path
from unittest.mock import MagicMock

import pytest

from orchestrator.polygon.state import PolygonState, load_polygon_state, save_polygon_state
from orchestrator.polygon.steps.base import PolygonStep, PolygonStepError, run_step
from orchestrator.polygon.steps.upload_problem_lib import STEP, STEP_NAME, make_step
from orchestrator.spec import load_spec

FIXTURES_DIR = Path(__file__).parent / "fixtures"
PROBLEM_ID = "example-problem"
POLYGON_ID = 4242
LIB_CONTENT = b"#pragma once\n// test stub\n"


@pytest.fixture
def spec():
    return load_spec(FIXTURES_DIR / "valid-spec.yaml")


@pytest.fixture
def outputs_dir(tmp_path):
    return tmp_path / "outputs"


@pytest.fixture
def templates_dir(tmp_path):
    path = tmp_path / "templates"
    path.mkdir()
    (path / "problem_lib.h").write_bytes(LIB_CONTENT)
    return path


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


def _run(spec, outputs_dir, templates_dir, client) -> str:
    return run_step(
        make_step(templates_dir), PROBLEM_ID, spec, outputs_dir=outputs_dir, client=client
    )


def test_step_is_a_polygon_step_with_expected_name():
    assert isinstance(STEP, PolygonStep)
    assert STEP.name == STEP_NAME == "upload_problem_lib"


def test_missing_polygon_state_points_to_create_problem(outputs_dir, templates_dir, spec):
    client = MagicMock()

    with pytest.raises(PolygonStepError, match="create_problem"):
        _run(spec, outputs_dir, templates_dir, client)

    client.call.assert_not_called()


def test_missing_problem_lib_in_templates_dir(tmp_path, outputs_dir, spec):
    _link(outputs_dir)
    empty_templates = tmp_path / "empty-templates"
    empty_templates.mkdir()
    client = MagicMock()

    with pytest.raises(PolygonStepError, match="problem_lib.h"):
        _run(spec, outputs_dir, empty_templates, client)

    client.call.assert_not_called()


def test_uploads_problem_lib_as_resource(outputs_dir, templates_dir, spec):
    _link(outputs_dir)
    client = MagicMock()

    message = _run(spec, outputs_dir, templates_dir, client)

    client.call.assert_called_once()
    method, params = client.call.call_args.args
    assert method == "problem.saveFile"
    assert params == {"problemId": str(POLYGON_ID), "type": "resource", "name": "problem_lib.h"}
    files = client.call.call_args.kwargs["files"]
    assert files["file"] == ("problem_lib.h", LIB_CONTENT)
    assert str(POLYGON_ID) in message


def test_reupload_on_every_run(outputs_dir, templates_dir, spec):
    _link(outputs_dir)
    client = MagicMock()

    _run(spec, outputs_dir, templates_dir, client)
    _run(spec, outputs_dir, templates_dir, client)

    assert client.call.call_count == 2


def test_upload_is_recorded_in_polygon_state(outputs_dir, templates_dir, spec):
    _link(outputs_dir)

    _run(spec, outputs_dir, templates_dir, MagicMock())

    record = load_polygon_state(PROBLEM_ID, outputs_dir=outputs_dir).steps[STEP_NAME]
    assert set(record) == {"sha256", "sent_at"}


def test_status_not_run_then_done_then_stale(outputs_dir, templates_dir, spec):
    step = make_step(templates_dir)
    _link(outputs_dir)
    assert step.compute_status(PROBLEM_ID, outputs_dir)[0] == "not run"

    _run(spec, outputs_dir, templates_dir, MagicMock())
    assert step.compute_status(PROBLEM_ID, outputs_dir)[0] == "done"

    (templates_dir / "problem_lib.h").write_bytes(LIB_CONTENT + b"// changed\n")
    assert step.compute_status(PROBLEM_ID, outputs_dir)[0] == "stale"

    (templates_dir / "problem_lib.h").unlink()
    assert step.compute_status(PROBLEM_ID, outputs_dir)[0] == "stale"
