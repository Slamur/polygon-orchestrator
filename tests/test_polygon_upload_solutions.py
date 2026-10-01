import logging
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from orchestrator.polygon.client import PolygonApiError
from orchestrator.polygon.state import PolygonState, load_polygon_state, save_polygon_state
from orchestrator.polygon.steps.base import PolygonStep, PolygonStepError, run_step
from orchestrator.polygon.steps.upload_solutions import STEP, STEP_NAME
from orchestrator.spec import load_spec

FIXTURES_DIR = Path(__file__).parent / "fixtures"
PROBLEM_ID = "example-problem"
POLYGON_ID = 4242
SOLUTIONS = {
    "ok_py_main_draft.py": b"print(1)\n",
    "ok_cpp_alt_draft.cpp": b"int main() {}\n",
    "tl_py_slow_draft.py": b"print(2)\n",
    "tl_cpp_bad_draft.cpp": b"int main() { for(;;); }\n",
    "wa_cpp_offbyone_draft.cpp": b"int main() { return 0; }\n",
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


def _solutions_dir(outputs_dir: Path) -> Path:
    path = outputs_dir / PROBLEM_ID / "solutions"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _write_solutions(outputs_dir: Path, files: dict[str, bytes] = SOLUTIONS) -> Path:
    solutions_dir = _solutions_dir(outputs_dir)
    for name, content in files.items():
        (solutions_dir / name).write_bytes(content)
    return solutions_dir


def _run(spec, outputs_dir, client) -> str:
    return run_step(STEP, PROBLEM_ID, spec, outputs_dir=outputs_dir, client=client)


def _tags(client: MagicMock) -> dict[str, str]:
    return {c.args[1]["name"]: c.args[1]["tag"] for c in client.call.call_args_list}


def test_step_is_a_polygon_step_with_expected_name():
    assert isinstance(STEP, PolygonStep)
    assert STEP.name == STEP_NAME == "upload_solutions"


def test_missing_polygon_state_points_to_create_problem(outputs_dir, spec):
    _write_solutions(outputs_dir)
    client = MagicMock()

    with pytest.raises(PolygonStepError, match="create_problem"):
        _run(spec, outputs_dir, client)

    client.call.assert_not_called()


def test_missing_solutions_dir_points_to_solutions_draft(outputs_dir, spec):
    _link(outputs_dir)
    client = MagicMock()

    with pytest.raises(PolygonStepError, match="solutions_draft"):
        _run(spec, outputs_dir, client)

    client.call.assert_not_called()


def test_empty_solutions_dir_is_an_error(outputs_dir, spec):
    _link(outputs_dir)
    _solutions_dir(outputs_dir)
    client = MagicMock()

    with pytest.raises(PolygonStepError, match="no files in"):
        _run(spec, outputs_dir, client)

    client.call.assert_not_called()


def test_filename_without_scheme_is_an_error(outputs_dir, spec):
    _link(outputs_dir)
    _write_solutions(outputs_dir, {"ok_cpp_main_draft.cpp": b"", "solution.cpp": b""})
    client = MagicMock()

    with pytest.raises(PolygonStepError, match="<verdict>_<language>_<description>_<author>"):
        _run(spec, outputs_dir, client)

    client.call.assert_not_called()


def test_unknown_verdict_is_an_error(outputs_dir, spec):
    _link(outputs_dir)
    _write_solutions(outputs_dir, {"ok_cpp_main_draft.cpp": b"", "xx_cpp_foo_bar.cpp": b""})
    client = MagicMock()

    with pytest.raises(PolygonStepError, match=r"'xx'.*allowed set"):
        _run(spec, outputs_dir, client)

    client.call.assert_not_called()


def test_uploads_each_solution_with_its_tag(outputs_dir, spec):
    _link(outputs_dir)
    _write_solutions(outputs_dir)
    client = MagicMock()

    message = _run(spec, outputs_dir, client)

    expected = {
        "ok_cpp_alt_draft.cpp": "OK",
        "ok_py_main_draft.py": "MA",
        "tl_cpp_bad_draft.cpp": "TL",
        "tl_py_slow_draft.py": "TL",
        "wa_cpp_offbyone_draft.cpp": "WA",
    }
    assert [c.args for c in client.call.call_args_list] == [
        (
            "problem.saveSolution",
            {"problemId": str(POLYGON_ID), "name": name, "tag": tag, "file": SOLUTIONS[name]},
        )
        for name, tag in sorted(expected.items())
    ]
    assert "5 solution(s)" in message
    assert "ok_py_main_draft.py=MA" in message


def test_main_falls_back_to_cpp_without_py_ok(outputs_dir, spec):
    _link(outputs_dir)
    _write_solutions(
        outputs_dir, {"ok_java_main_draft.java": b"", "ok_cpp_alt_draft.cpp": b""}
    )
    client = MagicMock()

    _run(spec, outputs_dir, client)

    assert _tags(client) == {"ok_cpp_alt_draft.cpp": "MA", "ok_java_main_draft.java": "OK"}


@pytest.mark.parametrize("py_name", ["ok_python_main_draft.py", "ok_python3_main_draft.py"])
def test_python_language_aliases_win_main_over_cpp(outputs_dir, spec, py_name):
    _link(outputs_dir)
    _write_solutions(outputs_dir, {py_name: b"", "ok_cpp_alt_draft.cpp": b""})
    client = MagicMock()

    _run(spec, outputs_dir, client)

    assert _tags(client) == {py_name: "MA", "ok_cpp_alt_draft.cpp": "OK"}


def test_no_ok_solution_is_an_error(outputs_dir, spec):
    _link(outputs_dir)
    _write_solutions(
        outputs_dir, {"wa_cpp_offbyone_draft.cpp": b"", "tl_py_slow_draft.py": b""}
    )
    client = MagicMock()

    with pytest.raises(PolygonStepError, match="Main correct"):
        _run(spec, outputs_dir, client)

    client.call.assert_not_called()


def test_unprioritized_language_falls_back_to_alphabetical_with_warning(
    outputs_dir, spec, caplog
):
    _link(outputs_dir)
    _write_solutions(
        outputs_dir, {"ok_pascal_main_draft.pas": b"", "ok_pascal_alt_draft.pas": b""}
    )
    client = MagicMock()

    with caplog.at_level(logging.WARNING, logger="orchestrator.polygon.steps.upload_solutions"):
        _run(spec, outputs_dir, client)

    assert _tags(client) == {"ok_pascal_alt_draft.pas": "MA", "ok_pascal_main_draft.pas": "OK"}
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert "ok_pascal_alt_draft.pas" in warnings[0].getMessage()


def test_ml_mle_are_tagged_ml_and_re_is_tagged_rj(outputs_dir, spec):
    _link(outputs_dir)
    _write_solutions(
        outputs_dir,
        {
            "ok_cpp_main_draft.cpp": b"",
            "ml_cpp_bigarray_draft.cpp": b"",
            "mle_cpp_vectors_draft.cpp": b"",
            "re_cpp_overflow_draft.cpp": b"",
        },
    )
    client = MagicMock()

    _run(spec, outputs_dir, client)

    assert _tags(client) == {
        "ml_cpp_bigarray_draft.cpp": "ML",
        "mle_cpp_vectors_draft.cpp": "ML",
        "ok_cpp_main_draft.cpp": "MA",
        "re_cpp_overflow_draft.cpp": "RJ",
    }


def test_tle_is_tagged_tl(outputs_dir, spec):
    _link(outputs_dir)
    _write_solutions(outputs_dir, {"ok_cpp_main_draft.cpp": b"", "tle_java_slow_draft.java": b""})
    client = MagicMock()

    _run(spec, outputs_dir, client)

    assert _tags(client)["tle_java_slow_draft.java"] == "TL"


def test_save_solution_error_propagates_and_is_not_recorded(outputs_dir, spec):
    _link(outputs_dir)
    _write_solutions(outputs_dir)
    client = MagicMock()
    client.call.side_effect = [None, PolygonApiError("problem.saveSolution", "boom")]

    with pytest.raises(PolygonApiError):
        _run(spec, outputs_dir, client)

    assert client.call.call_count == 2
    assert STEP_NAME not in load_polygon_state(PROBLEM_ID, outputs_dir=outputs_dir).steps


def test_upload_is_recorded_in_polygon_state(outputs_dir, spec):
    _link(outputs_dir)
    _write_solutions(outputs_dir)

    _run(spec, outputs_dir, MagicMock())

    record = load_polygon_state(PROBLEM_ID, outputs_dir=outputs_dir).steps[STEP_NAME]
    assert set(record) == {"sha256", "tags", "sent_at"}
    assert record["tags"]["ok_py_main_draft.py"] == "MA"


def test_status_not_run_then_done_then_stale(outputs_dir, spec):
    _link(outputs_dir)
    solutions_dir = _write_solutions(outputs_dir)
    assert STEP.compute_status(PROBLEM_ID, outputs_dir)[0] == "not run"

    _run(spec, outputs_dir, MagicMock())
    assert STEP.compute_status(PROBLEM_ID, outputs_dir)[0] == "done"

    (solutions_dir / "ok_cpp_alt_draft.cpp").write_bytes(b"// changed\n")
    assert STEP.compute_status(PROBLEM_ID, outputs_dir)[0] == "stale"


def test_status_stale_after_rename_or_removal(outputs_dir, spec):
    _link(outputs_dir)
    solutions_dir = _write_solutions(outputs_dir)
    _run(spec, outputs_dir, MagicMock())

    (solutions_dir / "wa_cpp_offbyone_draft.cpp").rename(solutions_dir / "ok_cpp_fixed_draft.cpp")
    assert STEP.compute_status(PROBLEM_ID, outputs_dir)[0] == "stale"

    for path in solutions_dir.iterdir():
        path.unlink()
    assert STEP.compute_status(PROBLEM_ID, outputs_dir)[0] == "stale"
