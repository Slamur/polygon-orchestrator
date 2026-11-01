"""Шаг `tutorial_draft`: вход из файлов задачи, запоминание `--ref`, команда
`llm tutorial` и то, что `llm generate` этот шаг не запускает."""

from pathlib import Path
from unittest.mock import patch

import pytest

from orchestrator import cli, pipeline
from orchestrator.cache import load_cache_entry
from orchestrator.model_router import ModelResponse
from orchestrator.spec import load_problem_spec
from orchestrator.steps import tutorial_draft

REPO_ROOT = Path(__file__).parent.parent
PROMPTS_DIR = REPO_ROOT / "prompts"
TEMPLATES_DIR = REPO_ROOT / "templates"
FIXTURES_DIR = Path(__file__).parent / "fixtures"
STEP = "tutorial_draft"
DRAFT_TEXT = "Заметим, что ответ монотонен. Тут докажи.\n"
_RESPONSE = ModelResponse(status="confirmed", artifacts={"tutorial.tex": "tutorial\n"}, notes=[])


def _write(path: Path, content: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


@pytest.fixture
def problems_dir(tmp_path):
    return tmp_path / "problems"


@pytest.fixture
def problem_dir(problems_dir):
    """Задача `p1` с драфтом, условием и двумя решениями."""
    directory = problems_dir / "p1"
    spec = (FIXTURES_DIR / "valid-spec.yaml").read_text(encoding="utf-8")
    _write(
        directory / "authoring" / "spec.yaml",
        spec.replace("problem_id: valid-spec", "problem_id: p1"),
    )
    _write(directory / "authoring" / "draft-tutorial.md", DRAFT_TEXT)
    _write(directory / "statement" / "legend.tex", "legend text\n")
    _write(directory / "statement" / "input_format.tex", "input text\n")
    _write(directory / "solutions" / "ok.cpp", "// ok solution\n")
    _write(directory / "solutions" / "wa_greedy.cpp", "// wa solution\n")
    return directory


def _run(problems_dir, **kwargs):
    return pipeline.run_pipeline(
        "p1",
        only_step=kwargs.pop("only_step", STEP),
        prompts_dir=PROMPTS_DIR,
        problems_dir=problems_dir,
        templates_dir=TEMPLATES_DIR,
        **kwargs,
    )


def _generate(problems_dir, *, response=_RESPONSE, **kwargs):
    """Прогон шага с моком модели; (результат, вызовы модели)."""
    with patch("orchestrator.model_router.call_model", return_value=response) as mock_call:
        result = _run(problems_dir, **kwargs)
    return result, mock_call


def _outcome(result):
    assert len(result.outcomes) == 1
    return result.outcomes[0]


def _user_prompt(mock_call) -> str:
    return mock_call.call_args.args[2]


def _status(problems_dir):
    statuses, spec_error = pipeline.compute_step_statuses(
        "p1", prompts_dir=PROMPTS_DIR, problems_dir=problems_dir, templates_dir=TEMPLATES_DIR
    )
    assert spec_error is None
    return next(status for status in statuses if status.step_name == STEP)


def _cli_args(problems_dir) -> list[str]:
    return [
        "--base-dir",
        str(problems_dir.parent),
        "--prompts-dir",
        str(PROMPTS_DIR),
        "--templates-dir",
        str(TEMPLATES_DIR),
    ]


def test_writes_tutorial_from_draft_statement_and_refs(problems_dir, problem_dir):
    result, mock_call = _generate(problems_dir, tutorial_refs=["ok.cpp"])

    assert _outcome(result).status == "confirmed"
    assert (problem_dir / "statement" / "tutorial.tex").read_text(encoding="utf-8") == "tutorial\n"
    prompt = _user_prompt(mock_call)
    assert DRAFT_TEXT.strip() in prompt
    assert "legend text" in prompt
    assert "// ok solution" in prompt
    assert "// wa solution" not in prompt
    assert mock_call.call_args.args[0] == STEP
    assert list(mock_call.call_args.kwargs["context_documents"]) == ["tutorials/tutorial.md"]


def test_prompt_shows_only_the_examples_of_the_spec(problems_dir, problem_dir):
    spec = load_problem_spec("p1", problems_dir=problems_dir)

    _, mock_call = _generate(problems_dir)

    prompt = _user_prompt(mock_call)
    assert spec.statement_draft.sample_examples[0].input.strip() in prompt
    assert spec.solutions.algorithm_hint not in prompt
    assert spec.statement_draft.legend_sketch not in prompt


def test_refs_accept_the_solutions_prefix_and_duplicates(problems_dir, problem_dir):
    inputs = tutorial_draft.read_inputs(
        "p1", ["solutions/ok.cpp", "ok.cpp"], problems_dir=problems_dir
    )
    assert tutorial_draft.solution_names(inputs) == ["ok.cpp"]


@pytest.mark.parametrize("ref", ["../secret.cpp", "solutions/../../x", "a/b.cpp"])
def test_ref_outside_solutions_is_an_error(problems_dir, problem_dir, ref):
    with pytest.raises(ValueError, match="--ref"):
        tutorial_draft.read_inputs("p1", [ref], problems_dir=problems_dir)


@pytest.mark.parametrize(
    "prepare",
    [
        lambda d: _write(d / "authoring" / "draft-tutorial.md", "  \n"),
        lambda d: (d / "statement" / "legend.tex").unlink()
        or (d / "statement" / "input_format.tex").unlink(),
    ],
    ids=["empty draft", "no statement"],
)
def test_missing_input_stops_before_the_model(problems_dir, problem_dir, prepare):
    prepare(problem_dir)

    result, mock_call = _generate(problems_dir)

    assert _outcome(result).status == pipeline.OUTCOME_ERROR
    assert not result.ok
    mock_call.assert_not_called()


def test_unknown_ref_stops_before_the_model(problems_dir, problem_dir):
    result, mock_call = _generate(problems_dir, tutorial_refs=["nope.cpp"])

    assert _outcome(result).status == pipeline.OUTCOME_ERROR
    assert "nope.cpp" in _outcome(result).detail
    mock_call.assert_not_called()


def test_removed_solution_cannot_be_a_ref(problems_dir, problem_dir):
    _write(problem_dir / "solutions" / "old.cpp.removed", "// old\n")

    result, mock_call = _generate(problems_dir, tutorial_refs=["old.cpp.removed"])

    assert _outcome(result).status == pipeline.OUTCOME_ERROR
    mock_call.assert_not_called()


def test_existing_tutorial_does_not_get_into_the_statement_input(problems_dir, problem_dir):
    _write(problem_dir / "statement" / "tutorial.tex", "hand written\n")

    inputs = tutorial_draft.read_inputs("p1", [], problems_dir=problems_dir)

    assert "statement/tutorial.tex" not in inputs


def test_handwritten_tutorial_is_revised_and_backed_up(problems_dir, problem_dir):
    _write(problem_dir / "statement" / "tutorial.tex", "hand written\n")

    _, mock_call = _generate(problems_dir)

    prompt = _user_prompt(mock_call)
    assert "Режим доработки" in prompt
    assert "hand written" in prompt
    backups = list((problem_dir / ".llm_backup").glob("*/statement/tutorial.tex"))
    assert [path.read_text(encoding="utf-8") for path in backups] == ["hand written\n"]


def test_fresh_does_not_show_the_current_tutorial(problems_dir, problem_dir):
    _write(problem_dir / "statement" / "tutorial.tex", "hand written\n")

    _, mock_call = _generate(problems_dir, fresh=True)

    assert "hand written" not in _user_prompt(mock_call)


def test_refs_are_remembered_by_the_next_run_and_status(problems_dir, problem_dir):
    _generate(problems_dir, tutorial_refs=["ok.cpp", "wa_greedy.cpp"])
    assert _status(problems_dir).state == pipeline.STATUS_CACHE_HIT

    result, mock_call = _generate(problems_dir)

    assert _outcome(result).status == pipeline.OUTCOME_CACHE_HIT
    mock_call.assert_not_called()

    _, mock_call = _generate(problems_dir, force=True)
    prompt = _user_prompt(mock_call)
    assert "// ok solution" in prompt
    assert "// wa solution" in prompt


def test_new_refs_replace_the_list_and_empty_list_clears_it(problems_dir, problem_dir):
    _generate(problems_dir, tutorial_refs=["ok.cpp", "wa_greedy.cpp"])

    _, mock_call = _generate(problems_dir, tutorial_refs=["ok.cpp"])
    mock_call.assert_called_once()
    entry = load_cache_entry("p1", STEP, problems_dir=problems_dir)
    assert tutorial_draft.remembered_refs(entry.input_values) == ["ok.cpp"]

    _, mock_call = _generate(problems_dir, tutorial_refs=[])
    mock_call.assert_called_once()
    assert "// ok solution" not in _user_prompt(mock_call).split("Было:")[0]
    entry = load_cache_entry("p1", STEP, problems_dir=problems_dir)
    assert tutorial_draft.remembered_refs(entry.input_values) == []


def test_changed_ref_solution_makes_the_step_stale_and_shows_the_change(problems_dir, problem_dir):
    _generate(problems_dir, tutorial_refs=["ok.cpp"])

    _write(problem_dir / "solutions" / "ok.cpp", "// changed solution\n")

    status = _status(problems_dir)
    assert status.state == pipeline.STATUS_STALE
    assert "solutions/ok.cpp" in status.detail

    _, mock_call = _generate(problems_dir)
    prompt = _user_prompt(mock_call)
    assert "// changed solution" in prompt
    assert "Было:" in prompt


def test_changed_unrelated_solution_keeps_the_cache(problems_dir, problem_dir):
    _generate(problems_dir, tutorial_refs=["ok.cpp"])

    _write(problem_dir / "solutions" / "wa_greedy.cpp", "// changed\n")

    assert _status(problems_dir).state == pipeline.STATUS_CACHE_HIT


@pytest.mark.parametrize(
    "path",
    ["authoring/draft-tutorial.md", "statement/legend.tex"],
)
def test_changed_draft_or_statement_makes_the_step_stale(problems_dir, problem_dir, path):
    _generate(problems_dir)

    _write(problem_dir / path, "changed\n")

    status = _status(problems_dir)
    assert status.state == pipeline.STATUS_STALE
    assert path in status.detail


def test_manual_edit_of_the_tutorial_does_not_make_the_step_stale(problems_dir, problem_dir):
    _generate(problems_dir)

    _write(problem_dir / "statement" / "tutorial.tex", "edited by hand\n")

    assert _status(problems_dir).state == pipeline.STATUS_CACHE_HIT


def test_deleted_remembered_ref_blocks_with_a_hint(problems_dir, problem_dir):
    _generate(problems_dir, tutorial_refs=["ok.cpp"])

    (problem_dir / "solutions" / "ok.cpp").unlink()

    status = _status(problems_dir)
    assert status.state == pipeline.STATUS_BLOCKED
    assert "--no-ref" in status.detail


def test_status_without_draft_is_skipped(problems_dir, problem_dir):
    (problem_dir / "authoring" / "draft-tutorial.md").unlink()

    assert _status(problems_dir).state == pipeline.STATUS_SKIPPED_OPTIONAL


def test_status_with_draft_is_not_run(problems_dir, problem_dir):
    assert _status(problems_dir).state == pipeline.STATUS_NOT_RUN


def test_apply_uses_the_refs_of_the_saved_response(problems_dir, problem_dir):
    # Ответ без `tutorial.tex` не применяется, но сохраняется — со своим входом.
    bad = ModelResponse(status="confirmed", artifacts={"other.tex": "x"}, notes=[])
    with patch("orchestrator.model_router.call_model", return_value=bad):
        _run(problems_dir, tutorial_refs=["ok.cpp"])
    assert not (problem_dir / "statement" / "tutorial.tex").exists()

    status = _status(problems_dir)
    assert "llm apply --step tutorial_draft" in status.detail

    result, mock_call = _generate(problems_dir, apply_pending=True)

    mock_call.assert_not_called()
    assert _outcome(result).status != pipeline.OUTCOME_ERROR


def test_accept_takes_the_current_tutorial(problems_dir, problem_dir):
    _generate(problems_dir, tutorial_refs=["ok.cpp"])
    _write(problem_dir / "authoring" / "draft-tutorial.md", "changed\n")

    statuses, spec_error = pipeline.accept_steps(
        "p1",
        only_step=STEP,
        prompts_dir=PROMPTS_DIR,
        problems_dir=problems_dir,
        templates_dir=TEMPLATES_DIR,
    )

    assert spec_error is None
    assert statuses[0].state == pipeline.STATUS_ACCEPTED
    assert _status(problems_dir).state == pipeline.STATUS_CACHE_HIT
    entry = load_cache_entry("p1", STEP, problems_dir=problems_dir)
    assert tutorial_draft.remembered_refs(entry.input_values) == ["ok.cpp"]


def test_plain_generate_does_not_run_the_step(problems_dir, problem_dir):
    seen = []

    def fake_call(step, *args, **kwargs):
        seen.append(step)
        raise AssertionError("stop at the first step")

    with patch("orchestrator.model_router.call_model", side_effect=fake_call):
        with pytest.raises(AssertionError):
            pipeline.run_pipeline(
                "p1",
                prompts_dir=PROMPTS_DIR,
                problems_dir=problems_dir,
                templates_dir=TEMPLATES_DIR,
            )
    assert STEP not in seen
    assert STEP not in pipeline.STEP_ORDER


def test_generate_does_not_accept_the_step_name():
    with pytest.raises(SystemExit):
        cli.build_parser().parse_args(["problems/p1", "llm", "generate", "--step", STEP])


def test_cli_tutorial_passes_repeated_refs(problems_dir, problem_dir, capsys):
    with patch("orchestrator.model_router.call_model", return_value=_RESPONSE) as mock_call:
        code = cli.main(
            _cli_args(problems_dir)
            + ["problems/p1", "llm", "tutorial", "--ref", "ok.cpp", "--ref", "wa_greedy.cpp"]
        )

    assert code == 0
    prompt = _user_prompt(mock_call)
    assert "// ok solution" in prompt
    assert "// wa solution" in prompt
    out = capsys.readouterr().out
    assert "solutions as context: ok.cpp, wa_greedy.cpp" in out
    assert (problem_dir / "llm_reports" / "tutorial_draft.md").is_file()


def test_cli_no_ref_clears_the_list(problems_dir, problem_dir):
    _generate(problems_dir, tutorial_refs=["ok.cpp"])

    with patch("orchestrator.model_router.call_model", return_value=_RESPONSE) as mock_call:
        code = cli.main(_cli_args(problems_dir) + ["problems/p1", "llm", "tutorial", "--no-ref"])

    assert code == 0
    mock_call.assert_called_once()
    entry = load_cache_entry("p1", STEP, problems_dir=problems_dir)
    assert tutorial_draft.remembered_refs(entry.input_values) == []


def test_cli_ref_and_no_ref_are_exclusive():
    with pytest.raises(SystemExit):
        cli.build_parser().parse_args(
            ["problems/p1", "llm", "tutorial", "--ref", "ok.cpp", "--no-ref"]
        )


def test_cli_without_draft_is_an_error_before_the_model(problems_dir, problem_dir, capsys):
    (problem_dir / "authoring" / "draft-tutorial.md").unlink()

    with patch("orchestrator.model_router.call_model") as mock_call:
        code = cli.main(_cli_args(problems_dir) + ["problems/p1", "llm", "tutorial"])

    assert code == 1
    assert "draft-tutorial.md" in capsys.readouterr().err
    mock_call.assert_not_called()


def test_cli_ref_with_a_group_is_an_error(problems_dir, problem_dir):
    with patch("orchestrator.model_router.call_model") as mock_call:
        with pytest.raises(SystemExit):
            cli.main(_cli_args(problems_dir) + ["problems", "llm", "tutorial", "--ref", "ok.cpp"])
    mock_call.assert_not_called()


def test_ref_completion_lists_solutions_of_the_problem(problems_dir, problem_dir):
    _write(problem_dir / "solutions" / "old.cpp.removed", "// old\n")
    args = cli.build_parser().parse_args(
        ["--base-dir", str(problems_dir.parent), "problems/p1", "llm", "tutorial"]
    )

    assert cli._complete_solution_names("", args) == ["ok.cpp", "wa_greedy.cpp"]


def test_ref_completion_tolerates_no_solutions(tmp_path):
    args = cli.build_parser().parse_args(
        ["--base-dir", str(tmp_path), "problems/p1", "llm", "tutorial"]
    )

    assert cli._complete_solution_names("", args) == []
