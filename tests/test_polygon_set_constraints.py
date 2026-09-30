from pathlib import Path
from unittest.mock import MagicMock

import pytest

from orchestrator.polygon.state import PolygonState, save_polygon_state
from orchestrator.polygon.steps.base import PolygonStep, PolygonStepError, run_step
from orchestrator.polygon.steps.set_constraints import STEP, STEP_NAME
from orchestrator.spec import load_spec

FIXTURES_DIR = Path(__file__).parent / "fixtures"
PROBLEM_ID = "example-problem"
POLYGON_ID = 4242


@pytest.fixture
def spec():
    return load_spec(FIXTURES_DIR / "valid-spec.yaml")


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


def _write_constraints(outputs_dir: Path, content: str) -> None:
    path = outputs_dir / PROBLEM_ID / "constraints.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _run(spec, outputs_dir, client) -> str:
    return run_step(STEP, PROBLEM_ID, spec, outputs_dir=outputs_dir, client=client)


def _assert_update_info_called(client) -> None:
    client.call.assert_called_once()
    method, params = client.call.call_args.args
    assert method == "problem.updateInfo"
    assert params["problemId"] == str(POLYGON_ID)
    assert params["timeLimit"] == "2000"
    assert params["memoryLimit"] == "256"
    assert params["interactive"] == "false"
    assert params["inputFile"] == ""
    assert params["outputFile"] == ""


def test_step_is_a_polygon_step_with_expected_name():
    assert isinstance(STEP, PolygonStep)
    assert STEP.name == STEP_NAME == "set_constraints"


def test_missing_polygon_state_points_to_create_problem(tmp_path, spec):
    _write_constraints(tmp_path, "limits:\n  time_limit_seconds: 2\n  memory_limit_mb: 256\n")
    client = MagicMock()

    with pytest.raises(PolygonStepError, match="create_problem"):
        _run(spec, tmp_path, client)

    client.call.assert_not_called()


def test_missing_constraints_yaml_points_to_constraints_pick(tmp_path, spec):
    _link(tmp_path)
    client = MagicMock()

    with pytest.raises(PolygonStepError, match="constraints_pick"):
        _run(spec, tmp_path, client)

    client.call.assert_not_called()


def test_limits_nested_under_limits(tmp_path, spec):
    _link(tmp_path)
    _write_constraints(tmp_path, "limits:\n  time_limit_seconds: 2\n  memory_limit_mb: 256\n")
    client = MagicMock()

    message = _run(spec, tmp_path, client)

    _assert_update_info_called(client)
    assert "2000ms" in message and "256MB" in message


def test_limits_at_root(tmp_path, spec):
    _link(tmp_path)
    _write_constraints(tmp_path, "time_limit_seconds: 2\nmemory_limit_mb: 256\n")
    client = MagicMock()

    _run(spec, tmp_path, client)

    _assert_update_info_called(client)


def test_conflicting_limits_in_both_places(tmp_path, spec):
    _link(tmp_path)
    _write_constraints(
        tmp_path,
        "time_limit_seconds: 1\nmemory_limit_mb: 256\n"
        "limits:\n  time_limit_seconds: 2\n  memory_limit_mb: 256\n",
    )
    client = MagicMock()

    with pytest.raises(PolygonStepError, match="разными значениями"):
        _run(spec, tmp_path, client)

    client.call.assert_not_called()


def test_identical_limits_in_both_places(tmp_path, spec):
    _link(tmp_path)
    _write_constraints(
        tmp_path,
        "time_limit_seconds: 2\nmemory_limit_mb: 256\n"
        "limits:\n  time_limit_seconds: 2\n  memory_limit_mb: 256\n",
    )
    client = MagicMock()

    _run(spec, tmp_path, client)

    _assert_update_info_called(client)


def test_null_time_limit(tmp_path, spec):
    _link(tmp_path)
    _write_constraints(tmp_path, "limits:\n  time_limit_seconds: null\n  memory_limit_mb: 256\n")
    client = MagicMock()

    with pytest.raises(PolygonStepError, match="time_limit_seconds"):
        _run(spec, tmp_path, client)

    client.call.assert_not_called()


def test_no_limits_anywhere(tmp_path, spec):
    _link(tmp_path)
    _write_constraints(tmp_path, "groups: []\n")
    client = MagicMock()

    with pytest.raises(PolygonStepError, match="не найдены"):
        _run(spec, tmp_path, client)

    client.call.assert_not_called()


@pytest.mark.parametrize("value", ["'2'", "true", "0", "-1"])
def test_non_positive_or_non_numeric_limit(tmp_path, spec, value):
    _link(tmp_path)
    _write_constraints(tmp_path, f"time_limit_seconds: {value}\nmemory_limit_mb: 256\n")
    client = MagicMock()

    with pytest.raises(PolygonStepError, match="положительным числом"):
        _run(spec, tmp_path, client)

    client.call.assert_not_called()


def test_fractional_time_limit_rounds_to_ms(tmp_path, spec):
    _link(tmp_path)
    # 2.01 * 1000 == 2009.9999999999998 — int() дал бы 2009
    _write_constraints(tmp_path, "time_limit_seconds: 2.01\nmemory_limit_mb: 256\n")
    client = MagicMock()

    _run(spec, tmp_path, client)

    assert client.call.call_args.args[1]["timeLimit"] == "2010"


def test_non_mapping_yaml(tmp_path, spec):
    _link(tmp_path)
    _write_constraints(tmp_path, "- just\n- a list\n")
    client = MagicMock()

    with pytest.raises(PolygonStepError, match="словарь"):
        _run(spec, tmp_path, client)

    client.call.assert_not_called()
