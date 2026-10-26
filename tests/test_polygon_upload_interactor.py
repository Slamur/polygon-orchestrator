from pathlib import Path
from unittest.mock import MagicMock

import pytest

from orchestrator.polygon.state import PolygonState, load_polygon_state, save_polygon_state
from orchestrator.polygon.steps.base import PolygonStep, PolygonStepError
from orchestrator.polygon.steps.upload_interactor import (
    NOT_INTERACTIVE_MESSAGE,
    STEP,
    STEP_NAME,
)
from orchestrator.spec import load_spec

from polygon_sync_helpers import run_and_record

FIXTURES_DIR = Path(__file__).parent / "fixtures"
PROBLEM_ID = "example-problem"
POLYGON_ID = 4242
INTERACTOR_CONTENT = (
    b'#include "testlib.h"\nint main(int argc, char* argv[]) { registerInteraction(argc, argv); }\n'
)


@pytest.fixture
def spec():
    return load_spec(FIXTURES_DIR / "interactive-spec.yaml")


@pytest.fixture
def plain_spec():
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


def _write_interactor(problems_dir: Path, content: bytes = INTERACTOR_CONTENT) -> None:
    path = problems_dir / PROBLEM_ID / "interactor.cpp"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)


def _run(spec, problems_dir, client, *, incremental: bool = False) -> str:
    return run_and_record(
        STEP, PROBLEM_ID, spec, problems_dir, client, incremental=incremental
    ).message


def test_step_is_a_polygon_step_with_expected_name():
    assert isinstance(STEP, PolygonStep)
    assert STEP.name == STEP_NAME == "upload_interactor"


def test_plain_problem_is_skipped_without_network(problems_dir, plain_spec):
    client = MagicMock()

    assert _run(plain_spec, problems_dir, client) == NOT_INTERACTIVE_MESSAGE
    client.call.assert_not_called()


def test_uploads_and_sets_interactor(problems_dir, spec):
    _link(problems_dir)
    _write_interactor(problems_dir)
    client = MagicMock()

    _run(spec, problems_dir, client)

    assert [c.args[0] for c in client.call.call_args_list] == [
        "problem.saveFile",
        "problem.setInteractor",
    ]
    save_params = client.call.call_args_list[0].args[1]
    assert save_params["type"] == "source"
    assert save_params["name"] == "interactor.cpp"
    assert save_params["file"] == INTERACTOR_CONTENT
    assert client.call.call_args_list[1].args[1] == {
        "problemId": str(POLYGON_ID),
        "interactor": "interactor.cpp",
    }
    synced = load_polygon_state(PROBLEM_ID, problems_dir=problems_dir).synced
    assert {"interactor.cpp", "interactor"} <= set(synced)


def test_unchanged_interactor_is_not_reuploaded(problems_dir, spec):
    _link(problems_dir)
    _write_interactor(problems_dir)
    _run(spec, problems_dir, MagicMock())
    client = MagicMock()

    _run(spec, problems_dir, client, incremental=True)

    client.call.assert_not_called()


def test_missing_interactor_points_to_interactor_draft(problems_dir, spec):
    _link(problems_dir)
    client = MagicMock()

    with pytest.raises(PolygonStepError, match="interactor_draft"):
        _run(spec, problems_dir, client)

    client.call.assert_not_called()


def test_status(problems_dir, spec):
    assert STEP.compute_status(PROBLEM_ID, problems_dir)[0] == "done"  # не интерактивная
    _link(problems_dir)
    _write_interactor(problems_dir)
    assert STEP.compute_status(PROBLEM_ID, problems_dir)[0] == "not run"
    _run(spec, problems_dir, MagicMock())
    assert STEP.compute_status(PROBLEM_ID, problems_dir)[0] == "done"
    _write_interactor(problems_dir, INTERACTOR_CONTENT + b"// edit\n")
    assert STEP.compute_status(PROBLEM_ID, problems_dir)[0] == "stale"
