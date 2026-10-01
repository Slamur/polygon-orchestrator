from pathlib import Path
from unittest.mock import MagicMock

import pytest

from orchestrator.polygon.client import PolygonApiError
from orchestrator.polygon.state import PolygonState, load_polygon_state, save_polygon_state
from orchestrator.polygon.steps.base import PolygonStep, PolygonStepError, run_step
from orchestrator.polygon.steps.upload_checker import STEP, STEP_NAME
from orchestrator.spec import load_spec

FIXTURES_DIR = Path(__file__).parent / "fixtures"
PROBLEM_ID = "example-problem"
POLYGON_ID = 4242
CHECKER_CONTENT = b'#include "testlib.h"\nint main(int argc, char* argv[]) { registerTestlibCmd(argc, argv); }\n'


def _spec(*, custom_needed: bool, standard: str | None):
    spec = load_spec(FIXTURES_DIR / "valid-spec.yaml")
    checker = spec.checker.model_copy(
        update={
            "custom_needed": custom_needed,
            "standard": standard,
            "custom_comparison_notes": "сравнение по правилу X" if custom_needed else None,
        }
    )
    return spec.model_copy(update={"checker": checker})


@pytest.fixture
def custom_spec():
    return _spec(custom_needed=True, standard=None)


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


def _write_checker(outputs_dir: Path, content: bytes = CHECKER_CONTENT) -> Path:
    path = outputs_dir / PROBLEM_ID / "checker.cpp"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


def _run(spec, outputs_dir, client) -> str:
    return run_step(STEP, PROBLEM_ID, spec, outputs_dir=outputs_dir, client=client)


def test_step_is_a_polygon_step_with_expected_name():
    assert isinstance(STEP, PolygonStep)
    assert STEP.name == STEP_NAME == "upload_checker"


@pytest.mark.parametrize(
    "spec",
    [
        # custom: checker.cpp на месте — упасть должно именно на состоянии
        _spec(custom_needed=True, standard=None),
        _spec(custom_needed=False, standard="ncmp"),
        # standard=None: состояние проверяется раньше дыры в спеке
        _spec(custom_needed=False, standard=None),
    ],
    ids=["custom", "standard", "standard-missing"],
)
def test_missing_polygon_state_points_to_create_problem(outputs_dir, spec):
    _write_checker(outputs_dir)
    client = MagicMock()

    with pytest.raises(PolygonStepError, match="create_problem"):
        _run(spec, outputs_dir, client)

    client.call.assert_not_called()


def test_custom_missing_checker_points_to_checker_draft(outputs_dir, custom_spec):
    _link(outputs_dir)
    client = MagicMock()

    with pytest.raises(PolygonStepError, match="checker_draft"):
        _run(custom_spec, outputs_dir, client)

    client.call.assert_not_called()


def test_custom_uploads_then_sets_checker(outputs_dir, custom_spec):
    _link(outputs_dir)
    _write_checker(outputs_dir)
    client = MagicMock()

    message = _run(custom_spec, outputs_dir, client)

    assert client.call.call_count == 2
    save_call, set_call = client.call.call_args_list
    assert save_call.args == (
        "problem.saveFile",
        {
            "problemId": str(POLYGON_ID),
            "type": "source",
            "name": "checker.cpp",
            "file": CHECKER_CONTENT,
        },
    )
    assert set_call.args == (
        "problem.setChecker",
        {"problemId": str(POLYGON_ID), "checker": "checker.cpp"},
    )
    assert str(POLYGON_ID) in message


@pytest.mark.parametrize(
    "standard, expected",
    [("std::wcmp.cpp", "std::wcmp.cpp"), ("ncmp", "std::ncmp.cpp")],
)
def test_standard_only_sets_checker_without_upload(outputs_dir, standard, expected):
    _link(outputs_dir)
    # даже если checker.cpp лежит на диске — при custom_needed=false не загружается
    _write_checker(outputs_dir)
    client = MagicMock()

    message = _run(_spec(custom_needed=False, standard=standard), outputs_dir, client)

    client.call.assert_called_once_with(
        "problem.setChecker", {"problemId": str(POLYGON_ID), "checker": expected}
    )
    assert "problem.saveFile" not in [c.args[0] for c in client.call.call_args_list]
    assert expected in message


@pytest.mark.parametrize("standard", [None, "   "])
def test_standard_missing_is_reported_as_spec_gap(outputs_dir, standard):
    _link(outputs_dir)
    client = MagicMock()

    with pytest.raises(PolygonStepError, match="checker.standard не задан"):
        _run(_spec(custom_needed=False, standard=standard), outputs_dir, client)

    client.call.assert_not_called()


def test_custom_save_file_error_propagates_and_skips_set_checker(outputs_dir, custom_spec):
    _link(outputs_dir)
    _write_checker(outputs_dir)
    client = MagicMock()
    client.call.side_effect = [PolygonApiError("problem.saveFile", "boom")]

    with pytest.raises(PolygonApiError):
        _run(custom_spec, outputs_dir, client)

    assert [c.args[0] for c in client.call.call_args_list] == ["problem.saveFile"]
    assert STEP_NAME not in load_polygon_state(PROBLEM_ID, outputs_dir=outputs_dir).steps


def test_custom_status_not_run_then_done_then_stale(outputs_dir, custom_spec):
    _link(outputs_dir)
    checker_path = _write_checker(outputs_dir)
    assert STEP.compute_status(PROBLEM_ID, outputs_dir)[0] == "not run"

    _run(custom_spec, outputs_dir, MagicMock())
    assert STEP.compute_status(PROBLEM_ID, outputs_dir)[0] == "done"

    checker_path.write_bytes(CHECKER_CONTENT + b"// changed\n")
    assert STEP.compute_status(PROBLEM_ID, outputs_dir)[0] == "stale"

    checker_path.unlink()
    assert STEP.compute_status(PROBLEM_ID, outputs_dir)[0] == "stale"


def test_standard_status_done_with_checker_name(outputs_dir):
    _link(outputs_dir)
    assert STEP.compute_status(PROBLEM_ID, outputs_dir)[0] == "not run"

    _run(_spec(custom_needed=False, standard="ncmp"), outputs_dir, MagicMock())

    status, detail = STEP.compute_status(PROBLEM_ID, outputs_dir)
    assert status == "done"
    assert "std::ncmp.cpp" in detail
