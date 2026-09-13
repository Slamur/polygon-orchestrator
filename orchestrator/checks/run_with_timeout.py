"""Timeout-обёртка вокруг запуска сгенерированного бинаря.

Как и compile_check, это минимальная страховка вместо полноценного судящего
контура (см. CLAUDE.md, раздел "Скоуп (текущий)"): просто факт "уложился по
времени или нет". Различение TLE/MLE и прочий анализ вердикта — вне скоупа.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass
class RunResult:
    stdout: str
    stderr: str
    exit_code: int | None
    timed_out: bool


def run_with_timeout(
    binary: Path,
    args: list[str],
    timeout_seconds: float,
    stdin_data: str | None = None,
) -> RunResult:
    """Запускает `binary` с `args`, убивая процесс по `timeout_seconds`."""

    try:
        result = subprocess.run(
            [str(binary), *args],
            input=stdin_data,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout if isinstance(exc.stdout, str) else (exc.stdout or b"").decode(errors="replace")
        stderr = exc.stderr if isinstance(exc.stderr, str) else (exc.stderr or b"").decode(errors="replace")
        return RunResult(stdout=stdout, stderr=stderr, exit_code=None, timed_out=True)

    return RunResult(
        stdout=result.stdout,
        stderr=result.stderr,
        exit_code=result.returncode,
        timed_out=False,
    )
