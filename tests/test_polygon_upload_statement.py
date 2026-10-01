from pathlib import Path
from unittest.mock import MagicMock

import pytest

from orchestrator.polygon.client import PolygonApiError
from orchestrator.polygon.state import PolygonState, load_polygon_state, save_polygon_state
from orchestrator.polygon.steps.base import PolygonStep, PolygonStepError, run_step
from orchestrator.polygon.steps.upload_statement import STEP, STEP_NAME
from orchestrator.spec import load_spec

FIXTURES_DIR = Path(__file__).parent / "fixtures"
PROBLEM_ID = "example-problem"
POLYGON_ID = 4242
TEX_CONTENT = {
    "legend.tex": "Легенда.\n",
    "input_format.tex": "В первой строке задано $n$.\n",
    "output_format.tex": "Выведите одно число.\n",
    "notes.tex": "Пояснение к примеру.\n",
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


def _statement_dir(outputs_dir: Path) -> Path:
    return outputs_dir / PROBLEM_ID / "statement"


def _write_tex(outputs_dir: Path, skip: str | None = None) -> None:
    statement_dir = _statement_dir(outputs_dir)
    statement_dir.mkdir(parents=True, exist_ok=True)
    for name, content in TEX_CONTENT.items():
        if name != skip:
            (statement_dir / name).write_text(content, encoding="utf-8")


def _write_examples(outputs_dir: Path, examples: dict[int, str]) -> None:
    examples_dir = _statement_dir(outputs_dir) / "examples"
    examples_dir.mkdir(parents=True, exist_ok=True)
    for index, content in examples.items():
        (examples_dir / f"example_{index}.txt").write_text(content, encoding="utf-8")


def _run(spec, outputs_dir, client) -> str:
    return run_step(STEP, PROBLEM_ID, spec, outputs_dir=outputs_dir, client=client)


def test_step_is_a_polygon_step_with_expected_name():
    assert isinstance(STEP, PolygonStep)
    assert STEP.name == STEP_NAME == "upload_statement"


def test_missing_polygon_state_points_to_create_problem(outputs_dir, spec):
    _write_tex(outputs_dir)
    _write_examples(outputs_dir, {1: "1\n"})
    client = MagicMock()

    with pytest.raises(PolygonStepError, match="create_problem"):
        _run(spec, outputs_dir, client)

    client.call.assert_not_called()


def test_missing_tex_file_is_named(outputs_dir, spec):
    _link(outputs_dir)
    _write_tex(outputs_dir, skip="notes.tex")
    _write_examples(outputs_dir, {1: "1\n"})
    client = MagicMock()

    with pytest.raises(PolygonStepError, match=r"notes\.tex"):
        _run(spec, outputs_dir, client)

    client.call.assert_not_called()


def test_missing_examples_dir(outputs_dir, spec):
    _link(outputs_dir)
    _write_tex(outputs_dir)
    client = MagicMock()

    with pytest.raises(PolygonStepError, match="examples"):
        _run(spec, outputs_dir, client)

    client.call.assert_not_called()


def test_gap_in_example_numbering(outputs_dir, spec):
    _link(outputs_dir)
    _write_tex(outputs_dir)
    _write_examples(outputs_dir, {1: "1\n", 3: "3\n"})
    client = MagicMock()

    with pytest.raises(PolygonStepError, match=r"not contiguous.*\[1, 3\]"):
        _run(spec, outputs_dir, client)

    client.call.assert_not_called()


def test_saves_statement_then_examples_in_order(outputs_dir, spec):
    _link(outputs_dir)
    _write_tex(outputs_dir)
    _write_examples(outputs_dir, {1: "3\n1 2 3\n", 2: "1\n5\n"})
    client = MagicMock()

    message = _run(spec, outputs_dir, client)

    assert client.call.call_count == 3
    statement_call, *test_calls = client.call.call_args_list
    assert statement_call.args == (
        "problem.saveStatement",
        {
            "problemId": str(POLYGON_ID),
            "lang": "russian",
            "name": spec.title.ru,
            "legend": TEX_CONTENT["legend.tex"],
            "input": TEX_CONTENT["input_format.tex"],
            "output": TEX_CONTENT["output_format.tex"],
            "notes": TEX_CONTENT["notes.tex"],
        },
    )
    assert [c.args for c in test_calls] == [
        (
            "problem.saveTest",
            {
                "problemId": str(POLYGON_ID),
                "testset": "tests",
                "testIndex": "1",
                "testInput": "3\n1 2 3\n",
                "useInStatements": "true",
            },
        ),
        (
            "problem.saveTest",
            {
                "problemId": str(POLYGON_ID),
                "testset": "tests",
                "testIndex": "2",
                "testInput": "1\n5\n",
                "useInStatements": "true",
            },
        ),
    ]
    assert str(POLYGON_ID) in message


def test_single_example(outputs_dir, spec):
    _link(outputs_dir)
    _write_tex(outputs_dir)
    _write_examples(outputs_dir, {1: "7\n"})
    client = MagicMock()

    _run(spec, outputs_dir, client)

    test_calls = [c for c in client.call.call_args_list if c.args[0] == "problem.saveTest"]
    assert len(test_calls) == 1
    assert test_calls[0].args[1]["testIndex"] == "1"


def test_save_test_error_propagates_and_is_not_recorded(outputs_dir, spec):
    _link(outputs_dir)
    _write_tex(outputs_dir)
    _write_examples(outputs_dir, {1: "1\n", 2: "2\n"})
    client = MagicMock()
    client.call.side_effect = [None, PolygonApiError("problem.saveTest", "boom")]

    with pytest.raises(PolygonApiError):
        _run(spec, outputs_dir, client)

    assert STEP_NAME not in load_polygon_state(PROBLEM_ID, outputs_dir=outputs_dir).steps


def test_status_not_run_then_done_then_stale(outputs_dir, spec):
    _link(outputs_dir)
    _write_tex(outputs_dir)
    _write_examples(outputs_dir, {1: "1\n"})
    assert STEP.compute_status(PROBLEM_ID, outputs_dir)[0] == "not run"

    _run(spec, outputs_dir, MagicMock())
    assert STEP.compute_status(PROBLEM_ID, outputs_dir)[0] == "done"

    _write_examples(outputs_dir, {1: "2\n"})
    assert STEP.compute_status(PROBLEM_ID, outputs_dir)[0] == "stale"

    (_statement_dir(outputs_dir) / "legend.tex").unlink()
    assert STEP.compute_status(PROBLEM_ID, outputs_dir)[0] == "stale"
