"""Timeout-обёртка вокруг запуска сгенерированного бинаря.

Как и compile_check, это минимальная страховка вместо полноценного судящего
контура (см. CLAUDE.md, раздел "Скоуп (текущий)"): просто факт "уложился по
времени или нет" и прошедшее (wall-clock) время. Различение TLE/MLE и прочий
анализ вердикта — вне скоупа.
"""

from __future__ import annotations

import resource
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path


@dataclass
class RunResult:
    stdout: str
    stderr: str
    exit_code: int | None
    timed_out: bool
    elapsed_seconds: float = 0.0


def _raise_stack_limit() -> None:
    """Мягкий лимит стека — до жёсткого: на Polygon стек большой, и глубокая
    рекурсия в C++ не должна падать только из-за дефолтных 8 МБ."""
    _, hard = resource.getrlimit(resource.RLIMIT_STACK)
    resource.setrlimit(resource.RLIMIT_STACK, (hard, hard))


def run_with_timeout(
    binary: Path,
    args: list[str],
    timeout_seconds: float,
    stdin_data: str | None = None,
    *,
    unlimited_stack: bool = False,
) -> RunResult:
    """Запускает `binary` с `args`, убивая процесс по `timeout_seconds`.
    `unlimited_stack` — поднять лимит стека процесса до жёсткого."""

    started = time.monotonic()
    try:
        result = subprocess.run(
            [str(binary), *args],
            input=stdin_data,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            preexec_fn=_raise_stack_limit if unlimited_stack else None,
        )
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout if isinstance(exc.stdout, str) else (exc.stdout or b"").decode(errors="replace")
        stderr = exc.stderr if isinstance(exc.stderr, str) else (exc.stderr or b"").decode(errors="replace")
        return RunResult(
            stdout=stdout,
            stderr=stderr,
            exit_code=None,
            timed_out=True,
            elapsed_seconds=time.monotonic() - started,
        )

    return RunResult(
        stdout=result.stdout,
        stderr=result.stderr,
        exit_code=result.returncode,
        timed_out=False,
        elapsed_seconds=time.monotonic() - started,
    )
