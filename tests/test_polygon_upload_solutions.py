import logging
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

from orchestrator.polygon.client import PolygonApiError
from orchestrator.polygon.state import load_polygon_state
from orchestrator.polygon.steps.base import PolygonStepError
from orchestrator.polygon.steps.upload_solutions import STEP
from orchestrator.spec import load_spec

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


def _solutions_dir(problems_dir: Path) -> Path:
    path = problems_dir / PROBLEM_ID / "solutions"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _write_solutions(problems_dir: Path, files: dict[str, bytes] = SOLUTIONS) -> Path:
    solutions_dir = _solutions_dir(problems_dir)
    for name, content in files.items():
        (solutions_dir / name).write_bytes(content)
    return solutions_dir


def _run(spec, problems_dir, client, *, incremental: bool = False) -> str:
    return run_and_record(
        STEP, PROBLEM_ID, spec, problems_dir, client, incremental=incremental
    ).message


def _uploads(client: MagicMock) -> list:
    return [c for c in client.call.call_args_list if c.args[0] == "problem.saveSolution"]


def _tags(client: MagicMock) -> dict[str, str]:
    return {c.args[1]["name"]: c.args[1]["tag"] for c in _uploads(client)}


def _client_with_solutions(solutions: list[dict]) -> MagicMock:
    client = MagicMock()
    client.call.side_effect = lambda method, params: (
        solutions if method == "problem.solutions" else None
    )
    return client


def test_missing_polygon_state_points_to_create_problem(problems_dir, spec):
    _write_solutions(problems_dir)
    client = MagicMock()

    with pytest.raises(PolygonStepError, match="create_problem"):
        _run(spec, problems_dir, client)

    client.call.assert_not_called()


def test_missing_solutions_dir_points_to_solutions_draft(problems_dir, spec):
    link(problems_dir)
    client = MagicMock()

    with pytest.raises(PolygonStepError, match="solutions_draft"):
        _run(spec, problems_dir, client)

    client.call.assert_not_called()


def test_empty_solutions_dir_is_an_error(problems_dir, spec):
    link(problems_dir)
    _solutions_dir(problems_dir)
    client = MagicMock()

    with pytest.raises(PolygonStepError, match="no files in"):
        _run(spec, problems_dir, client)

    client.call.assert_not_called()


def test_filename_without_scheme_is_an_error(problems_dir, spec):
    link(problems_dir)
    _write_solutions(problems_dir, {"ok_cpp_main_draft.cpp": b"", "solution.cpp": b""})
    client = MagicMock()

    with pytest.raises(PolygonStepError, match="<verdict>_<language>_<description>_<author>"):
        _run(spec, problems_dir, client)

    client.call.assert_not_called()


def test_unknown_verdict_is_an_error(problems_dir, spec):
    link(problems_dir)
    _write_solutions(problems_dir, {"ok_cpp_main_draft.cpp": b"", "xx_cpp_foo_bar.cpp": b""})
    client = MagicMock()

    with pytest.raises(PolygonStepError, match=r"'xx'.*allowed set"):
        _run(spec, problems_dir, client)

    client.call.assert_not_called()


def test_uploads_each_solution_with_its_tag(problems_dir, spec):
    link(problems_dir)
    _write_solutions(problems_dir)
    client = MagicMock()

    message = _run(spec, problems_dir, client)

    expected = {
        "ok_cpp_alt_draft.cpp": "MA",
        "ok_py_main_draft.py": "OK",
        "tl_cpp_bad_draft.cpp": "TL",
        "tl_py_slow_draft.py": "TL",
        "wa_cpp_offbyone_draft.cpp": "WA",
    }
    assert [c.args for c in _uploads(client)] == [
        (
            "problem.saveSolution",
            {"problemId": str(POLYGON_ID), "name": name, "tag": tag, "file": SOLUTIONS[name]},
        )
        for name, tag in sorted(expected.items())
    ]
    assert "5 solution(s)" in message
    assert "ok_cpp_alt_draft.cpp=MA" in message


def test_solutions_marked_removed_are_not_uploaded(problems_dir, spec):
    link(problems_dir)
    _write_solutions(
        problems_dir,
        {"ok_cpp_alt_draft.cpp": b"int main() {}\n", "wa_cpp_old_draft.cpp.removed": b""},
    )
    client = MagicMock()

    _run(spec, problems_dir, client)

    assert _tags(client) == {"ok_cpp_alt_draft.cpp": "MA"}


def test_main_prefers_cpp_over_java_and_py(problems_dir, spec):
    link(problems_dir)
    _write_solutions(
        problems_dir,
        {"ok_py_main_draft.py": b"", "ok_java_main_draft.java": b"", "ok_cpp_alt_draft.cpp": b""},
    )
    client = MagicMock()

    _run(spec, problems_dir, client)

    assert _tags(client) == {
        "ok_cpp_alt_draft.cpp": "MA",
        "ok_java_main_draft.java": "OK",
        "ok_py_main_draft.py": "OK",
    }


def test_main_falls_back_to_java_without_cpp_ok(problems_dir, spec):
    link(problems_dir)
    _write_solutions(
        problems_dir, {"ok_py_main_draft.py": b"", "ok_java_main_draft.java": b""}
    )
    client = MagicMock()

    _run(spec, problems_dir, client)

    assert _tags(client) == {"ok_java_main_draft.java": "MA", "ok_py_main_draft.py": "OK"}


@pytest.mark.parametrize("py_name", ["ok_python_main_draft.py", "ok_python3_main_draft.py"])
def test_python_language_aliases_win_main_over_unprioritized(problems_dir, spec, py_name):
    link(problems_dir)
    _write_solutions(problems_dir, {py_name: b"", "ok_pascal_alt_draft.pas": b""})
    client = MagicMock()

    _run(spec, problems_dir, client)

    assert _tags(client) == {py_name: "MA", "ok_pascal_alt_draft.pas": "OK"}


def test_cpp_language_alias_wins_main_over_java(problems_dir, spec):
    link(problems_dir)
    _write_solutions(problems_dir, {"ok_c++_main_draft.cpp": b"", "ok_java_alt_draft.java": b""})
    client = MagicMock()

    _run(spec, problems_dir, client)

    assert _tags(client) == {"ok_c++_main_draft.cpp": "MA", "ok_java_alt_draft.java": "OK"}


def test_no_ok_solution_is_an_error(problems_dir, spec):
    link(problems_dir)
    _write_solutions(
        problems_dir, {"wa_cpp_offbyone_draft.cpp": b"", "tl_py_slow_draft.py": b""}
    )
    client = MagicMock()

    with pytest.raises(PolygonStepError, match="Main correct"):
        _run(spec, problems_dir, client)

    client.call.assert_not_called()


def test_unprioritized_language_falls_back_to_alphabetical_with_warning(
    problems_dir, spec, caplog
):
    link(problems_dir)
    _write_solutions(
        problems_dir, {"ok_pascal_main_draft.pas": b"", "ok_pascal_alt_draft.pas": b""}
    )
    client = MagicMock()

    with caplog.at_level(logging.WARNING, logger="orchestrator.polygon.steps.upload_solutions"):
        _run(spec, problems_dir, client)

    assert _tags(client) == {"ok_pascal_alt_draft.pas": "MA", "ok_pascal_main_draft.pas": "OK"}
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert "ok_pascal_alt_draft.pas" in warnings[0].getMessage()


def test_existing_main_on_polygon_is_kept_when_new_solution_sorts_first(problems_dir, spec):
    link(problems_dir)
    _write_solutions(problems_dir, {"ok_cpp_b_draft.cpp": b"", "ok_cpp_a_draft.cpp": b""})
    client = _client_with_solutions(
        [{"name": "ok_cpp_b_draft.cpp", "tag": "MA"}, {"name": "wa_cpp_c_draft.cpp", "tag": "WA"}]
    )

    _run(spec, problems_dir, client)

    assert _tags(client) == {"ok_cpp_a_draft.cpp": "OK", "ok_cpp_b_draft.cpp": "MA"}


def test_no_solution_gets_main_when_polygon_main_is_not_local(problems_dir, spec):
    link(problems_dir)
    _write_solutions(problems_dir, {"ok_cpp_new_draft.cpp": b""})
    client = _client_with_solutions([{"name": "old_main.cpp", "tag": "MA"}])

    _run(spec, problems_dir, client)

    assert _tags(client) == {"ok_cpp_new_draft.cpp": "OK"}


def test_polygon_main_keeps_tag_regardless_of_verdict_in_name(problems_dir, spec):
    link(problems_dir)
    _write_solutions(problems_dir, {"ok_cpp_alt_draft.cpp": b"", "tl_cpp_slow_draft.cpp": b""})
    client = _client_with_solutions([{"name": "tl_cpp_slow_draft.cpp", "tag": "MA"}])

    _run(spec, problems_dir, client)

    assert _tags(client) == {"ok_cpp_alt_draft.cpp": "OK", "tl_cpp_slow_draft.cpp": "MA"}


def test_solution_on_polygon_keeps_its_tag_instead_of_verdict_in_name(problems_dir, spec):
    link(problems_dir)
    _write_solutions(
        problems_dir,
        {"ok_cpp_main_draft.cpp": b"", "wa_cpp_tagged_draft.cpp": b"", "wa_cpp_new_draft.cpp": b""},
    )
    client = _client_with_solutions(
        [
            {"name": "ok_cpp_main_draft.cpp", "tag": "MA"},
            {"name": "wa_cpp_tagged_draft.cpp", "tag": "TL"},
        ]
    )

    _run(spec, problems_dir, client)

    assert _tags(client) == {
        "ok_cpp_main_draft.cpp": "MA",
        "wa_cpp_new_draft.cpp": "WA",
        "wa_cpp_tagged_draft.cpp": "TL",
    }


def test_existing_solution_becomes_main_when_polygon_has_no_main(problems_dir, spec):
    link(problems_dir)
    _write_solutions(problems_dir, {"ok_cpp_alt_draft.cpp": b"", "ok_py_alt_draft.py": b""})
    client = _client_with_solutions(
        [{"name": "ok_cpp_alt_draft.cpp", "tag": "OK"}, {"name": "ok_py_alt_draft.py", "tag": "TO"}]
    )

    _run(spec, problems_dir, client)

    assert _tags(client) == {"ok_cpp_alt_draft.cpp": "MA", "ok_py_alt_draft.py": "TO"}


def test_main_is_chosen_when_polygon_has_solutions_without_main(problems_dir, spec):
    link(problems_dir)
    _write_solutions(problems_dir, {"ok_cpp_alt_draft.cpp": b""})
    client = _client_with_solutions([{"name": "ok_py_old_draft.py", "tag": "OK"}])

    _run(spec, problems_dir, client)

    assert _tags(client) == {"ok_cpp_alt_draft.cpp": "MA"}


def test_nothing_changed_makes_no_requests(problems_dir, spec):
    link(problems_dir)
    _write_solutions(problems_dir)
    _run(spec, problems_dir, MagicMock())
    client = MagicMock()

    _run(spec, problems_dir, client, incremental=True)

    client.call.assert_not_called()


def test_ml_mle_are_tagged_ml_and_re_is_tagged_rj(problems_dir, spec):
    link(problems_dir)
    _write_solutions(
        problems_dir,
        {
            "ok_cpp_main_draft.cpp": b"",
            "ml_cpp_bigarray_draft.cpp": b"",
            "mle_cpp_vectors_draft.cpp": b"",
            "re_cpp_overflow_draft.cpp": b"",
            "il_java_noflush_draft.java": b"",
        },
    )
    client = MagicMock()

    _run(spec, problems_dir, client)

    assert _tags(client) == {
        "ml_cpp_bigarray_draft.cpp": "ML",
        "mle_cpp_vectors_draft.cpp": "ML",
        "ok_cpp_main_draft.cpp": "MA",
        "re_cpp_overflow_draft.cpp": "RJ",
        "il_java_noflush_draft.java": "RJ",
    }


def test_tle_is_tagged_tl(problems_dir, spec):
    link(problems_dir)
    _write_solutions(problems_dir, {"ok_cpp_main_draft.cpp": b"", "tle_java_slow_draft.java": b""})
    client = MagicMock()

    _run(spec, problems_dir, client)

    assert _tags(client)["tle_java_slow_draft.java"] == "TL"


def test_save_solution_error_propagates_and_is_not_recorded(problems_dir, spec):
    link(problems_dir)
    _write_solutions(problems_dir)
    client = MagicMock()
    client.call.side_effect = [[], None, PolygonApiError("problem.saveSolution", "boom")]

    with pytest.raises(PolygonApiError):
        _run(spec, problems_dir, client)

    assert len(_uploads(client)) == 2
    assert load_polygon_state(PROBLEM_ID, problems_dir=problems_dir).synced == {}


def test_upload_is_recorded_in_polygon_state(problems_dir, spec):
    link(problems_dir)
    _write_solutions(problems_dir)

    _run(spec, problems_dir, MagicMock())

    synced = load_polygon_state(PROBLEM_ID, problems_dir=problems_dir).synced
    assert "solutions/ok_py_main_draft.py" in synced
    assert all(key.startswith("solutions/") for key in synced)


def test_incremental_run_uploads_only_changed_solution_with_its_tag(problems_dir, spec):
    link(problems_dir)
    _write_solutions(problems_dir)
    _run(spec, problems_dir, MagicMock())
    (problems_dir / PROBLEM_ID / "solutions" / "ok_py_main_draft.py").write_bytes(b"# changed\n")
    client = MagicMock()

    _run(spec, problems_dir, client, incremental=True)

    assert [(c.args[1]["name"], c.args[1]["tag"]) for c in _uploads(client)] == [
        ("ok_py_main_draft.py", "OK")
    ]


def test_status_not_run_then_done_then_stale(problems_dir, spec):
    link(problems_dir)
    solutions_dir = _write_solutions(problems_dir)
    assert STEP.compute_status(PROBLEM_ID, problems_dir)[0] == "not run"

    _run(spec, problems_dir, MagicMock())
    assert STEP.compute_status(PROBLEM_ID, problems_dir)[0] == "done"

    (solutions_dir / "ok_cpp_alt_draft.cpp").write_bytes(b"// changed\n")
    assert STEP.compute_status(PROBLEM_ID, problems_dir)[0] == "stale"


def test_status_stale_after_rename_or_removal(problems_dir, spec):
    link(problems_dir)
    solutions_dir = _write_solutions(problems_dir)
    _run(spec, problems_dir, MagicMock())

    (solutions_dir / "wa_cpp_offbyone_draft.cpp").rename(solutions_dir / "ok_cpp_fixed_draft.cpp")
    assert STEP.compute_status(PROBLEM_ID, problems_dir)[0] == "stale"

    for path in solutions_dir.iterdir():
        path.unlink()
    assert STEP.compute_status(PROBLEM_ID, problems_dir)[0] == "stale"


def test_tl_ok_is_tagged_to(problems_dir, spec):
    link(problems_dir)
    _write_solutions(
        problems_dir, {"ok_cpp_main_draft.cpp": b"", "tl_ok_java_slowio_draft.java": b""}
    )
    client = MagicMock()

    _run(spec, problems_dir, client)

    assert _tags(client)["tl_ok_java_slowio_draft.java"] == "TO"
