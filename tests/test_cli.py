from pathlib import Path
from unittest.mock import patch

import pytest

from orchestrator import cli, cli_commands
from orchestrator.model_router import ModelResponse

REPO_ROOT = Path(__file__).parent.parent
PROMPTS_DIR = REPO_ROOT / "prompts"
TEMPLATES_DIR = REPO_ROOT / "templates"
FIXTURES_DIR = Path(__file__).parent / "fixtures"

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
        status="confirmed", artifacts={"constraints.yaml": "n_max: 100000"}, notes=[]
    ),
    "generators_and_script": ModelResponse(
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


def _write_spec(outputs_dir: Path, problem_id: str) -> None:
    content = (FIXTURES_DIR / "valid-spec.yaml").read_text(encoding="utf-8")
    content = content.replace("problem_id: valid-spec", f"problem_id: {problem_id}")
    spec_path = outputs_dir / problem_id / "spec.yaml"
    spec_path.parent.mkdir(parents=True, exist_ok=True)
    spec_path.write_text(content, encoding="utf-8")


def _base_args(outputs_dir: Path) -> list[str]:
    return [
        "--prompts-dir",
        str(PROMPTS_DIR),
        "--outputs-dir",
        str(outputs_dir),
        "--templates-dir",
        str(TEMPLATES_DIR),
    ]


@pytest.fixture
def outputs_dir(tmp_path):
    return tmp_path / "outputs"


def test_run_single_problem_prints_step_outcomes_and_exits_zero(outputs_dir, tmp_path, capsys):
    _write_spec(outputs_dir, "p1")

    with patch("orchestrator.model_router.call_model", side_effect=_fake_call_model):
        rc = cli.main(_base_args(outputs_dir) + ["p1", "llm", "generate"])

    assert rc == 0
    out = capsys.readouterr().out
    assert "=== p1 ===" in out
    assert "statement_draft: confirmed" in out
    assert "solutions_draft: confirmed" in out


def test_run_writes_llm_generation_log(outputs_dir, tmp_path, capsys):
    _write_spec(outputs_dir, "p1")

    with patch("orchestrator.model_router.call_model", side_effect=_fake_call_model):
        cli.main(_base_args(outputs_dir) + ["p1", "llm", "generate"])

    log = (outputs_dir / "p1" / cli_commands.LLM_GENERATION_LOG).read_text(encoding="utf-8")
    assert "statement_draft" in log
    assert not (outputs_dir / "p1" / "log.txt").exists()


def test_run_log_accumulates_runs_with_header_and_step_starts(outputs_dir, tmp_path, capsys):
    _write_spec(outputs_dir, "p1")
    argv = _base_args(outputs_dir) + ["p1", "llm", "generate", "--step", "statement_draft"]

    with patch("orchestrator.model_router.call_model", side_effect=_fake_call_model):
        cli.main(argv)
        cli.main(argv + ["--force"])

    log = (outputs_dir / "p1" / cli_commands.LLM_GENERATION_LOG).read_text(encoding="utf-8")
    first = log.index(f"===== run started: orchestrator {' '.join(argv)} =====")
    second = log.index(f"===== run started: orchestrator {' '.join(argv + ['--force'])} =====")
    assert first < second
    assert log.count("[statement_draft] step started") == 2


def test_run_uncertain_exits_nonzero_and_prints_notes(outputs_dir, tmp_path, capsys):
    _write_spec(outputs_dir, "p1")

    uncertain_response = ModelResponse(
        status="uncertain",
        artifacts={},
        notes=[{"field": "statement_draft.formal_output_sketch", "kind": "uncertain", "explanation": "нет точности"}],
    )
    with patch("orchestrator.model_router.call_model", return_value=uncertain_response):
        rc = cli.main(_base_args(outputs_dir) + ["p1", "llm", "generate"])

    assert rc == 1
    out = capsys.readouterr().out
    assert "statement_draft: uncertain" in out
    assert "formal_output_sketch" in out
    assert "нет точности" in out


def test_status_reports_not_run_before_any_run(outputs_dir, tmp_path, capsys):
    _write_spec(outputs_dir, "p1")

    rc = cli.main(_base_args(outputs_dir) + ["p1", "llm", "status"])

    assert rc == 0
    out = capsys.readouterr().out
    assert "statement_draft: not run" in out


def test_status_reports_cache_hit_after_run(outputs_dir, tmp_path, capsys):
    _write_spec(outputs_dir, "p1")

    with patch("orchestrator.model_router.call_model", side_effect=_fake_call_model):
        cli.main(_base_args(outputs_dir) + ["p1", "llm", "generate"])
    capsys.readouterr()  # discard run's own output

    rc = cli.main(_base_args(outputs_dir) + ["p1", "llm", "status"])

    assert rc == 0
    out = capsys.readouterr().out
    assert out.count("cache hit") == 4


def test_accept_marks_stale_step_and_reports_it(outputs_dir, tmp_path, capsys):
    _write_spec(outputs_dir, "p1")
    with patch("orchestrator.model_router.call_model", side_effect=_fake_call_model):
        cli.main(_base_args(outputs_dir) + ["p1", "llm", "generate"])
    (outputs_dir / "p1" / "constraints.yaml").write_text("n_max: 200000", encoding="utf-8")
    capsys.readouterr()

    with patch("orchestrator.model_router.call_model") as mock_call:
        rc = cli.main(_base_args(outputs_dir) + ["p1", "llm", "accept"])

    mock_call.assert_not_called()
    assert rc == 0
    out = capsys.readouterr().out
    assert "generators_and_script: accepted — was stale: " in out
    assert "upstream: constraints.yaml" in out

    cli.main(_base_args(outputs_dir) + ["p1", "llm", "status"])
    assert capsys.readouterr().out.count("cache hit") == 4


def test_accept_before_any_run_exits_nonzero(outputs_dir, tmp_path, capsys):
    _write_spec(outputs_dir, "p1")

    rc = cli.main(_base_args(outputs_dir) + ["p1", "llm", "accept"])

    assert rc == 1
    assert "statement_draft: not accepted" in capsys.readouterr().out


def test_problem_id_is_required(outputs_dir, tmp_path):
    with pytest.raises(SystemExit):
        cli.main(_base_args(outputs_dir) + ["llm", "generate"])


def test_problem_id_goes_before_command(outputs_dir, tmp_path):
    with pytest.raises(SystemExit):
        cli.main(_base_args(outputs_dir) + ["llm", "generate", "p1"])


def test_all_is_not_supported(outputs_dir, tmp_path):
    with pytest.raises(SystemExit) as exc_info:
        cli.main(_base_args(outputs_dir) + ["--all", "llm", "generate"])
    assert exc_info.value.code == 2
