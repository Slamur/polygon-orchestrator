from pathlib import Path
from unittest.mock import MagicMock

import pytest
from polygon_sync_helpers import (
    FIXTURES_DIR,
    POLYGON_ID,
    PROBLEM_ID,
    link,
    run_and_record,
)

from orchestrator.polygon.state import load_polygon_state
from orchestrator.polygon.steps.base import PolygonStepError
from orchestrator.polygon.steps.set_constraints import STEP
from orchestrator.spec import load_spec


@pytest.fixture
def spec():
    return load_spec(FIXTURES_DIR / "valid-spec.yaml")


def _write_limits(problems_dir: Path, content: str) -> None:
    path = problems_dir / PROBLEM_ID / "limits.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _run(spec, problems_dir, client, *, incremental: bool = False) -> str:
    return run_and_record(
        STEP, PROBLEM_ID, spec, problems_dir, client, incremental=incremental
    ).message


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


def test_missing_polygon_state_points_to_create_problem(tmp_path, spec):
    _write_limits(tmp_path, "limits:\n  time_limit_seconds: 2\n  memory_limit_mb: 256\n")
    client = MagicMock()

    with pytest.raises(PolygonStepError, match="create_problem"):
        _run(spec, tmp_path, client)

    client.call.assert_not_called()


def test_missing_limits_yaml_points_to_limits_from_constraints(tmp_path, spec):
    link(tmp_path)
    client = MagicMock()

    with pytest.raises(PolygonStepError, match="limits_from_constraints"):
        _run(spec, tmp_path, client)

    client.call.assert_not_called()


def test_limits_nested_under_limits(tmp_path, spec):
    link(tmp_path)
    _write_limits(tmp_path, "limits:\n  time_limit_seconds: 2\n  memory_limit_mb: 256\n")
    client = MagicMock()

    message = _run(spec, tmp_path, client)

    _assert_update_info_called(client)
    assert "2000ms" in message and "256MB" in message


def test_unreadable_limits_are_a_step_error_before_network(tmp_path, spec):
    link(tmp_path)
    _write_limits(
        tmp_path,
        "time_limit_seconds: 1\nmemory_limit_mb: 256\n"
        "limits:\n  time_limit_seconds: 2\n  memory_limit_mb: 256\n",
    )
    client = MagicMock()

    with pytest.raises(PolygonStepError, match="different values"):
        _run(spec, tmp_path, client)

    client.call.assert_not_called()


# --- status ---


def _status(problems_dir: Path) -> tuple[str, str]:
    return STEP.compute_status(PROBLEM_ID, problems_dir)


def test_successful_run_records_sent_params(tmp_path, spec):
    link(tmp_path)
    _write_limits(tmp_path, "limits:\n  time_limit_seconds: 2\n  memory_limit_mb: 256\n")
    client = MagicMock()

    _run(spec, tmp_path, client)

    assert list(load_polygon_state(PROBLEM_ID, problems_dir=tmp_path).synced) == ["limits"]


def test_incremental_run_skips_unchanged_limits_and_sends_changed(tmp_path, spec):
    link(tmp_path)
    _write_limits(tmp_path, "limits:\n  time_limit_seconds: 2\n  memory_limit_mb: 256\n")
    _run(spec, tmp_path, MagicMock())
    client = MagicMock()

    # те же значения в другом месте файла — отправлять нечего
    _write_limits(tmp_path, "time_limit_seconds: 2\nmemory_limit_mb: 256\ngroups: []\n")
    assert _run(spec, tmp_path, client, incremental=True) == "no changes since the last sync, skipped"
    client.call.assert_not_called()

    _write_limits(tmp_path, "time_limit_seconds: 2\nmemory_limit_mb: 512\n")
    _run(spec, tmp_path, client, incremental=True)
    assert client.call.call_args.args[1]["memoryLimit"] == "512"


def test_failed_api_call_records_nothing(tmp_path, spec):
    link(tmp_path)
    _write_limits(tmp_path, "limits:\n  time_limit_seconds: 2\n  memory_limit_mb: 256\n")
    client = MagicMock()
    client.call.side_effect = RuntimeError("network down")

    with pytest.raises(RuntimeError):
        _run(spec, tmp_path, client)

    assert load_polygon_state(PROBLEM_ID, problems_dir=tmp_path).synced == {}
    assert _status(tmp_path)[0] == "not run"


def test_status_not_run_without_state(tmp_path):
    assert _status(tmp_path)[0] == "not run"


def test_status_not_run_when_linked_but_never_sent(tmp_path):
    link(tmp_path)
    _write_limits(tmp_path, "time_limit_seconds: 2\nmemory_limit_mb: 256\n")

    assert _status(tmp_path) == ("not run", "")


def test_status_done_after_run(tmp_path, spec):
    link(tmp_path)
    _write_limits(tmp_path, "limits:\n  time_limit_seconds: 2\n  memory_limit_mb: 256\n")
    _run(spec, tmp_path, MagicMock())

    assert _status(tmp_path)[0] == "done"


def test_status_done_when_limits_move_between_root_and_limits(tmp_path, spec):
    # те же значения, другое расположение — отправили бы то же самое
    link(tmp_path)
    _write_limits(tmp_path, "limits:\n  time_limit_seconds: 2\n  memory_limit_mb: 256\n")
    _run(spec, tmp_path, MagicMock())
    _write_limits(tmp_path, "time_limit_seconds: 2\nmemory_limit_mb: 256\ngroups: []\n")

    assert _status(tmp_path)[0] == "done"


def test_status_stale_when_limits_changed(tmp_path, spec):
    link(tmp_path)
    _write_limits(tmp_path, "limits:\n  time_limit_seconds: 2\n  memory_limit_mb: 256\n")
    _run(spec, tmp_path, MagicMock())
    _write_limits(tmp_path, "limits:\n  time_limit_seconds: 2\n  memory_limit_mb: 512\n")

    assert _status(tmp_path) == ("stale", "changed since the last sync: limits")


def test_status_stale_when_limits_yaml_removed(tmp_path, spec):
    link(tmp_path)
    _write_limits(tmp_path, "limits:\n  time_limit_seconds: 2\n  memory_limit_mb: 256\n")
    _run(spec, tmp_path, MagicMock())
    (tmp_path / PROBLEM_ID / "limits.yaml").unlink()

    status, detail = _status(tmp_path)

    assert status == "stale"
    assert "limits_from_constraints" in detail


@pytest.fixture
def interactive_spec():
    return load_spec(FIXTURES_DIR / "interactive-spec.yaml")


def test_interactive_spec_sets_interactive_flag(tmp_path, interactive_spec):
    link(tmp_path)
    _write_limits(tmp_path, "limits:\n  time_limit_seconds: 2\n  memory_limit_mb: 256\n")
    client = MagicMock()

    message = _run(interactive_spec, tmp_path, client)

    method, params = client.call.call_args.args
    assert method == "problem.updateInfo"
    assert params["interactive"] == "true"
    assert "interactive=true" in message
    assert "interactive" in load_polygon_state(PROBLEM_ID, problems_dir=tmp_path).synced


def test_plain_spec_does_not_record_interactive_flag(tmp_path, spec):
    # У обычных задач флага в базе нет — как у синхронизированных до его появления.
    link(tmp_path)
    _write_limits(tmp_path, "limits:\n  time_limit_seconds: 2\n  memory_limit_mb: 256\n")

    _run(spec, tmp_path, MagicMock())

    assert "interactive" not in load_polygon_state(PROBLEM_ID, problems_dir=tmp_path).synced


def test_switching_interactive_flag_reuploads_with_same_limits(tmp_path, spec, interactive_spec):
    link(tmp_path)
    _write_limits(tmp_path, "limits:\n  time_limit_seconds: 2\n  memory_limit_mb: 256\n")
    _run(spec, tmp_path, MagicMock())

    client = MagicMock()
    _run(interactive_spec, tmp_path, client, incremental=True)
    assert client.call.call_args.args[1]["interactive"] == "true"

    # И обратно: флаг теперь в базе, поэтому снятие тоже замечается.
    client = MagicMock()
    _run(spec, tmp_path, client, incremental=True)
    assert client.call.call_args.args[1]["interactive"] == "false"

    client = MagicMock()
    _run(spec, tmp_path, client, incremental=True)
    client.call.assert_not_called()
