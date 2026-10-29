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


@pytest.fixture
def problems_dir(tmp_path):
    return tmp_path / "problems"


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
    argv = _base_args(problems_dir) + ["problems/p1", "llm", "generate", "--step", "statement_draft"]

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
        notes=[{"field": "statement_draft.formal_output_sketch", "kind": "uncertain", "explanation": "нет точности"}],
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

    with patch(
        "orchestrator.steps.statement_draft._validate_statement_artifacts", reject
    ), patch("orchestrator.model_router.call_model", side_effect=_fake_call_model):
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
    assert (problems_dir / "p1" / "statement" / "legend.tex").is_file()


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
    (problems_dir / "p1" / "authoring" / "constraints.yaml").write_text(_CONSTRAINTS_YAML.replace("100000", "200000"), encoding="utf-8")
    capsys.readouterr()

    with patch("orchestrator.model_router.call_model") as mock_call:
        rc = cli.main(_base_args(problems_dir) + ["problems/p1", "llm", "accept"])

    mock_call.assert_not_called()
    assert rc == 0
    out = capsys.readouterr().out
    # В консоли — только что изменилось в целом, список частей — в отчёте.
    assert (
        "tests_plan: accepted — was stale: spec (or upstream artifact) changed\n"
        in out
    )
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
