from pathlib import Path
from unittest.mock import patch

import pytest

from orchestrator import cli
from orchestrator.model_router import ModelResponse

REPO_ROOT = Path(__file__).parent.parent
PROMPTS_DIR = REPO_ROOT / "prompts"
TEMPLATES_DIR = REPO_ROOT / "templates"
FIXTURES_DIR = Path(__file__).parent / "fixtures"

_RESPONSES = {
    "statement_draft": ModelResponse(status="confirmed", artifacts={"statement.tex": "tex"}, notes=[]),
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


def _write_spec(specs_dir: Path, problem_id: str) -> None:
    content = (FIXTURES_DIR / "valid-spec.yaml").read_text(encoding="utf-8")
    content = content.replace("problem_id: valid-spec", f"problem_id: {problem_id}")
    (specs_dir / f"{problem_id}.yaml").write_text(content, encoding="utf-8")


def _base_args(specs_dir: Path, outputs_dir: Path) -> list[str]:
    return [
        "--specs-dir",
        str(specs_dir),
        "--prompts-dir",
        str(PROMPTS_DIR),
        "--outputs-dir",
        str(outputs_dir),
        "--templates-dir",
        str(TEMPLATES_DIR),
    ]


@pytest.fixture
def specs_dir(tmp_path):
    specs = tmp_path / "specs"
    specs.mkdir()
    return specs


def test_run_single_problem_prints_step_outcomes_and_exits_zero(specs_dir, tmp_path, capsys):
    _write_spec(specs_dir, "p1")
    outputs_dir = tmp_path / "outputs"

    with patch("orchestrator.model_router.call_model", side_effect=_fake_call_model):
        rc = cli.main(_base_args(specs_dir, outputs_dir) + ["run", "p1"])

    assert rc == 0
    out = capsys.readouterr().out
    assert "=== p1 ===" in out
    assert "statement_draft: confirmed" in out
    assert "solutions_draft: confirmed" in out


def test_run_uncertain_exits_nonzero_and_prints_notes(specs_dir, tmp_path, capsys):
    _write_spec(specs_dir, "p1")
    outputs_dir = tmp_path / "outputs"

    uncertain_response = ModelResponse(
        status="uncertain",
        artifacts={},
        notes=[{"field": "statement_draft.formal_output_sketch", "kind": "uncertain", "explanation": "нет точности"}],
    )
    with patch("orchestrator.model_router.call_model", return_value=uncertain_response):
        rc = cli.main(_base_args(specs_dir, outputs_dir) + ["run", "p1"])

    assert rc == 1
    out = capsys.readouterr().out
    assert "statement_draft: uncertain" in out
    assert "formal_output_sketch" in out
    assert "нет точности" in out


def test_run_all_continues_past_invalid_spec_and_reports_exit_code(specs_dir, tmp_path, capsys):
    _write_spec(specs_dir, "good")
    # problem_id внутри файла не совпадает с именем файла -> SpecValidationError
    bad_content = (FIXTURES_DIR / "valid-spec.yaml").read_text(encoding="utf-8")
    (specs_dir / "bad.yaml").write_text(bad_content, encoding="utf-8")  # problem_id остаётся "valid-spec"
    outputs_dir = tmp_path / "outputs"

    with patch("orchestrator.model_router.call_model", side_effect=_fake_call_model):
        rc = cli.main(_base_args(specs_dir, outputs_dir) + ["run", "--all"])

    assert rc == 1  # хотя бы одна задача провалилась
    out = capsys.readouterr().out
    assert "=== bad ===" in out
    assert "не прошёл валидацию" in out
    assert "=== good ===" in out
    assert "statement_draft: confirmed" in out


def test_run_all_with_no_specs_reports_and_exits_zero(specs_dir, tmp_path, capsys):
    outputs_dir = tmp_path / "outputs"
    rc = cli.main(_base_args(specs_dir, outputs_dir) + ["run", "--all"])
    assert rc == 0
    assert "не найдено" in capsys.readouterr().out


def test_status_reports_not_run_before_any_run(specs_dir, tmp_path, capsys):
    _write_spec(specs_dir, "p1")
    outputs_dir = tmp_path / "outputs"

    rc = cli.main(_base_args(specs_dir, outputs_dir) + ["status"])

    assert rc == 0
    out = capsys.readouterr().out
    assert "statement_draft: not run" in out


def test_status_reports_cache_hit_after_run(specs_dir, tmp_path, capsys):
    _write_spec(specs_dir, "p1")
    outputs_dir = tmp_path / "outputs"

    with patch("orchestrator.model_router.call_model", side_effect=_fake_call_model):
        cli.main(_base_args(specs_dir, outputs_dir) + ["run", "p1"])
    capsys.readouterr()  # discard run's own output

    rc = cli.main(_base_args(specs_dir, outputs_dir) + ["status", "--problem", "p1"])

    assert rc == 0
    out = capsys.readouterr().out
    assert out.count("cache hit") == 4


def test_run_all_and_step_are_mutually_incompatible(specs_dir, tmp_path):
    outputs_dir = tmp_path / "outputs"
    with pytest.raises(SystemExit) as exc_info:
        cli.main(_base_args(specs_dir, outputs_dir) + ["run", "--all", "--step", "statement_draft"])
    assert exc_info.value.code == 2


def test_run_requires_problem_id_or_all(specs_dir, tmp_path):
    outputs_dir = tmp_path / "outputs"
    with pytest.raises(SystemExit):
        cli.main(_base_args(specs_dir, outputs_dir) + ["run"])
