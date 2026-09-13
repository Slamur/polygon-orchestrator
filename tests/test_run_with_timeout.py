import shutil
from pathlib import Path

import pytest

from orchestrator.checks.run_with_timeout import RunResult, run_with_timeout

SLEEP_BINARY = shutil.which("sleep")


@pytest.mark.skipif(SLEEP_BINARY is None, reason="системная утилита sleep не найдена")
def test_run_with_timeout_kills_long_running_process():
    result = run_with_timeout(Path(SLEEP_BINARY), ["2"], timeout_seconds=0.2, stdin_data=None)

    assert isinstance(result, RunResult)
    assert result.timed_out is True
    assert result.exit_code is None


def test_run_with_timeout_returns_output_within_timeout():
    echo_binary = shutil.which("echo")
    assert echo_binary is not None

    result = run_with_timeout(Path(echo_binary), ["hello"], timeout_seconds=5.0, stdin_data=None)

    assert result.timed_out is False
    assert result.exit_code == 0
    assert result.stdout.strip() == "hello"


def test_run_with_timeout_passes_stdin_data():
    cat_binary = shutil.which("cat")
    assert cat_binary is not None

    result = run_with_timeout(Path(cat_binary), [], timeout_seconds=5.0, stdin_data="ping\n")

    assert result.timed_out is False
    assert result.exit_code == 0
    assert result.stdout == "ping\n"
