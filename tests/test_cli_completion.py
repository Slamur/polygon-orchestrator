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
def outputs(tmp_path):
    outputs = tmp_path / "outputs"
    (outputs / "my-task").mkdir(parents=True)
    (outputs / "my-task" / "spec.yaml").write_text("", encoding="utf-8")
    (outputs / "example-problem").mkdir()
    (outputs / "example-problem" / "spec.yaml").write_text("", encoding="utf-8")
    (outputs / "only-output").mkdir()
    (outputs / ".hidden").mkdir()
    (outputs / "stray-file").write_text("", encoding="utf-8")
    return outputs


def _parse(outputs: Path, *command: str):
    return cli.build_parser().parse_args(["--outputs-dir", str(outputs), *command])


def test_completer_lists_problem_directories_with_or_without_spec(outputs):
    args = _parse(outputs, "x", "polygon", "status")

    assert cli._complete_problem_ids("", args) == [
        "example-problem",
        "my-task",
        "only-output",
    ]


def test_completers_tolerate_missing_directories(tmp_path):
    args = _parse(tmp_path / "no-outputs", "x", "polygon", "status")

    assert cli._complete_problem_ids("", args) == []


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


def test_tab_completes_problem_id_by_prefix(outputs):
    pytest.importorskip("argcomplete")
    base = f"orchestrator --outputs-dir {outputs}"

    assert _complete(f"{base} ") == ["example-problem", "my-task", "only-output"]
    assert _complete(f"{base} my") == ["my-task"]
    assert _complete(f"{base} o") == ["only-output"]


def test_tab_completes_commands_and_step_names():
    pytest.importorskip("argcomplete")

    assert _complete("orchestrator my-task polygon pu") == ["push", "pull"]
    assert _complete("orchestrator my-task llm generate --step statement") == ["statement_draft"]


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
