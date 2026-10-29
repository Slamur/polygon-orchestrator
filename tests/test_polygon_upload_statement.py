from pathlib import Path
from unittest.mock import MagicMock

import pytest

from orchestrator.polygon.client import PolygonApiError
from orchestrator.polygon.state import PolygonState, load_polygon_state, save_polygon_state
from orchestrator.polygon.steps.base import PolygonStep, PolygonStepError
from orchestrator.polygon.steps.upload_statement import STEP, STEP_NAME
from orchestrator.spec import load_spec

from polygon_sync_helpers import run_and_record

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


def _statement_dir(problems_dir: Path) -> Path:
    return problems_dir / PROBLEM_ID / "statement"


def _write_tex(problems_dir: Path, skip: str | None = None) -> None:
    statement_dir = _statement_dir(problems_dir)
    statement_dir.mkdir(parents=True, exist_ok=True)
    for name, content in TEX_CONTENT.items():
        if name != skip:
            (statement_dir / name).write_text(content, encoding="utf-8")


def _write_examples(problems_dir: Path, examples: dict[int, str]) -> None:
    examples_dir = _statement_dir(problems_dir) / "examples"
    examples_dir.mkdir(parents=True, exist_ok=True)
    for index, content in examples.items():
        (examples_dir / f"example_{index}.txt").write_text(content, encoding="utf-8")


def _run(spec, problems_dir, client, *, incremental: bool = False) -> str:
    return run_and_record(
        STEP, PROBLEM_ID, spec, problems_dir, client, incremental=incremental
    ).message


def test_step_is_a_polygon_step_with_expected_name():
    assert isinstance(STEP, PolygonStep)
    assert STEP.name == STEP_NAME == "upload_statement"


def test_missing_polygon_state_points_to_create_problem(problems_dir, spec):
    _write_tex(problems_dir)
    _write_examples(problems_dir, {1: "1\n"})
    client = MagicMock()

    with pytest.raises(PolygonStepError, match="create_problem"):
        _run(spec, problems_dir, client)

    client.call.assert_not_called()


def test_missing_tex_file_is_named(problems_dir, spec):
    _link(problems_dir)
    _write_tex(problems_dir, skip="notes.tex")
    _write_examples(problems_dir, {1: "1\n"})
    client = MagicMock()

    with pytest.raises(PolygonStepError, match=r"notes\.tex"):
        _run(spec, problems_dir, client)

    client.call.assert_not_called()


def test_missing_examples_dir(problems_dir, spec):
    _link(problems_dir)
    _write_tex(problems_dir)
    client = MagicMock()

    with pytest.raises(PolygonStepError, match="examples"):
        _run(spec, problems_dir, client)

    client.call.assert_not_called()


def test_gap_in_example_numbering(problems_dir, spec):
    _link(problems_dir)
    _write_tex(problems_dir)
    _write_examples(problems_dir, {1: "1\n", 3: "3\n"})
    client = MagicMock()

    with pytest.raises(PolygonStepError, match=r"not contiguous.*\[1, 3\]"):
        _run(spec, problems_dir, client)

    client.call.assert_not_called()


def test_saves_statement_then_examples_in_order(problems_dir, spec):
    _link(problems_dir)
    _write_tex(problems_dir)
    _write_examples(problems_dir, {1: "3\n1 2 3\n", 2: "1\n5\n"})
    client = MagicMock()

    message = _run(spec, problems_dir, client)

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
                "testUseInStatements": "true",
            },
        ),
        (
            "problem.saveTest",
            {
                "problemId": str(POLYGON_ID),
                "testset": "tests",
                "testIndex": "2",
                "testInput": "1\n5\n",
                "testUseInStatements": "true",
            },
        ),
    ]
    assert str(POLYGON_ID) in message


def test_single_example(problems_dir, spec):
    _link(problems_dir)
    _write_tex(problems_dir)
    _write_examples(problems_dir, {1: "7\n"})
    client = MagicMock()

    _run(spec, problems_dir, client)

    test_calls = [c for c in client.call.call_args_list if c.args[0] == "problem.saveTest"]
    assert len(test_calls) == 1
    assert test_calls[0].args[1]["testIndex"] == "1"


def test_save_test_error_propagates_and_is_not_recorded(problems_dir, spec):
    _link(problems_dir)
    _write_tex(problems_dir)
    _write_examples(problems_dir, {1: "1\n", 2: "2\n"})
    client = MagicMock()
    client.call.side_effect = [None, PolygonApiError("problem.saveTest", "boom")]

    with pytest.raises(PolygonApiError):
        _run(spec, problems_dir, client)

    assert load_polygon_state(PROBLEM_ID, problems_dir=problems_dir).synced == {}


def test_incremental_run_uploads_only_changed_parts(problems_dir, spec):
    _link(problems_dir)
    _write_tex(problems_dir)
    _write_examples(problems_dir, {1: "1\n", 2: "2\n"})
    _run(spec, problems_dir, MagicMock())
    client = MagicMock()

    assert _run(spec, problems_dir, client, incremental=True) == "no changes since the last sync, skipped"
    client.call.assert_not_called()

    _write_examples(problems_dir, {2: "22\n"})
    message = _run(spec, problems_dir, client, incremental=True)
    assert [(c.args[0], c.args[1].get("testIndex")) for c in client.call.call_args_list] == [
        ("problem.saveTest", "2")
    ]
    assert "1 of 2 sample test(s)" in message

    client.reset_mock()
    (_statement_dir(problems_dir) / "notes.tex").write_text("new notes", encoding="utf-8")
    _run(spec, problems_dir, client, incremental=True)
    [statement_call] = client.call.call_args_list
    assert statement_call.args[0] == "problem.saveStatement"
    # saveStatement принимает разделы только вместе — уходят все четыре
    assert {"legend", "input", "output", "notes"} <= set(statement_call.args[1])


def test_status_not_run_then_done_then_stale(problems_dir, spec):
    _link(problems_dir)
    _write_tex(problems_dir)
    _write_examples(problems_dir, {1: "1\n"})
    assert STEP.compute_status(PROBLEM_ID, problems_dir)[0] == "not run"

    _run(spec, problems_dir, MagicMock())
    assert STEP.compute_status(PROBLEM_ID, problems_dir)[0] == "done"

    _write_examples(problems_dir, {1: "2\n"})
    assert STEP.compute_status(PROBLEM_ID, problems_dir)[0] == "stale"

    (_statement_dir(problems_dir) / "legend.tex").unlink()
    assert STEP.compute_status(PROBLEM_ID, problems_dir)[0] == "stale"


def test_name_txt_is_seeded_from_spec_title_once(problems_dir, spec):
    _link(problems_dir)
    _write_tex(problems_dir)
    _write_examples(problems_dir, {1: "1\n"})
    name_path = _statement_dir(problems_dir) / "name.txt"

    _run(spec, problems_dir, MagicMock())
    assert name_path.read_text(encoding="utf-8") == spec.title.ru + "\n"

    name_path.write_text("Другое название\n", encoding="utf-8")
    client = MagicMock()
    message = _run(spec, problems_dir, client, incremental=True)

    [statement_call] = client.call.call_args_list
    assert statement_call.args[1]["name"] == "Другое название"
    assert "Другое название" in message


def test_empty_name_txt_is_an_error(problems_dir, spec):
    _link(problems_dir)
    _write_tex(problems_dir)
    _write_examples(problems_dir, {1: "1\n"})
    (_statement_dir(problems_dir) / "name.txt").write_text("\n", encoding="utf-8")
    client = MagicMock()

    with pytest.raises(PolygonStepError, match=r"name\.txt is empty"):
        _run(spec, problems_dir, client)

    client.call.assert_not_called()


def test_status_sees_name_change(problems_dir, spec):
    _link(problems_dir)
    _write_tex(problems_dir)
    _write_examples(problems_dir, {1: "1\n"})
    _run(spec, problems_dir, MagicMock())

    (_statement_dir(problems_dir) / "name.txt").write_text("Другое\n", encoding="utf-8")

    status, detail = STEP.compute_status(PROBLEM_ID, problems_dir)
    assert status == "stale"
    assert "statement/name" in detail


@pytest.fixture
def interactive_spec():
    return load_spec(FIXTURES_DIR / "interactive-spec.yaml")


def _write_interactive(problems_dir: Path) -> None:
    _write_tex(problems_dir)
    (_statement_dir(problems_dir) / "interaction.tex").write_text("Протокол.\n", encoding="utf-8")
    _write_examples(problems_dir, {1: "10 3\n"})
    examples_dir = _statement_dir(problems_dir) / "examples"
    (examples_dir / "example_1.input_for_statement.txt").write_text("10\n\n<\n", encoding="utf-8")
    (examples_dir / "example_1.output_for_statement.txt").write_text("\n? 5\n\n", encoding="utf-8")


def _calls(client, method):
    return [c.args[1] for c in client.call.call_args_list if c.args[0] == method]


def test_interactive_uploads_protocol_and_dialogues(problems_dir, interactive_spec):
    _link(problems_dir)
    _write_interactive(problems_dir)
    client = MagicMock()

    _run(interactive_spec, problems_dir, client)

    [statement] = _calls(client, "problem.saveStatement")
    assert statement["interaction"] == "Протокол.\n"
    [test] = _calls(client, "problem.saveTest")
    assert test["testInput"] == "10 3\n"
    assert test["testInputForStatements"] == "10\n\n<\n"
    assert test["testOutputForStatements"] == "\n? 5\n\n"


def test_interactive_without_protocol_file_is_named(problems_dir, interactive_spec):
    _link(problems_dir)
    _write_interactive(problems_dir)
    (_statement_dir(problems_dir) / "interaction.tex").unlink()
    client = MagicMock()

    with pytest.raises(PolygonStepError, match="interaction.tex"):
        _run(interactive_spec, problems_dir, client)

    client.call.assert_not_called()


def test_dialogue_change_reuploads_only_that_example(problems_dir, interactive_spec):
    _link(problems_dir)
    _write_interactive(problems_dir)
    _run(interactive_spec, problems_dir, MagicMock())
    examples_dir = _statement_dir(problems_dir) / "examples"
    (examples_dir / "example_1.output_for_statement.txt").write_text("\n? 4\n\n", encoding="utf-8")
    client = MagicMock()

    _run(interactive_spec, problems_dir, client, incremental=True)

    assert _calls(client, "problem.saveStatement") == []
    [test] = _calls(client, "problem.saveTest")
    assert test["testOutputForStatements"] == "\n? 4\n\n"
    assert STEP.compute_status(PROBLEM_ID, problems_dir)[0] == "done"


def test_removed_statement_output_is_cleared_on_polygon(problems_dir, interactive_spec):
    _link(problems_dir)
    _write_interactive(problems_dir)
    _run(interactive_spec, problems_dir, MagicMock())
    (_statement_dir(problems_dir) / "examples" / "example_1.output_for_statement.txt").unlink()
    client = MagicMock()

    _run(interactive_spec, problems_dir, client, incremental=True)

    [test] = _calls(client, "problem.saveTest")
    assert test["testInputForStatements"] == "10\n\n<\n"
    assert test["testOutputForStatements"] == ""
    synced = load_polygon_state(PROBLEM_ID, problems_dir=problems_dir).synced
    assert "statement/examples/example_1.output_for_statement.txt" not in synced

    client = MagicMock()
    assert "no changes" in _run(interactive_spec, problems_dir, client, incremental=True)
    client.call.assert_not_called()


def test_plain_problem_uploads_only_statement_output(problems_dir, spec):
    _link(problems_dir)
    _write_tex(problems_dir)
    _write_examples(problems_dir, {1: "1\n"})
    examples_dir = _statement_dir(problems_dir) / "examples"
    (examples_dir / "example_1.output_for_statement.txt").write_text("2\n", encoding="utf-8")
    (examples_dir / "example_1.verify_for_statement.txt").write_text("true\n", encoding="utf-8")
    client = MagicMock()

    _run(spec, problems_dir, client)

    [test] = _calls(client, "problem.saveTest")
    assert test["testOutputForStatements"] == "2\n"
    assert test["verifyInputOutputForStatements"] == "true"
    assert "testInputForStatements" not in test


def test_verify_flag_removal_sends_false(problems_dir, spec):
    _link(problems_dir)
    _write_tex(problems_dir)
    _write_examples(problems_dir, {1: "1\n"})
    verify = _statement_dir(problems_dir) / "examples" / "example_1.verify_for_statement.txt"
    verify.write_text("true\n", encoding="utf-8")
    _run(spec, problems_dir, MagicMock())
    verify.unlink()
    client = MagicMock()

    _run(spec, problems_dir, client, incremental=True)

    [test] = _calls(client, "problem.saveTest")
    assert test["verifyInputOutputForStatements"] == "false"


def test_verify_flag_must_be_true(problems_dir, spec):
    _link(problems_dir)
    _write_tex(problems_dir)
    _write_examples(problems_dir, {1: "1\n"})
    verify = _statement_dir(problems_dir) / "examples" / "example_1.verify_for_statement.txt"
    verify.write_text("no\n", encoding="utf-8")

    with pytest.raises(PolygonStepError, match="must contain 'true'"):
        _run(spec, problems_dir, MagicMock())


def test_plain_problem_sends_no_interaction(problems_dir, spec):
    _link(problems_dir)
    _write_tex(problems_dir)
    _write_examples(problems_dir, {1: "1\n"})
    client = MagicMock()

    _run(spec, problems_dir, client)

    [statement] = _calls(client, "problem.saveStatement")
    assert "interaction" not in statement
    [test] = _calls(client, "problem.saveTest")
    assert "testInputForStatements" not in test
