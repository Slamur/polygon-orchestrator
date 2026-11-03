from pathlib import Path
from unittest.mock import patch

import pytest

from orchestrator import cli, cli_commands
from orchestrator.model_router import ModelResponse

REPO_ROOT = Path(__file__).parent.parent
PROMPTS_DIR = REPO_ROOT / "prompts"
TEMPLATES_DIR = REPO_ROOT / "templates"
FIXTURES_DIR = Path(__file__).parent / "fixtures"
_TESTS_PLAN_YAML = (FIXTURES_DIR / "tests-plan.yaml").read_text(encoding="utf-8")

_CONSTRAINTS_YAML = "n_max: 100000\nlimits:\n  time_limit_seconds: 1\n  memory_limit_mb: 256\n"

_RESPONSES = {
    "statement_draft": ModelResponse(
        status="confirmed",
        artifacts={
            "legend.tex": "tex",
            "input_format.tex": "tex",
            "output_format.tex": "tex",
            "notes.tex": "tex",
            "examples/example_1.txt": "1\n",
        },
        notes=[],
    ),
    "constraints_pick": ModelResponse(
        status="confirmed", artifacts={"constraints.yaml": _CONSTRAINTS_YAML}, notes=[]
    ),
    "tests_plan": ModelResponse(
        status="confirmed", artifacts={"tests_plan.yaml": _TESTS_PLAN_YAML}, notes=[]
    ),
    "generators_draft": ModelResponse(
        status="confirmed", artifacts={"gen_rand.cpp": "int main(){}"}, notes=[]
    ),
    "test_script_draft": ModelResponse(
        status="confirmed", artifacts={"test_script_groups": "<#-- fm --#>"}, notes=[]
    ),
    "solutions_draft": ModelResponse(
        status="confirmed", artifacts={"ok_cpp_draft.cpp": "int main(){}"}, notes=[]
    ),
    "checker_draft": ModelResponse(
        status="confirmed", artifacts={"checker.cpp": "int main(){}"}, notes=[]
    ),
}


def _fake_call_model(step, system_prompt, user_prompt, context_documents=None):
    return _RESPONSES[step]


def _write_spec(problems_dir: Path, problem_id: str) -> None:
    content = (FIXTURES_DIR / "valid-spec.yaml").read_text(encoding="utf-8")
    content = content.replace("problem_id: valid-spec", f"problem_id: {problem_id}")
    spec_path = problems_dir / problem_id / "authoring" / "spec.yaml"
    spec_path.parent.mkdir(parents=True, exist_ok=True)
    spec_path.write_text(content, encoding="utf-8")


def _base_args(problems_dir: Path) -> list[str]:
    return [
        "--prompts-dir",
        str(PROMPTS_DIR),
        "--base-dir",
        str(problems_dir.parent),
        "--templates-dir",
        str(TEMPLATES_DIR),
    ]


def test_run_single_problem_prints_step_outcomes_and_exits_zero(problems_dir, tmp_path, capsys):
    _write_spec(problems_dir, "p1")

    with patch("orchestrator.model_router.call_model", side_effect=_fake_call_model):
        rc = cli.main(_base_args(problems_dir) + ["problems/p1", "llm", "generate"])

    assert rc == 0
    out = capsys.readouterr().out
    assert "=== p1 ===" in out
    assert "statement_draft: confirmed" in out
    assert "solutions_draft: confirmed" in out


def test_run_writes_llm_generation_log(problems_dir, tmp_path, capsys):
    _write_spec(problems_dir, "p1")

    with patch("orchestrator.model_router.call_model", side_effect=_fake_call_model):
        cli.main(_base_args(problems_dir) + ["problems/p1", "llm", "generate"])

    log = (problems_dir / "p1" / cli_commands.LLM_GENERATION_LOG).read_text(encoding="utf-8")
    assert "statement_draft" in log
    assert not (problems_dir / "p1" / "log.txt").exists()


def test_run_log_accumulates_runs_with_header_and_step_starts(problems_dir, tmp_path, capsys):
    _write_spec(problems_dir, "p1")
    argv = _base_args(problems_dir) + [
        "problems/p1",
        "llm",
        "generate",
        "--step",
        "statement_draft",
    ]

    with patch("orchestrator.model_router.call_model", side_effect=_fake_call_model):
        cli.main(argv)
        cli.main(argv + ["--force"])

    log = (problems_dir / "p1" / cli_commands.LLM_GENERATION_LOG).read_text(encoding="utf-8")
    first = log.index(f"===== run started: orchestrator {' '.join(argv)} =====")
    second = log.index(f"===== run started: orchestrator {' '.join(argv + ['--force'])} =====")
    assert first < second
    assert log.count("[statement_draft] step started") == 2


def test_run_uncertain_exits_nonzero_and_prints_notes(problems_dir, tmp_path, capsys):
    _write_spec(problems_dir, "p1")

    uncertain_response = ModelResponse(
        status="uncertain",
        artifacts={},
        notes=[
            {
                "field": "statement_draft.formal_output_sketch",
                "kind": "uncertain",
                "explanation": "нет точности",
            }
        ],
    )
    with patch("orchestrator.model_router.call_model", return_value=uncertain_response):
        rc = cli.main(_base_args(problems_dir) + ["problems/p1", "llm", "generate"])

    assert rc == 1
    out = capsys.readouterr().out
    assert "statement_draft: uncertain" in out
    assert "(1 uncertain)" in out
    assert "нет точности" not in out
    report = (problems_dir / "p1" / "llm_reports" / "statement_draft.md").read_text(
        encoding="utf-8"
    )
    assert "## Uncertain — the spec needs fixing (1)" in report
    assert "- **statement_draft.formal_output_sketch** — нет точности" in report
    log = (problems_dir / "p1" / cli_commands.LLM_GENERATION_LOG).read_text(encoding="utf-8")
    assert "[uncertain] statement_draft.formal_output_sketch: нет точности" in log


def test_rejected_response_is_reported_and_applied_by_llm_apply(problems_dir, tmp_path, capsys):
    _write_spec(problems_dir, "p1")
    argv = _base_args(problems_dir) + ["problems/p1", "llm"]

    def reject(expected):
        def validate(artifacts):
            raise ValueError("the set of files does not match")

        return validate

    with (
        patch("orchestrator.steps.statement_draft._validate_statement_artifacts", reject),
        patch("orchestrator.model_router.call_model", side_effect=_fake_call_model),
    ):
        rc = cli.main(argv + ["generate", "--step", "statement_draft"])

    assert rc == 1
    out = capsys.readouterr().out
    assert (
        "statement_draft: conflict — the set of files does not match — "
        "nothing of the response is applied" in out
    )
    assert "llm apply --step statement_draft" in out
    assert "report: " in out and "statement_draft.md" in out
    report = (problems_dir / "p1" / "llm_reports" / "statement_draft.md").read_text(
        encoding="utf-8"
    )
    assert "# statement_draft: conflict" in report
    assert "- `legend.tex` — the response is not applied as a whole" in report
    assert "CONFLICTS.md" in report
    log = (problems_dir / "p1" / cli_commands.LLM_GENERATION_LOG).read_text(encoding="utf-8")
    assert (
        "not applied: legend.tex, input_format.tex, output_format.tex, notes.tex "
        "— the response is not applied as a whole" in log
    )

    with patch("orchestrator.model_router.call_model") as mock_call:
        rc = cli.main(argv + ["apply"])

    mock_call.assert_not_called()
    assert rc == 0
    out = capsys.readouterr().out
    assert "statement_draft: confirmed — saved model response applied" in out
    assert "constraints_pick: nothing to apply" in out
    assert (problems_dir / "p1" / "statement" / "ru" / "legend.tex").is_file()


def test_status_reports_not_run_before_any_run(problems_dir, tmp_path, capsys):
    _write_spec(problems_dir, "p1")

    rc = cli.main(_base_args(problems_dir) + ["problems/p1", "llm", "status"])

    assert rc == 0
    out = capsys.readouterr().out
    assert "statement_draft: not run" in out


def test_problem_in_layout_before_authoring_is_moved_on_first_command(problems_dir, capsys):
    _write_spec(problems_dir, "p1")
    problem_dir = problems_dir / "p1"
    (problem_dir / "authoring" / "spec.yaml").rename(problem_dir / "spec.yaml")
    (problem_dir / "draft.md").write_text("idea\n", encoding="utf-8")
    (problem_dir / "authoring").rmdir()

    rc = cli.main(_base_args(problems_dir) + ["problems/p1", "llm", "status"])

    assert rc == 0
    assert "statement_draft: not run" in capsys.readouterr().out
    assert sorted(path.name for path in (problem_dir / "authoring").iterdir()) == [
        "draft.md",
        "spec.yaml",
    ]
    assert not (problem_dir / "spec.yaml").exists()


def test_problem_with_spec_in_both_layouts_is_an_error(problems_dir, capsys):
    _write_spec(problems_dir, "p1")
    (problems_dir / "p1" / "spec.yaml").write_text("old\n", encoding="utf-8")

    rc = cli.main(_base_args(problems_dir) + ["problems/p1", "llm", "status"])

    assert rc == 1
    assert "keep the actual one in authoring/" in capsys.readouterr().err
    assert (problems_dir / "p1" / "spec.yaml").exists()


def test_status_reports_cache_hit_after_run(problems_dir, tmp_path, capsys):
    _write_spec(problems_dir, "p1")

    with patch("orchestrator.model_router.call_model", side_effect=_fake_call_model):
        cli.main(_base_args(problems_dir) + ["problems/p1", "llm", "generate"])
    capsys.readouterr()  # discard run's own output

    rc = cli.main(_base_args(problems_dir) + ["problems/p1", "llm", "status"])

    assert rc == 0
    out = capsys.readouterr().out
    assert out.count("cache hit") == 7


def test_accept_marks_stale_step_and_reports_it(problems_dir, tmp_path, capsys):
    _write_spec(problems_dir, "p1")
    with patch("orchestrator.model_router.call_model", side_effect=_fake_call_model):
        cli.main(_base_args(problems_dir) + ["problems/p1", "llm", "generate"])
    (problems_dir / "p1" / "authoring" / "constraints.yaml").write_text(
        _CONSTRAINTS_YAML.replace("100000", "200000"), encoding="utf-8"
    )
    capsys.readouterr()

    with patch("orchestrator.model_router.call_model") as mock_call:
        rc = cli.main(_base_args(problems_dir) + ["problems/p1", "llm", "accept"])

    mock_call.assert_not_called()
    assert rc == 0
    out = capsys.readouterr().out
    # В консоли — только что изменилось в целом, список частей — в отчёте.
    assert "tests_plan: accepted — was stale: spec (or upstream artifact) changed\n" in out
    assert "upstream: constraints.yaml" not in out
    report = problems_dir / "p1" / "llm_reports" / "accept.md"
    assert f"report: {report}" in out
    report_text = report.read_text(encoding="utf-8")
    assert "## tests_plan: accepted" in report_text
    assert "upstream: constraints.yaml" in report_text

    cli.main(_base_args(problems_dir) + ["problems/p1", "llm", "status"])
    assert capsys.readouterr().out.count("cache hit") == 7


def test_accept_before_any_run_exits_nonzero(problems_dir, tmp_path, capsys):
    _write_spec(problems_dir, "p1")

    rc = cli.main(_base_args(problems_dir) + ["problems/p1", "llm", "accept"])

    assert rc == 1
    out = capsys.readouterr().out
    assert "statement_draft: not accepted" in out
    assert "llm_reports" in out


def test_problem_id_is_required(problems_dir, tmp_path):
    with pytest.raises(SystemExit):
        cli.main(_base_args(problems_dir) + ["llm", "generate"])


def test_problem_id_goes_before_command(problems_dir, tmp_path):
    with pytest.raises(SystemExit):
        cli.main(_base_args(problems_dir) + ["llm", "generate", "p1"])


def test_all_is_not_supported(problems_dir, tmp_path):
    with pytest.raises(SystemExit) as exc_info:
        cli.main(_base_args(problems_dir) + ["--all", "llm", "generate"])
    assert exc_info.value.code == 2


def _mark_spec(problems_dir: Path, problem_id: str) -> None:
    """Пометки `init --draft` в спеке — как их оставляет модель."""
    spec_path = problems_dir / problem_id / "authoring" / "spec.yaml"
    text = spec_path.read_text(encoding="utf-8")
    spec_path.write_text(
        text + "# PROPOSED, REVIEW ME: inferred\n#   SUGGESTION: idea\n", encoding="utf-8"
    )


def test_generate_warns_about_review_marks_but_still_runs(problems_dir, capsys):
    _write_spec(problems_dir, "p1")
    _mark_spec(problems_dir, "p1")

    with patch("orchestrator.model_router.call_model", side_effect=_fake_call_model):
        rc = cli.main(_base_args(problems_dir) + ["problems/p1", "llm", "generate"])

    assert rc == 0
    out = capsys.readouterr().out
    assert "2 PROPOSED / SUGGESTION mark(s)" in out
    assert "# PROPOSED, REVIEW ME: inferred" in out
    assert "statement_draft: confirmed" in out
    log = (problems_dir / "p1" / "llm_generation.log").read_text(encoding="utf-8")
    assert "PROPOSED / SUGGESTION mark(s)" in log


def test_status_warns_about_review_marks_only_when_present(problems_dir, capsys):
    _write_spec(problems_dir, "p1")
    cli.main(_base_args(problems_dir) + ["problems/p1", "llm", "status"])
    assert "mark(s)" not in capsys.readouterr().out

    _mark_spec(problems_dir, "p1")
    rc = cli.main(_base_args(problems_dir) + ["problems/p1", "llm", "status"])

    assert rc == 0
    assert "2 PROPOSED / SUGGESTION mark(s)" in capsys.readouterr().out


def _write_group(problems_dir: Path) -> None:
    """Группа `contest/2026` с двумя задачами и задача вне её."""
    for path in ("contest/2026/task-a", "contest/2026/task-b", "other/task-c"):
        _write_spec(problems_dir, path)


def _answer(answer):
    """Подмена `input`: `answer` — ответ или исключение."""
    return patch("builtins.input", side_effect=[answer])


def test_group_lists_problems_and_runs_command_for_each_after_confirmation(problems_dir, capsys):
    _write_group(problems_dir)

    with _answer("y") as ask, patch.object(cli_commands, "cmd_local_clear", return_value=0) as cmd:
        rc = cli.main(_base_args(problems_dir) + ["problems/contest/2026/", "local", "clear"])

    assert rc == 0
    assert "'local clear'" in ask.call_args.args[0]
    assert cmd.call_count == 2
    out = capsys.readouterr().out
    assert "  task-a  problems/contest/2026/task-a\n" in out
    assert "  task-b  problems/contest/2026/task-b\n" in out
    assert "task-c" not in out
    assert "[2/2] problems/contest/2026/task-b" in out
    assert "2 of 2 problem(s) ok" in out


@pytest.mark.parametrize("answer", ["", "n", "no", EOFError()])
def test_group_without_confirmation_runs_nothing(problems_dir, capsys, answer):
    _write_group(problems_dir)

    with _answer(answer), patch.object(cli_commands, "cmd_local_clear") as cmd:
        rc = cli.main(_base_args(problems_dir) + ["problems/contest", "local", "clear"])

    assert rc == 1
    cmd.assert_not_called()
    assert "aborted" in capsys.readouterr().out


def test_group_passes_each_problem_path_to_the_command(problems_dir):
    _write_group(problems_dir)
    seen = []

    def record(args):
        seen.append((args.problems_dir, args.problem_id))
        return 0

    with _answer("yes"), patch.object(cli_commands, "cmd_local_clear", side_effect=record):
        cli.main(_base_args(problems_dir) + ["problems", "local", "clear"])

    assert seen == [
        (problems_dir, "contest/2026/task-a"),
        (problems_dir, "contest/2026/task-b"),
        (problems_dir, "other/task-c"),
    ]


def test_group_continues_after_failure_and_reports_failed_problems(problems_dir, capsys):
    _write_group(problems_dir)
    results = [1, RuntimeError("boom"), 0]

    with _answer("y"), patch.object(cli_commands, "cmd_local_clear", side_effect=results) as cmd:
        rc = cli.main(_base_args(problems_dir) + ["problems", "local", "clear"])

    assert rc == 1
    assert cmd.call_count == 3
    captured = capsys.readouterr()
    assert "RuntimeError: boom" in captured.err
    assert "1 of 3 problem(s) ok" in captured.out
    failed = captured.out.split("failed:")[1]
    assert "problems/contest/2026/task-a" in failed
    assert "problems/contest/2026/task-b" in failed
    assert "task-c" not in failed


@pytest.mark.parametrize("command", [["llm", "status"], ["polygon", "status"]])
def test_group_status_commands_do_not_ask_for_confirmation(problems_dir, capsys, command):
    _write_group(problems_dir)

    with patch("builtins.input") as ask:
        cli.main(_base_args(problems_dir) + ["problems/contest", *command])

    ask.assert_not_called()
    assert "[2/2] problems/contest/2026/task-b" in capsys.readouterr().out


def test_group_migrates_only_after_confirmation(problems_dir):
    legacy = problems_dir / "contest" / "task-a"
    legacy.mkdir(parents=True)
    (legacy / "spec.yaml").write_text("problem_id: task-a\n", encoding="utf-8")

    with _answer("n"):
        cli.main(_base_args(problems_dir) + ["problems/contest", "local", "clear"])
    assert (legacy / "spec.yaml").exists()

    with _answer("y"):
        cli.main(_base_args(problems_dir) + ["problems/contest", "local", "clear"])
    assert (legacy / "authoring" / "spec.yaml").exists()


def test_group_name_does_not_have_to_be_a_problem_id(problems_dir, capsys):
    _write_group(problems_dir)

    rc = cli.main(_base_args(problems_dir) + ["problems/contest/2026", "polygon", "status"])

    assert rc == 0


def test_problem_address_is_not_treated_as_a_group(problems_dir):
    _write_group(problems_dir)

    with (
        patch("builtins.input") as ask,
        patch.object(cli_commands, "cmd_local_clear", return_value=0) as cmd,
    ):
        cli.main(_base_args(problems_dir) + ["problems/other/task-c", "local", "clear"])

    ask.assert_not_called()
    cmd.assert_called_once()


def test_group_rejects_polygon_id(problems_dir, capsys):
    _write_group(problems_dir)

    with pytest.raises(SystemExit):
        cli.main(
            _base_args(problems_dir) + ["problems/contest", "polygon", "pull", "--polygon-id", "1"]
        )

    assert "--polygon-id names one problem" in capsys.readouterr().err


def test_init_does_not_expand_a_group(problems_dir, capsys):
    _write_group(problems_dir)

    with patch("builtins.input") as ask:
        rc = cli.main(_base_args(problems_dir) + ["problems/contest/group", "init"])
        assert rc == 0
        rc = cli.main(_base_args(problems_dir) + ["problems/other", "init"])

    ask.assert_not_called()
    assert rc == 1
    assert "can't be nested" in capsys.readouterr().err
