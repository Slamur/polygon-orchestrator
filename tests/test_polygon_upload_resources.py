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
from orchestrator.polygon.steps.upload_resources import make_step
from orchestrator.spec import load_spec

LIB_CONTENT = b"#pragma once\n// test stub\n"


@pytest.fixture
def spec():
    return load_spec(FIXTURES_DIR / "valid-spec.yaml")


@pytest.fixture
def templates_dir(tmp_path):
    path = tmp_path / "templates"
    (path).mkdir(parents=True)
    (path / "problem_lib.h").write_bytes(LIB_CONTENT)
    return path


def _run(spec, problems_dir, templates_dir, client, *, incremental: bool = False) -> str:
    return run_and_record(
        make_step(templates_dir), PROBLEM_ID, spec, problems_dir, client, incremental=incremental
    ).message


def test_missing_polygon_state_points_to_create_problem(problems_dir, templates_dir, spec):
    client = MagicMock()

    with pytest.raises(PolygonStepError, match="create_problem"):
        _run(spec, problems_dir, templates_dir, client)

    client.call.assert_not_called()


def test_missing_template_and_problem_copy(tmp_path, problems_dir, spec):
    link(problems_dir)
    empty_templates = tmp_path / "empty-templates"
    empty_templates.mkdir()
    client = MagicMock()

    with pytest.raises(PolygonStepError, match="problem_lib.h"):
        _run(spec, problems_dir, empty_templates, client)

    client.call.assert_not_called()


def test_seeds_problem_copy_from_template_and_uploads_it(problems_dir, templates_dir, spec):
    link(problems_dir)
    client = MagicMock()

    message = _run(spec, problems_dir, templates_dir, client)

    assert (problems_dir / PROBLEM_ID / "resources" / "problem_lib.h").read_bytes() == LIB_CONTENT

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


def test_problem_copy_wins_over_template(problems_dir, templates_dir, spec):
    link(problems_dir)
    copy = problems_dir / PROBLEM_ID / "resources" / "problem_lib.h"
    copy.parent.mkdir()
    copy.write_bytes(b"// edited\n")
    client = MagicMock()

    _run(spec, problems_dir, templates_dir, client)

    assert client.call.call_args.args[1]["file"] == b"// edited\n"
    assert (templates_dir / "problem_lib.h").read_bytes() == LIB_CONTENT


def test_template_change_does_not_touch_existing_copy(problems_dir, templates_dir, spec):
    link(problems_dir)
    _run(spec, problems_dir, templates_dir, MagicMock())
    (templates_dir / "problem_lib.h").write_bytes(b"// new template\n")
    client = MagicMock()

    message = _run(spec, problems_dir, templates_dir, client, incremental=True)

    assert message == "no changes since the last sync, skipped"
    client.call.assert_not_called()


def test_reupload_on_every_run(problems_dir, templates_dir, spec):
    link(problems_dir)
    client = MagicMock()

    _run(spec, problems_dir, templates_dir, client)
    _run(spec, problems_dir, templates_dir, client)

    assert client.call.call_count == 2


def test_upload_is_recorded_in_polygon_state(problems_dir, templates_dir, spec):
    link(problems_dir)

    _run(spec, problems_dir, templates_dir, MagicMock())

    assert list(load_polygon_state(PROBLEM_ID, problems_dir=problems_dir).synced) == [
        "problem_lib.h"
    ]


def test_incremental_run_skips_unchanged_copy(problems_dir, templates_dir, spec):
    link(problems_dir)
    _run(spec, problems_dir, templates_dir, MagicMock())
    client = MagicMock()

    message = _run(spec, problems_dir, templates_dir, client, incremental=True)

    assert message == "no changes since the last sync, skipped"
    client.call.assert_not_called()


def test_status_not_run_then_done_then_stale(problems_dir, templates_dir, spec):
    step = make_step(templates_dir)
    link(problems_dir)
    assert step.compute_status(PROBLEM_ID, problems_dir)[0] == "not run"

    _run(spec, problems_dir, templates_dir, MagicMock())
    assert step.compute_status(PROBLEM_ID, problems_dir)[0] == "done"

    # the template after copying has no effect
    (templates_dir / "problem_lib.h").write_bytes(b"// new template\n")
    assert step.compute_status(PROBLEM_ID, problems_dir)[0] == "done"

    copy = problems_dir / PROBLEM_ID / "resources" / "problem_lib.h"
    copy.write_bytes(LIB_CONTENT + b"// changed\n")
    assert step.compute_status(PROBLEM_ID, problems_dir)[0] == "stale"

    # no copy — the template push will copy is compared
    copy.unlink()
    assert step.compute_status(PROBLEM_ID, problems_dir)[0] == "stale"
    (templates_dir / "problem_lib.h").write_bytes(LIB_CONTENT)
    assert step.compute_status(PROBLEM_ID, problems_dir)[0] == "done"


# --- Resources other than problem_lib.h ---


def _own(problems_dir, name: str, content: bytes):
    path = problems_dir / PROBLEM_ID / "resources" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


def _uploaded_names(client) -> list[str]:
    return [call.args[1]["name"] for call in client.call.call_args_list]


def test_defaults_are_not_uploaded(problems_dir, templates_dir, spec):
    link(problems_dir)
    client = MagicMock()

    _run(spec, problems_dir, templates_dir, client)

    assert _uploaded_names(client) == ["problem_lib.h"]


def test_own_resource_is_uploaded(problems_dir, templates_dir, spec):
    link(problems_dir)
    _own(problems_dir, "testlib.h", b"// own testlib\n")
    _own(problems_dir, "old.h.removed", b"// removed\n")
    client = MagicMock()

    message = _run(spec, problems_dir, templates_dir, client)

    assert _uploaded_names(client) == ["problem_lib.h", "testlib.h"]
    assert client.call.call_args_list[1].args[1]["file"] == b"// own testlib\n"
    assert "testlib.h" in message
    assert set(load_polygon_state(PROBLEM_ID, problems_dir=problems_dir).synced) == {
        "problem_lib.h",
        "resources/testlib.h",
    }


def test_only_changed_resources_are_uploaded(problems_dir, templates_dir, spec):
    link(problems_dir)
    path = _own(problems_dir, "testlib.h", b"// own testlib\n")
    _run(spec, problems_dir, templates_dir, MagicMock())
    path.write_bytes(b"// edited testlib\n")
    client = MagicMock()

    _run(spec, problems_dir, templates_dir, client, incremental=True)

    assert _uploaded_names(client) == ["testlib.h"]


def test_status_sees_own_resources(problems_dir, templates_dir, spec):
    step = make_step(templates_dir)
    link(problems_dir)
    path = _own(problems_dir, "testlib.h", b"// own testlib\n")
    _run(spec, problems_dir, templates_dir, MagicMock())
    assert step.compute_status(PROBLEM_ID, problems_dir)[0] == "done"

    path.write_bytes(b"// edited testlib\n")
    status, detail = step.compute_status(PROBLEM_ID, problems_dir)
    assert status == "stale"
    assert "resources/testlib.h" in detail
