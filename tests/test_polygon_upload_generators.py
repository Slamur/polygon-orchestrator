from pathlib import Path
from unittest.mock import MagicMock

import pytest

from orchestrator.polygon.client import PolygonApiError
from orchestrator.polygon.state import PolygonState, load_polygon_state, save_polygon_state
from orchestrator.polygon.steps.base import PolygonStep, PolygonStepError
from orchestrator.polygon.steps.upload_generators import STEP, STEP_NAME
from orchestrator.spec import load_spec

from polygon_sync_helpers import run_and_record

FIXTURES_DIR = Path(__file__).parent / "fixtures"
PROBLEM_ID = "example-problem"
POLYGON_ID = 4242
GENERATORS = {
    "gen_random.cpp": b"// random\nint main() {}\n",
    "gen_edge.cpp": b"// edge\nint main() {}\n",
    "gen_max.cpp": b"// max\nint main() {}\n",
}


@pytest.fixture
def spec():
    return load_spec(FIXTURES_DIR / "valid-spec.yaml")


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


def _generators_dir(outputs_dir: Path) -> Path:
    path = outputs_dir / PROBLEM_ID / "generators"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _write_generators(outputs_dir: Path, files: dict[str, bytes] = GENERATORS) -> Path:
    generators_dir = _generators_dir(outputs_dir)
    for name, content in files.items():
        (generators_dir / name).write_bytes(content)
    return generators_dir


def _run(spec, outputs_dir, client, *, incremental: bool = False) -> str:
    return run_and_record(
        STEP, PROBLEM_ID, spec, outputs_dir, client, incremental=incremental
    ).message


def test_step_is_a_polygon_step_with_expected_name():
    assert isinstance(STEP, PolygonStep)
    assert STEP.name == STEP_NAME == "upload_generators"


def test_missing_polygon_state_points_to_create_problem(outputs_dir, spec):
    _write_generators(outputs_dir)
    client = MagicMock()

    with pytest.raises(PolygonStepError, match="create_problem"):
        _run(spec, outputs_dir, client)

    client.call.assert_not_called()


def test_missing_generators_dir_points_to_generators_and_script(outputs_dir, spec):
    _link(outputs_dir)
    client = MagicMock()

    with pytest.raises(PolygonStepError, match="generators_and_script"):
        _run(spec, outputs_dir, client)

    client.call.assert_not_called()


def test_empty_generators_dir_is_an_error(outputs_dir, spec):
    _link(outputs_dir)
    _generators_dir(outputs_dir)
    client = MagicMock()

    with pytest.raises(PolygonStepError, match=r"\.cpp"):
        _run(spec, outputs_dir, client)

    client.call.assert_not_called()


def test_uploads_each_generator_in_alphabetical_order(outputs_dir, spec):
    _link(outputs_dir)
    _write_generators(outputs_dir)
    client = MagicMock()

    message = _run(spec, outputs_dir, client)

    expected_names = ["gen_edge.cpp", "gen_max.cpp", "gen_random.cpp"]
    assert [c.args for c in client.call.call_args_list] == [
        (
            "problem.saveFile",
            {
                "problemId": str(POLYGON_ID),
                "type": "source",
                "name": name,
                "file": GENERATORS[name],
            },
        )
        for name in expected_names
    ]
    assert "3 generator(s)" in message
    assert str(POLYGON_ID) in message


def test_non_cpp_files_are_not_uploaded(outputs_dir, spec):
    _link(outputs_dir)
    generators_dir = _write_generators(outputs_dir, {"gen_random.cpp": GENERATORS["gen_random.cpp"]})
    (generators_dir / "helpers.h").write_bytes(b"#pragma once\n")
    (generators_dir / "notes.txt").write_bytes(b"notes\n")
    client = MagicMock()

    _run(spec, outputs_dir, client)

    assert [c.args[1]["name"] for c in client.call.call_args_list] == ["gen_random.cpp"]


def test_only_non_cpp_files_is_an_error(outputs_dir, spec):
    _link(outputs_dir)
    (_generators_dir(outputs_dir) / "helpers.h").write_bytes(b"#pragma once\n")
    client = MagicMock()

    with pytest.raises(PolygonStepError):
        _run(spec, outputs_dir, client)

    client.call.assert_not_called()


def test_save_file_error_propagates_and_is_not_recorded(outputs_dir, spec):
    _link(outputs_dir)
    _write_generators(outputs_dir)
    client = MagicMock()
    client.call.side_effect = [None, PolygonApiError("problem.saveFile", "boom")]

    with pytest.raises(PolygonApiError):
        _run(spec, outputs_dir, client)

    assert client.call.call_count == 2
    assert load_polygon_state(PROBLEM_ID, outputs_dir=outputs_dir).synced == {}


def test_upload_is_recorded_in_polygon_state(outputs_dir, spec):
    _link(outputs_dir)
    _write_generators(outputs_dir)

    _run(spec, outputs_dir, MagicMock())

    synced = load_polygon_state(PROBLEM_ID, outputs_dir=outputs_dir).synced
    assert sorted(synced) == [
        "generators/gen_edge.cpp",
        "generators/gen_max.cpp",
        "generators/gen_random.cpp",
    ]


def test_incremental_run_uploads_only_new_and_changed_generators(outputs_dir, spec):
    _link(outputs_dir)
    generators_dir = _write_generators(outputs_dir)
    _run(spec, outputs_dir, MagicMock())

    client = MagicMock()
    assert _run(spec, outputs_dir, client, incremental=True) == "no changes since the last sync, skipped"
    client.call.assert_not_called()

    (generators_dir / "gen_max.cpp").write_bytes(b"// changed\n")
    (generators_dir / "gen_new.cpp").write_bytes(b"// new\n")
    message = _run(spec, outputs_dir, client, incremental=True)

    assert [c.args[1]["name"] for c in client.call.call_args_list] == ["gen_max.cpp", "gen_new.cpp"]
    assert "2 of 4 generator(s)" in message


def test_status_not_run_then_done_then_stale(outputs_dir, spec):
    _link(outputs_dir)
    generators_dir = _write_generators(outputs_dir)
    assert STEP.compute_status(PROBLEM_ID, outputs_dir)[0] == "not run"

    _run(spec, outputs_dir, MagicMock())
    assert STEP.compute_status(PROBLEM_ID, outputs_dir)[0] == "done"

    (generators_dir / "gen_max.cpp").write_bytes(b"// changed\n")
    assert STEP.compute_status(PROBLEM_ID, outputs_dir)[0] == "stale"


def test_status_stale_after_rename_or_removal(outputs_dir, spec):
    _link(outputs_dir)
    generators_dir = _write_generators(outputs_dir)
    _run(spec, outputs_dir, MagicMock())

    (generators_dir / "gen_max.cpp").rename(generators_dir / "gen_big.cpp")
    assert STEP.compute_status(PROBLEM_ID, outputs_dir)[0] == "stale"

    for path in generators_dir.iterdir():
        path.unlink()
    assert STEP.compute_status(PROBLEM_ID, outputs_dir)[0] == "stale"
