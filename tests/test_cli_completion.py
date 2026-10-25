"""Tab-дополнение адреса задачи и условие, на котором оно держится: модуль
`cli` не тянет пайплайны при импорте (см. докстринг `orchestrator/cli.py`)."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

from orchestrator import cli, pipeline, step_order
from orchestrator.polygon import pipeline as polygon_pipeline

REPO_ROOT = Path(__file__).parent.parent


def _touch(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("", encoding="utf-8")


@pytest.fixture
def base(tmp_path):
    _touch(tmp_path / "problems" / "my-task" / "spec.yaml")
    _touch(tmp_path / "problems" / "contest" / "2026" / "example-problem" / "spec.yaml")
    _touch(tmp_path / "problems" / "contest" / "pulled-only" / "polygon_state.json")
    (tmp_path / "problems" / "contest" / "empty-group").mkdir()
    _touch(tmp_path / "problems" / ".hidden" / "spec.yaml")
    _touch(tmp_path / "problems" / "stray-file")
    _touch(tmp_path / "examples" / "array" / "prefix-sums" / "spec.yaml")
    return tmp_path


def _parse(base: Path, *command: str):
    return cli.build_parser().parse_args(["--base-dir", str(base), *command])


def test_completer_lists_problem_addresses_in_both_roots(base):
    args = _parse(base, "problems/x", "polygon", "status")

    assert cli._complete_problem_addresses("", args) == [
        "problems/contest/2026/example-problem",
        "problems/contest/pulled-only",
        "problems/my-task",
        "examples/array/prefix-sums",
    ]


def test_completers_tolerate_missing_directories(tmp_path):
    args = _parse(tmp_path / "nothing", "problems/x", "polygon", "status")

    assert cli._complete_problem_addresses("", args) == []
    assert cli._complete_draft_names("", args) == []


def test_draft_completer_lists_draft_files(base):
    _touch(base / "drafts" / "my-idea.md")
    _touch(base / "drafts" / ".gitkeep")
    args = _parse(base, "problems/x", "init")

    assert cli._complete_draft_names("", args) == ["my-idea.md"]


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


def test_tab_completes_problem_address_by_prefix(base):
    pytest.importorskip("argcomplete")
    line = f"orchestrator --base-dir {base}"

    assert _complete(f"{line} problems/m") == ["problems/my-task"]
    assert _complete(f"{line} problems/contest/") == [
        "problems/contest/2026/example-problem",
        "problems/contest/pulled-only",
    ]
    assert _complete(f"{line} ex") == ["examples/array/prefix-sums"]


def test_tab_completes_commands_and_step_names():
    pytest.importorskip("argcomplete")

    assert _complete("orchestrator problems/my-task polygon pu") == ["push", "pull"]
    assert _complete("orchestrator problems/my-task llm generate --step statement") == [
        "statement_draft"
    ]


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
