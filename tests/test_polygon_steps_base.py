import logging
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from orchestrator.polygon.client import PolygonApiError
from orchestrator.polygon.steps import base
from orchestrator.polygon.state import PolygonState, save_polygon_state
from orchestrator.polygon.steps.base import (
    PolygonStep,
    PolygonStepError,
    StepContext,
    StepResult,
    changed_items,
    require_polygon_state,
    run_step,
    sync_status,
)
from orchestrator.spec import load_spec

FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture
def spec():
    return load_spec(FIXTURES_DIR / "valid-spec.yaml")


@pytest.fixture
def no_default_client(monkeypatch):
    """Любая попытка создать PolygonClient по умолчанию — ошибка теста."""
    factory = MagicMock(side_effect=AssertionError("PolygonClient() не должен создаваться"))
    monkeypatch.setattr(base, "PolygonClient", factory)
    return factory


def _step(check_done_result, execute_result="did it") -> tuple[PolygonStep, MagicMock, MagicMock]:
    check_done = MagicMock(return_value=check_done_result)
    execute = MagicMock(return_value=StepResult(execute_result, {"k": "v"}))
    step = PolygonStep(
        name="fake",
        check_done=check_done,
        execute=execute,
        compute_status=MagicMock(return_value=("not run", "")),
    )
    return step, check_done, execute


def test_already_done_skips_execute_and_never_builds_client(tmp_path, spec, no_default_client):
    step, check_done, execute = _step("already done")

    result = run_step(step, "p1", spec, outputs_dir=tmp_path)

    assert result == StepResult("already done")
    execute.assert_not_called()
    no_default_client.assert_not_called()
    ctx = check_done.call_args.args[0]
    assert ctx == StepContext(problem_id="p1", spec=spec, outputs_dir=tmp_path)


def test_not_done_executes_with_given_client(tmp_path, spec):
    step, _, execute = _step(None, "did it")
    client = MagicMock()

    result = run_step(step, "p1", spec, outputs_dir=tmp_path, client=client)

    assert result == StepResult("did it", {"k": "v"})
    ctx, passed_client = execute.call_args.args
    assert ctx.problem_id == "p1"
    assert passed_client is client
    client.call.assert_not_called()


def test_not_done_builds_default_client_lazily(tmp_path, spec, monkeypatch):
    default_client = MagicMock()
    monkeypatch.setattr(base, "PolygonClient", MagicMock(return_value=default_client))
    step, _, execute = _step(None)

    run_step(step, "p1", spec, outputs_dir=tmp_path)

    assert execute.call_args.args[1] is default_client


def test_execute_error_propagates(tmp_path, spec):
    step, _, execute = _step(None)
    execute.side_effect = PolygonApiError("problem.create", "boom")

    with pytest.raises(PolygonApiError, match="boom"):
        run_step(step, "p1", spec, outputs_dir=tmp_path, client=MagicMock())


@pytest.mark.parametrize("check_done_result, expected", [("already done", "already done"), (None, "did it")])
def test_message_is_logged_with_step_name(tmp_path, spec, caplog, check_done_result, expected):
    step, _, _ = _step(check_done_result)

    with caplog.at_level(logging.INFO, logger="orchestrator.polygon.steps.base"):
        run_step(step, "p1", spec, outputs_dir=tmp_path, client=MagicMock())

    assert f"[fake] {expected}" in caplog.messages


def test_require_polygon_state_returns_saved_state(tmp_path, spec):
    saved = PolygonState(problem_id="p1", polygon_id=123, created_at="2026-09-19T12:00:00+00:00")
    save_polygon_state("p1", saved, outputs_dir=tmp_path)
    ctx = StepContext(problem_id="p1", spec=spec, outputs_dir=tmp_path)

    assert require_polygon_state(ctx) == saved


def test_require_polygon_state_points_to_create_problem(tmp_path, spec):
    ctx = StepContext(problem_id="p1", spec=spec, outputs_dir=tmp_path)

    with pytest.raises(PolygonStepError, match="create_problem"):
        require_polygon_state(ctx)


def test_synced_baseline_is_passed_to_execute(tmp_path, spec):
    step, _, execute = _step(None)

    run_step(step, "p1", spec, outputs_dir=tmp_path, client=MagicMock(), synced={"a": "1"})

    assert execute.call_args.args[0].synced == {"a": "1"}


def _ctx(spec, synced):
    return StepContext(problem_id="p1", spec=spec, outputs_dir=Path("."), synced=synced)


def test_changed_items_without_baseline_returns_everything(spec):
    items = {"a": "1", "b": "2"}

    assert changed_items(_ctx(spec, None), items) == items


def test_changed_items_returns_new_and_changed_only(spec):
    ctx = _ctx(spec, {"a": "1", "b": "old", "gone": "x"})

    assert changed_items(ctx, {"a": "1", "b": "2", "c": "3"}) == {"b": "2", "c": "3"}


def _save_synced(tmp_path, synced):
    save_polygon_state(
        "p1",
        PolygonState(
            problem_id="p1", polygon_id=1, created_at="x", revision=3, synced=synced
        ),
        outputs_dir=tmp_path,
    )


def test_sync_status_not_run_without_own_keys(tmp_path):
    _save_synced(tmp_path, {"other/x": "1"})

    status = sync_status("p1", tmp_path, key_prefix="gen/", read_items=lambda: {"gen/a": "1"})

    assert status == ("not run", "")


def test_sync_status_done_and_stale(tmp_path):
    _save_synced(tmp_path, {"gen/a": "1", "gen/b": "2"})

    assert sync_status("p1", tmp_path, key_prefix="gen/", read_items=lambda: {"gen/a": "1"})[0] == "done"
    assert sync_status(
        "p1", tmp_path, key_prefix="gen/", read_items=lambda: {"gen/a": "1", "gen/c": "3"}
    ) == ("stale", "changed since the last sync: gen/c")


def test_sync_status_unreadable_files(tmp_path):
    def unreadable():
        raise PolygonStepError("no files")

    assert sync_status("p1", tmp_path, key_prefix="gen/", read_items=unreadable)[0] == "not run"
    _save_synced(tmp_path, {"gen/a": "1"})
    assert sync_status("p1", tmp_path, key_prefix="gen/", read_items=unreadable)[0] == "stale"
