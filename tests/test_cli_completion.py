"""Tab-дополнение `problem_id` и условие, на котором оно держится: модуль
`cli` не тянет пайплайны при импорте (см. докстринг `orchestrator/cli.py`)."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

from orchestrator import cli, pipeline, step_order
from orchestrator.polygon import pipeline as polygon_pipeline

REPO_ROOT = Path(__file__).parent.parent


@pytest.fixture
def dirs(tmp_path):
    specs = tmp_path / "specs"
    specs.mkdir()
    (specs / "my-task.yaml").write_text("", encoding="utf-8")
    (specs / "example-problem.yaml").write_text("", encoding="utf-8")
    (specs / "notes.txt").write_text("", encoding="utf-8")
    outputs = tmp_path / "outputs"
    (outputs / "example-problem").mkdir(parents=True)
    (outputs / "only-output").mkdir()
    (outputs / ".hidden").mkdir()
    (outputs / "stray-file").write_text("", encoding="utf-8")
    return specs, outputs


def _parse(specs: Path, outputs: Path, *command: str):
    return cli.build_parser().parse_args(
        ["--specs-dir", str(specs), "--outputs-dir", str(outputs), *command]
    )


def test_spec_completer_lists_spec_file_stems(dirs):
    specs, outputs = dirs
    args = _parse(specs, outputs, "llm", "status")

    assert cli._complete_spec_ids("", args) == ["example-problem", "my-task"]


def test_spec_and_output_completer_adds_output_directories(dirs):
    specs, outputs = dirs
    args = _parse(specs, outputs, "polygon", "status", "x")

    assert cli._complete_spec_and_output_ids("", args) == [
        "example-problem",
        "my-task",
        "only-output",
    ]


def test_completers_tolerate_missing_directories(tmp_path):
    args = _parse(tmp_path / "no-specs", tmp_path / "no-outputs", "polygon", "status", "x")

    assert cli._complete_spec_and_output_ids("", args) == []


def _complete(line: str) -> list[str]:
    """Запускает CLI так же, как это делает shell при нажатии Tab (протокол
    argcomplete: строка в `COMP_LINE`, кандидаты — в файловый дескриптор 8)."""
    env = {
        **os.environ,
        "_ARGCOMPLETE": "1",
        "_ARGCOMPLETE_IFS": "\n",
        "COMP_LINE": line,
        "COMP_POINT": str(len(line)),
        "COMP_TYPE": "9",
    }
    result = subprocess.run(
        ["sh", "-c", 'exec "$0" -m orchestrator.cli 8>&1 9>/dev/null', sys.executable],
        env=env,
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=30,
    )
    return [candidate.strip() for candidate in result.stdout.split("\n") if candidate.strip()]


def test_tab_completes_problem_id_by_prefix(dirs):
    pytest.importorskip("argcomplete")
    specs, outputs = dirs
    base = f"orchestrator --specs-dir {specs} --outputs-dir {outputs}"

    assert _complete(f"{base} llm generate ") == ["example-problem", "my-task"]
    assert _complete(f"{base} polygon push my") == ["my-task"]
    assert _complete(f"{base} polygon pull o") == ["only-output"]


def test_tab_completes_commands_and_step_names():
    pytest.importorskip("argcomplete")

    assert _complete("orchestrator polygon pu") == ["push", "pull"]
    assert _complete("orchestrator llm generate --step statement") == ["statement_draft"]


def test_importing_cli_does_not_import_pipelines():
    code = (
        "import sys, orchestrator.cli; "
        "heavy = {'pydantic', 'requests', 'orchestrator.pipeline', 'orchestrator.cli_commands'}; "
        "sys.exit(1 if heavy & set(sys.modules) else 0)"
    )
    result = subprocess.run([sys.executable, "-c", code], cwd=REPO_ROOT, timeout=30)

    assert result.returncode == 0


def test_step_order_matches_registered_steps():
    assert list(pipeline._STEP_MODULES) == step_order.STEP_ORDER
    assert list(polygon_pipeline._POLYGON_STEPS) == step_order.POLYGON_STEP_ORDER
    for name, step in polygon_pipeline._POLYGON_STEPS.items():
        assert step.name == name
