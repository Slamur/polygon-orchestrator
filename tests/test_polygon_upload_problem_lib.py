from pathlib import Path
from unittest.mock import MagicMock

import pytest

from orchestrator.polygon.state import PolygonState, load_polygon_state, save_polygon_state
from orchestrator.polygon.steps.base import PolygonStep, PolygonStepError
from orchestrator.polygon.steps.upload_problem_lib import STEP, STEP_NAME, make_step
from orchestrator.spec import load_spec

from polygon_sync_helpers import run_and_record

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
    (path).mkdir(parents=True)
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


def _run(spec, outputs_dir, templates_dir, client, *, incremental: bool = False) -> str:
    return run_and_record(
        make_step(templates_dir), PROBLEM_ID, spec, outputs_dir, client, incremental=incremental
    ).message


def test_step_is_a_polygon_step_with_expected_name():
    assert isinstance(STEP, PolygonStep)
    assert STEP.name == STEP_NAME == "upload_problem_lib"


def test_missing_polygon_state_points_to_create_problem(outputs_dir, templates_dir, spec):
    client = MagicMock()

    with pytest.raises(PolygonStepError, match="create_problem"):
        _run(spec, outputs_dir, templates_dir, client)

    client.call.assert_not_called()


def test_missing_template_and_problem_copy(tmp_path, outputs_dir, spec):
    _link(outputs_dir)
    empty_templates = tmp_path / "empty-templates"
    empty_templates.mkdir()
    client = MagicMock()

    with pytest.raises(PolygonStepError, match="problem_lib.h"):
        _run(spec, outputs_dir, empty_templates, client)

    client.call.assert_not_called()


def test_seeds_problem_copy_from_template_and_uploads_it(outputs_dir, templates_dir, spec):
    _link(outputs_dir)
    client = MagicMock()

    message = _run(spec, outputs_dir, templates_dir, client)

    assert (outputs_dir / PROBLEM_ID / "problem_lib.h").read_bytes() == LIB_CONTENT

    client.call.assert_called_once()
    method, params = client.call.call_args.args
    assert method == "problem.saveFile"
    assert params == {
        "problemId": str(POLYGON_ID),
        "type": "resource",
        "name": "problem_lib.h",
        "file": LIB_CONTENT,
    }
    assert str(POLYGON_ID) in message


def test_problem_copy_wins_over_template(outputs_dir, templates_dir, spec):
    _link(outputs_dir)
    (outputs_dir / PROBLEM_ID / "problem_lib.h").write_bytes(b"// edited\n")
    client = MagicMock()

    _run(spec, outputs_dir, templates_dir, client)

    assert client.call.call_args.args[1]["file"] == b"// edited\n"
    assert (templates_dir / "problem_lib.h").read_bytes() == LIB_CONTENT


def test_template_change_does_not_touch_existing_copy(outputs_dir, templates_dir, spec):
    _link(outputs_dir)
    _run(spec, outputs_dir, templates_dir, MagicMock())
    (templates_dir / "problem_lib.h").write_bytes(b"// new template\n")
    client = MagicMock()

    message = _run(spec, outputs_dir, templates_dir, client, incremental=True)

    assert message == "no changes since the last sync, skipped"
    client.call.assert_not_called()


def test_reupload_on_every_run(outputs_dir, templates_dir, spec):
    _link(outputs_dir)
    client = MagicMock()

    _run(spec, outputs_dir, templates_dir, client)
    _run(spec, outputs_dir, templates_dir, client)

    assert client.call.call_count == 2


def test_upload_is_recorded_in_polygon_state(outputs_dir, templates_dir, spec):
    _link(outputs_dir)

    _run(spec, outputs_dir, templates_dir, MagicMock())

    assert list(load_polygon_state(PROBLEM_ID, outputs_dir=outputs_dir).synced) == ["problem_lib.h"]


def test_incremental_run_skips_unchanged_copy(outputs_dir, templates_dir, spec):
    _link(outputs_dir)
    _run(spec, outputs_dir, templates_dir, MagicMock())
    client = MagicMock()

    message = _run(spec, outputs_dir, templates_dir, client, incremental=True)

    assert message == "no changes since the last sync, skipped"
    client.call.assert_not_called()


def test_status_not_run_then_done_then_stale(outputs_dir, templates_dir, spec):
    step = make_step(templates_dir)
    _link(outputs_dir)
    assert step.compute_status(PROBLEM_ID, outputs_dir)[0] == "not run"

    _run(spec, outputs_dir, templates_dir, MagicMock())
    assert step.compute_status(PROBLEM_ID, outputs_dir)[0] == "done"

    # шаблон после копирования не влияет
    (templates_dir / "problem_lib.h").write_bytes(b"// new template\n")
    assert step.compute_status(PROBLEM_ID, outputs_dir)[0] == "done"

    copy = outputs_dir / PROBLEM_ID / "problem_lib.h"
    copy.write_bytes(LIB_CONTENT + b"// changed\n")
    assert step.compute_status(PROBLEM_ID, outputs_dir)[0] == "stale"

    # копии нет — сравнивается шаблон, который push скопирует
    copy.unlink()
    assert step.compute_status(PROBLEM_ID, outputs_dir)[0] == "stale"
    (templates_dir / "problem_lib.h").write_bytes(LIB_CONTENT)
    assert step.compute_status(PROBLEM_ID, outputs_dir)[0] == "done"
