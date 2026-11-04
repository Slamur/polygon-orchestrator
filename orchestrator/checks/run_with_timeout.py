"""A timeout wrapper around running a generated binary.

Like compile_check, this is a minimal safety net instead of a full judging
loop (see CLAUDE.md, the "Scope (current)" section): just the fact "fit in
time or not" and the elapsed (wall-clock) time. Distinguishing TLE/MLE and other
verdict analysis is out of scope.
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
    """The soft stack limit — up to the hard one: on Polygon the stack is large, and deep
    recursion in C++ mustn't fail just because of the default 8 MB."""
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
    """Runs `binary` with `args`, killing the process after `timeout_seconds`.
    `unlimited_stack` — raise the process's stack limit to the hard one."""

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
        stdout = (
            exc.stdout
            if isinstance(exc.stdout, str)
            else (exc.stdout or b"").decode(errors="replace")
        )
        stderr = (
            exc.stderr
            if isinstance(exc.stderr, str)
            else (exc.stderr or b"").decode(errors="replace")
        )
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
