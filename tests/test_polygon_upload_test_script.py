from pathlib import Path
from unittest.mock import MagicMock

import pytest

from orchestrator.polygon.client import PolygonApiError
from orchestrator.polygon.state import PolygonState, load_polygon_state, save_polygon_state
from orchestrator.polygon.steps.base import PolygonStep, PolygonStepError
from orchestrator.polygon.steps.upload_test_script import STEP, STEP_NAME
from orchestrator.spec import load_spec

from polygon_sync_helpers import run_and_record

FIXTURES_DIR = Path(__file__).parent / "fixtures"
PROBLEM_ID = "example-problem"
POLYGON_ID = 4242
SCRIPT = "gen_random 1 10 > $\ngen_random 2 100 > $\n"


@pytest.fixture
def groups_spec():
    spec = load_spec(FIXTURES_DIR / "valid-spec.yaml")
    assert spec.generation.script_style == "groups"
    return spec


@pytest.fixture
def spec(groups_spec):
    return groups_spec.model_copy(
        update={"generation": groups_spec.generation.model_copy(update={"script_style": "flat"})}
    )


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


def _write_script(problems_dir: Path, content: str = SCRIPT, name: str = "test_script") -> Path:
    path = problems_dir / PROBLEM_ID / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def _run(spec, problems_dir, client, *, incremental: bool = False) -> str:
    return run_and_record(
        STEP, PROBLEM_ID, spec, problems_dir, client, incremental=incremental
    ).message


def test_step_is_a_polygon_step_with_expected_name():
    assert isinstance(STEP, PolygonStep)
    assert STEP.name == STEP_NAME == "upload_test_script"


def test_groups_style_rejected_before_network(problems_dir, groups_spec):
    # всё остальное на месте — отказ только из-за groups
    _link(problems_dir)
    _write_script(problems_dir, name="test_script_groups")
    client = MagicMock()

    with pytest.raises(PolygonStepError, match="groups"):
        _run(groups_spec, problems_dir, client)

    client.call.assert_not_called()
    assert load_polygon_state(PROBLEM_ID, problems_dir=problems_dir).synced == {}


def test_missing_polygon_state_points_to_create_problem(problems_dir, spec):
    _write_script(problems_dir)
    client = MagicMock()

    with pytest.raises(PolygonStepError, match="create_problem"):
        _run(spec, problems_dir, client)

    client.call.assert_not_called()


def test_missing_script_points_to_generators_and_script(problems_dir, spec):
    _link(problems_dir)
    client = MagicMock()

    with pytest.raises(PolygonStepError, match="generators_and_script"):
        _run(spec, problems_dir, client)

    client.call.assert_not_called()


def test_uploads_script_via_save_script(problems_dir, spec):
    _link(problems_dir)
    _write_script(problems_dir)
    client = MagicMock()

    message = _run(spec, problems_dir, client)

    client.call.assert_called_once_with(
        "problem.saveScript",
        {"problemId": str(POLYGON_ID), "testset": "tests", "source": SCRIPT},
    )
    assert "test_script" in message and str(POLYGON_ID) in message


def test_status_not_run_before_upload(problems_dir, spec):
    _link(problems_dir)
    _write_script(problems_dir)

    assert STEP.compute_status(PROBLEM_ID, problems_dir) == ("not run", "")


def test_incremental_run_skips_unchanged_script(problems_dir, spec):
    _link(problems_dir)
    _write_script(problems_dir)
    _run(spec, problems_dir, MagicMock())
    client = MagicMock()

    assert _run(spec, problems_dir, client, incremental=True) == "no changes since the last sync, skipped"
    client.call.assert_not_called()


def test_status_done_then_stale_after_script_change(problems_dir, spec):
    _link(problems_dir)
    path = _write_script(problems_dir)
    _run(spec, problems_dir, MagicMock())

    assert STEP.compute_status(PROBLEM_ID, problems_dir)[0] == "done"

    path.write_text(SCRIPT + "gen_random 3 1000 > $\n", encoding="utf-8")
    status, detail = STEP.compute_status(PROBLEM_ID, problems_dir)
    assert status == "stale"
    assert "test_script" in detail


def test_status_stale_when_script_removed(problems_dir, spec):
    _link(problems_dir)
    path = _write_script(problems_dir)
    _run(spec, problems_dir, MagicMock())

    path.unlink()

    assert STEP.compute_status(PROBLEM_ID, problems_dir)[0] == "stale"


def test_api_error_does_not_record_step(problems_dir, spec):
    _link(problems_dir)
    _write_script(problems_dir)
    client = MagicMock()
    client.call.side_effect = PolygonApiError("problem.saveScript", "boom")

    with pytest.raises(PolygonApiError):
        _run(spec, problems_dir, client)

    assert STEP.compute_status(PROBLEM_ID, problems_dir) == ("not run", "")
