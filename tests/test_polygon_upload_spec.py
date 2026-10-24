from pathlib import Path
from unittest.mock import MagicMock

import pytest

from orchestrator.polygon.state import PolygonState, load_polygon_state, save_polygon_state
from orchestrator.polygon.steps.base import PolygonStep, PolygonStepError
from orchestrator.polygon.steps.upload_spec import STEP, STEP_NAME
from orchestrator.spec import load_spec

from polygon_sync_helpers import run_and_record

FIXTURES_DIR = Path(__file__).parent / "fixtures"
PROBLEM_ID = "example-problem"
POLYGON_ID = 4242


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


def _write_spec(problems_dir: Path) -> bytes:
    # Как есть, с комментариями: загружается файл, а не разобранный спек.
    content = b"# author's comment\n" + (FIXTURES_DIR / "valid-spec.yaml").read_bytes()
    path = problems_dir / PROBLEM_ID / "spec.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return content


def _run(spec, problems_dir, client, *, incremental: bool = False) -> str:
    return run_and_record(
        STEP, PROBLEM_ID, spec, problems_dir, client, incremental=incremental
    ).message


def test_step_is_a_polygon_step_with_expected_name():
    assert isinstance(STEP, PolygonStep)
    assert STEP.name == STEP_NAME == "upload_spec"


def test_missing_polygon_state_points_to_create_problem(problems_dir, spec):
    _write_spec(problems_dir)
    client = MagicMock()

    with pytest.raises(PolygonStepError, match="create_problem"):
        _run(spec, problems_dir, client)

    client.call.assert_not_called()


def test_missing_spec_file(problems_dir, spec):
    _link(problems_dir)
    client = MagicMock()

    with pytest.raises(PolygonStepError, match="spec.yaml"):
        _run(spec, problems_dir, client)

    client.call.assert_not_called()


def test_uploads_spec_file_as_is_as_attachment(problems_dir, spec):
    _link(problems_dir)
    content = _write_spec(problems_dir)
    client = MagicMock()

    message = _run(spec, problems_dir, client)

    client.call.assert_called_once()
    method, params = client.call.call_args.args
    assert method == "problem.saveFile"
    assert params == {
        "problemId": str(POLYGON_ID),
        "type": "aux",
        "name": "spec.yaml",
        "file": content,
    }
    assert str(POLYGON_ID) in message
    assert list(load_polygon_state(PROBLEM_ID, problems_dir=problems_dir).synced) == ["spec.yaml"]


def test_incremental_run_skips_unchanged_spec(problems_dir, spec):
    _link(problems_dir)
    _write_spec(problems_dir)
    _run(spec, problems_dir, MagicMock())
    client = MagicMock()

    message = _run(spec, problems_dir, client, incremental=True)

    assert message == "no changes since the last sync, skipped"
    client.call.assert_not_called()


def test_status_not_run_then_done_then_stale(problems_dir, spec):
    _link(problems_dir)
    content = _write_spec(problems_dir)
    assert STEP.compute_status(PROBLEM_ID, problems_dir)[0] == "not run"

    _run(spec, problems_dir, MagicMock())
    assert STEP.compute_status(PROBLEM_ID, problems_dir)[0] == "done"

    (problems_dir / PROBLEM_ID / "spec.yaml").write_bytes(content + b"# edited\n")
    assert STEP.compute_status(PROBLEM_ID, problems_dir)[0] == "stale"
